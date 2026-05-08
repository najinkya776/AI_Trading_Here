"""
Trade Memory — Feedback Loop for AI Learning
=============================================
Stores every closed trade's outcome + the market conditions at entry.
The Market Analyst reads these as few-shot examples — the more real trades
you accumulate, the better Claude understands YOUR specific setups.

Storage: logs/trade_memory.json  (plain text, easy to inspect and edit)

Usage:
    from agents.trade_memory import TradeMemory
    memory = TradeMemory()

    # When a trade closes:
    memory.save_outcome(
        trade_id="PAPER_20240320104500",
        symbol="NIFTY", action="BUY",
        entry=22450, sl=22380, target=22590,
        exit_price=22585, pnl=10125,
        strategy="ORB_Breakout",
        conditions_snapshot=snapshot,   # full dict from nse_live.get_market_snapshot()
    )

    # Get stats:
    print(memory.get_stats())
"""

import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

MEMORY_FILE = Path("logs/trade_memory.json")

# Fallback few-shot guide used until you have real trade history
_DEFAULT_FEW_SHOT_GUIDE = """
=== TRADING FRAMEWORK (building history — using proven NSE F&O edge patterns) ===

HIGH-PROBABILITY SETUP PATTERNS:

PATTERN 1 — ORB Breakout (9:20–9:45 AM):
  Signal:  Price breaks above/below 9:15–9:45 opening range with volume surge
  Filters: VIX < 16, ADX > 25, volume > 1.5× average, 15-min aligned with breakout
  SL:      Below/above ORB opposite level + 0.3× ATR buffer
  Target:  1.5× to 2× ORB range projected from breakout point
  Edge:    65–70% win rate when all conditions met

PATTERN 2 — VWAP Pullback (10:30–11:30 AM):
  Signal:  Price pulls back to VWAP after trend established, holds, resumes
  Filters: PCR supports direction, RSI not extreme, 15-min trending
  SL:      Below/above VWAP + 0.5× ATR
  Target:  Previous high/low of the trend move
  Edge:    60–65% win rate

PATTERN 3 — Afternoon Trend Continuation (2:00–3:00 PM):
  Signal:  Strong trend since morning, price consolidates briefly then resumes
  Filters: Same trend direction as 11 AM, ADX still > 20, no major news
  SL:      ATR × 1.5 from entry
  Target:  ATR × 3.0 from entry (1:2 R:R minimum)
  Edge:    58–63% win rate

SKIP THESE SITUATIONS:
  ✗ VIX > 20  (unpredictable swings, widen SL too much)
  ✗ 15-min and 5-min biases are opposite  (choppy, no edge)
  ✗ ADX < 18  (no trend, high whipsaw risk)
  ✗ PCR strongly contradicts signal direction
  ✗ Price within 30 pts of Max Pain on expiry day (magnetic pull)
  ✗ Within 30 min of RBI policy / budget / US Fed announcements

=== END FRAMEWORK ===
"""


class TradeMemory:
    def __init__(self, filepath: Path = MEMORY_FILE):
        self.filepath = filepath
        self._records: list[dict] = self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    def save_outcome(
        self,
        trade_id: str,
        symbol: str,
        action: str,
        entry: float,
        sl: float,
        target: float,
        exit_price: float,
        pnl: float,
        strategy: str = "",
        conditions_snapshot: dict | None = None,
    ):
        """
        Record a closed trade outcome.
        Call this from paper_trader.py or upstox_trader.py when a trade closes.
        """
        pts = round(exit_price - entry if action == "BUY" else entry - exit_price, 2)

        record = {
            "id":           trade_id,
            "date":         datetime.now().strftime("%Y-%m-%d"),
            "time":         datetime.now().strftime("%H:%M"),
            "symbol":       symbol,
            "action":       action,
            "strategy":     strategy,
            "entry":        entry,
            "sl":           sl,
            "target":       target,
            "exit_price":   exit_price,
            "pnl":          round(pnl, 2),
            "pts_gained":   pts,
            "outcome":      "WIN" if pnl > 0 else "LOSS",
            "conditions":   _strip_candles(conditions_snapshot or {}),
        }
        self._records.append(record)
        self._save()
        log.info(f"Trade memory saved | {symbol} {action} | {record['outcome']} | PnL=₹{pnl:,.0f}")

    def get_winning_trades(self, n: int = 10) -> list[dict]:
        """Top N winning trades by PnL — used as few-shot examples."""
        wins = [r for r in self._records if r["outcome"] == "WIN"]
        wins.sort(key=lambda x: x["pnl"], reverse=True)
        return wins[:n]

    def get_stats(self) -> dict:
        """Overall performance stats — shown in Telegram alerts and dashboard."""
        if not self._records:
            return {
                "total": 0, "wins": 0, "losses": 0,
                "win_rate_pct": 0.0, "total_pnl": 0.0,
                "avg_win": 0.0, "avg_loss": 0.0, "expectancy": 0.0,
            }

        wins   = [r for r in self._records if r["outcome"] == "WIN"]
        losses = [r for r in self._records if r["outcome"] == "LOSS"]
        total_pnl = sum(r["pnl"] for r in self._records)

        avg_win  = sum(r["pnl"] for r in wins)   / len(wins)   if wins   else 0
        avg_loss = sum(r["pnl"] for r in losses) / len(losses) if losses else 0
        win_rate = len(wins) / len(self._records)
        expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

        return {
            "total":        len(self._records),
            "wins":         len(wins),
            "losses":       len(losses),
            "win_rate_pct": round(win_rate * 100, 1),
            "total_pnl":    round(total_pnl, 2),
            "avg_win":      round(avg_win,   2),
            "avg_loss":     round(avg_loss,  2),
            "expectancy":   round(expectancy, 2),  # expected PnL per trade
        }

    def format_for_prompt(self, n: int = 8) -> str:
        """
        Returns a formatted string for Claude's system prompt.
        If no history yet, returns the default framework guide.
        This is the core of the AI feedback loop.
        """
        wins = self.get_winning_trades(n)
        if not wins:
            return _DEFAULT_FEW_SHOT_GUIDE

        lines = ["=== YOUR BEST WINNING TRADE PATTERNS (study and replicate these) ===\n"]

        for i, w in enumerate(wins, 1):
            c    = w.get("conditions", {})
            tf5  = c.get("tf_5m",  {})
            tf15 = c.get("tf_15m", {})
            ind5 = tf5.get("indicators", {})
            opt  = c.get("options", {})
            tc   = c.get("time_context", {})

            lines.append(
                f"WIN #{i}: {w['symbol']} {w['action']} | {w['date']} {w['time']} "
                f"| Strategy: {w.get('strategy', '?')}\n"
                f"  Price:    entry={w['entry']}  sl={w['sl']}  "
                f"target={w['target']}  exit={w['exit_price']}\n"
                f"  Result:   +{w['pts_gained']}pts  PnL=+₹{w['pnl']:,.0f}  "
                f"[{w['outcome']}]\n"
                f"  5m:       bias={tf5.get('bias','?')}  "
                f"bullish={tf5.get('bullish_count','?')}/6  "
                f"RSI={ind5.get('rsi','?')}  ADX={ind5.get('adx','?')}  "
                f"ATR={ind5.get('atr','?')}\n"
                f"  15m:      bias={tf15.get('bias','?')}  "
                f"bullish={tf15.get('bullish_count','?')}/6\n"
                f"  Options:  PCR={opt.get('pcr','?')}  "
                f"sentiment={opt.get('sentiment','?')}  "
                f"max_pain={opt.get('max_pain','?')}\n"
                f"  VIX:      {c.get('india_vix','?')} ({c.get('vix_level','?')})\n"
                f"  Window:   {tc.get('window','?')}  "
                f"Expiry={tc.get('is_expiry','?')}\n"
            )

        lines.append("=== END WINNING PATTERNS ===")
        lines.append(f"Current stats: {self.get_stats()}\n")
        return "\n".join(lines)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _load(self) -> list[dict]:
        if self.filepath.exists():
            try:
                with open(self.filepath) as f:
                    return json.load(f)
            except Exception as e:
                log.warning(f"Could not load trade memory: {e}")
        return []

    def _save(self):
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(self.filepath, "w") as f:
            json.dump(self._records, f, indent=2, default=str)


def _strip_candles(snapshot: dict) -> dict:
    """Remove bulky candle arrays before storing — keep only indicators."""
    clean = dict(snapshot)
    for tf in ("tf_5m", "tf_15m"):
        if tf in clean and isinstance(clean[tf], dict):
            clean[tf] = {k: v for k, v in clean[tf].items() if k != "candles"}
    return clean


if __name__ == "__main__":
    mem = TradeMemory()
    print("=== Trade Memory Stats ===")
    print(mem.get_stats())
    print("\n=== Few-Shot Prompt Section ===")
    print(mem.format_for_prompt())
