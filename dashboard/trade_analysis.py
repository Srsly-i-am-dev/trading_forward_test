"""
ABC Pattern Trade Analysis Dashboard

Interactive Streamlit dashboard for analyzing MT5 forward test results.
Run: streamlit run dashboard/trade_analysis.py
"""

import os
import sys

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# When run via `streamlit run dashboard/trade_analysis.py`, Streamlit adds
# the script's directory to sys.path, making `dashboard` resolve to the
# current directory rather than a package. Import trade_parser directly.
from trade_parser import (
    compute_equity_curve,
    compute_overall_stats,
    compute_symbol_stats,
    filter_trades,
    load_and_parse,
)

DEFAULT_FILE = os.path.join(
    os.path.expanduser("~"), "Downloads", "ReportHistory-52801643 (1).html"
)

st.set_page_config(
    page_title="ABC Pattern Trade Analysis",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_data
def cached_parse(file_path: str):
    return load_and_parse(file_path)


# ── SIDEBAR ──────────────────────────────────────────────────────────────

st.sidebar.title("Filters")

uploaded = st.sidebar.file_uploader("Upload MT5 HTML Report", type=["html", "htm"])
if uploaded:
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as tmp:
        tmp.write(uploaded.read())
        file_path = tmp.name
else:
    file_path = st.sidebar.text_input("Or enter file path", value=DEFAULT_FILE)

if not os.path.exists(file_path):
    st.error(f"File not found: {file_path}")
    st.stop()

positions, open_pos, summary = cached_parse(file_path)

if positions.empty:
    st.error("No trades parsed from the report.")
    st.stop()

# Date filter
min_date = positions["open_time"].min().date()
max_date = positions["open_time"].max().date()
date_range = st.sidebar.date_input(
    "Date Range",
    value=(pd.Timestamp("2026-03-30").date(), pd.Timestamp("2026-04-01").date()),
    min_value=min_date,
    max_value=max_date,
)

if len(date_range) == 2:
    start_date = str(date_range[0])
    end_date = str(pd.Timestamp(date_range[1]) + pd.Timedelta(days=1))
else:
    start_date = str(date_range[0])
    end_date = str(pd.Timestamp(date_range[0]) + pd.Timedelta(days=1))

# Symbol filter
all_symbols = sorted(positions["symbol"].unique())
selected_symbols = st.sidebar.multiselect("Symbols", all_symbols, default=all_symbols)

# Direction filter
direction = st.sidebar.radio("Direction", ["All", "Buy", "Sell"], horizontal=True)

# Exit type filter
exit_types = st.sidebar.multiselect(
    "Exit Types", ["tp", "sl", "signal"], default=["tp", "sl", "signal"]
)

# Position exclusion
exclude_input = st.sidebar.text_area(
    "Exclude Position IDs (comma-separated)",
    placeholder="e.g. 1563025063, 1563037278",
)
exclude_positions = [
    x.strip() for x in exclude_input.split(",") if x.strip()
] if exclude_input else []

# ── APPLY FILTERS ────────────────────────────────────────────────────────

df = filter_trades(positions, start_date, end_date, exclude_positions)

if selected_symbols:
    df = df[df["symbol"].isin(selected_symbols)]

if direction == "Buy":
    df = df[df["trade_type"] == "buy"]
elif direction == "Sell":
    df = df[df["trade_type"] == "sell"]

if exit_types:
    df = df[df["exit_type"].isin(exit_types)]

df = df.reset_index(drop=True)

# ── TITLE ────────────────────────────────────────────────────────────────

st.title("ABC Pattern - Trade Analysis")
st.caption(f"Analyzing {len(df)} trades from {start_date} to {end_date.split('T')[0] if 'T' in end_date else end_date[:10]}")

if df.empty:
    st.warning("No trades match the current filters.")
    st.stop()

# ── 1. OVERALL SUMMARY METRICS ──────────────────────────────────────────

overall = compute_overall_stats(df)

st.header("Overall Summary")
cols = st.columns(8)
cols[0].metric("Total Trades", overall["total_trades"])
cols[1].metric("Win Rate", f"{overall['win_rate']}%")
cols[2].metric("Net P&L", f"${overall['net_pnl']:,.2f}",
               delta_color="normal" if overall["net_pnl"] >= 0 else "inverse")
cols[3].metric("Profit Factor", f"{overall['profit_factor']:.2f}")
cols[4].metric("Avg Win", f"${overall['avg_win']:,.2f}")
cols[5].metric("Avg Loss", f"${overall['avg_loss']:,.2f}")
cols[6].metric("Max Consec. Losses", overall["max_consecutive_losses"])
cols[7].metric("Avg RRR (Intended)", f"{overall['avg_rrr_intended']:.2f}")

col_a, col_b, col_c = st.columns(3)
col_a.metric("TP Hits", f"{overall['tp_hits']} ({overall['tp_hits']/overall['total_trades']*100:.1f}%)")
col_b.metric("SL Hits", f"{overall['sl_hits']} ({overall['sl_hits']/overall['total_trades']*100:.1f}%)")
col_c.metric("Signal Closes", f"{overall['signal_closes']} ({overall['signal_closes']/overall['total_trades']*100:.1f}%)")

st.divider()

# ── 2. EQUITY CURVE ─────────────────────────────────────────────────────

st.header("Equity Curve")
equity = compute_equity_curve(df)

fig_eq = go.Figure()
fig_eq.add_trace(go.Scatter(
    x=equity["close_time"],
    y=equity["cumulative_pnl"],
    mode="lines+markers",
    name="Cumulative P&L",
    line=dict(color="#00E676", width=2),
    marker=dict(size=4),
    hovertemplate="<b>%{x}</b><br>P&L: $%{y:,.2f}<br>%{customdata[0]} %{customdata[1]}<br>Exit: %{customdata[2]}<extra></extra>",
    customdata=equity[["symbol", "trade_type", "exit_type"]].values,
))

# Drawdown fill
fig_eq.add_trace(go.Scatter(
    x=equity["close_time"],
    y=equity["drawdown"],
    mode="lines",
    name="Drawdown",
    line=dict(color="rgba(255,23,68,0.3)", width=1),
    fill="tozeroy",
    fillcolor="rgba(255,23,68,0.1)",
))

fig_eq.update_layout(
    height=400,
    xaxis_title="Time",
    yaxis_title="Cumulative P&L ($)",
    hovermode="x unified",
    template="plotly_dark",
)
st.plotly_chart(fig_eq, use_container_width=True)

st.divider()

# ── 3. PER-SYMBOL BREAKDOWN ─────────────────────────────────────────────

st.header("Per-Symbol Breakdown")
sym_stats = compute_symbol_stats(df)

if not sym_stats.empty:
    # Highlight profitable/losing symbols
    def color_profit(val):
        if isinstance(val, (int, float)):
            if val > 0:
                return "color: #00E676"
            elif val < 0:
                return "color: #FF1744"
        return ""

    display_cols = [
        "symbol", "trades", "wins", "losses", "win_rate", "total_profit",
        "profit_factor", "avg_win", "avg_loss", "avg_rrr_intended",
        "avg_rrr_actual", "max_consecutive_wins", "max_consecutive_losses",
        "max_drawdown", "tp_hits", "sl_hits", "signal_closes", "tp_hit_rate",
        "avg_duration_min",
    ]
    styled = sym_stats[display_cols].style.map(
        color_profit, subset=["total_profit", "max_drawdown"]
    ).format({
        "win_rate": "{:.1f}%",
        "total_profit": "${:,.2f}",
        "profit_factor": "{:.2f}",
        "avg_win": "${:,.2f}",
        "avg_loss": "${:,.2f}",
        "avg_rrr_intended": "{:.2f}",
        "avg_rrr_actual": "{:.2f}",
        "max_drawdown": "${:,.2f}",
        "tp_hit_rate": "{:.1f}%",
        "avg_duration_min": "{:.0f} min",
    })
    st.dataframe(styled, use_container_width=True, height=500)

st.divider()

# ── 4. PER-SYMBOL PROFIT BAR CHART ──────────────────────────────────────

st.header("Profit by Symbol")
if not sym_stats.empty:
    sym_sorted = sym_stats.sort_values("total_profit")
    colors = ["#00E676" if x > 0 else "#FF1744" for x in sym_sorted["total_profit"]]

    fig_bar = go.Figure(go.Bar(
        x=sym_sorted["total_profit"],
        y=sym_sorted["symbol"],
        orientation="h",
        marker_color=colors,
        text=[f"${x:,.0f}" for x in sym_sorted["total_profit"]],
        textposition="outside",
        hovertemplate="<b>%{y}</b><br>P&L: $%{x:,.2f}<extra></extra>",
    ))
    fig_bar.update_layout(
        height=max(400, len(sym_sorted) * 30),
        xaxis_title="Net P&L ($)",
        yaxis_title="",
        template="plotly_dark",
    )
    st.plotly_chart(fig_bar, use_container_width=True)

st.divider()

# ── 5. WIN RATE BY SYMBOL ────────────────────────────────────────────────

st.header("Win Rate by Symbol")
if not sym_stats.empty:
    overall_wr = overall["win_rate"]
    fig_wr = go.Figure()
    fig_wr.add_trace(go.Bar(
        x=sym_stats["symbol"],
        y=sym_stats["win_rate"],
        marker_color=["#00E676" if x >= overall_wr else "#FF1744" for x in sym_stats["win_rate"]],
        text=[f"{x:.1f}%" for x in sym_stats["win_rate"]],
        textposition="outside",
        hovertemplate="<b>%{x}</b><br>Win Rate: %{y:.1f}%<br>Trades: %{customdata}<extra></extra>",
        customdata=sym_stats["trades"],
    ))
    fig_wr.add_hline(
        y=overall_wr, line_dash="dash", line_color="yellow",
        annotation_text=f"Overall: {overall_wr:.1f}%",
    )
    fig_wr.update_layout(
        height=400,
        yaxis_title="Win Rate (%)",
        template="plotly_dark",
    )
    st.plotly_chart(fig_wr, use_container_width=True)

st.divider()

# ── 6. EXIT ANALYSIS ─────────────────────────────────────────────────────

st.header("Exit Analysis")

col1, col2 = st.columns(2)

with col1:
    st.subheader("Exit Type Distribution")
    exit_counts = df["exit_type"].value_counts()
    fig_pie = px.pie(
        names=exit_counts.index,
        values=exit_counts.values,
        color=exit_counts.index,
        color_discrete_map={"tp": "#00E676", "sl": "#FF1744", "signal": "#FFD600"},
        hole=0.4,
    )
    fig_pie.update_layout(template="plotly_dark", height=350)
    st.plotly_chart(fig_pie, use_container_width=True)

with col2:
    st.subheader("Exit Type P&L Impact")
    exit_pnl = df.groupby("exit_type").agg(
        count=("profit", "count"),
        total_pnl=("net_pnl", "sum"),
        avg_pnl=("net_pnl", "mean"),
        avg_duration=("duration_minutes", "mean"),
    ).round(2)
    st.dataframe(exit_pnl, use_container_width=True)

# Trades that went right direction but didn't hit TP
st.subheader("Wasted Potential: Signal-Closed Losing Trades")
st.caption("Trades closed by a new signal (not TP/SL) that ended as losses — these may have been profitable at some point.")

signal_losses = df[(df["exit_type"] == "signal") & (~df["is_win"])].copy()
if not signal_losses.empty:
    wasted_cols = [
        "open_time", "close_time", "symbol", "trade_type", "entry_price",
        "close_price", "sl", "tp", "profit", "net_pnl", "rrr_intended",
        "rrr_actual", "duration_minutes",
    ]
    st.dataframe(
        signal_losses[wasted_cols].sort_values("profit"),
        use_container_width=True,
        height=300,
    )
    st.metric(
        "Total Wasted (Signal-Closed Losses)",
        f"${signal_losses['net_pnl'].sum():,.2f}",
        f"{len(signal_losses)} trades",
    )
else:
    st.info("No signal-closed losing trades in current filter.")

# TP vs SL dollar comparison
st.subheader("TP Wins vs SL Losses")
tp_wins = df[df["exit_type"] == "tp"]
sl_losses = df[(df["exit_type"] == "sl") & (~df["is_win"])]
c1, c2, c3 = st.columns(3)
c1.metric("TP Hit Profit", f"${tp_wins['net_pnl'].sum():,.2f}", f"{len(tp_wins)} trades")
c2.metric("SL Hit Loss", f"${sl_losses['net_pnl'].sum():,.2f}", f"{len(sl_losses)} trades")
money_left = tp_wins["net_pnl"].sum() + sl_losses["net_pnl"].sum()
c3.metric("Net (TP - SL)", f"${money_left:,.2f}")

st.divider()

# ── 7. INDIVIDUAL TRADE LOG ──────────────────────────────────────────────

st.header("Individual Trade Log")

search = st.text_input("Search trades (symbol, type, etc.)")
trade_log = df.copy()
if search:
    mask = trade_log.astype(str).apply(lambda row: row.str.contains(search, case=False).any(), axis=1)
    trade_log = trade_log[mask]

display_trade_cols = [
    "open_time", "close_time", "position_id", "symbol", "trade_type",
    "volume", "entry_price", "close_price", "sl", "tp", "profit",
    "commission", "swap", "net_pnl", "exit_type", "is_win",
    "rrr_intended", "rrr_actual", "pips", "duration_minutes",
]

st.dataframe(
    trade_log[display_trade_cols],
    use_container_width=True,
    height=500,
)

csv = trade_log.to_csv(index=False)
st.download_button("Download CSV", csv, "abc_trade_analysis.csv", "text/csv")

st.divider()

# ── 8. OPEN POSITIONS ────────────────────────────────────────────────────

if not open_pos.empty:
    st.header("Open Positions (Unrealized)")
    st.dataframe(open_pos, use_container_width=True)
    total_unrealized = open_pos["unrealized_pnl"].sum()
    st.metric("Total Unrealized P&L", f"${total_unrealized:,.2f}")
    st.divider()

# ── 9. EXIT ZONE OPTIMIZER ───────────────────────────────────────────────

st.header("Exit Zone Optimizer")
st.caption("Replays each trade using 5-min price data to find optimal exit strategies. "
           "Symbols without data (BNBUSD, SOLUSD, US30) are skipped.")

from exit_optimizer import (
    run_all_simulations,
    summarize_simulations,
    summarize_by_symbol,
)


@st.cache_data
def cached_simulations(_trades_hash, trades_json):
    """Run simulations with caching. _trades_hash for cache key."""
    from io import StringIO
    trades_df = pd.read_json(StringIO(trades_json))
    trades_df["open_time"] = pd.to_datetime(trades_df["open_time"])
    trades_df["close_time"] = pd.to_datetime(trades_df["close_time"])
    trades_df["position_id"] = trades_df["position_id"].astype(str)
    return run_all_simulations(trades_df)


trades_hash = hash(df[["position_id", "symbol"]].to_string())

with st.spinner("Running exit simulations on 5-min data (this may take a moment)..."):
    sim_results = cached_simulations(trades_hash, df.to_json())

analyzed = sim_results["analyzed_trades"]
total = sim_results["total_trades"]
skipped = sim_results["skipped_symbols"]

st.info(f"Analyzed **{analyzed}/{total}** trades. Skipped symbols (no data): {', '.join(sorted(skipped)) if skipped else 'none'}")

# ── 9a. MFE/MAE Analysis ────────────────────────────────────────────────

st.subheader("MFE / MAE Analysis")
st.caption("MFE = Max Favorable Excursion (best the trade got). MAE = Max Adverse Excursion (worst it got).")

mfe_df = sim_results["mfe_mae"]
valid_mfe = mfe_df[mfe_df["has_data"] == True].copy()

if not valid_mfe.empty:
    # Merge with trade data for context
    valid_mfe["position_id"] = valid_mfe["position_id"].astype(str)
    mfe_merged = df.merge(valid_mfe, on="position_id", how="inner")

    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("Avg MFE", f"{valid_mfe['mfe_pips'].mean():.1f} pips")
    mc2.metric("Avg MAE", f"{valid_mfe['mae_pips'].mean():.1f} pips")
    mc3.metric("MFE/MAE Ratio", f"{valid_mfe['mfe_pips'].mean() / valid_mfe['mae_pips'].mean():.2f}")
    wasted_mfe = mfe_merged[~mfe_merged["is_win"]]
    mc4.metric("Avg MFE on Losers", f"{wasted_mfe['mfe_pips'].mean():.1f} pips" if not wasted_mfe.empty else "N/A")

    # MFE vs Actual P&L scatter
    fig_mfe = go.Figure()
    colors = ["#00E676" if w else "#FF1744" for w in mfe_merged["is_win"]]
    fig_mfe.add_trace(go.Scatter(
        x=mfe_merged["mfe_pips"],
        y=mfe_merged["pips"],
        mode="markers",
        marker=dict(color=colors, size=8, opacity=0.7),
        text=mfe_merged["symbol"],
        hovertemplate="<b>%{text}</b><br>MFE: %{x:.1f} pips<br>Actual: %{y:.1f} pips<extra></extra>",
    ))
    fig_mfe.add_trace(go.Scatter(
        x=[0, valid_mfe["mfe_pips"].max()],
        y=[0, valid_mfe["mfe_pips"].max()],
        mode="lines", line=dict(dash="dash", color="yellow"),
        name="Perfect Exit (MFE=Actual)",
    ))
    fig_mfe.update_layout(
        title="MFE vs Actual P&L (pips) — Gap = Money Left on Table",
        xaxis_title="Max Favorable Excursion (pips)",
        yaxis_title="Actual P&L (pips)",
        template="plotly_dark",
        height=450,
    )
    st.plotly_chart(fig_mfe, use_container_width=True)

    # MFE per symbol
    mfe_by_sym = mfe_merged.groupby("symbol").agg(
        trades=("mfe_pips", "count"),
        avg_mfe=("mfe_pips", "mean"),
        avg_mae=("mae_pips", "mean"),
        avg_actual=("pips", "mean"),
    ).round(1)
    mfe_by_sym["wasted_pips"] = mfe_by_sym["avg_mfe"] - mfe_by_sym["avg_actual"]
    mfe_by_sym = mfe_by_sym.sort_values("wasted_pips", ascending=False)
    st.dataframe(mfe_by_sym, use_container_width=True)

st.divider()

# ── 9b. Strategy Comparison ──────────────────────────────────────────────

st.subheader("Exit Strategy Comparison")
st.caption("Each strategy is simulated bar-by-bar on 5-min data. Improvement = sim pips - actual pips.")

summary = summarize_simulations(sim_results)
if not summary.empty:
    def color_improvement(val):
        if isinstance(val, (int, float)):
            return "color: #00E676" if val > 0 else "color: #FF1744"
        return ""

    styled_summary = summary.style.map(
        color_improvement, subset=["improvement_pips", "total_sim_pips"]
    ).format({
        "total_sim_pips": "{:,.1f}",
        "total_actual_pips": "{:,.1f}",
        "improvement_pips": "{:+,.1f}",
        "sim_win_rate": "{:.1f}%",
        "avg_sim_pips": "{:,.1f}",
        "avg_improvement": "{:+,.1f}",
    })
    st.dataframe(styled_summary, use_container_width=True)

    # Bar chart of improvements
    fig_strat = go.Figure(go.Bar(
        x=summary["strategy"],
        y=summary["improvement_pips"],
        marker_color=["#00E676" if x > 0 else "#FF1744" for x in summary["improvement_pips"]],
        text=[f"{x:+,.0f}" for x in summary["improvement_pips"]],
        textposition="outside",
        hovertemplate="<b>%{x}</b><br>Param: %{customdata}<br>Improvement: %{y:+,.1f} pips<extra></extra>",
        customdata=summary["param"],
    ))
    fig_strat.update_layout(
        title="Total Pip Improvement by Strategy",
        yaxis_title="Improvement (pips)",
        template="plotly_dark",
        height=400,
    )
    st.plotly_chart(fig_strat, use_container_width=True)

st.divider()

# ── 9c. Best Strategy Per Symbol ─────────────────────────────────────────

st.subheader("Best Strategy Breakdown by Symbol")

best_strategy = st.selectbox(
    "Select strategy to drill down",
    [k for k in sim_results.keys()
     if k not in ("skipped_symbols", "analyzed_trades", "total_trades", "mfe_mae")
     and isinstance(sim_results[k], pd.DataFrame) and not sim_results[k].empty],
    index=0,
)

if best_strategy:
    sym_breakdown = summarize_by_symbol(sim_results, best_strategy)
    if not sym_breakdown.empty:
        styled_bd = sym_breakdown.style.map(
            color_improvement, subset=["improvement_pips"]
        ).format({
            "sim_win_rate": "{:.1f}%",
            "total_actual_pips": "{:,.1f}",
            "total_sim_pips": "{:,.1f}",
            "improvement_pips": "{:+,.1f}",
            "avg_improvement": "{:+,.1f}",
        })
        st.dataframe(styled_bd, use_container_width=True)

st.divider()

# ── 9d. Key Findings ─────────────────────────────────────────────────────

st.subheader("Key Findings")

if not summary.empty:
    best = summary.iloc[0]
    worst = summary.iloc[-1]

    st.markdown(f"""
**Best Strategy:** `{best['strategy']}` ({best['param']}) — **{best['improvement_pips']:+,.0f} pips** improvement, {best['sim_win_rate']:.1f}% win rate

**Worst Strategy:** `{worst['strategy']}` ({worst['param']}) — **{worst['improvement_pips']:+,.0f} pips**, {worst['sim_win_rate']:.1f}% win rate

**Insights:**
- Trades have high MFE ({valid_mfe['mfe_pips'].mean():.0f} avg pips) but low capture — the ABC pattern *finds good entries* but the TP is set too far
- **Trailing stops** dominate because they lock in profits when the move starts reversing
- **Tighter TP (0.75x SL)** works well — taking smaller, more reliable wins beats waiting for full target
- **Time-based exits** (60-120 min) are effective — if it hasn't hit TP in 1-2 hours, close it
- **Higher RR targets (1.5x, 2.0x)** are net negative — the market doesn't reach them often enough
""")

# Download full simulation data
all_sim_rows = []
for key, sdf in sim_results.items():
    if isinstance(sdf, pd.DataFrame) and not sdf.empty and "strategy" in sdf.columns:
        all_sim_rows.append(sdf)
if all_sim_rows:
    full_sim = pd.concat(all_sim_rows, ignore_index=True)
    sim_csv = full_sim.to_csv(index=False)
    st.download_button("Download All Simulation Data (CSV)", sim_csv,
                       "exit_simulations.csv", "text/csv")
