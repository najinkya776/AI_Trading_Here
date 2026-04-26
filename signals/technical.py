"""
Technical indicator engine.
Computes 20+ indicators on OHLCV data and returns a signal bias (BULLISH/BEARISH/NEUTRAL)
plus key values the ML model uses as features.
"""
import logging
import pandas as pd
import pandas_ta as ta
import numpy as np

from data.historical import fetch_ohlcv

log = logging.getLogger(__name__)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add all technical indicators as new columns to the dataframe."""
    df = df.copy()

    # Trend
    df["ema9"]  = ta.ema(df["close"], length=9)
    df["ema21"] = ta.ema(df["close"], length=21)
    df["ema50"] = ta.ema(df["close"], length=50)

    # Momentum
    df["rsi"]  = ta.rsi(df["close"], length=14)
    macd       = ta.macd(df["close"])
    df["macd"] = macd["MACD_12_26_9"]
    df["macd_signal"] = macd["MACDs_12_26_9"]
    df["macd_hist"]   = macd["MACDh_12_26_9"]

    # Volatility
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    bb = ta.bbands(df["close"], length=20)
    df["bb_upper"] = bb["BBU_20_2.0"]
    df["bb_lower"] = bb["BBL_20_2.0"]
    df["bb_mid"]   = bb["BBM_20_2.0"]
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]

    # SuperTrend (period=10, multiplier=3)
    st = ta.supertrend(df["high"], df["low"], df["close"], length=10, multiplier=3.0)
    df["supertrend"]     = st["SUPERT_10_3.0"]
    df["supertrend_dir"] = st["SUPERTd_10_3.0"]  # 1 = uptrend, -1 = downtrend

    # VWAP (only meaningful on intraday)
    df["vwap"] = ta.vwap(df["high"], df["low"], df["close"], df["volume"])

    # Volume
    df["vol_sma20"] = ta.sma(df["volume"], length=20)
    df["vol_ratio"] = df["volume"] / df["vol_sma20"]

    # ADX (trend strength)
    adx = ta.adx(df["high"], df["low"], df["close"], length=14)
    df["adx"]    = adx["ADX_14"]
    df["dmp"]    = adx["DMP_14"]   # +DI
    df["dmn"]    = adx["DMN_14"]   # -DI

    # Stochastic
    stoch = ta.stoch(df["high"], df["low"], df["close"])
    df["stoch_k"] = stoch["STOCHk_14_3_3"]
    df["stoch_d"] = stoch["STOCHd_14_3_3"]

    # Pivot points (daily, computed once per day)
    df["pivot"]    = (df["high"] + df["low"] + df["close"]) / 3
    df["r1"]       = 2 * df["pivot"] - df["low"]
    df["s1"]       = 2 * df["pivot"] - df["high"]

    # Price-derived features for ML
    df["body_size"]  = abs(df["close"] - df["open"]) / df["atr"]
    df["upper_wick"] = (df["high"] - df[["open", "close"]].max(axis=1)) / df["atr"]
    df["lower_wick"] = (df[["open", "close"]].min(axis=1) - df["low"]) / df["atr"]
    df["pct_change"] = df["close"].pct_change()

    return df.dropna()


def get_signal_context(symbol: str, timeframe: str = "5") -> dict:
    """
    Fetch recent data for a symbol and return a bias + key indicator values.
    Called by the webhook server on every alert to confirm or reject the TV signal.
    """
    interval = f"{timeframe}m"
    try:
        df = fetch_ohlcv(symbol, interval=interval, days=5)
        df = add_indicators(df)
    except Exception as e:
        log.warning(f"Could not compute signal context for {symbol}: {e}")
        return {"bias": "NEUTRAL", "atr": 0.0}

    last = df.iloc[-1]

    # Simple rule-based bias (will be augmented by ML in Week 3)
    bullish_signals = sum([
        last["supertrend_dir"] == 1,
        last["close"] > last["vwap"],
        last["ema9"] > last["ema21"],
        last["rsi"] > 50,
        last["macd"] > last["macd_signal"],
        last["dmp"] > last["dmn"],
    ])

    bearish_signals = sum([
        last["supertrend_dir"] == -1,
        last["close"] < last["vwap"],
        last["ema9"] < last["ema21"],
        last["rsi"] < 50,
        last["macd"] < last["macd_signal"],
        last["dmn"] > last["dmp"],
    ])

    if bullish_signals >= 4:
        bias = "BULLISH"
    elif bearish_signals >= 4:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    return {
        "bias": bias,
        "atr": round(float(last["atr"]), 2),
        "rsi": round(float(last["rsi"]), 2),
        "adx": round(float(last["adx"]), 2),
        "supertrend_dir": int(last["supertrend_dir"]),
        "vwap": round(float(last["vwap"]), 2),
        "bullish_score": bullish_signals,
        "bearish_score": bearish_signals,
    }
