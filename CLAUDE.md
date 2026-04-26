# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI trading bot for the Indian market (NSE Index F&O — Nifty/BankNifty intraday).
**Phase 1:** TradingView paper trading via webhooks. **Phase 2:** Upstox live trading.
The user is a trader-first with limited Python background — keep code readable and well-explained.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Start webhook server (receives TradingView alerts)
python -m webhook.server
# or
uvicorn webhook.server:app --host 0.0.0.0 --port 8000 --reload

# Start Streamlit dashboard
streamlit run dashboard/app.py

# Run backtester
python -m backtest.backtester --symbol NIFTY --days 180 --interval 5m

# Train ML model
python -m signals.ml_model --mode train --symbol NIFTY --days 365

# Test historical data fetch
python -m data.historical

# Test options chain
python -m data.options_chain

# Start real-time feed (Phase 2 only, needs Upstox access token)
python -m data.realtime_feed

# Test webhook manually (curl)
curl -X POST http://localhost:8000/webhook \
  -H "Content-Type: application/json" \
  -d '{"secret":"test","symbol":"NIFTY","action":"BUY","price":22500,"strategy":"test","timeframe":"5"}'
```

## Architecture

```
TradingView Alert → webhook/server.py → signals/ (AI confirmation) → risk/ → execution/
```

Every incoming alert goes through three gates before a paper/live order is placed:
1. **`signals/technical.py`** — checks if 4+ of 6 technical indicators agree with the alert direction
2. **`signals/regime.py`** — blocks trades when market is volatile (VIX > 20) or ADX too low
3. **`risk/risk_manager.py`** — enforces daily loss limit, max positions, time-of-day filter

## Key Files

| File | Purpose |
|---|---|
| `config/settings.py` | All config — reads from `.env`. Single source of truth for all parameters. |
| `webhook/server.py` | FastAPI app receiving TradingView POST alerts at `/webhook` |
| `execution/paper_trader.py` | Virtual portfolio + SQLite trade log. Mirrors `upstox_trader.py` interface. |
| `execution/upstox_trader.py` | Phase 2 live orders. Activated only when `LIVE_TRADING=true` in `.env`. |
| `signals/technical.py` | `add_indicators(df)` adds 20+ columns. `get_signal_context()` returns bias + ATR. |
| `signals/ml_model.py` | XGBoost classifier. Train with `MLModel.train()`, predict with `MLModel.predict(row)`. |
| `signals/regime.py` | `get_regime(symbol)` returns `'trending'|'ranging'|'volatile'`. |
| `risk/risk_manager.py` | `get_position_size(entry, sl)` returns qty (0 = don't trade). |
| `backtest/backtester.py` | Walk-forward backtest using SuperTrend+VWAP strategy. |
| `data/options_chain.py` | NSE options chain scraper — PCR, Max Pain, OI analysis. |
| `dashboard/app.py` | Streamlit UI — portfolio, trade log, regime, backtest runner. |
| `notifications/telegram_bot.py` | `send_message(text)` async function for trade alerts. |

## `.env` Setup

Copy `.env.example` to `.env` and fill in:
- `UPSTOX_API_KEY`, `UPSTOX_API_SECRET` — from Upstox developer portal
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — from @BotFather
- `WEBHOOK_SECRET` — any random string; paste same value in TradingView alert
- `LIVE_TRADING=false` — keep false until paper trading is validated

## ngrok Setup (for TradingView webhooks in dev)

```bash
ngrok http 8000
# Copy the https://xxxx.ngrok.io URL
# In TradingView alert: Webhook URL = https://xxxx.ngrok.io/webhook
```

## TradingView Alert JSON Format

```json
{"secret":"YOUR_WEBHOOK_SECRET","symbol":"NIFTY","action":"{{strategy.order.action}}","price":{{close}},"strategy":"SuperTrend_VWAP","timeframe":"5"}
```

Pine Script is in `tradingview_pine_scripts/supertrend_vwap_strategy.pine`.

## Data Flow for Adding a New Strategy

1. Create new Pine Script in `tradingview_pine_scripts/`
2. Set alert webhook pointing to `/webhook` with same JSON format
3. Bot automatically runs through the AI confirmation + risk gates
4. No backend code change needed — the webhook server is strategy-agnostic

## Paper → Live Switch

Set `LIVE_TRADING=true` in `.env`. The webhook server will use `UpstoxTrader` instead of `PaperTrader`. Requires valid `UPSTOX_ACCESS_TOKEN`.

## Indian Market Hours

- Market open: 9:15 AM IST
- Time filter active: 9:30 AM – 3:00 PM (bot skips first/last 15 min)
- Auto square-off: 3:20 PM IST
- All times handled via `pytz.timezone("Asia/Kolkata")` in `config/settings.py`
