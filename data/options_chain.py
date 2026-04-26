"""
NSE Options Chain Data Fetcher.
Fetches live OI (Open Interest), PCR (Put-Call Ratio), and Max Pain from NSE.

These are powerful F&O-specific signals:
  - PCR > 1.2  → bearish (more puts = hedging = smart money expects fall)
  - PCR < 0.8  → bullish (more calls = bullish sentiment)
  - Max Pain   → price tends to gravitate toward max pain on expiry
"""
import logging
import requests
import pandas as pd

log = logging.getLogger(__name__)

NSE_OPTION_CHAIN_URL = "https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

_session = requests.Session()
_session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=10)  # set cookies


def get_options_chain(symbol: str = "NIFTY") -> pd.DataFrame:
    """
    Fetch full options chain for a symbol.
    Returns DataFrame with columns: strike, expiry, ce_oi, pe_oi, ce_ltp, pe_ltp, ...
    """
    url = NSE_OPTION_CHAIN_URL.format(symbol=symbol.upper())
    try:
        resp = _session.get(url, headers=NSE_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.error(f"Failed to fetch options chain for {symbol}: {e}")
        return pd.DataFrame()

    records = []
    for row in data.get("records", {}).get("data", []):
        strike = row.get("strikePrice", 0)
        expiry = row.get("expiryDate", "")
        ce = row.get("CE", {})
        pe = row.get("PE", {})
        records.append({
            "strike":    strike,
            "expiry":    expiry,
            "ce_oi":     ce.get("openInterest", 0),
            "ce_chng_oi":ce.get("changeinOpenInterest", 0),
            "ce_ltp":    ce.get("lastPrice", 0),
            "ce_iv":     ce.get("impliedVolatility", 0),
            "pe_oi":     pe.get("openInterest", 0),
            "pe_chng_oi":pe.get("changeinOpenInterest", 0),
            "pe_ltp":    pe.get("lastPrice", 0),
            "pe_iv":     pe.get("impliedVolatility", 0),
        })

    df = pd.DataFrame(records)
    return df


def get_pcr(symbol: str = "NIFTY", expiry: str = None) -> float:
    """
    Put-Call Ratio based on total OI.
    PCR > 1.2 = bearish, PCR < 0.8 = bullish, 0.8–1.2 = neutral.
    """
    df = get_options_chain(symbol)
    if df.empty:
        return 1.0  # neutral fallback

    if expiry:
        df = df[df["expiry"] == expiry]

    total_ce_oi = df["ce_oi"].sum()
    total_pe_oi = df["pe_oi"].sum()

    if total_ce_oi == 0:
        return 1.0

    pcr = total_pe_oi / total_ce_oi
    log.info(f"{symbol} PCR = {pcr:.3f} (CE OI={total_ce_oi:,}, PE OI={total_pe_oi:,})")
    return round(pcr, 3)


def get_max_pain(symbol: str = "NIFTY", expiry: str = None) -> float:
    """
    Max Pain = strike price where total OI value (pain) is minimum for option writers.
    Price tends to close near max pain on expiry.
    """
    df = get_options_chain(symbol)
    if df.empty:
        return 0.0

    if expiry:
        df = df[df["expiry"] == expiry]
    else:
        # Use nearest expiry
        expiries = df["expiry"].unique()
        expiry = expiries[0] if len(expiries) > 0 else None
        if expiry:
            df = df[df["expiry"] == expiry]

    strikes = df["strike"].unique()
    pain_values = {}

    for test_strike in strikes:
        ce_pain = ((test_strike - df["strike"]).clip(lower=0) * df["ce_oi"]).sum()
        pe_pain = ((df["strike"] - test_strike).clip(lower=0) * df["pe_oi"]).sum()
        pain_values[test_strike] = ce_pain + pe_pain

    max_pain_strike = min(pain_values, key=pain_values.get)
    log.info(f"{symbol} Max Pain = {max_pain_strike}")
    return float(max_pain_strike)


def get_oi_summary(symbol: str = "NIFTY") -> dict:
    """Quick summary for dashboard and ML features."""
    pcr = get_pcr(symbol)
    max_pain = get_max_pain(symbol)

    if pcr > 1.2:
        sentiment = "BEARISH"
    elif pcr < 0.8:
        sentiment = "BULLISH"
    else:
        sentiment = "NEUTRAL"

    return {
        "symbol": symbol,
        "pcr": pcr,
        "max_pain": max_pain,
        "oi_sentiment": sentiment,
    }


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    print(get_oi_summary("NIFTY"))
    print(get_oi_summary("BANKNIFTY"))
