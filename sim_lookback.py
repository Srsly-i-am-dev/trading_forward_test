"""
Simulate ABC pattern with different pivot lookback (rightbars) values.
Compare rightbars = 3, 5, 7, 10 across all symbols and 3 days of 5-min data.

Replicates the PineScript logic:
  - ta.pivothigh(high, lookback, lookback) / ta.pivotlow(low, lookback, lookback)
  - ABC pattern detection (bullish: lower low -> high -> higher low; bearish: inverse)
  - SL = 0.5% beyond C
  - TP = MFE pips from C (or geometric fallback)
  - Proximity filter: entry only if price < 25% of TP distance from C
  - Trade outcome: simulate forward bars until TP or SL is hit (or 500 bars timeout)
"""
import os, glob, sys
import pandas as pd
import numpy as np
from collections import defaultdict

DATA_DIR = r"C:\Users\DEV\OneDrive\Documents\GitHub\trading_forward_test\5 min data"

# MFE values (pips) — same as indicator
MFE_PIPS = {
    "EURUSD": 29.0, "ETHUSD": 845.0, "ADAUSD": 1280.0, "GBPUSD": 20.5,
    "USDCHF": 7.7, "AUDJPY": 26.0, "AUDUSD": 14.3, "BTCUSD": 425.3,
    "USDJPY": 18.4, "GBPJPY": 36.1, "NZDUSD": 10.8, "USDCAD": 10.1,
    "EURCHF": 5.5, "EURAUD": 14.5,
}
# Special strategies
GBPAUD_SL_RATIO = 0.75  # TP = 0.75 * SL distance
EURJPY_RR = 1.0         # TP = 1:1 RR

PIP_SIZES = {
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001,
    "NZDUSD": 0.0001, "USDCAD": 0.0001, "USDCHF": 0.0001,
    "EURCHF": 0.0001, "EURAUD": 0.0001, "GBPAUD": 0.0001,
    "USDJPY": 0.01, "GBPJPY": 0.01, "EURJPY": 0.01, "AUDJPY": 0.01,
    "XAUUSD": 0.01, "BTCUSD": 1.0, "ETHUSD": 1.0,
    "ADAUSD": 0.00001,
}

SL_PERCENT = 0.5  # 0.5% beyond C
PROXIMITY_THRESHOLD = 0.25  # 25% of TP distance
RISK_DOLLARS = 100.0  # risk per trade for P&L calc
MAX_BARS_IN_TRADE = 500  # timeout


def load_symbol_data(symbol):
    """Load and concatenate all 3 days of 1-min data, resample to 5-min."""
    # Map symbol name to file prefix
    file_prefix = symbol[:3] + "-" + symbol[3:]
    pattern = os.path.join(DATA_DIR, f"{file_prefix}_Minute_2026-0*.csv")
    files = sorted(glob.glob(pattern))
    # Avoid duplicate files like "(1).csv"
    files = [f for f in files if "(1)" not in f]

    if not files:
        return None

    dfs = []
    for f in files:
        df = pd.read_csv(f)
        df["UTC"] = pd.to_datetime(df["UTC"], format="%d.%m.%Y %H:%M:%S.%f UTC")
        dfs.append(df)

    df = pd.concat(dfs).sort_values("UTC").reset_index(drop=True)

    # Resample 1-min to 5-min
    df = df.set_index("UTC")
    ohlc = df.resample("5min").agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"
    }).dropna().reset_index()

    return ohlc


def find_pivots(df, lookback):
    """Find pivot highs and lows like PineScript ta.pivothigh/ta.pivotlow.
    A pivot high at bar i means high[i] >= all highs in [i-lookback, i+lookback].
    Returns arrays of pivot values (NaN where no pivot)."""
    n = len(df)
    highs = df["High"].values
    lows = df["Low"].values

    pivot_highs = np.full(n, np.nan)
    pivot_lows = np.full(n, np.nan)

    for i in range(lookback, n - lookback):
        # Pivot high: bar i is highest in window
        window_h = highs[i - lookback: i + lookback + 1]
        if highs[i] == np.max(window_h):
            pivot_highs[i] = highs[i]

        # Pivot low: bar i is lowest in window
        window_l = lows[i - lookback: i + lookback + 1]
        if lows[i] == np.min(window_l):
            pivot_lows[i] = lows[i]

    return pivot_highs, pivot_lows


def compute_tp(symbol, c_level, sl_level, is_bull, a_level, b_level):
    """Compute TP using MFE or geometric fallback."""
    pip = PIP_SIZES.get(symbol, 0.0001)

    if symbol == "GBPAUD":
        sl_dist = abs(c_level - sl_level)
        tp_dist = sl_dist * GBPAUD_SL_RATIO
        return c_level + tp_dist if is_bull else c_level - tp_dist
    elif symbol == "EURJPY":
        sl_dist = abs(c_level - sl_level)
        tp_dist = sl_dist * EURJPY_RR
        return c_level + tp_dist if is_bull else c_level - tp_dist
    elif symbol in MFE_PIPS:
        mfe = MFE_PIPS[symbol]
        tp_dist = mfe * pip
        return c_level + tp_dist if is_bull else c_level - tp_dist
    else:
        # Geometric fallback
        if a_level != 0:
            return (b_level * c_level) / a_level
        return None


def simulate_trade(df, entry_bar, entry_price, tp, sl, is_bull):
    """Simulate forward from entry_bar. Return (outcome, exit_price, bars_held)."""
    for i in range(entry_bar + 1, min(entry_bar + MAX_BARS_IN_TRADE, len(df))):
        h = df["High"].values[i]
        l = df["Low"].values[i]

        if is_bull:
            # Check SL first (conservative)
            if l <= sl:
                return "sl", sl, i - entry_bar
            if h >= tp:
                return "tp", tp, i - entry_bar
        else:
            if h >= sl:
                return "sl", sl, i - entry_bar
            if l <= tp:
                return "tp", tp, i - entry_bar

    return "timeout", df["Close"].values[min(entry_bar + MAX_BARS_IN_TRADE, len(df) - 1)], MAX_BARS_IN_TRADE


def run_simulation(symbol, df, lookback):
    """Run full ABC pattern simulation for a symbol with given lookback."""
    pivot_highs, pivot_lows = find_pivots(df, lookback)
    n = len(df)
    closes = df["Close"].values
    highs = df["High"].values
    lows = df["Low"].values

    trades = []

    # Track last 2 pivot highs and lows (like PineScript)
    ph_vals = []  # (bar_index, value)
    pl_vals = []

    for i in range(n):
        if not np.isnan(pivot_highs[i]):
            ph_vals.append((i, pivot_highs[i]))
            if len(ph_vals) > 2:
                ph_vals = ph_vals[-2:]

        if not np.isnan(pivot_lows[i]):
            pl_vals.append((i, pivot_lows[i]))
            if len(pl_vals) > 2:
                pl_vals = pl_vals[-2:]

        # Need at least 2 of each to form pattern
        if len(ph_vals) < 1 or len(pl_vals) < 2:
            continue
        if len(ph_vals) < 2 or len(pl_vals) < 1:
            continue

        # The pivot is confirmed lookback bars AFTER it occurs.
        # At bar i, the most recently confirmed pivot was at bar i-lookback (if it exists).
        # But we already computed pivots — they're confirmed when we see them at bar (pivot_bar + lookback).
        # The signal fires at bar (pivot_bar + lookback), i.e., the confirmation bar.

        # Check bullish: pl2 < ph1 < pl1 (chronological), pl1 > pl2
        # A=pl2(lower low), B=ph1(high between), C=pl1(higher low)
        pl2_bar, pl2_val = pl_vals[-2]
        pl1_bar, pl1_val = pl_vals[-1]

        # The C point (pl1) is confirmed at bar pl1_bar + lookback
        c_confirm_bar = pl1_bar + lookback

        if i == c_confirm_bar and pl1_val > pl2_val:
            # Find the ph between pl2 and pl1
            best_ph = None
            for pb, pv in ph_vals:
                if pl2_bar < pb < pl1_bar and pv > pl2_val and pv > pl1_val:
                    best_ph = (pb, pv)

            if best_ph is not None:
                a_val = pl2_val  # A
                b_val = best_ph[1]  # B
                c_val = pl1_val  # C

                sl = c_val * (1 - SL_PERCENT / 100)
                tp = compute_tp(symbol, c_val, sl, True, a_val, b_val)
                if tp is None:
                    continue

                entry_price = closes[i]
                tp_dist = tp - c_val
                if tp_dist <= 0:
                    continue

                # Proximity filter
                used = entry_price - c_val
                if used < 0:
                    continue  # price below C, skip
                if entry_price > tp or entry_price < sl:
                    continue  # already past TP or SL
                if tp_dist > 0 and (used / tp_dist) >= PROXIMITY_THRESHOLD:
                    continue  # too far from C

                # Simulate trade
                outcome, exit_price, bars = simulate_trade(df, i, entry_price, tp, sl, True)
                sl_dist = entry_price - sl
                if sl_dist > 0:
                    pnl = RISK_DOLLARS * (exit_price - entry_price) / sl_dist
                else:
                    pnl = 0

                trades.append({
                    "symbol": symbol, "type": "buy", "entry_bar": i,
                    "entry_price": entry_price, "c_level": c_val,
                    "tp": tp, "sl": sl, "outcome": outcome,
                    "exit_price": exit_price, "pnl": round(pnl, 2),
                    "bars_held": bars, "lookback": lookback,
                    "time": df["UTC"].values[i],
                })

        # Check bearish: ph2 < pl1 < ph1 (chronological), ph1 < ph2
        if len(ph_vals) >= 2:
            ph2_bar, ph2_val = ph_vals[-2]
            ph1_bar, ph1_val = ph_vals[-1]
            c_confirm_bar_bear = ph1_bar + lookback

            if i == c_confirm_bar_bear and ph1_val < ph2_val:
                # Find pl between ph2 and ph1
                best_pl = None
                for pb, pv in pl_vals:
                    if ph2_bar < pb < ph1_bar and pv < ph2_val and pv < ph1_val:
                        best_pl = (pb, pv)

                if best_pl is not None:
                    a_val = ph2_val  # A
                    b_val = best_pl[1]  # B
                    c_val = ph1_val  # C

                    sl = c_val * (1 + SL_PERCENT / 100)
                    tp = compute_tp(symbol, c_val, sl, False, a_val, b_val)
                    if tp is None:
                        continue

                    entry_price = closes[i]
                    tp_dist = c_val - tp
                    if tp_dist <= 0:
                        continue

                    # Proximity filter
                    used = c_val - entry_price
                    if used < 0:
                        continue  # price above C
                    if entry_price < tp or entry_price > sl:
                        continue
                    if tp_dist > 0 and (used / tp_dist) >= PROXIMITY_THRESHOLD:
                        continue

                    outcome, exit_price, bars = simulate_trade(df, i, entry_price, tp, sl, False)
                    sl_dist = sl - entry_price
                    if sl_dist > 0:
                        pnl = RISK_DOLLARS * (entry_price - exit_price) / sl_dist
                    else:
                        pnl = 0

                    trades.append({
                        "symbol": symbol, "type": "sell", "entry_bar": i,
                        "entry_price": entry_price, "c_level": c_val,
                        "tp": tp, "sl": sl, "outcome": outcome,
                        "exit_price": exit_price, "pnl": round(pnl, 2),
                        "bars_held": bars, "lookback": lookback,
                        "time": df["UTC"].values[i],
                    })

    return trades


def main():
    symbols = [
        "EURUSD", "EURJPY", "GBPAUD", "GBPUSD", "USDCHF", "ETHUSD", "ADAUSD",
        "AUDJPY", "AUDUSD", "BTCUSD", "USDJPY", "GBPJPY", "NZDUSD", "USDCAD",
        "EURCHF", "EURAUD",
    ]

    lookbacks = [3, 5, 7, 10]

    all_trades = []

    for symbol in symbols:
        df = load_symbol_data(symbol)
        if df is None:
            print(f"  No data for {symbol}, skipping")
            continue

        print(f"  {symbol}: {len(df)} bars (5-min)", end="")

        for lb in lookbacks:
            trades = run_simulation(symbol, df, lb)
            all_trades.extend(trades)
            wins = sum(1 for t in trades if t["pnl"] > 0)
            total = len(trades)
            pnl = sum(t["pnl"] for t in trades)
            print(f" | lb={lb}: {total}t", end="")

        print()

    print("\n" + "=" * 80)
    print("LOOKBACK COMPARISON SUMMARY")
    print("=" * 80)

    for lb in lookbacks:
        subset = [t for t in all_trades if t["lookback"] == lb]
        if not subset:
            continue

        total = len(subset)
        wins = sum(1 for t in subset if t["pnl"] > 0)
        losses = total - wins
        wr = wins / total * 100 if total > 0 else 0
        total_pnl = sum(t["pnl"] for t in subset)
        avg_pnl = total_pnl / total if total > 0 else 0
        gross_win = sum(t["pnl"] for t in subset if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in subset if t["pnl"] <= 0))
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
        tp_hits = sum(1 for t in subset if t["outcome"] == "tp")
        sl_hits = sum(1 for t in subset if t["outcome"] == "sl")
        timeouts = sum(1 for t in subset if t["outcome"] == "timeout")
        avg_bars = np.mean([t["bars_held"] for t in subset])
        delay_mins = lb * 5

        print(f"\n  Lookback = {lb} (delay = {delay_mins} min)")
        print(f"  {'='*50}")
        print(f"  Trades:       {total:4d} ({wins}W / {losses}L)")
        print(f"  Win Rate:     {wr:5.1f}%")
        print(f"  Net P&L:      ${total_pnl:>+9.2f}  (avg ${avg_pnl:>+.2f}/trade)")
        print(f"  Profit Factor:{pf:6.2f}")
        print(f"  TP hits:      {tp_hits:4d} ({tp_hits/total*100:.0f}%)")
        print(f"  SL hits:      {sl_hits:4d} ({sl_hits/total*100:.0f}%)")
        print(f"  Timeouts:     {timeouts:4d} ({timeouts/total*100:.0f}%)")
        print(f"  Avg hold:     {avg_bars:.0f} bars ({avg_bars*5:.0f} min)")

    # Per-symbol breakdown for the top 2
    print("\n" + "=" * 80)
    print("PER-SYMBOL COMPARISON (lb=7 vs lb=10)")
    print("=" * 80)
    print(f"  {'Symbol':10s} | {'lb=7 trades':>10s} {'WR':>6s} {'P&L':>10s} {'PF':>6s} | {'lb=10 trades':>11s} {'WR':>6s} {'P&L':>10s} {'PF':>6s}")
    print(f"  {'-'*90}")

    symbols_seen = sorted(set(t["symbol"] for t in all_trades))
    for sym in symbols_seen:
        for lb_pair in [(7, 10)]:
            vals = {}
            for lb in lb_pair:
                subset = [t for t in all_trades if t["lookback"] == lb and t["symbol"] == sym]
                total = len(subset)
                if total == 0:
                    vals[lb] = (0, 0, 0, 0)
                    continue
                wins = sum(1 for t in subset if t["pnl"] > 0)
                wr = wins / total * 100
                pnl = sum(t["pnl"] for t in subset)
                gw = sum(t["pnl"] for t in subset if t["pnl"] > 0)
                gl = abs(sum(t["pnl"] for t in subset if t["pnl"] <= 0))
                pf = gw / gl if gl > 0 else 99.9
                vals[lb] = (total, wr, pnl, pf)

            t7, wr7, pnl7, pf7 = vals[7]
            t10, wr10, pnl10, pf10 = vals[10]
            print(f"  {sym:10s} | {t7:10d} {wr7:5.1f}% ${pnl7:>+9.2f} {pf7:5.2f} | {t10:11d} {wr10:5.1f}% ${pnl10:>+9.2f} {pf10:5.2f}")

    # Final recommendation
    print("\n" + "=" * 80)
    print("RECOMMENDATION")
    print("=" * 80)
    best_lb = None
    best_score = -999999
    for lb in lookbacks:
        subset = [t for t in all_trades if t["lookback"] == lb]
        if not subset:
            continue
        total = len(subset)
        pnl = sum(t["pnl"] for t in subset)
        wr = sum(1 for t in subset if t["pnl"] > 0) / total * 100
        gw = sum(t["pnl"] for t in subset if t["pnl"] > 0)
        gl = abs(sum(t["pnl"] for t in subset if t["pnl"] <= 0))
        pf = gw / gl if gl > 0 else 99.9
        # Score: weight P&L heavily, bonus for PF > 1.0 and decent WR
        score = pnl + (pf - 1.0) * 100 + (wr - 50) * 5
        if score > best_score:
            best_score = score
            best_lb = lb
        print(f"  lb={lb:2d} ({lb*5:2d} min): P&L=${pnl:>+9.2f} | WR={wr:.1f}% | PF={pf:.2f} | score={score:.1f}")

    print(f"\n  >>> BEST: lookback = {best_lb} ({best_lb * 5} min delay) <<<")


if __name__ == "__main__":
    main()
