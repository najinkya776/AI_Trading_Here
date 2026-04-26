"""
Market Regime Detector.

Classifies the current market as:
  - "trending"  → ADX > 25, strong directional move (best for our strategies)
  - "ranging"   → ADX < 20, choppy sideways market (avoid or use mean-reversion)
  - "volatile"  → India VIX spike / high ATR relative to average (dangerous, reduce size)

Called before every trade to decide whether to participate.
"""
import logging
import requests
import pandas as pd
import pandas_ta as ta

from data.historical import fetch_ohlcv

log = logging.getLogger(__name__)

VIX_DANGER_LEVEL = 20.0     # India VIX above this = volatile regime
ADX_TREND_LEVEL  = 25.0     # ADX above this = trending
ADX_RANGE_LEVEL  = 20.0     # ADX below this = ranging


def get_regime(symbol: str = "NIFTY") -> str:
    """
    Returns: 'trending' | 'ranging' | 'volatile'
    """
    try:
        vix = _get_india_vix()
        if vix and vix > VIX_DANGER_LEVEL:
            log.info(f"Regime=volatile | VIX={vix:.1f}")
            return "volatile"
    except Exception as e:
        log.warning(f"Could not fetch India VIX: {e}")

    try:
        adx = _get_adx(symbol)
        if adx > ADX_TREND_LEVEL:
            log.info(f"Regime=trending | ADX={adx:.1f}")
            return "trending"
        elif adx < ADX_RANGE_LEVEL:
            log.info(f"Regime=ranging | ADX={adx:.1f}")
            return "ranging"
        else:
            log.info(f"Regime=trending (borderline) | ADX={adx:.1f}")
            return "trending"
    except Exception as e:
        log.warning(f"Could not compute ADX for regime: {e}")
        return "ranging"  # safe default: skip the trade


def _get_india_vix() -> float | None:
    """Fetch India VIX from NSE website (unofficial, may change)."""
    url = "https://www.nseindia.com/api/allIndices"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Referer": "https://www.nseindia.com",
    }
    resp = requests.get(url, headers=headers, timeout=5)
    resp.raise_for_status()
    data = resp.json()
    for item in data.get("data", []):
        if item.get("indexSymbol") == "India VIX":
            return float(item["last"])
    return None


def _get_adx(symbol: str, period: int = 14) -> float:
    df = fetch_ohlcv(symbol, interval="15m", days=3)
    adx_df = ta.adx(df["high"], df["low"], df["close"], length=period)
    return float(adx_df["ADX_14"].iloc[-1])


def regime_summary() -> dict:
    """Full regime snapshot — used by dashboard."""
    vix = None
    try:
        vix = _get_india_vix()
    except Exception:
        pass

    nifty_regime = get_regime("NIFTY")
    bank_regime  = get_regime("BANKNIFTY")

    return {
        "india_vix": vix,
        "nifty_regime": nifty_regime,
        "banknifty_regime": bank_regime,
        "trade_allowed": nifty_regime != "volatile",
    }
