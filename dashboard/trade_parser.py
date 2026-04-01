"""
MT5 Trade History HTML Parser & Statistics Engine

Parses UTF-16LE encoded MT5 Trade History Reports, extracts trades,
classifies exit types, and computes per-symbol statistics.
"""

import re
from datetime import datetime
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

# ── Pip sizes for exit classification tolerance ──────────────────────────
PIP_SIZES = {
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001,
    "NZDUSD": 0.0001, "USDCAD": 0.0001, "USDCHF": 0.0001,
    "EURCHF": 0.0001, "EURAUD": 0.0001, "GBPAUD": 0.0001,
    "EURJPY": 0.01, "USDJPY": 0.01, "GBPJPY": 0.01, "AUDJPY": 0.01,
    "XAUUSD": 0.01, "BTCUSD": 1.0, "ETHUSD": 0.01,
    "SOLUSD": 0.001, "ADAUSD": 0.00001, "BNBUSD": 0.001,
    "US30": 1.0,
}

DEFAULT_PIP = 0.0001
EXIT_TOLERANCE_PIPS = 3  # pips tolerance for SL/TP match


def read_mt5_html(file_path: str) -> str:
    """Read UTF-16LE encoded MT5 HTML report and return clean HTML string."""
    raw = Path(file_path).read_bytes()
    # Strip BOM if present
    if raw[:2] == b"\xff\xfe":
        raw = raw[2:]
    return raw.decode("utf-16-le", errors="replace")


def _parse_volume(vol_str: str) -> float:
    """Parse volume string, handling '1K' notation."""
    vol_str = vol_str.strip()
    m = re.match(r"^([\d.]+)\s*K$", vol_str, re.IGNORECASE)
    if m:
        return float(m.group(1)) * 1000
    return float(vol_str)


def _parse_float(s: str) -> float:
    """Parse a float string, handling spaces in numbers like '51 829.58'."""
    s = s.strip().replace("\xa0", "").replace(" ", "")
    if not s or s == "——":
        return 0.0
    return float(s)


def _parse_datetime(s: str) -> datetime:
    """Parse MT5 datetime format: '2026.03.30 06:25:03'."""
    s = s.strip()
    return datetime.strptime(s, "%Y.%m.%d %H:%M:%S")


def _get_pip_size(symbol: str) -> float:
    return PIP_SIZES.get(symbol, DEFAULT_PIP)


def _classify_exit(symbol: str, trade_type: str, close_price: float,
                   sl: float, tp: float) -> str:
    """Classify exit type by comparing close_price to SL/TP levels."""
    pip = _get_pip_size(symbol)
    tol = EXIT_TOLERANCE_PIPS * pip

    if tp > 0 and abs(close_price - tp) <= tol:
        return "tp"
    if sl > 0 and abs(close_price - sl) <= tol:
        return "sl"
    return "signal"


def parse_positions(soup: BeautifulSoup) -> pd.DataFrame:
    """Parse the Positions (closed trades) section from the HTML report."""
    trades = []

    # Find the Positions header
    all_rows = soup.find_all("tr")
    in_positions = False
    positions_header_found = False

    for row in all_rows:
        # Detect section headers
        th = row.find("th")
        if th:
            text = th.get_text(strip=True)
            if text == "Positions":
                in_positions = True
                positions_header_found = True
                continue
            elif text in ("Orders", "Deals", "Open Positions", "Results") and positions_header_found:
                in_positions = False
                continue

        if not in_positions:
            continue

        # Skip header rows (bgcolor=#E5F0FC) and spacer rows
        bgcolor = row.get("bgcolor", "")
        if bgcolor not in ("#FFFFFF", "#F7F7F7"):
            continue

        cells = row.find_all("td")
        if len(cells) < 10:
            continue

        # Extract cell texts
        cell_texts = []
        signal_id = ""
        for cell in cells:
            cls = cell.get("class", [])
            if "hidden" in cls:
                signal_id = cell.get_text(strip=True)
                continue
            cell_texts.append(cell.get_text(strip=True))

        # Positions row: OpenTime, PositionID, Symbol, Type, Volume, EntryPrice, SL, TP, CloseTime, ClosePrice, Commission, Swap, Profit
        if len(cell_texts) < 13:
            continue

        try:
            open_time = _parse_datetime(cell_texts[0])
            position_id = cell_texts[1]
            symbol = cell_texts[2]
            trade_type = cell_texts[3]  # buy/sell
            volume = _parse_volume(cell_texts[4])
            entry_price = _parse_float(cell_texts[5])
            sl = _parse_float(cell_texts[6])
            tp = _parse_float(cell_texts[7])
            close_time = _parse_datetime(cell_texts[8])
            close_price = _parse_float(cell_texts[9])
            commission = _parse_float(cell_texts[10])
            swap = _parse_float(cell_texts[11])
            profit = _parse_float(cell_texts[12])
        except (ValueError, IndexError):
            continue

        # Classify exit type
        exit_type = _classify_exit(symbol, trade_type, close_price, sl, tp)

        # Compute RRR
        pip = _get_pip_size(symbol)
        if trade_type == "buy":
            sl_dist = entry_price - sl if sl > 0 else 0
            tp_dist = tp - entry_price if tp > 0 else 0
            actual_dist = close_price - entry_price
        else:  # sell
            sl_dist = sl - entry_price if sl > 0 else 0
            tp_dist = entry_price - tp if tp > 0 else 0
            actual_dist = entry_price - close_price

        rrr_intended = tp_dist / sl_dist if sl_dist > 0 else 0.0
        rrr_actual = actual_dist / sl_dist if sl_dist > 0 else 0.0

        # Pips gained/lost
        pips = actual_dist / pip if pip > 0 else 0.0

        duration_minutes = (close_time - open_time).total_seconds() / 60

        net_pnl = profit + commission + swap

        trades.append({
            "open_time": open_time,
            "close_time": close_time,
            "position_id": position_id,
            "symbol": symbol,
            "trade_type": trade_type,
            "signal_id": signal_id,
            "volume": volume,
            "entry_price": entry_price,
            "sl": sl,
            "tp": tp,
            "close_price": close_price,
            "commission": commission,
            "swap": swap,
            "profit": profit,
            "net_pnl": net_pnl,
            "exit_type": exit_type,
            "is_win": profit > 0,
            "rrr_intended": round(rrr_intended, 2),
            "rrr_actual": round(rrr_actual, 2),
            "pips": round(pips, 1),
            "duration_minutes": round(duration_minutes, 1),
        })

    return pd.DataFrame(trades)


def parse_open_positions(soup: BeautifulSoup) -> pd.DataFrame:
    """Parse the Open Positions section."""
    positions = []
    all_rows = soup.find_all("tr")
    in_open = False

    for row in all_rows:
        th = row.find("th")
        if th:
            text = th.get_text(strip=True)
            if text == "Open Positions":
                in_open = True
                continue
            elif in_open and text in ("Results",):
                in_open = False
                continue

        if not in_open:
            continue

        bgcolor = row.get("bgcolor", "")
        if bgcolor not in ("#FFFFFF", "#F7F7F7"):
            continue

        cells = row.find_all("td")
        cell_texts = []
        comment = ""
        for cell in cells:
            colspan = cell.get("colspan", "1")
            if colspan == "3":
                comment = cell.get_text(strip=True)
                continue
            cell_texts.append(cell.get_text(strip=True))

        if len(cell_texts) < 11:
            continue

        try:
            positions.append({
                "open_time": _parse_datetime(cell_texts[0]),
                "position_id": cell_texts[1],
                "symbol": cell_texts[2],
                "trade_type": cell_texts[3],
                "volume": _parse_volume(cell_texts[4]),
                "entry_price": _parse_float(cell_texts[5]),
                "sl": _parse_float(cell_texts[6]),
                "tp": _parse_float(cell_texts[7]),
                "market_price": _parse_float(cell_texts[8]),
                "swap": _parse_float(cell_texts[9]),
                "unrealized_pnl": _parse_float(cell_texts[10]),
                "comment": comment,
            })
        except (ValueError, IndexError):
            continue

    return pd.DataFrame(positions)


def parse_summary(soup: BeautifulSoup) -> dict:
    """Parse the Results summary section."""
    summary = {}
    all_rows = soup.find_all("tr")
    in_results = False

    for row in all_rows:
        cells = row.find_all("td")
        for i, cell in enumerate(cells):
            text = cell.get_text(strip=True)
            if ":" in text and i + 1 < len(cells):
                key = text.rstrip(":")
                val = cells[i + 1].get_text(strip=True)
                summary[key] = val

    return summary


def filter_trades(df: pd.DataFrame, start_date: str = "2026-03-30",
                  end_date: str = "2026-04-02",
                  exclude_positions: list = None) -> pd.DataFrame:
    """Filter trades by date range and excluded position IDs."""
    if df.empty:
        return df

    mask = (df["open_time"] >= start_date) & (df["open_time"] < end_date)
    filtered = df[mask].copy()

    if exclude_positions:
        filtered = filtered[~filtered["position_id"].isin(exclude_positions)]

    return filtered.reset_index(drop=True)


def compute_symbol_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Compute per-symbol statistics."""
    if df.empty:
        return pd.DataFrame()

    stats = []
    for symbol, group in df.groupby("symbol"):
        wins = group[group["is_win"]]
        losses = group[~group["is_win"]]
        gross_profit = wins["profit"].sum()
        gross_loss = abs(losses["profit"].sum())

        # Max consecutive wins/losses
        max_con_wins = _max_consecutive(group["is_win"].values, True)
        max_con_losses = _max_consecutive(group["is_win"].values, False)

        # Max drawdown (peak-to-trough of cumulative P&L)
        cum_pnl = group.sort_values("close_time")["net_pnl"].cumsum()
        peak = cum_pnl.cummax()
        drawdown = (cum_pnl - peak).min()

        stats.append({
            "symbol": symbol,
            "trades": len(group),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(group) * 100, 1) if len(group) > 0 else 0,
            "total_profit": round(group["net_pnl"].sum(), 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf"),
            "avg_win": round(wins["profit"].mean(), 2) if len(wins) > 0 else 0,
            "avg_loss": round(losses["profit"].mean(), 2) if len(losses) > 0 else 0,
            "avg_rrr_intended": round(group["rrr_intended"].mean(), 2),
            "avg_rrr_actual": round(group["rrr_actual"].mean(), 2),
            "max_consecutive_wins": max_con_wins,
            "max_consecutive_losses": max_con_losses,
            "max_drawdown": round(drawdown, 2),
            "tp_hits": len(group[group["exit_type"] == "tp"]),
            "sl_hits": len(group[group["exit_type"] == "sl"]),
            "signal_closes": len(group[group["exit_type"] == "signal"]),
            "tp_hit_rate": round(
                len(group[group["exit_type"] == "tp"]) / len(group) * 100, 1
            ) if len(group) > 0 else 0,
            "avg_duration_min": round(group["duration_minutes"].mean(), 1),
        })

    return pd.DataFrame(stats).sort_values("total_profit", ascending=False).reset_index(drop=True)


def compute_equity_curve(df: pd.DataFrame) -> pd.DataFrame:
    """Compute equity curve from trades sorted by close time."""
    if df.empty:
        return pd.DataFrame()

    sorted_df = df.sort_values("close_time").reset_index(drop=True)
    sorted_df["cumulative_pnl"] = sorted_df["net_pnl"].cumsum()
    sorted_df["peak"] = sorted_df["cumulative_pnl"].cummax()
    sorted_df["drawdown"] = sorted_df["cumulative_pnl"] - sorted_df["peak"]

    return sorted_df[["close_time", "symbol", "trade_type", "net_pnl",
                       "cumulative_pnl", "peak", "drawdown", "exit_type"]].copy()


def compute_overall_stats(df: pd.DataFrame) -> dict:
    """Compute overall portfolio statistics."""
    if df.empty:
        return {}

    wins = df[df["is_win"]]
    losses = df[~df["is_win"]]
    gross_profit = wins["profit"].sum()
    gross_loss = abs(losses["profit"].sum())

    cum_pnl = df.sort_values("close_time")["net_pnl"].cumsum()
    peak = cum_pnl.cummax()
    max_dd = (cum_pnl - peak).min()

    return {
        "total_trades": len(df),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(df) * 100, 1) if len(df) > 0 else 0,
        "net_pnl": round(df["net_pnl"].sum(), 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0,
        "avg_win": round(wins["profit"].mean(), 2) if len(wins) > 0 else 0,
        "avg_loss": round(losses["profit"].mean(), 2) if len(losses) > 0 else 0,
        "max_consecutive_wins": _max_consecutive(df.sort_values("close_time")["is_win"].values, True),
        "max_consecutive_losses": _max_consecutive(df.sort_values("close_time")["is_win"].values, False),
        "max_drawdown": round(max_dd, 2),
        "avg_rrr_intended": round(df["rrr_intended"].mean(), 2),
        "tp_hits": len(df[df["exit_type"] == "tp"]),
        "sl_hits": len(df[df["exit_type"] == "sl"]),
        "signal_closes": len(df[df["exit_type"] == "signal"]),
    }


def _max_consecutive(arr, value) -> int:
    """Count max consecutive occurrences of value in array."""
    max_count = 0
    count = 0
    for v in arr:
        if v == value:
            count += 1
            max_count = max(max_count, count)
        else:
            count = 0
    return max_count


def load_and_parse(file_path: str) -> tuple:
    """
    Main entry point: load HTML, parse all sections.
    Returns (positions_df, open_positions_df, summary_dict)
    """
    html = read_mt5_html(file_path)
    soup = BeautifulSoup(html, "html.parser")

    positions = parse_positions(soup)
    open_pos = parse_open_positions(soup)
    summary = parse_summary(soup)

    return positions, open_pos, summary
