"""
AlgoTrading Agentic Workflow — 4-Agent Demo
============================================
Pipeline: MarketData → SignalAnalysis → RiskManagement → TradeExecution
Orchestrated by an Opus 4.7 coordinator with adaptive thinking.

Run:
    python -m agents.agentic_workflow
    # or from the AI_Trading1 folder:
    python agents/agentic_workflow.py

Needs:
    ANTHROPIC_API_KEY in your .env
    pip install anthropic python-dotenv
"""

import json
import os
import random
from datetime import datetime

import anthropic
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run_agentic_loop(client, model, system, tools, messages, thinking=None, max_tokens=2048):
    """
    Standard agentic loop: call Claude, handle tool_use, repeat until end_turn.
    Returns the final text response as a Python dict (parsed JSON if possible).
    """
    while True:
        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )
        if thinking:
            kwargs["thinking"] = thinking

        response = client.messages.create(**kwargs)

        if response.stop_reason == "end_turn":
            for block in response.content:
                if block.type == "text":
                    try:
                        return json.loads(block.text)
                    except json.JSONDecodeError:
                        return {"summary": block.text}
            return {}

        if response.stop_reason != "tool_use":
            break

        # Preserve full content (including thinking blocks) in history
        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = _dispatch_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })
        messages.append({"role": "user", "content": tool_results})

    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Simulated tool backend (mirrors real modules without needing live credentials)
# ─────────────────────────────────────────────────────────────────────────────

def _dispatch_tool(name, inputs):
    """Route tool calls to their simulated implementations."""
    handlers = {
        # MarketDataAgent tools
        "fetch_price_data":       _tool_fetch_price_data,
        "fetch_vix_data":         _tool_fetch_vix_data,
        "fetch_options_chain":    _tool_fetch_options_chain,
        # SignalAnalysisAgent tools
        "analyze_indicators":     _tool_analyze_indicators,
        "check_market_regime":    _tool_check_market_regime,
        "run_ml_prediction":      _tool_run_ml_prediction,
        # RiskManagementAgent tools
        "check_daily_pnl":        _tool_check_daily_pnl,
        "check_open_positions":   _tool_check_open_positions,
        "calculate_position_size": _tool_calculate_position_size,
        "check_market_hours":     _tool_check_market_hours,
        # TradeExecutionAgent tools
        "place_paper_order":      _tool_place_paper_order,
        "log_trade":              _tool_log_trade,
        "send_telegram_alert":    _tool_send_telegram_alert,
        # Orchestrator sub-agent tools
        "run_market_data_agent":      _subagent_market_data,
        "run_signal_analysis_agent":  _subagent_signal_analysis,
        "run_risk_management_agent":  _subagent_risk_management,
        "run_trade_execution_agent":  _subagent_trade_execution,
    }
    handler = handlers.get(name)
    if handler is None:
        return {"error": f"Unknown tool: {name}"}
    return handler(inputs)


# ── Market Data simulations ──

def _tool_fetch_price_data(inputs):
    symbol = inputs.get("symbol", "NIFTY")
    base = 22485.50 if symbol == "NIFTY" else 48200.00
    candles = []
    price = base
    for i in range(20):
        open_ = price
        high  = open_ + random.uniform(10, 60)
        low   = open_ - random.uniform(10, 60)
        close = random.uniform(low, high)
        candles.append({"open": round(open_, 2), "high": round(high, 2),
                        "low": round(low, 2), "close": round(close, 2),
                        "volume": random.randint(50000, 200000)})
        price = close
    return {
        "symbol": symbol,
        "timeframe": inputs.get("timeframe", "5m"),
        "current_price": round(price, 2),
        "candles": candles[-5:],   # last 5 for brevity
        "timestamp": datetime.now().isoformat(),
    }


def _tool_fetch_vix_data(inputs):
    vix = round(random.uniform(12.0, 22.0), 2)
    return {
        "india_vix": vix,
        "vix_change_pct": round(random.uniform(-3.0, 3.0), 2),
        "vix_level": "high" if vix > 20 else "moderate" if vix > 15 else "low",
    }


def _tool_fetch_options_chain(inputs):
    symbol = inputs.get("symbol", "NIFTY")
    spot = 22485.50 if symbol == "NIFTY" else 48200.00
    atm  = round(spot / 50) * 50
    return {
        "symbol": symbol,
        "spot_price": spot,
        "atm_strike": atm,
        "pcr": round(random.uniform(0.7, 1.4), 2),
        "max_pain": atm - 50,
        "oi_buildup": "call_writing" if random.random() > 0.5 else "put_writing",
        "iv_percentile": round(random.uniform(20, 80), 1),
    }


# ── Signal Analysis simulations ──

def _tool_analyze_indicators(inputs):
    bullish = random.randint(2, 6)
    return {
        "symbol": inputs.get("symbol", "NIFTY"),
        "bullish_signals": bullish,
        "bearish_signals": 6 - bullish,
        "total_indicators": 6,
        "supertrend": "bullish" if bullish >= 4 else "bearish",
        "rsi": round(random.uniform(35, 70), 1),
        "vwap_relation": "above" if bullish >= 3 else "below",
        "ema_alignment": bullish >= 4,
        "macd_signal": "bullish_cross" if bullish >= 4 else "bearish_cross",
        "atr": round(random.uniform(80, 150), 1),
        "bias": "bullish" if bullish >= 4 else "bearish",
        "signal_strength": round(bullish / 6 * 100, 1),
    }


def _tool_check_market_regime(inputs):
    regimes = ["trending", "ranging", "volatile"]
    regime  = random.choice(regimes)
    vix     = inputs.get("vix", 14.5)
    if float(vix) > 20:
        regime = "volatile"
    return {
        "regime": regime,
        "adx": round(random.uniform(15, 45), 1),
        "tradeable": regime != "volatile",
        "reason": (
            "VIX > 20 — blocking trades" if regime == "volatile"
            else "ADX < 20 — low trend strength" if regime == "ranging"
            else "Strong trend detected"
        ),
    }


def _tool_run_ml_prediction(inputs):
    confidence = round(random.uniform(0.45, 0.92), 3)
    direction  = "BUY" if confidence > 0.55 else "SELL" if confidence < 0.45 else "NEUTRAL"
    return {
        "prediction": direction,
        "confidence": confidence,
        "model": "XGBoost_v1",
        "features_used": 22,
        "agrees_with_alert": direction == inputs.get("alert_action", "BUY"),
    }


# ── Risk Management simulations ──

def _tool_check_daily_pnl(inputs):
    pnl = round(random.uniform(-3000, 8000), 2)
    limit = -5000
    return {
        "daily_pnl": pnl,
        "daily_loss_limit": limit,
        "limit_breached": pnl < limit,
        "trades_today": random.randint(0, 4),
        "pnl_status": "profitable" if pnl > 0 else "loss",
    }


def _tool_check_open_positions(inputs):
    count = random.randint(0, 2)
    return {
        "open_positions": count,
        "max_allowed": 3,
        "can_add_position": count < 3,
        "positions": [
            {"symbol": "NIFTY24500CE", "qty": 50, "entry": 120.5, "current": 135.0}
        ] if count > 0 else [],
    }


def _tool_calculate_position_size(inputs):
    entry = float(inputs.get("entry_price", 22485))
    sl    = float(inputs.get("stop_loss",   22400))
    risk  = abs(entry - sl)
    capital = 100000
    risk_pct = 0.01
    qty = int((capital * risk_pct) / risk) if risk > 0 else 0
    qty = min(qty, 50)  # cap at 1 lot (50 for NIFTY)
    return {
        "entry_price": entry,
        "stop_loss": sl,
        "risk_per_unit": round(risk, 2),
        "recommended_qty": qty,
        "lot_size": 50,
        "lots": qty // 50,
        "capital_at_risk": round(qty * risk, 2),
    }


def _tool_check_market_hours(inputs):
    now = datetime.now()
    hour, minute = now.hour, now.minute
    total_min = hour * 60 + minute
    ist_offset = 330  # UTC+5:30

    # Simulate IST (add offset to UTC for demo)
    ist_min = (total_min + ist_offset) % (24 * 60)
    open_min  = 9  * 60 + 30   # 9:30 AM IST
    close_min = 15 * 60 + 0    # 3:00 PM IST

    in_window = open_min <= ist_min <= close_min
    return {
        "market_open": True,
        "in_trading_window": in_window,
        "current_ist": f"{ist_min // 60:02d}:{ist_min % 60:02d}",
        "window": "09:30 – 15:00 IST",
        "auto_squareoff": "15:20 IST",
        "can_trade": in_window,
    }


# ── Trade Execution simulations ──

def _tool_place_paper_order(inputs):
    order_id = f"PAPER_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000,9999)}"
    return {
        "order_id": order_id,
        "status": "FILLED",
        "symbol": inputs.get("symbol", "NIFTY"),
        "action": inputs.get("action", "BUY"),
        "qty": inputs.get("qty", 50),
        "entry_price": inputs.get("price", 22485.50),
        "stop_loss": inputs.get("stop_loss", 22400.0),
        "target": inputs.get("target", 22600.0),
        "mode": "PAPER",
        "timestamp": datetime.now().isoformat(),
    }


def _tool_log_trade(inputs):
    return {
        "logged": True,
        "db": "logs/paper_trades.db",
        "trade_id": inputs.get("order_id", "N/A"),
        "message": "Trade recorded in SQLite",
    }


def _tool_send_telegram_alert(inputs):
    print(f"\n  [TELEGRAM] {inputs.get('message', '(no message)')}\n")
    return {
        "sent": True,
        "channel": "Telegram (simulated)",
        "message": inputs.get("message"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Sub-agent runners (called by Orchestrator tools)
# These spin up fresh Haiku agents for each pipeline stage.
# ─────────────────────────────────────────────────────────────────────────────

_HAIKU = "claude-haiku-4-5"

_CLIENT = None  # set once in run_demo()


def _get_client():
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _CLIENT


def _subagent_market_data(inputs):
    client  = _get_client()
    symbol  = inputs.get("symbol", "NIFTY")
    tf      = inputs.get("timeframe", "5")

    system = (
        "You are the MarketData Agent for an Indian NSE F&O trading bot. "
        "Your job: collect all market data needed for a trade decision. "
        "Call all three tools, then return a JSON summary with keys: "
        "current_price, vix, vix_level, pcr, max_pain, oi_buildup."
    )
    tools = [
        {
            "name": "fetch_price_data",
            "description": "Fetch recent OHLCV candles and current price for a symbol.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbol":    {"type": "string", "description": "NIFTY or BANKNIFTY"},
                    "timeframe": {"type": "string", "description": "Candle interval, e.g. 5m"},
                },
                "required": ["symbol"],
            },
        },
        {
            "name": "fetch_vix_data",
            "description": "Fetch India VIX current value and change.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        {
            "name": "fetch_options_chain",
            "description": "Fetch options chain data: PCR, Max Pain, OI buildup, IV percentile.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NIFTY or BANKNIFTY"},
                },
                "required": ["symbol"],
            },
        },
    ]
    messages = [{"role": "user", "content":
        f"Collect all market data for {symbol} on the {tf}-minute timeframe. "
        "Call all three tools, then return a JSON summary."}]

    return _run_agentic_loop(client, _HAIKU, system, tools, messages)


def _subagent_signal_analysis(inputs):
    client      = _get_client()
    symbol      = inputs.get("symbol", "NIFTY")
    alert_action = inputs.get("alert_action", "BUY")
    vix         = inputs.get("vix", 14.5)

    system = (
        "You are the SignalAnalysis Agent. Confirm whether a TradingView alert "
        "has enough technical evidence to proceed. "
        "Use all three tools, then return JSON with keys: "
        "confirmed (bool), bias, signal_strength, regime, ml_confidence, reason."
    )
    tools = [
        {
            "name": "analyze_indicators",
            "description": "Run 6 technical indicators and return bullish/bearish counts.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbol":       {"type": "string"},
                    "alert_action": {"type": "string", "description": "BUY or SELL"},
                },
                "required": ["symbol"],
            },
        },
        {
            "name": "check_market_regime",
            "description": "Return current market regime: trending, ranging, or volatile.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "vix":    {"type": "number", "description": "Current India VIX value"},
                },
                "required": ["symbol"],
            },
        },
        {
            "name": "run_ml_prediction",
            "description": "XGBoost model prediction for the next candle direction.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbol":       {"type": "string"},
                    "alert_action": {"type": "string", "description": "BUY or SELL from alert"},
                },
                "required": ["symbol"],
            },
        },
    ]
    messages = [{"role": "user", "content":
        f"A TradingView alert fired for {symbol}: action={alert_action}, VIX={vix}. "
        "Run all three analysis tools, then confirm (or reject) the signal. Return JSON."}]

    return _run_agentic_loop(client, _HAIKU, system, tools, messages)


def _subagent_risk_management(inputs):
    client = _get_client()
    symbol = inputs.get("symbol", "NIFTY")
    entry  = inputs.get("entry_price", 22485.50)
    sl     = inputs.get("stop_loss",   22400.0)

    system = (
        "You are the RiskManagement Agent. Decide if a trade is safe to place. "
        "Run all four checks, then return JSON with keys: "
        "approved (bool), qty, stop_loss, reason."
    )
    tools = [
        {
            "name": "check_daily_pnl",
            "description": "Check today's realised P&L against the daily loss limit.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "check_open_positions",
            "description": "Return number of open positions and whether more are allowed.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "calculate_position_size",
            "description": "Calculate safe lot size using 1% risk rule.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "entry_price": {"type": "number"},
                    "stop_loss":   {"type": "number"},
                },
                "required": ["entry_price", "stop_loss"],
            },
        },
        {
            "name": "check_market_hours",
            "description": "Confirm we are inside the allowed trading window (9:30–15:00 IST).",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
    ]
    messages = [{"role": "user", "content":
        f"Evaluate risk for a {symbol} trade: entry={entry}, stop_loss={sl}. "
        "Run all four checks and return a JSON approval decision."}]

    return _run_agentic_loop(client, _HAIKU, system, tools, messages)


def _subagent_trade_execution(inputs):
    client  = _get_client()
    symbol  = inputs.get("symbol", "NIFTY")
    action  = inputs.get("action", "BUY")
    price   = inputs.get("price", 22485.50)
    qty     = inputs.get("qty", 50)
    sl      = inputs.get("stop_loss", 22400.0)
    target  = inputs.get("target", 22600.0)
    reason  = inputs.get("reason", "AI-confirmed signal")

    system = (
        "You are the TradeExecution Agent. Place the paper order, log it, "
        "and send a Telegram notification. "
        "Return JSON with keys: order_id, status, message."
    )
    tools = [
        {
            "name": "place_paper_order",
            "description": "Place a paper (simulated) order in the virtual portfolio.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbol":    {"type": "string"},
                    "action":    {"type": "string", "description": "BUY or SELL"},
                    "qty":       {"type": "integer"},
                    "price":     {"type": "number"},
                    "stop_loss": {"type": "number"},
                    "target":    {"type": "number"},
                },
                "required": ["symbol", "action", "qty", "price"],
            },
        },
        {
            "name": "log_trade",
            "description": "Persist trade details to the SQLite trade log.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "symbol":   {"type": "string"},
                    "action":   {"type": "string"},
                    "qty":      {"type": "integer"},
                    "price":    {"type": "number"},
                },
                "required": ["order_id"],
            },
        },
        {
            "name": "send_telegram_alert",
            "description": "Send a Telegram message to the configured chat.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Alert text to send"},
                },
                "required": ["message"],
            },
        },
    ]
    msg_text = (
        f"Execute a paper {action} for {symbol}: price={price}, qty={qty}, "
        f"sl={sl}, target={target}. Reason: {reason}. "
        "Place the order, log it, send Telegram alert, then return JSON."
    )
    messages = [{"role": "user", "content": msg_text}]

    return _run_agentic_loop(client, _HAIKU, system, tools, messages)


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator — Opus 4.7 with adaptive thinking
# ─────────────────────────────────────────────────────────────────────────────

_OPUS = "claude-opus-4-7"

_ORCHESTRATOR_SYSTEM = """\
You are the Trading Orchestrator for an NSE F&O intraday bot (Nifty/BankNifty).
Your role: receive a TradingView webhook signal and coordinate a 4-agent pipeline
to decide whether to place a paper trade.

Pipeline (must run in order):
1. run_market_data_agent      → gather price, VIX, options chain
2. run_signal_analysis_agent  → confirm technical signals + ML + regime
3. run_risk_management_agent  → check P&L limit, positions, position size, hours
4. run_trade_execution_agent  → place paper order, log, notify Telegram

After all four agents complete, return a final JSON report:
{
  "decision": "TRADE_PLACED" | "TRADE_BLOCKED" | "SIGNAL_REJECTED",
  "symbol": "...",
  "action": "BUY" | "SELL",
  "order_id": "...",
  "entry_price": ...,
  "qty": ...,
  "stop_loss": ...,
  "target": ...,
  "reason": "one-sentence summary of the decision",
  "agent_results": {
    "market_data": {...},
    "signal_analysis": {...},
    "risk_management": {...},
    "trade_execution": {...}
  }
}

Rules:
- Block the trade if SignalAnalysis.confirmed == false
- Block the trade if RiskManagement.approved == false
- Always run all 4 agents; just skip TradeExecution action when blocking
- Set target = entry + 2 * (entry - stop_loss) for a 1:2 risk/reward
"""

_ORCHESTRATOR_TOOLS = [
    {
        "name": "run_market_data_agent",
        "description": "Launches the MarketData sub-agent to fetch price, VIX, and options chain data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol":    {"type": "string", "description": "NIFTY or BANKNIFTY"},
                "timeframe": {"type": "string", "description": "Candle interval e.g. 5"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "run_signal_analysis_agent",
        "description": "Launches the SignalAnalysis sub-agent to confirm the alert via technicals + ML + regime.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol":       {"type": "string"},
                "alert_action": {"type": "string", "description": "BUY or SELL"},
                "vix":          {"type": "number", "description": "India VIX from market data"},
            },
            "required": ["symbol", "alert_action"],
        },
    },
    {
        "name": "run_risk_management_agent",
        "description": "Launches the RiskManagement sub-agent to approve size and check daily limits.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol":       {"type": "string"},
                "entry_price":  {"type": "number"},
                "stop_loss":    {"type": "number"},
            },
            "required": ["symbol", "entry_price", "stop_loss"],
        },
    },
    {
        "name": "run_trade_execution_agent",
        "description": "Launches the TradeExecution sub-agent to place paper order + log + Telegram.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol":    {"type": "string"},
                "action":    {"type": "string", "description": "BUY or SELL"},
                "price":     {"type": "number"},
                "qty":       {"type": "integer"},
                "stop_loss": {"type": "number"},
                "target":    {"type": "number"},
                "reason":    {"type": "string"},
            },
            "required": ["symbol", "action", "price", "qty"],
        },
    },
]


def run_orchestrator(signal: dict) -> dict:
    """
    Feed a TradingView webhook signal dict to the Orchestrator.
    Returns the final trade decision dict.
    """
    client = _get_client()

    user_message = (
        f"New TradingView alert received:\n{json.dumps(signal, indent=2)}\n\n"
        "Run the full 4-agent pipeline and return the final JSON trade report."
    )

    messages = [{"role": "user", "content": user_message}]

    return _run_agentic_loop(
        client=client,
        model=_OPUS,
        system=_ORCHESTRATOR_SYSTEM,
        tools=_ORCHESTRATOR_TOOLS,
        messages=messages,
        thinking={"type": "adaptive", "display": "summarized"},
        max_tokens=4096,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Demo entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_demo():
    print("=" * 65)
    print("  AlgoTrading — 4-Agent Agentic Workflow Demo")
    print("  Orchestrator : Opus 4.7  (adaptive thinking)")
    print("  Sub-agents   : Haiku 4.5 (speed + cost)")
    print("=" * 65)

    # Sample TradingView webhook payload
    sample_signal = {
        "secret":    "demo_secret",
        "symbol":    "NIFTY",
        "action":    "BUY",
        "price":     22485.50,
        "strategy":  "SuperTrend_VWAP",
        "timeframe": "5",
        "timestamp": datetime.now().isoformat(),
    }

    print("\n[SIGNAL RECEIVED]")
    print(json.dumps(sample_signal, indent=2))
    print("\n[PIPELINE STARTING] ...\n")

    result = run_orchestrator(sample_signal)

    print("\n" + "=" * 65)
    print("  FINAL DECISION")
    print("=" * 65)
    print(json.dumps(result, indent=2, default=str))
    print("=" * 65)

    return result


if __name__ == "__main__":
    run_demo()
