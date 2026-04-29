"""
FastAPI webhook server — receives TradingView alerts and routes them to the signal engine.

TradingView alert message format (set this in your TV alert):
{
  "secret": "{{strategy.order.alert_message}}",
  "symbol": "NIFTY",
  "action": "BUY",
  "price": {{close}},
  "strategy": "SuperTrend_VWAP",
  "timeframe": "5"
}
"""
import logging
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

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


class TVAlert(BaseModel):
    secret: str
    symbol: str          # e.g. "NIFTY", "BANKNIFTY"
    action: str          # "BUY", "SELL", or "EXIT"
    price: float
    sl: float | None = None        # stop loss price (optional, sent by strategy)
    target: float | None = None    # take profit price (optional, sent by strategy)
    qty: int | None = None         # lot size (optional, defaults to 75 = 1 lot Nifty)
    strategy: str = ""
    timeframe: str = "5"


@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now(IST).isoformat()}


@app.post("/webhook")
async def receive_alert(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Validate secret so random internet traffic can't trigger trades
    if settings.webhook_secret and body.get("secret") != settings.webhook_secret:
        log.warning("Rejected alert — wrong secret")
        raise HTTPException(status_code=403, detail="Invalid secret")

    alert = TVAlert(**body)
    alert.action = alert.action.upper()

    if alert.action not in ("BUY", "SELL", "EXIT"):
        raise HTTPException(status_code=422, detail=f"Unknown action: {alert.action}")

    log.info(f"Alert received | {alert.symbol} | {alert.action} @ {alert.price} | strategy={alert.strategy}")

    # Import here to avoid circular imports at startup
    from signals.technical import get_signal_context
    from signals.regime import get_regime
    from risk.risk_manager import RiskManager
    from execution.paper_trader import PaperTrader

    # AI confirmation layer
    context = get_signal_context(alert.symbol, alert.timeframe)
    regime = get_regime(alert.symbol)

    risk = RiskManager()
    trader = PaperTrader()

    decision = _make_decision(alert, context, regime, risk)

    if decision["execute"]:
        result = trader.place_order(
            symbol=alert.symbol,
            action=alert.action,
            price=alert.price,
            sl=decision["sl"],
            target=decision["target"],
            qty=decision["qty"],
            reason=decision["reason"],
        )
        log.info(f"Paper order placed | {result}")
        return JSONResponse({"status": "executed", "order": result})
    else:
        log.info(f"Alert skipped | reason={decision['reason']}")
        return JSONResponse({"status": "skipped", "reason": decision["reason"]})


def _make_decision(alert: TVAlert, context: dict, regime: str, risk) -> dict:
    """
    Fuse TradingView signal + AI context + regime filter into a final decision.
    Strategy sends fixed TP/SL/qty (e.g. Strategy #17 = TP 40pts, SL 20pts, 75 units).
    AI layer can REJECT the trade but doesn't override the levels.
    """
    # Gate 1: Skip if market regime is unfavorable
    if regime == "volatile" and alert.action in ("BUY", "SELL"):
        return {"execute": False, "reason": "regime=volatile, skipping directional trade"}

    # Gate 2: Skip if AI technical context disagrees with TradingView signal
    tv_action = alert.action
    ai_bias = context.get("bias", "NEUTRAL")

    if tv_action == "BUY" and ai_bias == "BEARISH":
        return {"execute": False, "reason": "AI bias disagrees: BEARISH vs BUY alert"}
    if tv_action == "SELL" and ai_bias == "BULLISH":
        return {"execute": False, "reason": "AI bias disagrees: BULLISH vs SELL alert"}

    # Use fixed TP/SL/qty from strategy if provided, else fall back to ATR-based
    qty = alert.qty if alert.qty else 75   # default 1 lot Nifty
    if alert.sl is not None and alert.target is not None:
        sl = alert.sl
        target = alert.target
    else:
        atr = context.get("atr", alert.price * 0.003)
        sl = round(alert.price - atr * 1.5, 2) if tv_action == "BUY" else round(alert.price + atr * 1.5, 2)
        target = round(alert.price + atr * 3.0, 2) if tv_action == "BUY" else round(alert.price - atr * 3.0, 2)

    # Gate 3: Risk manager (daily loss limit, time-of-day, max positions)
    if not risk._can_trade():
        return {"execute": False, "reason": "Risk manager: trading not allowed (loss limit / time / positions)"}

    return {
        "execute": True,
        "sl": sl,
        "target": target,
        "qty": qty,
        "reason": f"regime={regime} | ai_bias={ai_bias} | strategy={alert.strategy}",
    }


if __name__ == "__main__":
    uvicorn.run("webhook.server:app", host="0.0.0.0", port=8000, reload=True)
