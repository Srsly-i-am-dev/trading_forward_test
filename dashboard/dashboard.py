import sqlite3

import pandas as pd
import streamlit as st

from config import AppConfig


@st.cache_data(ttl=5)
def load_data(db_path: str):
    conn = sqlite3.connect(db_path)
    try:
        signals = pd.read_sql_query("SELECT * FROM signals", conn)
        executions = pd.read_sql_query("SELECT * FROM executions", conn)
    finally:
        conn.close()
    return signals, executions


def main():
    cfg = AppConfig.from_env()
    st.set_page_config(page_title="Forward Testing Dashboard", layout="wide")
    st.title("TradingView -> cTrader Forward Testing Dashboard")
    st.caption("Ingestion health, execution outcomes, stream coverage, and latency.")

    signals, executions = load_data(cfg.db_path)
    if signals.empty:
        st.info("No signals recorded yet.")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Signals", len(signals))
    c2.metric("Unique Indicators", signals["indicator_id"].nunique())
    c3.metric("Unique Symbols", signals["normalized_symbol"].nunique())

    st.subheader("Ingestion Health")
    status_counts = signals["status"].value_counts()
    st.bar_chart(status_counts)

    if not executions.empty:
        st.subheader("Execution Outcomes")
        exec_counts = executions["status"].value_counts()
        st.bar_chart(exec_counts)

        latency = executions["latency_ms"].dropna()
        if not latency.empty:
            c4, c5, c6 = st.columns(3)
            c4.metric("Avg Latency (ms)", f"{latency.mean():.1f}")
            c5.metric("P95 Latency (ms)", f"{latency.quantile(0.95):.1f}")
            c6.metric("Max Latency (ms)", f"{latency.max():.1f}")
    else:
        st.warning("No executions recorded yet.")

    st.subheader("Strategy Stream Coverage (Indicator x Symbol)")
    coverage = (
        signals.groupby(["indicator_id", "normalized_symbol"])
        .size()
        .reset_index(name="count")
        .pivot(index="indicator_id", columns="normalized_symbol", values="count")
        .fillna(0)
    )
    st.dataframe(coverage, use_container_width=True)

    st.subheader("Signals Table")
    st.dataframe(signals.sort_values("received_at", ascending=False), use_container_width=True)

    st.subheader("Executions Table")
    st.dataframe(executions.sort_values("executed_at", ascending=False), use_container_width=True)


if __name__ == "__main__":
    main()

