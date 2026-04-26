"""
Historical OHLCV data fetcher.
Primary source: Upstox API (requires access token).
Fallback: yfinance (free, no auth needed — good for backtesting).
"""
import logging
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf
import pytz

log = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# yfinance ticker map for Indian indices
YF_TICKERS = {
    "NIFTY":     "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "FINNIFTY":  "NIFTY_FIN_SERVICE.NS",
}

# Upstox instrument key map
UPSTOX_KEYS = {
    "NIFTY":     "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY":  "NSE_INDEX|Nifty Fin Service",
}


def fetch_ohlcv(
    symbol: str,
    interval: str = "5m",
    days: int = 30,
    use_upstox: bool = False,
) -> pd.DataFrame:
    """
    Fetch OHLCV data for a symbol.

    Args:
        symbol:      'NIFTY', 'BANKNIFTY', or 'FINNIFTY'
        interval:    '1m', '5m', '15m', '1d'
        days:        how many calendar days of history
        use_upstox:  True = Upstox API (needs token), False = yfinance

    Returns:
        DataFrame with columns: open, high, low, close, volume, datetime (IST)
    """
    if use_upstox:
        return _fetch_upstox(symbol, interval, days)
    return _fetch_yfinance(symbol, interval, days)


def _fetch_yfinance(symbol: str, interval: str, days: int) -> pd.DataFrame:
    ticker = YF_TICKERS.get(symbol.upper())
    if not ticker:
        raise ValueError(f"Unknown symbol: {symbol}")

    end = datetime.now()
    start = end - timedelta(days=days)

    log.info(f"Fetching {symbol} ({ticker}) | interval={interval} | days={days}")
    df = yf.download(ticker, start=start, end=end, interval=interval, progress=False)

    if df.empty:
        raise RuntimeError(f"No data returned from yfinance for {symbol}")

    df = df.rename(columns=str.lower)
    df.index = pd.to_datetime(df.index).tz_convert(IST)
    df.index.name = "datetime"
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    df = df.round(2)
    log.info(f"Fetched {len(df)} candles for {symbol}")
    return df


def _fetch_upstox(symbol: str, interval: str, days: int) -> pd.DataFrame:
    """Fetch via Upstox HistoricalDataV2 API."""
    from config.settings import settings
    import upstox_client

    configuration = upstox_client.Configuration()
    configuration.access_token = settings.upstox_access_token

    api = upstox_client.HistoryApi(upstox_client.ApiClient(configuration))
    instrument_key = UPSTOX_KEYS.get(symbol.upper())
    if not instrument_key:
        raise ValueError(f"Unknown symbol: {symbol}")

    _interval_map = {"1m": "1minute", "5m": "5minute", "15m": "15minute", "1d": "day"}
    upstox_interval = _interval_map.get(interval, "5minute")

    end_date = datetime.now(IST).strftime("%Y-%m-%d")
    start_date = (datetime.now(IST) - timedelta(days=days)).strftime("%Y-%m-%d")

    log.info(f"Fetching {symbol} from Upstox | interval={upstox_interval}")
    resp = api.get_historical_candle_data1(instrument_key, upstox_interval, end_date, start_date, api_version="2.0")

    candles = resp.data.candles
    df = pd.DataFrame(candles, columns=["datetime", "open", "high", "low", "close", "volume", "oi"])
    df["datetime"] = pd.to_datetime(df["datetime"]).dt.tz_convert(IST)
    df = df.set_index("datetime").sort_index()
    df = df[["open", "high", "low", "close", "volume"]].round(2)
    log.info(f"Fetched {len(df)} candles for {symbol} from Upstox")
    return df


if __name__ == "__main__":
    # Quick test — run: python -m data.historical
    logging.basicConfig(level=logging.INFO)
    df = fetch_ohlcv("NIFTY", interval="5m", days=5)
    print(df.tail(10))
