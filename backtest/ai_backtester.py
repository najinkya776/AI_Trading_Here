"""
AI Backtester — Claude Opus 4.7 vs the old 4-of-6 indicator gate.

How it works:
1. Fetch historical 5m + 15m OHLCV and compute indicators (yfinance, free).
2. Identify entry signals using the same SuperTrend+VWAP+ADX logic as the classic bot.
3. For each signal, build a synthetic market snapshot from historical data and ask Claude.
4. Simulate both systems independently and compare win rate, PnL, and filter behaviour.

NOTE: Historical options chain data (PCR, Max Pain, OI) is not freely available,
so the AI backtest uses neutral placeholder values (PCR=1.0, VIX=15). Live results
may differ as Claude receives real options data in production.

Usage:
    python -m backtest.ai_backtester --symbol NIFTY --days 30
    python -m backtest.ai_backtester --symbol BANKNIFTY --days 14 --max-signals 30
"""

import argparse
import logging
from datetime import time as dtime
from typing import Optional

import pandas as pd

from data.historical import fetch_ohlcv
from signals.technical import add_indicators
from config.settings import settings

log = logging.getLogger(__name__)

# Trading time windows (IST)
_WINDOWS = {
    "ORB":       (dtime(9, 20), dtime(9, 44)),
    "TREND":     (dtime(10, 30), dtime(11, 30)),
    "AFTERNOON": (dtime(14, 0),  dtime(15, 0)),
}


def _get_window(t: dtime) -> str:
    for name, (start, end) in _WINDOWS.items():
        if start <= t <= end:
            return name
    return "AVOID"


def _compute_bias(row: pd.Series) -> tuple[str, int, int]:
    bullish = int(sum([
        row.get("supertrend_dir", 0) == 1,
        float(row.get("close", 0)) > float(row.get("vwap", 0)),
        float(row.get("ema9",   0)) > float(row.get("ema21", 0)),
        float(row.get("rsi",   50)) > 50,
        float(row.get("macd_hist", 0)) > 0,
        float(row.get("dmp",   0)) > float(row.get("dmn", 0)),
    ]))
    bearish = 6 - bullish
    bias = "BULLISH" if bullish >= 4 else ("BEARISH" if bearish >= 4 else "NEUTRAL")
    return bias, bullish, bearish


def _build_snapshot(
    symbol: str,
    dt: pd.Timestamp,
    df5: pd.DataFrame,
    df15: pd.DataFrame,
    idx5: int,
) -> dict:
    """
    Build a market snapshot from historical indicator data.
    Options chain fields use neutral placeholders — not available historically.
    """
    row5  = df5.iloc[idx5]
    price = float(row5["close"])
    t     = dt.time()

    # Last 5 candles
    candles = []
    for j in range(max(0, idx5 - 4), idx5 + 1):
        r = df5.iloc[j]
        candles.append({
            "time":   df5.index[j].strftime("%H:%M"),
            "open":   float(r["open"]),
            "high":   float(r["high"]),
            "low":    float(r["low"]),
            "close":  float(r["close"]),
            "volume": int(r["volume"]),
        })

    bias5, bull5, bear5 = _compute_bias(row5)

    # Nearest 15m candle
    idx15 = max(0, min(
        df15.index.searchsorted(dt, side="right") - 1,
        len(df15) - 1,
    ))
    row15 = df15.iloc[idx15]
    bias15, bull15, _ = _compute_bias(row15)

    # ORB levels — first 30 min of same day
    day = dt.date()
    day_5m = df5[df5.index.date == day]
    orb_bars = day_5m[
        (day_5m.index.time >= dtime(9, 15)) &
        (day_5m.index.time <= dtime(9, 44))
    ]
    if not orb_bars.empty:
        orb_high = float(orb_bars["high"].max())
        orb_low  = float(orb_bars["low"].min())
    else:
        orb_high = round(price * 1.002, 2)
        orb_low  = round(price * 0.998, 2)

    total_min = t.hour * 60 + t.minute

    return {
        "symbol":        symbol,
        "current_price": round(price, 2),
        "timestamp":     str(dt),
        "india_vix":     15.0,
        "vix_level":     "NORMAL",
        "tf_5m": {
            "bias":          bias5,
            "bullish_count": bull5,
            "bearish_count": bear5,
            "candles":       candles,
            "indicators": {
                "rsi":            round(float(row5.get("rsi",       50)),    2),
                "adx":            round(float(row5.get("adx",       20)),    2),
                "atr":            round(float(row5.get("atr",       price * 0.003)), 2),
                "macd_hist":      round(float(row5.get("macd_hist",  0)),    4),
                "ema9":           round(float(row5.get("ema9",       price)), 2),
                "ema21":          round(float(row5.get("ema21",      price)), 2),
                "ema50":          round(float(row5.get("ema50",      price)), 2),
                "vwap":           round(float(row5.get("vwap",       price)), 2),
                "supertrend_dir": int(row5.get("supertrend_dir", 1)),
                "stoch_k":        round(float(row5.get("stoch_k",    50)),   2),
                "stoch_d":        round(float(row5.get("stoch_d",    50)),   2),
                "bb_width":       round(float(row5.get("bb_width",   0.02)), 4),
            },
        },
        "tf_15m": {
            "bias":          bias15,
            "bullish_count": bull15,
            "bearish_count": 6 - bull15,
            "indicators": {
                "rsi":            round(float(row15.get("rsi",        50)),    2),
                "adx":            round(float(row15.get("adx",        20)),    2),
                "ema9":           round(float(row15.get("ema9",       price)), 2),
                "ema21":          round(float(row15.get("ema21",      price)), 2),
                "macd_hist":      round(float(row15.get("macd_hist",   0)),    4),
                "supertrend_dir": int(row15.get("supertrend_dir", 1)),
            },
        },
        "orb": {
            "high":       orb_high,
            "low":        orb_low,
            "range_size": round(orb_high - orb_low, 2),
            "breakout":   price > orb_high,
            "breakdown":  price < orb_low,
        },
        "options": {
            # Neutral placeholders — historical options data not available
            "pcr":               1.0,
            "sentiment":         "NEUTRAL",
            "max_pain":          round(price * 0.998 / 100) * 100,
            "price_vs_max_pain": round(price - round(price * 0.998 / 100) * 100, 2),
            "top_ce_oi_strike":  round(price * 1.01 / 100) * 100,
            "top_pe_oi_strike":  round(price * 0.99 / 100) * 100,
            "iv_avg":            15.0,
        },
        "time_context": {
            "window":           _get_window(t),
            "ist_time":         dt.strftime("%H:%M"),
            "in_trading_hours": True,
            "is_expiry":        dt.weekday() == 3,  # Thursday
            "mins_to_close":    max(0, 15 * 60 - total_min),
        },
    }


def _check_exit(pos: dict, row: pd.Series, total_min: int) -> tuple[Optional[str], float]:
    hit: Optional[str] = None
    exit_px = 0.0
    if pos["type"] == "BUY":
        if float(row["low"]) <= pos["sl"]:
            hit, exit_px = "SL", pos["sl"]
        elif float(row["high"]) >= pos["target"]:
            hit, exit_px = "TARGET", pos["target"]
    else:
        if float(row["high"]) >= pos["sl"]:
            hit, exit_px = "SL", pos["sl"]
        elif float(row["low"]) <= pos["target"]:
            hit, exit_px = "TARGET", pos["target"]
    if hit is None and total_min >= 15 * 60 - 5:
        hit, exit_px = "EOD", float(row["close"])
    return hit, exit_px


def _stats(trades: list, initial: float, final: float) -> dict:
    if not trades:
        return {"total": 0, "win_rate_pct": 0.0, "total_pnl": 0.0,
                "return_pct": 0.0, "avg_win": 0.0, "avg_loss": 0.0}
    df = pd.DataFrame(trades)
    wins   = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]
    return {
        "total":        len(df),
        "win_rate_pct": round(len(wins) / len(df) * 100, 1),
        "total_pnl":    round(df["pnl"].sum(), 2),
        "return_pct":   round((final - initial) / initial * 100, 2),
        "avg_win":      round(float(wins["pnl"].mean()), 2)   if len(wins)   else 0.0,
        "avg_loss":     round(float(losses["pnl"].mean()), 2) if len(losses) else 0.0,
    }


def run_ai_backtest(
    symbol: str = "NIFTY",
    days: int = 30,
    max_signals: int = 50,
    sl_atr_mult: float = 1.5,
    target_atr_mult: float = 3.0,
) -> dict:
    """
    Replay historical signals through Claude and the classic 4-of-6 gate side by side.

    Returns a comparison report dict with keys:
        signals_evaluated, old_system, ai_system,
        ai_filter_rate_pct, win_rate_improvement, pnl_improvement
    """
    print(f"\n{'='*60}")
    print(f"AI Backtest  |  {symbol}  |  {days} days  |  max_signals={max_signals}")
    print(f"NOTE: Each signal calls Claude Opus 4.7 (~$0.015/call)")
    print(f"Estimated cost: ~${max_signals * 0.015:.2f}")
    print(f"{'='*60}")

    df5  = add_indicators(fetch_ohlcv(symbol, interval="5m",  days=days))
    df15 = add_indicators(fetch_ohlcv(symbol, interval="15m", days=days))

    from agents.market_analyst import MarketAnalyst
    analyst = MarketAnalyst()

    initial_capital = settings.paper_capital
    capital_old = initial_capital
    capital_ai  = initial_capital

    old_trades:  list[dict] = []
    ai_trades:   list[dict] = []
    ai_skipped  = 0
    signals_evaluated = 0
    position_old: Optional[dict] = None
    position_ai:  Optional[dict] = None

    for i in range(1, len(df5)):
        row  = df5.iloc[i]
        prev = df5.iloc[i - 1]
        dt   = df5.index[i]

        hour, minute = dt.hour, dt.minute
        total_min = hour * 60 + minute
        if total_min < 9 * 60 + 30 or total_min >= 15 * 60:
            continue

        # ── Check exits ──────────────────────────────────────────────────────
        if position_old:
            hit, exit_px = _check_exit(position_old, row, total_min)
            if hit:
                mult = 1 if position_old["type"] == "BUY" else -1
                pnl  = round(mult * (exit_px - position_old["entry"]) * position_old["qty"], 2)
                capital_old += pnl
                old_trades.append({**position_old, "exit_price": exit_px,
                                   "pnl": pnl, "exit_reason": hit})
                position_old = None

        if position_ai:
            hit, exit_px = _check_exit(position_ai, row, total_min)
            if hit:
                mult = 1 if position_ai["type"] == "BUY" else -1
                pnl  = round(mult * (exit_px - position_ai["entry"]) * position_ai["qty"], 2)
                capital_ai += pnl
                ai_trades.append({**position_ai, "exit_price": exit_px,
                                  "pnl": pnl, "exit_reason": hit})
                position_ai = None

        # ── Check for entry signal ────────────────────────────────────────────
        if signals_evaluated >= max_signals:
            continue

        signal_action: Optional[str] = None
        if (prev["supertrend_dir"] == -1 and row["supertrend_dir"] == 1
                and float(row["close"]) > float(row["vwap"])
                and float(row["adx"]) > 20):
            signal_action = "BUY"
        elif (prev["supertrend_dir"] == 1 and row["supertrend_dir"] == -1
                and float(row["close"]) < float(row["vwap"])
                and float(row["adx"]) > 20):
            signal_action = "SELL"

        if signal_action is None:
            continue

        signals_evaluated += 1
        price = float(row["close"])
        atr   = float(row["atr"])

        # ── Old system: always enters on signal ──────────────────────────────
        if position_old is None:
            sl  = round(price - atr * sl_atr_mult, 2) if signal_action == "BUY" \
                  else round(price + atr * sl_atr_mult, 2)
            tgt = round(price + atr * target_atr_mult, 2) if signal_action == "BUY" \
                  else round(price - atr * target_atr_mult, 2)
            risk = abs(price - sl)
            qty  = max(1, int((capital_old * settings.risk_per_trade_pct / 100) / risk)) if risk > 0 else 1
            position_old = {"type": signal_action, "entry": price,
                            "sl": sl, "target": tgt, "qty": qty, "entry_idx": i}

        # ── AI system: only enters if Claude agrees ───────────────────────────
        if position_ai is None:
            try:
                snapshot = _build_snapshot(symbol, dt, df5, df15, i)
                signal_d = {"symbol": symbol, "action": signal_action,
                            "price": price, "strategy": "AI_Backtest", "timeframe": "5"}
                decision   = analyst.analyze(signal_d, snapshot)
                ai_action  = decision.get("decision", "SKIP")
                confidence = decision.get("confidence", 0.0)

                print(
                    f"  [{dt.strftime('%Y-%m-%d %H:%M')}]  signal={signal_action}  "
                    f"AI={ai_action} ({confidence:.0%})  "
                    f"{decision.get('reasoning', '')[:70]}"
                )

                if ai_action != "SKIP":
                    entry_ai = float(decision.get("entry_price") or price)
                    sl_ai    = float(decision.get("stop_loss")   or (
                                   round(price - atr * sl_atr_mult, 2) if ai_action == "BUY"
                                   else round(price + atr * sl_atr_mult, 2)))
                    tgt_ai   = float(decision.get("target")      or (
                                   round(price + atr * target_atr_mult, 2) if ai_action == "BUY"
                                   else round(price - atr * target_atr_mult, 2)))
                    risk_ai  = abs(entry_ai - sl_ai)
                    qty_ai   = max(1, int((capital_ai * settings.risk_per_trade_pct / 100) / risk_ai)) if risk_ai > 0 else 1
                    position_ai = {"type": ai_action, "entry": entry_ai,
                                   "sl": sl_ai, "target": tgt_ai, "qty": qty_ai, "entry_idx": i}
                else:
                    ai_skipped += 1

            except Exception as e:
                log.error(f"MarketAnalyst error at {dt}: {e}")
                ai_skipped += 1

    old_stats = _stats(old_trades, initial_capital, capital_old)
    ai_stats  = _stats(ai_trades,  initial_capital, capital_ai)

    report = {
        "signals_evaluated":    signals_evaluated,
        "old_system":           {**old_stats, "skipped": signals_evaluated - len(old_trades)},
        "ai_system":            {**ai_stats,  "skipped": ai_skipped},
        "ai_filter_rate_pct":   round(ai_skipped / max(1, signals_evaluated) * 100, 1),
        "win_rate_improvement": round(ai_stats["win_rate_pct"] - old_stats["win_rate_pct"], 1),
        "pnl_improvement":      round(ai_stats["total_pnl"]   - old_stats["total_pnl"],    2),
    }

    _print_report(report)
    return report


def _print_report(r: dict):
    old = r["old_system"]
    ai  = r["ai_system"]
    print(f"\n{'='*60}")
    print(f"COMPARISON REPORT — {r['signals_evaluated']} signals evaluated")
    print(f"{'='*60}")
    print(f"{'Metric':<28} {'Old System':>12} {'AI System':>12}")
    print(f"{'-'*52}")
    for k, label in [
        ("total",        "Trades Taken"),
        ("win_rate_pct", "Win Rate (%)"),
        ("total_pnl",    "Total PnL (₹)"),
        ("return_pct",   "Return (%)"),
        ("avg_win",      "Avg Win (₹)"),
        ("avg_loss",     "Avg Loss (₹)"),
        ("skipped",      "Skipped"),
    ]:
        print(f"{label:<28} {old.get(k, 0):>12} {ai.get(k, 0):>12}")
    print(f"{'AI Filter Rate':<28} {'—':>12} {r['ai_filter_rate_pct']:>11}%")
    print(f"{'Win Rate Improvement':<28} {'—':>12} {r['win_rate_improvement']:>+11.1f}%")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description="AI vs Classic System Backtest")
    parser.add_argument("--symbol",      default="NIFTY",
                        help="NIFTY or BANKNIFTY")
    parser.add_argument("--days",        type=int, default=30,
                        help="Days of history to replay")
    parser.add_argument("--max-signals", type=int, default=50,
                        help="Max Claude API calls. 50 signals ≈ $0.75 (Opus 4.7)")
    args = parser.parse_args()
    run_ai_backtest(symbol=args.symbol, days=args.days, max_signals=args.max_signals)
