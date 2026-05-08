"""
Market Analyst Agent — AI-Powered Trade Decision Engine
=========================================================
Replaces the old 4-of-6 indicator vote with Claude Opus 4.7 + adaptive thinking.

Claude receives a complete market snapshot (5m + 15m data, VIX, options chain,
ORB levels, time window) plus your historical winning trade examples, then reasons
through multi-timeframe alignment, institutional flow, and statistical edge to
return a structured BUY / SELL / SKIP decision with confidence score.

Usage:
    from agents.market_analyst import MarketAnalyst
    analyst = MarketAnalyst()
    decision = analyst.analyze(signal, snapshot)
    # decision = {"decision": "BUY", "confidence": 0.78, "entry_price": ..., ...}
"""

import json
import logging
import os

import anthropic
from dotenv import load_dotenv

from agents.trade_memory import TradeMemory

load_dotenv()
log = logging.getLogger(__name__)

_MODEL          = "claude-opus-4-7"
_MIN_CONFIDENCE = 0.68   # below this threshold, decision is forced to SKIP

# ─────────────────────────────────────────────────────────────────────────────
# System prompt template — {few_shot_section} is filled at runtime from memory
# ─────────────────────────────────────────────────────────────────────────────
_SYSTEM_TEMPLATE = """\
You are an expert NSE F&O intraday trader specialising in Nifty and BankNifty index options.
You think statistically — every decision is based on edge probability, not hope.

{few_shot_section}

YOUR ANALYSIS FRAMEWORK
━━━━━━━━━━━━━━━━━━━━━━━

1. MULTI-TIMEFRAME ALIGNMENT (most important filter)
   ▸ Check 15-min chart first — it sets the trend direction for the day.
   ▸ Only take BUY signals when 15-min bias is BULLISH.
   ▸ Only take SELL signals when 15-min bias is BEARISH.
   ▸ If 5-min and 15-min biases are OPPOSITE → edge disappears → SKIP.
   ▸ Both aligned and bullish/bearish count ≥ 4/6 = strong setup.

2. OPTIONS FLOW (institutional money signal)
   ▸ PCR > 1.2: heavy put writing = market makers expect support → bullish bias.
   ▸ PCR < 0.8: heavy call writing = market makers capping rally → bearish bias.
   ▸ Top CE OI strike = hard resistance ceiling (price stalls or reverses here).
   ▸ Top PE OI strike = strong support floor (price bounces here).
   ▸ Price far above Max Pain on expiry Thursday = gravitational pull down → favor SELL.
   ▸ When options flow contradicts signal → reduce confidence by 0.10–0.15.

3. TIME WINDOWS — only trade high-probability windows
   ▸ ORB (9:20–9:45):       Opening Range Breakout. High momentum, tight SL.
   ▸ TREND (10:30–11:30):   Post-opening trend continuation. Most consistent.
   ▸ AFTERNOON (14:00–15:00): Final push before EOD. Follow morning trend.
   ▸ AVOID all other windows unless confidence would otherwise be ≥ 0.85.

4. VOLATILITY
   ▸ VIX < 14:  ideal. Boost confidence +0.05.
   ▸ VIX 14–18: normal. No adjustment.
   ▸ VIX 18–20: elevated. Reduce confidence -0.07 and tighten SL.
   ▸ VIX > 20:  danger. Reduce confidence -0.15. Consider SKIP.
   ▸ ADX > 25:  strong trend. Boost confidence +0.05.
   ▸ ADX < 20:  no trend, choppy. Reduce confidence -0.10.

5. STOP LOSS & TARGET CALCULATION (non-negotiable 1:2 minimum R:R)
   ▸ SL distance = ATR(14) × 1.5  (from 5-min ATR)
   ▸ Target      = entry + 2 × SL distance  (1:2 R:R minimum)
   ▸ Never place SL tighter than 0.3% of current price.
   ▸ On ORB: SL = other side of ORB ± 0.3× ATR buffer.

DECISION THRESHOLDS
   ▸ confidence ≥ 0.70 → execute (BUY or SELL)
   ▸ confidence 0.55–0.69 → SKIP (edge not clear enough)
   ▸ confidence < 0.55 → SKIP immediately

OUTPUT FORMAT
Return ONLY valid JSON — no markdown, no explanation outside the JSON:
{{
  "decision":    "BUY" | "SELL" | "SKIP",
  "confidence":  <float 0.0–1.0>,
  "entry_price": <float>,
  "stop_loss":   <float>,
  "target":      <float>,
  "time_window": "ORB" | "TREND" | "AFTERNOON" | "AVOID",
  "mtf_aligned": <bool>,
  "key_factors": ["<factor1>", "<factor2>", "<factor3>"],
  "reasoning":   "<2–3 sentences explaining the decision concisely>"
}}
"""


class MarketAnalyst:
    """
    AI-powered trade decision agent.
    One instance per session — reuses the Anthropic client and trade memory.
    """

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.memory = TradeMemory()

    def analyze(self, signal: dict, snapshot: dict) -> dict:
        """
        Evaluate a TradingView signal against the live market snapshot.

        Args:
            signal:   dict with keys: symbol, action, price, strategy, timeframe
            snapshot: output of data.nse_live.get_market_snapshot()

        Returns:
            dict with keys: decision, confidence, entry_price, stop_loss, target,
                            time_window, mtf_aligned, key_factors, reasoning
        """
        # Build system prompt with fresh few-shot examples each call
        few_shot   = self.memory.format_for_prompt(n=8)
        system_msg = _SYSTEM_TEMPLATE.format(few_shot_section=few_shot)
        user_msg   = _build_user_message(signal, snapshot)

        log.info(
            f"MarketAnalyst: evaluating {signal.get('symbol')} "
            f"{signal.get('action')} @ {signal.get('price')}"
        )

        try:
            response = self.client.messages.create(
                model=_MODEL,
                max_tokens=1024,
                thinking={"type": "adaptive", "display": "summarized"},
                system=system_msg,
                messages=[{"role": "user", "content": user_msg}],
            )

            raw_text = ""
            for block in response.content:
                if block.type == "text":
                    raw_text = block.text.strip()
                    break

            # Strip markdown fences if Claude wraps the JSON
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]

            result = json.loads(raw_text)

            # Hard-enforce the confidence threshold
            if result.get("confidence", 0) < _MIN_CONFIDENCE:
                result["decision"] = "SKIP"

            log.info(
                f"Decision: {result.get('decision')} | "
                f"confidence={result.get('confidence', 0):.2f} | "
                f"{result.get('reasoning', '')[:100]}"
            )
            return result

        except json.JSONDecodeError as e:
            log.error(f"JSON parse error from Claude: {e} | raw={raw_text[:200]}")
            return _skip(f"Response parse error: {e}")
        except Exception as e:
            log.error(f"MarketAnalyst.analyze() error: {e}", exc_info=True)
            return _skip(f"Analyst error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Message builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_user_message(signal: dict, snapshot: dict) -> str:
    """Format the full market snapshot into a clean, readable context block."""
    tf5   = snapshot.get("tf_5m",  {})
    tf15  = snapshot.get("tf_15m", {})
    ind5  = tf5.get("indicators",  {})
    ind15 = tf15.get("indicators", {})
    opt   = snapshot.get("options",       {})
    orb   = snapshot.get("orb",           {})
    tc    = snapshot.get("time_context",  {})
    price = snapshot.get("current_price", signal.get("price", 0))

    above_below_vwap = "ABOVE" if price > ind5.get("vwap", 0) else "BELOW"
    st_5m  = "BULLISH ▲" if ind5.get("supertrend_dir") == 1 else "BEARISH ▼"
    st_15m = "BULLISH ▲" if ind15.get("supertrend_dir") == 1 else "BEARISH ▼"

    # Last 5 candles as a mini table
    candles_txt = ""
    for c in tf5.get("candles", [])[-5:]:
        body = "▲" if c["close"] >= c["open"] else "▼"
        candles_txt += (
            f"  {c['time']}  O:{c['open']:>8}  H:{c['high']:>8}  "
            f"L:{c['low']:>8}  C:{c['close']:>8} {body}  vol:{c['volume']:,}\n"
        )

    return f"""
╔══ NEW TRADINGVIEW ALERT ══════════════════════════════════╗
  Symbol    : {signal.get('symbol', '?')}
  Direction : {signal.get('action', '?')}
  Trigger ₹ : {signal.get('price', '?')}
  Strategy  : {signal.get('strategy', '?')}
  Timeframe : {signal.get('timeframe', '5')} min
╚═══════════════════════════════════════════════════════════╝

━━ LIVE MARKET SNAPSHOT ─ {snapshot.get('timestamp', '')[:16]} IST ━━

Current Price: ₹{price:,}

TIME
  Window         : {tc.get('window', '?')}  ({tc.get('ist_time', '?')} IST)
  In Trading Hrs : {tc.get('in_trading_hours', '?')}
  Expiry Thursday: {tc.get('is_expiry', '?')}
  Mins to Close  : {tc.get('mins_to_close', '?')}

VOLATILITY
  India VIX  : {snapshot.get('india_vix', 'N/A')}  ({snapshot.get('vix_level', '?')})

5-MIN CHART (primary entry timeframe)
  Bias       : {tf5.get('bias', '?')}  ({tf5.get('bullish_count', '?')}/6 bullish, {tf5.get('bearish_count', '?')}/6 bearish)
  SuperTrend : {st_5m}
  VWAP       : {ind5.get('vwap', '?')}  → price is {above_below_vwap} VWAP
  EMA 9/21/50: {ind5.get('ema9','?')} / {ind5.get('ema21','?')} / {ind5.get('ema50','?')}
  RSI(14)    : {ind5.get('rsi', '?')}
  MACD hist  : {ind5.get('macd_hist', '?')}  ({'bullish' if (ind5.get('macd_hist') or 0) > 0 else 'bearish'} momentum)
  ADX        : {ind5.get('adx', '?')}  ({'trending' if (ind5.get('adx') or 0) > 25 else 'ranging/weak'})
  ATR(14)    : {ind5.get('atr', '?')}  (volatility / position sizing reference)
  Stoch K/D  : {ind5.get('stoch_k', '?')} / {ind5.get('stoch_d', '?')}
  BB Width   : {ind5.get('bb_width', '?')}

Last 5 candles (5m):
{candles_txt}
15-MIN CHART (trend direction — most important)
  Bias       : {tf15.get('bias', '?')}  ({tf15.get('bullish_count', '?')}/6 bullish)
  SuperTrend : {st_15m}
  RSI(14)    : {ind15.get('rsi', '?')}
  ADX        : {ind15.get('adx', '?')}
  EMA 9/21   : {ind15.get('ema9','?')} / {ind15.get('ema21','?')}
  MACD hist  : {ind15.get('macd_hist', '?')}

OPENING RANGE BREAKOUT (9:15–9:44)
  ORB High   : {orb.get('high', '?')}
  ORB Low    : {orb.get('low',  '?')}
  Range Size : {orb.get('range_size', '?')} pts
  Breakout ▲ : {orb.get('breakout',  '?')}
  Breakdown ▼: {orb.get('breakdown', '?')}

OPTIONS CHAIN (institutional flow)
  PCR                : {opt.get('pcr', '?')}  → {opt.get('sentiment', '?')}
  Max Pain           : {opt.get('max_pain', '?')}  (price vs max pain: {opt.get('price_vs_max_pain', '?')} pts)
  Key Resistance     : {opt.get('top_ce_oi_strike', '?')}  (highest CALL OI — call writers protecting this)
  Key Support        : {opt.get('top_pe_oi_strike', '?')}  (highest PUT  OI — put writers protecting this)
  Avg IV             : {opt.get('iv_avg', '?')}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TASK: Apply your framework to the above data.
Evaluate multi-timeframe alignment, options flow, time window, and volatility.
Return ONLY a JSON object — no extra text.
""".strip()


def _skip(reason: str) -> dict:
    return {
        "decision":    "SKIP",
        "confidence":  0.0,
        "entry_price": 0.0,
        "stop_loss":   0.0,
        "target":      0.0,
        "time_window": "AVOID",
        "mtf_aligned": False,
        "key_factors": [reason],
        "reasoning":   reason,
    }
