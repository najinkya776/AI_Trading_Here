"""
NSE Live Market Data Aggregator
================================
Builds a complete real-time market snapshot for the AI analyst:
  - 5-min candles + all technical indicators  (primary signal timeframe)
  - 15-min candles + all technical indicators (trend confirmation)
  - India VIX                                 (volatility filter)
  - Options chain: PCR, Max Pain, OI buildup  (institutional flow)
  - Opening Range Breakout levels             (ORB setup detector)
  - Time-window classification                (ORB / TREND / AFTERNOON / AVOID)

Usage:
    from data.nse_live import get_market_snapshot
    snapshot = get_market_snapshot("NIFTY")
"""

import logging
from datetime import datetime

import pytz
import pandas as pd

from data.historical import fetch_ohlcv
from data.options_chain import get_options_chain, get_pcr, get_max_pain
from signals.technical import add_indicators
from signals.regime import _get_india_vix

log = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# High-probability trading windows — (start_hhmm, end_hhmm, label)
TIME_WINDOWS = [
    (920,  945,  "ORB"),        # Opening Range Breakout: high momentum, tight SL
    (1030, 1130, "TREND"),      # Post-volatility trend continuation
    (1400, 1500, "AFTERNOON"),  # Afternoon trend push before EOD
]


def get_market_snapshot(symbol: str = "NIFTY") -> dict:
    """
    Fetch and package everything the AI analyst needs to decide on a trade.
    Returns a dict. On any fetch error, 'error' key is set and partial data returned.
    """
    now = datetime.now(IST)
    snapshot: dict = {
        "symbol":        symbol,
        "timestamp":     now.isoformat(),
        "current_price": 0.0,
        "error":         None,
    }

    try:
        # ── 1. Candle data — 5-min primary + 15-min confirmation ──────────────
        df_5m  = fetch_ohlcv(symbol, interval="5m",  days=5)
        df_15m = fetch_ohlcv(symbol, interval="15m", days=5)

        df_5m  = add_indicators(df_5m)
        df_15m = add_indicators(df_15m)

        snapshot["current_price"] = round(float(df_5m["close"].iloc[-1]), 2)
        snapshot["tf_5m"]         = _extract_tf_data(df_5m,  n_candles=20)
        snapshot["tf_15m"]        = _extract_tf_data(df_15m, n_candles=10)

        # ── 2. Opening Range Breakout levels ──────────────────────────────────
        snapshot["orb"] = _compute_orb(df_5m, now)

        # ── 3. India VIX ──────────────────────────────────────────────────────
        vix = None
        try:
            vix = _get_india_vix()
        except Exception as e:
            log.warning(f"VIX fetch failed: {e}")
        snapshot["india_vix"] = vix
        snapshot["vix_level"] = _classify_vix(vix)

        # ── 4. Options chain ──────────────────────────────────────────────────
        snapshot["options"] = _get_options_summary(symbol, snapshot["current_price"])

        # ── 5. Time context ───────────────────────────────────────────────────
        snapshot["time_context"] = _get_time_context(now)

    except Exception as e:
        log.error(f"get_market_snapshot failed for {symbol}: {e}", exc_info=True)
        snapshot["error"] = str(e)

    return snapshot


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_tf_data(df: pd.DataFrame, n_candles: int) -> dict:
    """Extract indicator snapshot + recent candles from a computed DataFrame."""
    last  = df.iloc[-1]
    recent = df.tail(n_candles)

    bullish = sum([
        last["supertrend_dir"] == 1,
        last["close"]  > last["vwap"],
        last["ema9"]   > last["ema21"],
        last["rsi"]    > 50,
        last["macd"]   > last["macd_signal"],
        last["dmp"]    > last["dmn"],
    ])
    bearish = 6 - bullish

    candles = []
    for ts, row in recent.iterrows():
        candles.append({
            "time":   ts.strftime("%H:%M"),
            "open":   round(float(row["open"]),   2),
            "high":   round(float(row["high"]),   2),
            "low":    round(float(row["low"]),    2),
            "close":  round(float(row["close"]),  2),
            "volume": int(row["volume"]),
        })

    return {
        "candles":       candles,
        "bullish_count": bullish,
        "bearish_count": bearish,
        "bias":          "BULLISH" if bullish >= 4 else ("BEARISH" if bearish >= 4 else "NEUTRAL"),
        "indicators": {
            "supertrend_dir": int(last["supertrend_dir"]),
            "vwap":           round(float(last["vwap"]),         2),
            "ema9":           round(float(last["ema9"]),         2),
            "ema21":          round(float(last["ema21"]),        2),
            "ema50":          round(float(last["ema50"]),        2),
            "rsi":            round(float(last["rsi"]),          2),
            "macd":           round(float(last["macd"]),         4),
            "macd_signal":    round(float(last["macd_signal"]), 4),
            "macd_hist":      round(float(last["macd_hist"]),   4),
            "atr":            round(float(last["atr"]),          2),
            "adx":            round(float(last["adx"]),          2),
            "bb_width":       round(float(last["bb_width"]),     4),
            "stoch_k":        round(float(last["stoch_k"]),      2),
            "stoch_d":        round(float(last["stoch_d"]),      2),
        },
    }


def _compute_orb(df_5m: pd.DataFrame, now: datetime) -> dict:
    """
    Opening Range = high/low of all 5-min candles from 9:15 to 9:44 AM IST.
    Breakout  = current price > ORB high  (BUY momentum)
    Breakdown = current price < ORB low   (SELL momentum)
    """
    today_candles = df_5m[df_5m.index.date == now.date()]
    orb_candles   = today_candles.between_time("09:15", "09:44")

    if orb_candles.empty:
        return {"high": 0, "low": 0, "range_size": 0, "breakout": False, "breakdown": False}

    orb_high = float(orb_candles["high"].max())
    orb_low  = float(orb_candles["low"].min())
    current  = float(df_5m["close"].iloc[-1])

    return {
        "high":       round(orb_high,          2),
        "low":        round(orb_low,           2),
        "range_size": round(orb_high - orb_low, 2),
        "breakout":   current > orb_high,
        "breakdown":  current < orb_low,
    }


def _get_options_summary(symbol: str, current_price: float) -> dict:
    """Fetch live options chain and extract institutional flow signals."""
    try:
        pcr      = get_pcr(symbol)
        max_pain = get_max_pain(symbol)
        df_chain = get_options_chain(symbol)
    except Exception as e:
        log.warning(f"Options chain fetch failed for {symbol}: {e}")
        return {"pcr": 1.0, "max_pain": 0, "sentiment": "NEUTRAL", "error": str(e)}

    # Options sentiment
    if pcr > 1.2:
        sentiment = "BULLISH"   # heavy put writing = market makers expect price to hold/rise
    elif pcr < 0.8:
        sentiment = "BEARISH"   # heavy call writing = market makers capping upside
    else:
        sentiment = "NEUTRAL"

    top_ce_strike = 0.0
    top_pe_strike = 0.0
    iv_avg        = 0.0

    if not df_chain.empty:
        # Use nearest expiry
        expiry = df_chain["expiry"].iloc[0] if "expiry" in df_chain.columns else None
        exp_df = df_chain[df_chain["expiry"] == expiry] if expiry else df_chain

        if "ce_oi" in exp_df.columns and len(exp_df) > 0:
            top_ce_strike = float(exp_df.loc[exp_df["ce_oi"].idxmax(), "strike"])
        if "pe_oi" in exp_df.columns and len(exp_df) > 0:
            top_pe_strike = float(exp_df.loc[exp_df["pe_oi"].idxmax(), "strike"])
        if "ce_iv" in exp_df.columns:
            iv_avg = float(exp_df["ce_iv"].replace(0, float("nan")).mean())

    return {
        "pcr":                round(pcr, 3),
        "max_pain":           max_pain,
        "sentiment":          sentiment,
        "top_ce_oi_strike":   top_ce_strike,          # key resistance level
        "top_pe_oi_strike":   top_pe_strike,           # key support level
        "iv_avg":             round(iv_avg, 2) if iv_avg and iv_avg == iv_avg else 0,
        "price_vs_max_pain":  round(current_price - max_pain, 2) if max_pain else 0,
    }


def _classify_vix(vix) -> str:
    if vix is None:
        return "unknown"
    if vix > 20:
        return "high"
    if vix > 15:
        return "moderate"
    return "low"


def _get_time_context(now: datetime) -> dict:
    """Classify current time into a trading window and return market hour flags."""
    hhmm        = now.hour * 100 + now.minute
    current_min = now.hour * 60 + now.minute

    window = "AVOID"
    for start, end, name in TIME_WINDOWS:
        if start <= hhmm <= end:
            window = name
            break

    close_min     = 15 * 60
    mins_to_close = max(0, close_min - current_min)
    is_expiry     = now.weekday() == 3   # Thursday = weekly Nifty expiry

    return {
        "ist_time":          now.strftime("%H:%M"),
        "window":            window,
        "mins_to_close":     mins_to_close,
        "is_expiry":         is_expiry,
        "day_of_week":       now.strftime("%A"),
        "in_trading_hours":  9 * 60 + 30 <= current_min <= 15 * 60,
    }


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    snap = get_market_snapshot("NIFTY")
    # Print without candle arrays to keep output readable
    snap_display = {k: v for k, v in snap.items() if k not in ("tf_5m", "tf_15m")}
    if "tf_5m" in snap:
        snap_display["tf_5m_bias"]  = snap["tf_5m"].get("bias")
        snap_display["tf_5m_indicators"] = snap["tf_5m"].get("indicators")
    if "tf_15m" in snap:
        snap_display["tf_15m_bias"] = snap["tf_15m"].get("bias")
    print(json.dumps(snap_display, indent=2, default=str))
