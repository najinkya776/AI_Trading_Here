"""
FastAPI webhook server — receives TradingView alerts and routes them through
the AI decision pipeline before placing any paper/live order.

NEW PIPELINE (replaces the old 4-of-6 indicator gate):
  TradingView Alert
      → NSE live snapshot (5m + 15m candles, VIX, options chain, ORB)
      → MarketAnalyst (Claude Opus 4.7 — MTF + options flow + time window)
      → RiskManager   (position sizing, daily loss limit, max positions)
      → PaperTrader / UpstoxTrader

TradingView alert message format (set this in your TV alert):
{
  "secret": "YOUR_WEBHOOK_SECRET",
  "symbol": "NIFTY",
  "action": "{{strategy.order.action}}",
  "price": {{close}},
  "strategy": "SuperTrend_VWAP",
  "timeframe": "5"
}
"""

import json
import logging
from collections import deque
from dataclasses import asdict
from datetime import datetime

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config.settings import settings, IST

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("logs/webhook.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

app = FastAPI(title="AI Trading Bot — Webhook Server")

from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Lazy-loaded singletons (avoid slow imports at startup)
_analyst  = None
_memory   = None
_risk     = None
_trader   = None

_recent_decisions: deque = deque(maxlen=10)  # last 10 signals — shown in control panel


def _get_analyst():
    global _analyst
    if _analyst is None:
        from agents.market_analyst import MarketAnalyst
        _analyst = MarketAnalyst()
    return _analyst


def _get_memory():
    global _memory
    if _memory is None:
        from agents.trade_memory import TradeMemory
        _memory = TradeMemory()
    return _memory


def _get_risk():
    global _risk
    if _risk is None:
        from risk.risk_manager import RiskManager
        _risk = RiskManager()
    return _risk


def _get_trader():
    global _trader
    if _trader is None:
        if settings.live_trading:
            from execution.upstox_trader import UpstoxTrader
            _trader = UpstoxTrader()
        else:
            from execution.paper_trader import PaperTrader
            _trader = PaperTrader()
    return _trader


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────

class TVAlert(BaseModel):
    secret:    str
    symbol:    str           # "NIFTY", "BANKNIFTY"
    action:    str           # "BUY", "SELL", "EXIT"
    price:     float
    sl:        float | None = None
    target:    float | None = None
    qty:       int   | None = None
    strategy:  str          = ""
    timeframe: str          = "5"


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now(IST).isoformat()}


@app.get("/api/status")
async def api_status():
    """Control panel endpoint — returns portfolio, positions, trades, AI decisions."""
    result: dict = {
        "server_time":       datetime.now(IST).isoformat(),
        "recent_decisions":  list(_recent_decisions),
        "portfolio":         {},
        "open_positions":    [],
        "memory_stats":      {},
        "recent_trades":     [],
    }

    try:
        trader = _get_trader()
        result["portfolio"]      = trader.get_portfolio_summary()
        result["open_positions"] = [asdict(p) for p in trader.open_positions]
    except Exception:
        pass

    try:
        from agents.trade_memory import TradeMemory
        result["memory_stats"] = TradeMemory().get_stats()
    except Exception:
        pass

    try:
        from sqlalchemy import text as _sql
        from execution.paper_trader import engine as _engine
        with _engine.connect() as conn:
            rows = conn.execute(_sql(
                "SELECT id, symbol, action, entry_price, sl, target, qty, "
                "entry_time, exit_price, exit_time, pnl, status, reason "
                "FROM trades ORDER BY entry_time DESC LIMIT 10"
            )).fetchall()
        _cols = ["id","symbol","action","entry_price","sl","target","qty",
                 "entry_time","exit_price","exit_time","pnl","status","reason"]
        result["recent_trades"] = [dict(zip(_cols, row)) for row in rows]
    except Exception:
        pass

    return result


@app.post("/webhook")
async def receive_alert(request: Request):
    # Parse body
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Validate secret
    if settings.webhook_secret and body.get("secret") != settings.webhook_secret:
        log.warning("Rejected alert — wrong secret")
        raise HTTPException(status_code=403, detail="Invalid secret")

    alert = TVAlert(**body)
    alert.action = alert.action.upper()

    if alert.action not in ("BUY", "SELL", "EXIT"):
        raise HTTPException(status_code=422, detail=f"Unknown action: {alert.action}")

    log.info(
        f"Alert received | {alert.symbol} {alert.action} @ {alert.price} "
        f"| strategy={alert.strategy}"
    )

    # EXIT signal — close position immediately without AI analysis
    if alert.action == "EXIT":
        return await _handle_exit(alert)

    return await _handle_trade_signal(alert)


# ─────────────────────────────────────────────────────────────────────────────
# Trade signal pipeline
# ─────────────────────────────────────────────────────────────────────────────

async def _handle_trade_signal(alert: TVAlert):
    """Full AI pipeline: snapshot → analyst → risk → execution."""

    # ── Step 1: Fetch live NSE market data ───────────────────────────────────
    log.info(f"Fetching NSE live snapshot for {alert.symbol}...")
    try:
        from data.nse_live import get_market_snapshot
        snapshot = get_market_snapshot(alert.symbol)
    except Exception as e:
        log.error(f"Market snapshot failed: {e}")
        return JSONResponse({"status": "error", "reason": f"Data fetch failed: {e}"})

    if snapshot.get("error"):
        log.warning(f"Snapshot partial error: {snapshot['error']}")

    # ── Step 2: AI analyst decision ──────────────────────────────────────────
    signal_dict = {
        "symbol":    alert.symbol,
        "action":    alert.action,
        "price":     alert.price,
        "strategy":  alert.strategy,
        "timeframe": alert.timeframe,
    }

    analyst  = _get_analyst()
    decision = analyst.analyze(signal_dict, snapshot)

    ai_decision = decision.get("decision", "SKIP")
    confidence  = decision.get("confidence", 0.0)
    reasoning   = decision.get("reasoning", "")

    log.info(f"AI decision: {ai_decision} | confidence={confidence:.2f} | {reasoning[:80]}")

    _recent_decisions.append({
        "time":       datetime.now(IST).strftime("%H:%M"),
        "symbol":     alert.symbol,
        "action":     alert.action,
        "strategy":   alert.strategy,
        "decision":   ai_decision,
        "confidence": confidence,
        "reasoning":  reasoning,
    })

    if ai_decision == "SKIP":
        return JSONResponse({
            "status":     "skipped",
            "reason":     reasoning,
            "confidence": confidence,
            "key_factors": decision.get("key_factors", []),
        })

    # ── Step 3: Risk management ───────────────────────────────────────────────
    entry = decision.get("entry_price") or alert.price
    sl    = decision.get("stop_loss")   or alert.sl
    tgt   = decision.get("target")      or alert.target

    # If strategy sent its own TP/SL, respect them — AI is a filter, not an override
    if alert.sl is not None:
        sl = alert.sl
    if alert.target is not None:
        tgt = alert.target

    if sl is None:
        from signals.technical import get_signal_context
        ctx = get_signal_context(alert.symbol, alert.timeframe)
        atr = ctx.get("atr", alert.price * 0.003)
        sl  = round(entry - atr * 1.5, 2) if ai_decision == "BUY" else round(entry + atr * 1.5, 2)
    if tgt is None:
        risk = abs(entry - sl)
        tgt  = round(entry + risk * 2, 2) if ai_decision == "BUY" else round(entry - risk * 2, 2)

    risk_mgr = _get_risk()
    if not risk_mgr._can_trade():
        return JSONResponse({
            "status": "skipped",
            "reason": "Risk manager block: daily loss limit / time window / max positions",
        })

    qty = alert.qty
    if qty is None:
        qty = risk_mgr.get_position_size(entry, sl)
    if qty <= 0:
        return JSONResponse({"status": "skipped", "reason": "Position size = 0"})

    # ── Step 4: Place order ───────────────────────────────────────────────────
    trader = _get_trader()
    order_result = trader.place_order(
        symbol=alert.symbol,
        action=ai_decision,
        price=entry,
        sl=sl,
        target=tgt,
        qty=qty,
        reason=(
            f"AI:{confidence:.0%} | {decision.get('time_window','')} | "
            f"mtf={'✓' if decision.get('mtf_aligned') else '✗'} | "
            f"{alert.strategy}"
        ),
        snapshot=snapshot,
        ai_json_str=json.dumps({**decision, "strategy": alert.strategy}),
    )

    log.info(f"Order placed | {order_result}")

    # ── Step 5: Telegram notification ────────────────────────────────────────
    _send_trade_alert(alert.symbol, ai_decision, entry, sl, tgt, qty, confidence, reasoning)

    return JSONResponse({
        "status":     "executed",
        "order":      order_result,
        "ai_decision": {
            "decision":    ai_decision,
            "confidence":  confidence,
            "entry":       entry,
            "sl":          sl,
            "target":      tgt,
            "time_window": decision.get("time_window"),
            "mtf_aligned": decision.get("mtf_aligned"),
            "key_factors": decision.get("key_factors", []),
            "reasoning":   reasoning,
        },
    })


async def _handle_exit(alert: TVAlert):
    """Close all open positions for the symbol."""
    log.info(f"EXIT signal for {alert.symbol} @ {alert.price}")
    trader = _get_trader()
    try:
        result = trader.close_all(symbol=alert.symbol, exit_price=alert.price)
        _send_exit_alert(alert.symbol, alert.price)
        return JSONResponse({"status": "closed", "result": result})
    except Exception as e:
        log.error(f"EXIT handler error: {e}")
        return JSONResponse({"status": "error", "reason": str(e)})


# ─────────────────────────────────────────────────────────────────────────────
# Notifications
# ─────────────────────────────────────────────────────────────────────────────

def _send_trade_alert(symbol, action, entry, sl, target, qty, confidence, reasoning):
    try:
        import asyncio
        from notifications.telegram_bot import send_message
        risk  = abs(entry - sl)
        rr    = round(abs(target - entry) / risk, 1) if risk > 0 else 0
        msg   = (
            f"🤖 AI TRADE SIGNAL\n"
            f"{'🟢 BUY' if action == 'BUY' else '🔴 SELL'} {symbol}\n"
            f"Entry: ₹{entry:,}  |  Qty: {qty}\n"
            f"SL:    ₹{sl:,}  (risk: ₹{risk:.0f}/unit)\n"
            f"Target:₹{target:,}  (R:R = 1:{rr})\n"
            f"Confidence: {confidence:.0%}\n"
            f"Reason: {reasoning[:120]}"
        )
        asyncio.create_task(send_message(msg))
    except Exception as e:
        log.warning(f"Telegram alert failed: {e}")


def _send_exit_alert(symbol, price):
    try:
        import asyncio
        from notifications.telegram_bot import send_message
        asyncio.create_task(send_message(f"🔔 EXIT {symbol} @ ₹{price:,}"))
    except Exception as e:
        log.warning(f"Telegram exit alert failed: {e}")


if __name__ == "__main__":
    uvicorn.run("webhook.server:app", host="0.0.0.0", port=8000, reload=True)
