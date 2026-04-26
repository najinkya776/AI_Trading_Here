"""
Streamlit Dashboard — run with: streamlit run dashboard/app.py

Shows:
  - Live portfolio summary & equity curve
  - Open positions with unrealized PnL
  - Trade history with filters
  - Market regime & options chain snapshot
  - Backtest launcher
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from sqlalchemy import text
from datetime import datetime

from config.settings import settings, IST
from execution.paper_trader import engine

st.set_page_config(
    page_title="AI Trading Bot",
    page_icon="",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("AI Trading Bot")
    mode = "LIVE" if settings.live_trading else "PAPER"
    color = "red" if settings.live_trading else "green"
    st.markdown(f"**Mode:** :{color}[{mode}]")
    st.markdown(f"**Time:** {datetime.now(IST).strftime('%H:%M:%S IST')}")
    st.divider()

    page = st.radio("Navigate", ["Portfolio", "Trades", "Market Regime", "Backtest", "Settings"])

# ── Load data helpers ─────────────────────────────────────────────────────────

@st.cache_data(ttl=10)
def load_trades() -> pd.DataFrame:
    with engine.connect() as conn:
        df = pd.read_sql("SELECT * FROM trades ORDER BY entry_time DESC", conn)
    return df


def load_capital() -> float:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT value FROM portfolio WHERE key='capital'")).fetchone()
    return float(row[0]) if row else settings.paper_capital


# ── Pages ─────────────────────────────────────────────────────────────────────

if page == "Portfolio":
    st.header("Portfolio Overview")

    capital = load_capital()
    trades = load_trades()
    open_trades = trades[trades["status"] == "OPEN"]
    closed_today = trades[
        (trades["status"] == "CLOSED") &
        (pd.to_datetime(trades["exit_time"]).dt.date == datetime.now(IST).date())
    ]

    daily_pnl = closed_today["pnl"].sum() if not closed_today.empty else 0.0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Capital", f"₹{capital:,.0f}")
    col2.metric("Daily PnL", f"₹{daily_pnl:+,.0f}", delta=f"{daily_pnl/settings.paper_capital*100:+.2f}%")
    col3.metric("Open Positions", len(open_trades))
    col4.metric("Trades Today", len(closed_today))

    st.divider()

    # Equity curve
    closed = trades[trades["status"] == "CLOSED"].sort_values("exit_time")
    if not closed.empty:
        equity = settings.paper_capital + closed["pnl"].cumsum()
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=closed["exit_time"], y=equity,
            mode="lines", name="Equity",
            line=dict(color="limegreen", width=2),
            fill="tozeroy", fillcolor="rgba(50,205,50,0.1)",
        ))
        fig.update_layout(title="Equity Curve", xaxis_title="Time", yaxis_title="Capital (₹)",
                          height=300, template="plotly_dark")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No closed trades yet — equity curve will appear here.")

    # Open positions
    if not open_trades.empty:
        st.subheader("Open Positions")
        st.dataframe(open_trades[["symbol", "action", "entry_price", "sl", "target", "qty", "entry_time"]],
                     use_container_width=True)
    else:
        st.info("No open positions.")


elif page == "Trades":
    st.header("Trade History")
    trades = load_trades()

    col1, col2 = st.columns(2)
    symbol_filter = col1.selectbox("Symbol", ["All"] + list(trades["symbol"].unique()))
    status_filter = col2.selectbox("Status", ["All", "OPEN", "CLOSED"])

    if symbol_filter != "All":
        trades = trades[trades["symbol"] == symbol_filter]
    if status_filter != "All":
        trades = trades[trades["status"] == status_filter]

    st.dataframe(trades, use_container_width=True)

    if not trades.empty:
        closed = trades[trades["status"] == "CLOSED"]
        if not closed.empty:
            wins = len(closed[closed["pnl"] > 0])
            total = len(closed)
            win_rate = wins / total * 100 if total > 0 else 0
            st.markdown(f"**Win Rate:** {win_rate:.1f}% | **Total PnL:** ₹{closed['pnl'].sum():+,.0f} | **Trades:** {total}")


elif page == "Market Regime":
    st.header("Market Regime & Options Chain")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Regime Detector")
        if st.button("Refresh Regime"):
            with st.spinner("Fetching..."):
                from signals.regime import regime_summary
                regime = regime_summary()
            st.metric("India VIX", f"{regime['india_vix']:.2f}" if regime['india_vix'] else "N/A")
            st.metric("Nifty Regime", regime["nifty_regime"].upper())
            st.metric("BankNifty Regime", regime["banknifty_regime"].upper())
            st.success("Trading Allowed") if regime["trade_allowed"] else st.error("Trading Blocked (Volatile)")

    with col2:
        st.subheader("Options Chain Summary")
        symbol = st.selectbox("Index", ["NIFTY", "BANKNIFTY"])
        if st.button("Fetch OI Data"):
            with st.spinner("Fetching from NSE..."):
                from data.options_chain import get_oi_summary
                oi = get_oi_summary(symbol)
            st.metric("PCR", oi["pcr"])
            st.metric("Max Pain", f"₹{oi['max_pain']:,.0f}")
            sentiment = oi["oi_sentiment"]
            if sentiment == "BULLISH":
                st.success(f"OI Sentiment: {sentiment}")
            elif sentiment == "BEARISH":
                st.error(f"OI Sentiment: {sentiment}")
            else:
                st.info(f"OI Sentiment: {sentiment}")


elif page == "Backtest":
    st.header("Backtest Engine")

    col1, col2, col3 = st.columns(3)
    bt_symbol   = col1.selectbox("Symbol", ["NIFTY", "BANKNIFTY"])
    bt_days     = col2.slider("History (days)", 30, 365, 180)
    bt_interval = col3.selectbox("Interval", ["5m", "15m"])

    if st.button("Run Backtest", type="primary"):
        with st.spinner("Running backtest... this may take 30–60 seconds"):
            from backtest.backtester import run_backtest
            result = run_backtest(symbol=bt_symbol, interval=bt_interval, days=bt_days)

        if result:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total Trades",   result["total_trades"])
            c2.metric("Win Rate",       f"{result['win_rate_pct']}%")
            c3.metric("Sharpe Ratio",   result["sharpe_ratio"])
            c4.metric("Max Drawdown",   f"{result['max_drawdown_pct']}%")

            c5, c6, c7, c8 = st.columns(4)
            c5.metric("Total PnL",      f"₹{result['total_pnl']:,}")
            c6.metric("Return",         f"{result['return_pct']}%")
            c7.metric("Profit Factor",  result["profit_factor"])
            c8.metric("Avg Win / Loss", f"₹{result['avg_win']:,.0f} / ₹{result['avg_loss']:,.0f}")


elif page == "Settings":
    st.header("Configuration")
    st.warning("Edit the `.env` file to change settings. Restart the bot after changes.")

    st.subheader("Current Settings")
    st.json({
        "live_trading": settings.live_trading,
        "paper_capital": settings.paper_capital,
        "max_positions": settings.max_positions,
        "risk_per_trade_pct": settings.risk_per_trade_pct,
        "daily_loss_limit_pct": settings.daily_loss_limit_pct,
        "upstox_api_key": "***" if settings.upstox_api_key else "NOT SET",
        "telegram": "configured" if settings.telegram_bot_token else "NOT SET",
        "webhook_secret": "***" if settings.webhook_secret else "NOT SET",
    })
