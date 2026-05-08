"""
Streamlit Dashboard — run with: streamlit run dashboard/app.py

Shows:
  - Live portfolio summary & equity curve
  - Open positions with unrealized PnL
  - Trade history with filters
  - AI Reasoning panel — per-trade Claude decisions, confidence, key factors
  - Market regime & options chain snapshot
  - Backtest launcher
"""
import json
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

    page = st.radio("Navigate", [
        "Portfolio", "Trades", "AI Reasoning", "Market Regime", "Backtest", "Settings"
    ])

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


@st.cache_data(ttl=30)
def load_memory_stats() -> dict:
    try:
        from agents.trade_memory import TradeMemory
        return TradeMemory().get_stats()
    except Exception:
        return {}


@st.cache_data(ttl=30)
def load_few_shot_preview() -> str:
    try:
        from agents.trade_memory import TradeMemory
        return TradeMemory().format_for_prompt(n=3)
    except Exception:
        return "Could not load trade memory."


def parse_ai_json(raw: str) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


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


elif page == "AI Reasoning":
    st.header("AI Reasoning & Trade Intelligence")

    tab1, tab2, tab3 = st.tabs(["Trade Decisions", "Performance Stats", "Few-Shot Examples"])

    # ── Tab 1: Per-trade AI decisions ────────────────────────────────────────
    with tab1:
        trades = load_trades()

        # Build a display table from ai_json
        rows = []
        for _, t in trades.iterrows():
            ai = parse_ai_json(t.get("ai_json", ""))
            rows.append({
                "ID":          t["id"],
                "Symbol":      t["symbol"],
                "Action":      t["action"],
                "Entry":       t["entry_price"],
                "Confidence":  f"{ai.get('confidence', 0):.0%}" if ai else "—",
                "Window":      ai.get("time_window", "—"),
                "MTF Aligned": "✓" if ai.get("mtf_aligned") else "✗" if ai else "—",
                "PnL":         f"₹{t['pnl']:+,.0f}" if t["status"] == "CLOSED" else "Open",
                "Status":      t["status"],
                "Entry Time":  t["entry_time"][:16] if t["entry_time"] else "",
            })

        if rows:
            df_display = pd.DataFrame(rows)
            st.dataframe(df_display, use_container_width=True, hide_index=True)
        else:
            st.info("No trades recorded yet.")

        # ── Trade detail viewer ──────────────────────────────────────────────
        st.divider()
        st.subheader("Trade Detail Viewer")

        trade_ids = trades["id"].tolist()
        if trade_ids:
            selected_id = st.selectbox("Select Trade ID", trade_ids)
            selected = trades[trades["id"] == selected_id].iloc[0]
            ai = parse_ai_json(selected.get("ai_json", ""))

            col1, col2, col3 = st.columns(3)
            col1.metric("Symbol",     f"{selected['symbol']} {selected['action']}")
            col2.metric("Confidence", f"{ai.get('confidence', 0):.0%}" if ai else "N/A")
            col3.metric("PnL",        f"₹{selected['pnl']:+,.0f}" if selected["status"] == "CLOSED" else "Open")

            if ai:
                col4, col5, col6 = st.columns(3)
                col4.metric("Time Window",  ai.get("time_window", "—"))
                col5.metric("MTF Aligned",  "Yes ✓" if ai.get("mtf_aligned") else "No ✗")
                col6.metric("Strategy",     ai.get("strategy", "—"))

                st.subheader("AI Reasoning")
                st.info(ai.get("reasoning", "No reasoning recorded."))

                st.subheader("Key Factors")
                factors = ai.get("key_factors", [])
                for f_ in factors:
                    st.markdown(f"- {f_}")

                with st.expander("Raw AI Decision JSON"):
                    st.json(ai)
            else:
                st.warning("No AI data for this trade — it may have been placed before the AI pipeline was active.")
        else:
            st.info("No trades to display.")

    # ── Tab 2: Performance stats from TradeMemory ────────────────────────────
    with tab2:
        st.subheader("AI Learning Stats")
        st.caption("Stats are computed from closed trades stored in logs/trade_memory.json")

        stats = load_memory_stats()
        if stats and stats.get("total", 0) > 0:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total Trades",  stats["total"])
            c2.metric("Win Rate",      f"{stats['win_rate_pct']}%")
            c3.metric("Total PnL",     f"₹{stats['total_pnl']:+,.0f}")
            c4.metric("Expectancy",    f"₹{stats['expectancy']:+,.0f} / trade")

            c5, c6 = st.columns(2)
            c5.metric("Avg Win",  f"₹{stats['avg_win']:+,.0f}")
            c6.metric("Avg Loss", f"₹{stats['avg_loss']:+,.0f}")

            # Win/loss bar chart
            fig = go.Figure(go.Bar(
                x=["Wins", "Losses"],
                y=[stats["wins"], stats["losses"]],
                marker_color=["limegreen", "tomato"],
                text=[stats["wins"], stats["losses"]],
                textposition="auto",
            ))
            fig.update_layout(title="Win / Loss Count", height=280,
                              template="plotly_dark", showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

            progress = min(stats["total"] / 50, 1.0)
            st.progress(progress, text=f"Few-shot quality: {stats['total']}/50 trades accumulated "
                        f"({'Using real trade history' if stats['total'] >= 8 else 'Using default patterns — accumulate more trades'})")
        else:
            st.info("No closed trades in trade memory yet. The AI will start learning once your first trades close.")
            st.caption("Tip: TradeMemory kicks in after the first SL/target hit or manual exit.")

    # ── Tab 3: Few-shot examples Claude is currently seeing ──────────────────
    with tab3:
        st.subheader("Current Few-Shot Prompt (What Claude Sees)")
        st.caption("This is the trading history / pattern guide injected into Claude's system prompt on every trade evaluation.")
        if st.button("Refresh"):
            st.cache_data.clear()
        preview = load_few_shot_preview()
        st.code(preview, language="text")


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

    backtest_type = st.radio("Backtest Mode", ["Classic (SuperTrend+VWAP)", "AI Comparison (Claude vs Classic)"],
                             horizontal=True)

    col1, col2, col3 = st.columns(3)
    bt_symbol   = col1.selectbox("Symbol", ["NIFTY", "BANKNIFTY"])
    bt_days     = col2.slider("History (days)", 7, 365, 30)
    bt_interval = col3.selectbox("Interval", ["5m", "15m"])

    if backtest_type == "Classic (SuperTrend+VWAP)":
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

    else:  # AI Comparison
        max_signals = st.slider("Max Claude API calls (signals to evaluate)", 10, 200, 50,
                                help="Each call costs ~$0.015 with Opus 4.7. 50 signals ≈ $0.75")
        st.warning(f"This will make up to {max_signals} Claude API calls. Estimated cost: ~${max_signals * 0.015:.2f}")

        if st.button("Run AI Comparison Backtest", type="primary"):
            with st.spinner("Running AI backtest — calling Claude for each signal..."):
                from backtest.ai_backtester import run_ai_backtest
                result = run_ai_backtest(symbol=bt_symbol, days=bt_days, max_signals=max_signals)

            if result:
                st.subheader("Results")
                old = result["old_system"]
                ai  = result["ai_system"]

                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("**Classic System (4-of-6 gate)**")
                    st.metric("Trades Taken", old["total"])
                    st.metric("Win Rate",     f"{old['win_rate_pct']}%")
                    st.metric("Total PnL",    f"₹{old['total_pnl']:+,.0f}")
                    st.metric("Avg Win",      f"₹{old['avg_win']:+,.0f}")
                    st.metric("Avg Loss",     f"₹{old['avg_loss']:+,.0f}")

                with col2:
                    st.markdown("**AI System (Claude Opus 4.7)**")
                    wr_delta = ai["win_rate_pct"] - old["win_rate_pct"]
                    st.metric("Trades Taken", ai["total"], delta=f"{ai['total'] - old['total']} vs classic")
                    st.metric("Win Rate",     f"{ai['win_rate_pct']}%", delta=f"{wr_delta:+.1f}%")
                    st.metric("Total PnL",    f"₹{ai['total_pnl']:+,.0f}",
                              delta=f"₹{result['pnl_improvement']:+,.0f}")
                    st.metric("Avg Win",      f"₹{ai['avg_win']:+,.0f}")
                    st.metric("Avg Loss",     f"₹{ai['avg_loss']:+,.0f}")

                st.divider()
                col3, col4 = st.columns(2)
                col3.metric("Signals Evaluated",  result["signals_evaluated"])
                col4.metric("AI Filter Rate",      f"{result['ai_filter_rate_pct']}%",
                            help="% of signals Claude skipped (higher = more selective)")

                if result["win_rate_improvement"] > 0:
                    st.success(f"AI improved win rate by {result['win_rate_improvement']:+.1f}%")
                elif result["win_rate_improvement"] < 0:
                    st.error(f"AI reduced win rate by {result['win_rate_improvement']:.1f}% — review thresholds")
                else:
                    st.info("No win rate difference — AI and classic performed similarly on this dataset")

                st.caption("Note: Historical backtest uses neutral options chain values (PCR=1.0, VIX=15). "
                           "Live results may differ as real options data is available.")


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
