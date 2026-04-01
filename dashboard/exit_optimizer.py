"""
Exit Zone Optimizer

Loads 5-min price data, replays each trade's price path, and computes:
- MFE (Maximum Favorable Excursion) — max unrealized profit during trade
- MAE (Maximum Adverse Excursion) — max unrealized loss during trade
- Simulated exits: trailing stop, partial TP, dynamic RR, time-based
- Optimal exit parameters per symbol
"""

import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from trade_parser import PIP_SIZES, _get_pip_size


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "5 min data")

# Map MT5 symbol names to filename prefixes
SYMBOL_TO_FILE_PREFIX = {
    "EURUSD": "EUR-USD", "GBPUSD": "GBP-USD", "AUDUSD": "AUD-USD",
    "NZDUSD": "NZD-USD", "USDCAD": "USD-CAD", "USDCHF": "USD-CHF",
    "EURCHF": "EUR-CHF", "EURAUD": "EUR-AUD", "GBPAUD": "GBP-AUD",
    "EURJPY": "EUR-JPY", "USDJPY": "USD-JPY", "GBPJPY": "GBP-JPY",
    "AUDJPY": "AUD-JPY", "BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD",
    "ADAUSD": "ADA-USD", "BNBUSD": "BNB-USD", "SOLUSD": "SOL-USD",
    "US30": "US30", "XAUUSD": "XAU-USD",
}


def _load_price_data(symbol: str) -> pd.DataFrame:
    """Load and concatenate all 5-min CSV files for a symbol."""
    prefix = SYMBOL_TO_FILE_PREFIX.get(symbol)
    if not prefix:
        return pd.DataFrame()

    pattern = f"{prefix}_Minute_*_UTC.csv"
    files = sorted(Path(DATA_DIR).glob(pattern))
    # Also try without (1) duplicates — prefer the original
    files = [f for f in files if "(1)" not in f.name]

    if not files:
        return pd.DataFrame()

    frames = []
    for f in files:
        try:
            df = pd.read_csv(f)
            df["UTC"] = pd.to_datetime(df["UTC"], format="%d.%m.%Y %H:%M:%S.%f UTC")
            frames.append(df)
        except Exception:
            continue

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values("UTC").reset_index(drop=True)
    return combined


def _get_price_path(price_data: pd.DataFrame, entry_time: datetime,
                    exit_time: datetime, buffer_minutes: int = 5) -> pd.DataFrame:
    """Get the price bars between entry and exit time."""
    start = entry_time - timedelta(minutes=buffer_minutes)
    end = exit_time + timedelta(minutes=buffer_minutes)
    mask = (price_data["UTC"] >= start) & (price_data["UTC"] <= end)
    return price_data[mask].copy()


def compute_mfe_mae(trades_df: pd.DataFrame) -> pd.DataFrame:
    """
    For each trade, compute MFE and MAE using 5-min price data.

    MFE = Maximum Favorable Excursion (best unrealized profit in pips)
    MAE = Maximum Adverse Excursion (worst unrealized loss in pips)
    """
    results = []
    price_cache = {}

    for _, trade in trades_df.iterrows():
        symbol = trade["symbol"]
        entry_price = trade["entry_price"]
        trade_type = trade["trade_type"]
        pip = _get_pip_size(symbol)

        # Load price data (cached per symbol)
        if symbol not in price_cache:
            price_cache[symbol] = _load_price_data(symbol)

        prices = price_cache[symbol]
        if prices.empty:
            results.append({
                "position_id": trade["position_id"],
                "mfe_pips": None,
                "mae_pips": None,
                "mfe_price": None,
                "mae_price": None,
                "mfe_time": None,
                "mae_time": None,
                "mfe_dollars": None,
                "mae_dollars": None,
                "has_data": False,
            })
            continue

        path = _get_price_path(prices, trade["open_time"], trade["close_time"])
        if path.empty:
            results.append({
                "position_id": trade["position_id"],
                "mfe_pips": None, "mae_pips": None,
                "mfe_price": None, "mae_price": None,
                "mfe_time": None, "mae_time": None,
                "mfe_dollars": None, "mae_dollars": None,
                "has_data": False,
            })
            continue

        if trade_type == "buy":
            # For buys: favorable = high goes up, adverse = low goes down
            best_price = path["High"].max()
            worst_price = path["Low"].min()
            mfe_pips = (best_price - entry_price) / pip
            mae_pips = (entry_price - worst_price) / pip
            mfe_time = path.loc[path["High"].idxmax(), "UTC"]
            mae_time = path.loc[path["Low"].idxmin(), "UTC"]
        else:
            # For sells: favorable = low goes down, adverse = high goes up
            best_price = path["Low"].min()
            worst_price = path["High"].max()
            mfe_pips = (entry_price - best_price) / pip
            mae_pips = (worst_price - entry_price) / pip
            mfe_time = path.loc[path["Low"].idxmin(), "UTC"]
            mae_time = path.loc[path["High"].idxmax(), "UTC"]

        # Dollar values (approximate using volume)
        volume = trade["volume"]
        # For forex standard lots, 1 pip = $10 per lot for USD pairs
        # Simplified: use actual profit ratio
        sl_dist = abs(trade["entry_price"] - trade["sl"]) if trade["sl"] > 0 else 0
        if sl_dist > 0:
            dollar_per_pip = abs(trade["profit"]) / (abs(trade["close_price"] - entry_price) / pip) if abs(trade["close_price"] - entry_price) > 0 else 0
        else:
            dollar_per_pip = 0

        results.append({
            "position_id": trade["position_id"],
            "mfe_pips": round(mfe_pips, 1),
            "mae_pips": round(mae_pips, 1),
            "mfe_price": round(best_price, 6),
            "mae_price": round(worst_price, 6),
            "mfe_time": mfe_time,
            "mae_time": mae_time,
            "mfe_dollars": round(mfe_pips * dollar_per_pip, 2) if dollar_per_pip > 0 else None,
            "mae_dollars": round(mae_pips * dollar_per_pip, 2) if dollar_per_pip > 0 else None,
            "has_data": True,
        })

    return pd.DataFrame(results)


def simulate_trailing_stop(trades_df: pd.DataFrame,
                           trail_pips: float = 15.0) -> pd.DataFrame:
    """
    Simulate a trailing stop for each trade.
    Trail activates once trade is in profit by trail_pips.
    Returns simulated exit price, P&L, and comparison to actual.
    """
    results = []
    price_cache = {}

    for _, trade in trades_df.iterrows():
        symbol = trade["symbol"]
        entry_price = trade["entry_price"]
        trade_type = trade["trade_type"]
        pip = _get_pip_size(symbol)
        trail_dist = trail_pips * pip

        if symbol not in price_cache:
            price_cache[symbol] = _load_price_data(symbol)

        prices = price_cache[symbol]
        if prices.empty:
            results.append(_empty_sim_result(trade, "trailing"))
            continue

        path = _get_price_path(prices, trade["open_time"], trade["close_time"])
        if path.empty:
            results.append(_empty_sim_result(trade, "trailing"))
            continue

        # Simulate bar by bar
        best_favorable = entry_price
        trail_stop = None
        sim_exit_price = None
        sim_exit_time = None

        for _, bar in path.iterrows():
            if trade_type == "buy":
                # Update best price
                if bar["High"] > best_favorable:
                    best_favorable = bar["High"]
                # Activate trail once in profit
                if best_favorable - entry_price >= trail_dist:
                    trail_stop = best_favorable - trail_dist
                # Check if trail stop hit
                if trail_stop is not None and bar["Low"] <= trail_stop:
                    sim_exit_price = trail_stop
                    sim_exit_time = bar["UTC"]
                    break
                # Check if original SL hit
                if trade["sl"] > 0 and bar["Low"] <= trade["sl"]:
                    sim_exit_price = trade["sl"]
                    sim_exit_time = bar["UTC"]
                    break
            else:  # sell
                if bar["Low"] < best_favorable:
                    best_favorable = bar["Low"]
                if entry_price - best_favorable >= trail_dist:
                    trail_stop = best_favorable + trail_dist
                if trail_stop is not None and bar["High"] >= trail_stop:
                    sim_exit_price = trail_stop
                    sim_exit_time = bar["UTC"]
                    break
                if trade["sl"] > 0 and bar["High"] >= trade["sl"]:
                    sim_exit_price = trade["sl"]
                    sim_exit_time = bar["UTC"]
                    break

        # If no exit triggered, use actual close
        if sim_exit_price is None:
            sim_exit_price = trade["close_price"]
            sim_exit_time = trade["close_time"]

        if trade_type == "buy":
            sim_pips = (sim_exit_price - entry_price) / pip
        else:
            sim_pips = (entry_price - sim_exit_price) / pip

        actual_pips = trade["pips"]
        improvement_pips = sim_pips - actual_pips

        results.append({
            "position_id": trade["position_id"],
            "symbol": symbol,
            "trade_type": trade_type,
            "strategy": "trailing",
            "param": f"{trail_pips}pip",
            "actual_pips": actual_pips,
            "sim_pips": round(sim_pips, 1),
            "improvement_pips": round(improvement_pips, 1),
            "sim_exit_price": round(sim_exit_price, 6),
            "sim_exit_time": sim_exit_time,
            "actual_profit": trade["profit"],
            "has_data": True,
        })

    return pd.DataFrame(results)


def simulate_partial_tp(trades_df: pd.DataFrame,
                        partial_pct: float = 0.5,
                        partial_tp_ratio: float = 0.5) -> pd.DataFrame:
    """
    Simulate taking partial profit at X% of the TP distance,
    then letting the rest run to TP or SL.

    partial_pct: fraction of position to close at partial TP
    partial_tp_ratio: fraction of TP distance where partial is taken
    """
    results = []
    price_cache = {}

    for _, trade in trades_df.iterrows():
        symbol = trade["symbol"]
        entry_price = trade["entry_price"]
        trade_type = trade["trade_type"]
        pip = _get_pip_size(symbol)
        tp = trade["tp"]
        sl = trade["sl"]

        if tp == 0 or sl == 0:
            results.append(_empty_sim_result(trade, "partial_tp"))
            continue

        if trade_type == "buy":
            tp_dist = tp - entry_price
            partial_level = entry_price + tp_dist * partial_tp_ratio
        else:
            tp_dist = entry_price - tp
            partial_level = entry_price - tp_dist * partial_tp_ratio

        if symbol not in price_cache:
            price_cache[symbol] = _load_price_data(symbol)

        prices = price_cache[symbol]
        if prices.empty:
            results.append(_empty_sim_result(trade, "partial_tp"))
            continue

        path = _get_price_path(prices, trade["open_time"], trade["close_time"])
        if path.empty:
            results.append(_empty_sim_result(trade, "partial_tp"))
            continue

        # Simulate
        partial_taken = False
        partial_pnl_pips = 0
        remainder_exit_price = None

        for _, bar in path.iterrows():
            if trade_type == "buy":
                if not partial_taken and bar["High"] >= partial_level:
                    partial_pnl_pips = (partial_level - entry_price) / pip * partial_pct
                    partial_taken = True
                if bar["High"] >= tp:
                    remainder_exit_price = tp
                    break
                if bar["Low"] <= sl:
                    remainder_exit_price = sl
                    break
            else:
                if not partial_taken and bar["Low"] <= partial_level:
                    partial_pnl_pips = (entry_price - partial_level) / pip * partial_pct
                    partial_taken = True
                if bar["Low"] <= tp:
                    remainder_exit_price = tp
                    break
                if bar["High"] >= sl:
                    remainder_exit_price = sl
                    break

        if remainder_exit_price is None:
            remainder_exit_price = trade["close_price"]

        if trade_type == "buy":
            remainder_pips = (remainder_exit_price - entry_price) / pip * (1 - partial_pct if partial_taken else 1)
        else:
            remainder_pips = (entry_price - remainder_exit_price) / pip * (1 - partial_pct if partial_taken else 1)

        sim_pips = partial_pnl_pips + remainder_pips
        actual_pips = trade["pips"]

        results.append({
            "position_id": trade["position_id"],
            "symbol": symbol,
            "trade_type": trade_type,
            "strategy": "partial_tp",
            "param": f"{int(partial_pct*100)}%@{int(partial_tp_ratio*100)}%TP",
            "actual_pips": actual_pips,
            "sim_pips": round(sim_pips, 1),
            "improvement_pips": round(sim_pips - actual_pips, 1),
            "sim_exit_price": round(remainder_exit_price, 6),
            "sim_exit_time": None,
            "actual_profit": trade["profit"],
            "has_data": True,
        })

    return pd.DataFrame(results)


def simulate_time_based_exit(trades_df: pd.DataFrame,
                             max_bars: int = 12) -> pd.DataFrame:
    """
    Simulate closing the trade after N 5-min bars (max_bars * 5 minutes)
    if neither TP nor SL has been hit.
    """
    results = []
    price_cache = {}

    for _, trade in trades_df.iterrows():
        symbol = trade["symbol"]
        entry_price = trade["entry_price"]
        trade_type = trade["trade_type"]
        pip = _get_pip_size(symbol)

        if symbol not in price_cache:
            price_cache[symbol] = _load_price_data(symbol)

        prices = price_cache[symbol]
        if prices.empty:
            results.append(_empty_sim_result(trade, "time_exit"))
            continue

        path = _get_price_path(prices, trade["open_time"], trade["close_time"])
        if path.empty:
            results.append(_empty_sim_result(trade, "time_exit"))
            continue

        sim_exit_price = None
        sim_exit_time = None
        bar_count = 0

        for _, bar in path.iterrows():
            bar_count += 1
            if trade_type == "buy":
                if trade["tp"] > 0 and bar["High"] >= trade["tp"]:
                    sim_exit_price = trade["tp"]
                    sim_exit_time = bar["UTC"]
                    break
                if trade["sl"] > 0 and bar["Low"] <= trade["sl"]:
                    sim_exit_price = trade["sl"]
                    sim_exit_time = bar["UTC"]
                    break
            else:
                if trade["tp"] > 0 and bar["Low"] <= trade["tp"]:
                    sim_exit_price = trade["tp"]
                    sim_exit_time = bar["UTC"]
                    break
                if trade["sl"] > 0 and bar["High"] >= trade["sl"]:
                    sim_exit_price = trade["sl"]
                    sim_exit_time = bar["UTC"]
                    break

            if bar_count >= max_bars:
                sim_exit_price = bar["Close"]
                sim_exit_time = bar["UTC"]
                break

        if sim_exit_price is None:
            sim_exit_price = trade["close_price"]
            sim_exit_time = trade["close_time"]

        if trade_type == "buy":
            sim_pips = (sim_exit_price - entry_price) / pip
        else:
            sim_pips = (entry_price - sim_exit_price) / pip

        actual_pips = trade["pips"]

        results.append({
            "position_id": trade["position_id"],
            "symbol": symbol,
            "trade_type": trade_type,
            "strategy": "time_exit",
            "param": f"{max_bars * 5}min",
            "actual_pips": actual_pips,
            "sim_pips": round(sim_pips, 1),
            "improvement_pips": round(sim_pips - actual_pips, 1),
            "sim_exit_price": round(sim_exit_price, 6),
            "sim_exit_time": sim_exit_time,
            "actual_profit": trade["profit"],
            "has_data": True,
        })

    return pd.DataFrame(results)


def simulate_dynamic_rr(trades_df: pd.DataFrame,
                        tp_multiplier: float = 1.5,
                        sl_multiplier: float = 1.0) -> pd.DataFrame:
    """
    Simulate using a different TP/SL ratio.
    Adjusts TP distance as tp_multiplier * original SL distance.
    """
    results = []
    price_cache = {}

    for _, trade in trades_df.iterrows():
        symbol = trade["symbol"]
        entry_price = trade["entry_price"]
        trade_type = trade["trade_type"]
        pip = _get_pip_size(symbol)
        sl = trade["sl"]

        if sl == 0:
            results.append(_empty_sim_result(trade, "dynamic_rr"))
            continue

        if trade_type == "buy":
            sl_dist = entry_price - sl
            new_tp = entry_price + sl_dist * tp_multiplier
            new_sl = entry_price - sl_dist * sl_multiplier
        else:
            sl_dist = sl - entry_price
            new_tp = entry_price - sl_dist * tp_multiplier
            new_sl = entry_price + sl_dist * sl_multiplier

        if symbol not in price_cache:
            price_cache[symbol] = _load_price_data(symbol)

        prices = price_cache[symbol]
        if prices.empty:
            results.append(_empty_sim_result(trade, "dynamic_rr"))
            continue

        path = _get_price_path(prices, trade["open_time"], trade["close_time"])
        if path.empty:
            results.append(_empty_sim_result(trade, "dynamic_rr"))
            continue

        sim_exit_price = None
        sim_exit_time = None

        for _, bar in path.iterrows():
            if trade_type == "buy":
                if bar["High"] >= new_tp:
                    sim_exit_price = new_tp
                    sim_exit_time = bar["UTC"]
                    break
                if bar["Low"] <= new_sl:
                    sim_exit_price = new_sl
                    sim_exit_time = bar["UTC"]
                    break
            else:
                if bar["Low"] <= new_tp:
                    sim_exit_price = new_tp
                    sim_exit_time = bar["UTC"]
                    break
                if bar["High"] >= new_sl:
                    sim_exit_price = new_sl
                    sim_exit_time = bar["UTC"]
                    break

        if sim_exit_price is None:
            sim_exit_price = trade["close_price"]
            sim_exit_time = trade["close_time"]

        if trade_type == "buy":
            sim_pips = (sim_exit_price - entry_price) / pip
        else:
            sim_pips = (entry_price - sim_exit_price) / pip

        actual_pips = trade["pips"]

        results.append({
            "position_id": trade["position_id"],
            "symbol": symbol,
            "trade_type": trade_type,
            "strategy": "dynamic_rr",
            "param": f"TP:{tp_multiplier}x SL:{sl_multiplier}x",
            "actual_pips": actual_pips,
            "sim_pips": round(sim_pips, 1),
            "improvement_pips": round(sim_pips - actual_pips, 1),
            "sim_exit_price": round(sim_exit_price, 6),
            "sim_exit_time": sim_exit_time,
            "actual_profit": trade["profit"],
            "has_data": True,
        })

    return pd.DataFrame(results)


def run_all_simulations(trades_df: pd.DataFrame) -> dict:
    """
    Run all exit strategies and return results dict.
    Skips symbols without 5-min data.
    """
    # Filter to symbols with data
    available = set()
    for sym in trades_df["symbol"].unique():
        prefix = SYMBOL_TO_FILE_PREFIX.get(sym)
        if prefix:
            files = list(Path(DATA_DIR).glob(f"{prefix}_Minute_*_UTC.csv"))
            files = [f for f in files if "(1)" not in f.name]
            if files:
                available.add(sym)

    df = trades_df[trades_df["symbol"].isin(available)].copy()
    skipped = set(trades_df["symbol"].unique()) - available

    results = {
        "mfe_mae": compute_mfe_mae(df),
        "trailing_10": simulate_trailing_stop(df, trail_pips=10),
        "trailing_15": simulate_trailing_stop(df, trail_pips=15),
        "trailing_20": simulate_trailing_stop(df, trail_pips=20),
        "partial_50_at_50": simulate_partial_tp(df, partial_pct=0.5, partial_tp_ratio=0.5),
        "partial_50_at_75": simulate_partial_tp(df, partial_pct=0.5, partial_tp_ratio=0.75),
        "time_30min": simulate_time_based_exit(df, max_bars=6),
        "time_60min": simulate_time_based_exit(df, max_bars=12),
        "time_120min": simulate_time_based_exit(df, max_bars=24),
        "rr_1_1": simulate_dynamic_rr(df, tp_multiplier=1.0, sl_multiplier=1.0),
        "rr_1_5_1": simulate_dynamic_rr(df, tp_multiplier=1.5, sl_multiplier=1.0),
        "rr_2_1": simulate_dynamic_rr(df, tp_multiplier=2.0, sl_multiplier=1.0),
        "rr_0_75_1": simulate_dynamic_rr(df, tp_multiplier=0.75, sl_multiplier=1.0),
        "skipped_symbols": skipped,
        "analyzed_trades": len(df),
        "total_trades": len(trades_df),
    }

    return results


def summarize_simulations(sim_results: dict) -> pd.DataFrame:
    """Create a summary comparison of all strategies."""
    rows = []
    for key, df in sim_results.items():
        if key in ("skipped_symbols", "analyzed_trades", "total_trades", "mfe_mae"):
            continue
        if not isinstance(df, pd.DataFrame) or df.empty:
            continue

        valid = df[df["has_data"] == True]
        if valid.empty:
            continue

        total_sim_pips = valid["sim_pips"].sum()
        total_actual_pips = valid["actual_pips"].sum()
        improvement = total_sim_pips - total_actual_pips
        win_count = (valid["sim_pips"] > 0).sum()
        win_rate = win_count / len(valid) * 100

        rows.append({
            "strategy": key,
            "param": valid.iloc[0]["param"],
            "trades": len(valid),
            "total_sim_pips": round(total_sim_pips, 1),
            "total_actual_pips": round(total_actual_pips, 1),
            "improvement_pips": round(improvement, 1),
            "sim_win_rate": round(win_rate, 1),
            "avg_sim_pips": round(valid["sim_pips"].mean(), 1),
            "avg_improvement": round(valid["improvement_pips"].mean(), 1),
        })

    return pd.DataFrame(rows).sort_values("improvement_pips", ascending=False)


def summarize_by_symbol(sim_results: dict, strategy_key: str) -> pd.DataFrame:
    """Summarize a specific strategy's results by symbol."""
    df = sim_results.get(strategy_key)
    if df is None or df.empty:
        return pd.DataFrame()

    valid = df[df["has_data"] == True]
    if valid.empty:
        return pd.DataFrame()

    rows = []
    for symbol, group in valid.groupby("symbol"):
        wins = (group["sim_pips"] > 0).sum()
        rows.append({
            "symbol": symbol,
            "trades": len(group),
            "sim_wins": wins,
            "sim_win_rate": round(wins / len(group) * 100, 1),
            "total_actual_pips": round(group["actual_pips"].sum(), 1),
            "total_sim_pips": round(group["sim_pips"].sum(), 1),
            "improvement_pips": round(group["improvement_pips"].sum(), 1),
            "avg_improvement": round(group["improvement_pips"].mean(), 1),
        })

    return pd.DataFrame(rows).sort_values("improvement_pips", ascending=False)


def _empty_sim_result(trade, strategy):
    return {
        "position_id": trade["position_id"],
        "symbol": trade["symbol"],
        "trade_type": trade["trade_type"],
        "strategy": strategy,
        "param": "",
        "actual_pips": trade["pips"],
        "sim_pips": trade["pips"],
        "improvement_pips": 0,
        "sim_exit_price": trade["close_price"],
        "sim_exit_time": trade["close_time"],
        "actual_profit": trade["profit"],
        "has_data": False,
    }
