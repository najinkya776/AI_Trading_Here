"""
Walk-forward backtester.
Runs technical signals on historical OHLCV data and produces a performance report.

Usage:
    python -m backtest.backtester --symbol NIFTY --days 180 --interval 5m
"""
import argparse
import logging
import numpy as np
import pandas as pd

from data.historical import fetch_ohlcv
from signals.technical import add_indicators
from config.settings import settings

log = logging.getLogger(__name__)


def run_backtest(
    symbol: str = "NIFTY",
    interval: str = "5m",
    days: int = 180,
    sl_atr_mult: float = 1.5,
    target_atr_mult: float = 3.0,
) -> dict:
    """
    Simulate trades on historical data using the SuperTrend + VWAP strategy.
    Returns a performance report dict.
    """
    print(f"\nRunning backtest | {symbol} | {interval} | {days} days")
    df = fetch_ohlcv(symbol, interval=interval, days=days)
    df = add_indicators(df)

    capital = settings.paper_capital
    initial_capital = capital
    trades = []
    position = None

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]

        # Skip first 15 min and last 20 min of day
        hour, minute = row.name.hour, row.name.minute
        total_min = hour * 60 + minute
        if total_min < 9 * 60 + 30 or total_min >= 15 * 60 + 0:
            continue

        if position is None:
            # Entry conditions: SuperTrend direction flips + price crosses VWAP
            if (prev["supertrend_dir"] == -1 and row["supertrend_dir"] == 1
                    and row["close"] > row["vwap"]
                    and row["adx"] > 20):
                sl     = row["close"] - row["atr"] * sl_atr_mult
                target = row["close"] + row["atr"] * target_atr_mult
                risk_per_unit = row["close"] - sl
                qty = max(1, int((capital * (settings.risk_per_trade_pct / 100)) / risk_per_unit))
                position = {"type": "BUY", "entry": row["close"], "sl": sl,
                            "target": target, "qty": qty, "entry_idx": i}

            elif (prev["supertrend_dir"] == 1 and row["supertrend_dir"] == -1
                    and row["close"] < row["vwap"]
                    and row["adx"] > 20):
                sl     = row["close"] + row["atr"] * sl_atr_mult
                target = row["close"] - row["atr"] * target_atr_mult
                risk_per_unit = sl - row["close"]
                qty = max(1, int((capital * (settings.risk_per_trade_pct / 100)) / risk_per_unit))
                position = {"type": "SELL", "entry": row["close"], "sl": sl,
                            "target": target, "qty": qty, "entry_idx": i}

        else:
            # Check exit conditions
            hit = None
            if position["type"] == "BUY":
                if row["low"] <= position["sl"]:
                    hit, exit_px = "SL", position["sl"]
                elif row["high"] >= position["target"]:
                    hit, exit_px = "TARGET", position["target"]
            else:
                if row["high"] >= position["sl"]:
                    hit, exit_px = "SL", position["sl"]
                elif row["low"] <= position["target"]:
                    hit, exit_px = "TARGET", position["target"]

            # End-of-day square-off
            if hit is None and total_min >= 15 * 60 - 5:
                hit, exit_px = "EOD", row["close"]

            if hit:
                mult = 1 if position["type"] == "BUY" else -1
                pnl = mult * (exit_px - position["entry"]) * position["qty"]
                capital += pnl
                trades.append({
                    "entry_price": position["entry"],
                    "exit_price": exit_px,
                    "type": position["type"],
                    "qty": position["qty"],
                    "pnl": round(pnl, 2),
                    "exit_reason": hit,
                    "duration_bars": i - position["entry_idx"],
                })
                position = None

    return _report(trades, initial_capital, capital)


def _report(trades: list, initial_capital: float, final_capital: float) -> dict:
    if not trades:
        print("No trades generated.")
        return {}

    df = pd.DataFrame(trades)
    wins = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]

    total_pnl   = df["pnl"].sum()
    win_rate    = len(wins) / len(df) * 100
    avg_win     = wins["pnl"].mean() if len(wins) else 0
    avg_loss    = losses["pnl"].mean() if len(losses) else 0
    profit_factor = abs(wins["pnl"].sum() / losses["pnl"].sum()) if len(losses) else float("inf")

    # Drawdown
    equity = np.array([initial_capital] + list(initial_capital + df["pnl"].cumsum()))
    peak   = np.maximum.accumulate(equity)
    dd     = (equity - peak) / peak * 100
    max_dd = abs(dd.min())

    # Sharpe (annualized, assume 250 trading days)
    daily_returns = df["pnl"] / initial_capital
    sharpe = (daily_returns.mean() / daily_returns.std() * np.sqrt(250)) if daily_returns.std() > 0 else 0

    report = {
        "total_trades": len(df),
        "win_rate_pct": round(win_rate, 2),
        "total_pnl": round(total_pnl, 2),
        "final_capital": round(final_capital, 2),
        "return_pct": round((final_capital - initial_capital) / initial_capital * 100, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe_ratio": round(sharpe, 2),
        "sl_exits": int((df["exit_reason"] == "SL").sum()),
        "target_exits": int((df["exit_reason"] == "TARGET").sum()),
        "eod_exits": int((df["exit_reason"] == "EOD").sum()),
    }

    print("\n=== BACKTEST RESULTS ===")
    for k, v in report.items():
        print(f"  {k:<25} {v}")
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="NIFTY")
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--interval", default="5m")
    args = parser.parse_args()
    run_backtest(symbol=args.symbol, days=args.days, interval=args.interval)
