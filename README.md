# AI Trading Bot — Indian Market (NSE F&O Intraday)

An AI-powered algorithmic trading bot for the Indian stock market, specifically designed for **Nifty and BankNifty intraday F&O trading**. Built with Python, powered by XGBoost ML and technical analysis, integrated with TradingView alerts and the Upstox broker API.

---

## What This Bot Does

- Receives trade signals from **TradingView** via webhooks (Pine Script strategies)
- Runs signals through a **3-gate AI confirmation layer** before placing any trade
- Executes **paper trades** (Phase 1) or **live Upstox orders** (Phase 2)
- Applies strict **risk management** — daily loss limits, position sizing, time filters
- Sends **Telegram alerts** for every trade entry, exit, and daily PnL
- Shows everything on a **live Streamlit dashboard**

---

## Architecture

```
TradingView Alert (Pine Script)
        │
        ▼
Webhook Server (FastAPI)  ←── ngrok (local dev)
        │
        ▼
┌───────────────────────────────┐
│       AI Signal Engine        │
│  ├── Technical Indicators     │  SuperTrend, VWAP, EMA, RSI, ATR, ADX
│  ├── ML Classifier (XGBoost)  │  Trained on 1 year of 5-min OHLCV data
│  └── Regime Detector          │  trending / ranging / volatile (India VIX)
└───────────────────────────────┘
        │
        ▼
Risk Manager
  ├── Position sizing (risk-based)
  ├── Daily loss limit (2% of capital)
  ├── Max 2 open positions
  └── Time filter (9:30 AM – 3:00 PM IST only)
        │
        ▼
Paper Trader (Phase 1) ──► SQLite DB
    OR
Upstox Live Trader (Phase 2)
        │
        ▼
Streamlit Dashboard + Telegram Bot
```

---

## Project Structure

```
AI_Trading1/
├── config/settings.py           # Central config — reads from .env
├── data/
│   ├── historical.py            # Upstox + yfinance OHLCV fetcher
│   ├── options_chain.py         # NSE live OI, PCR, Max Pain
│   └── realtime_feed.py         # Upstox WebSocket live feed (Phase 2)
├── signals/
│   ├── technical.py             # 20+ indicators (SuperTrend, VWAP, RSI...)
│   ├── ml_model.py              # XGBoost signal classifier
│   └── regime.py                # Market regime detector
├── execution/
│   ├── paper_trader.py          # Phase 1: virtual portfolio in SQLite
│   └── upstox_trader.py         # Phase 2: live Upstox API orders
├── risk/risk_manager.py         # Position sizing + daily loss controls
├── webhook/server.py            # FastAPI webhook receiver
├── backtest/backtester.py       # Walk-forward backtest engine
├── dashboard/app.py             # Streamlit dashboard
├── notifications/telegram_bot.py
└── tradingview_pine_scripts/    # Ready-to-use Pine Script strategies
```

---

## Quick Start

### 1. Prerequisites

| Item | Cost | Where |
|---|---|---|
| Python 3.11+ | Free | python.org |
| TradingView Pro | ~$15/month | tradingview.com |
| Upstox account + API | Free | upstox.com + developer.upstox.com |
| Telegram account | Free | telegram.org |
| ngrok | Free tier | ngrok.com |

### 2. Install

```bash
git clone https://github.com/YOUR_USERNAME/AI_Trading_Here.git
cd AI_Trading_Here
pip install -r requirements.txt
```

### 3. Configure

```bash
copy .env.example .env
# Edit .env with your API keys
```

Required `.env` values:
```
UPSTOX_API_KEY=...
UPSTOX_API_SECRET=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
WEBHOOK_SECRET=any_random_string
LIVE_TRADING=false
PAPER_CAPITAL=1000000
```

### 4. Run

```bash
# Terminal 1 — Start webhook server
python -m webhook.server

# Terminal 2 — Expose to internet for TradingView
ngrok http 8000

# Terminal 3 — Start dashboard
streamlit run dashboard/app.py
```

### 5. TradingView Setup

1. Open TradingView → add `tradingview_pine_scripts/supertrend_vwap_strategy.pine` to your Nifty/BankNifty 5-min chart
2. Create an Alert → set Webhook URL to your ngrok URL + `/webhook`
3. Set Alert Message:
```json
{"secret":"YOUR_WEBHOOK_SECRET","symbol":"NIFTY","action":"{{strategy.order.action}}","price":{{close}},"strategy":"SuperTrend_VWAP","timeframe":"5"}
```
4. Watch paper trades appear in the dashboard

---

## Backtest

```bash
python -m backtest.backtester --symbol NIFTY --days 180 --interval 5m
```

Sample output:
```
total_trades          47
win_rate_pct          55.32
total_pnl             82450
sharpe_ratio          1.34
max_drawdown_pct      8.7
profit_factor         1.62
```

---

## Train ML Model

```bash
python -m signals.ml_model --mode train --symbol NIFTY --days 365
```

Trains an XGBoost classifier on 1 year of 5-min data. Model saved to `models/xgb_nifty.pkl`.

---

## Accuracy Expectations

> Risk management matters more than raw accuracy in F&O intraday.

| Metric | Realistic | Good | Excellent |
|---|---|---|---|
| Win Rate | 48–55% | 55–60% | >60% |
| Risk:Reward | 1:1.5 | 1:2 | 1:2.5+ |
| Sharpe Ratio | 0.8–1.2 | 1.2–1.8 | >2.0 |
| Max Drawdown | <20% | <12% | <8% |

**Key insight:** A 50% win rate with 1:2 R:R = profitable. 5 wins × 2R − 5 losses × 1R = **+5R net**.

---

## Roadmap

- [x] Phase 1 — TradingView paper trading via webhooks
- [x] Backtesting engine (SuperTrend + VWAP strategy)
- [x] XGBoost ML signal classifier
- [x] Market regime detector (VIX + ADX)
- [x] Risk manager (position sizing, daily loss limits)
- [x] Streamlit dashboard
- [x] Telegram notifications
- [x] NSE options chain (PCR, OI, Max Pain)
- [ ] Phase 2 — Upstox live trading (after paper validation)
- [ ] Real-time WebSocket feed
- [ ] Options Greeks calculator
- [ ] Multi-strategy portfolio mode
- [ ] Cloud deployment (AWS/GCP)

---

## Disclaimer

This software is for **educational and research purposes only**. Trading in F&O involves substantial risk of loss. Always test thoroughly on paper before using real money. Past backtest performance does not guarantee future results. The authors are not responsible for any financial losses.

---

## License

MIT License — free to use, modify, and distribute.
