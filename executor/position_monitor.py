"""
Position Monitor — manages open positions with exit backup strategies
and monitors for rejected trades with auto-retry.

Runs as a background loop alongside the live webhook server.
Every 30 seconds:
  1. Checks open positions and applies exit backup strategies
  2. Scans the live DB for rejected/error executions and retries fixable ones
  3. Logs unrecoverable rejections to logs/rejected/ as text files

Usage:
    python executor/position_monitor.py
"""
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from dotenv import load_dotenv
load_dotenv(os.path.join(_project_root, ".env.live"), override=True)

import MetaTrader5 as mt5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("position_monitor")

# ── CONFIGURATION ────────────────────────────────────────────

MAGIC_NUMBER = int(os.getenv("MT5_MAGIC", "234001"))
CHECK_INTERVAL = 30  # seconds between checks
DB_PATH = os.path.join(_project_root, os.getenv("DB_PATH", "logs/trades_live.db"))
REJECTED_DIR = os.path.join(_project_root, "logs", "rejected")
MAX_RETRY_VOLUME = 5.0  # Hard cap on retry volume to prevent catastrophic orders

# Ensure rejected log directory exists
os.makedirs(REJECTED_DIR, exist_ok=True)

# Exit strategies per symbol (MT5 symbol names)
EXIT_RULES = {
    "EURUSD.sc": {
        "partial_tp": {
            "enabled": True,
            "close_pct": 0.5,       # close 50% of position
            "tp_pct": 0.75,         # when price reaches 75% of TP distance
        },
    },
    "EURJPY.sc": {
        # 1:1 RR handled by TP/SL, no extra exit needed
    },
    "GBPAUD.sc": {
        "time_exit": {
            "enabled": True,
            "max_minutes": 120,
        },
    },
    "ADAUSD": {
        "trailing_stop": {
            "enabled": True,
            "activation_pips": 10,
            "trail_pips": 10,
        },
    },
    "USDCHF.sc": {
        "time_exit": {
            "enabled": True,
            "max_minutes": 120,
        },
    },
    "GBPUSD.sc": {
        "time_exit": {
            "enabled": True,
            "max_minutes": 30,
        },
    },
    "ETHUSD": {
        "trailing_stop": {
            "enabled": True,
            "activation_pips": 10,
            "trail_pips": 10,
        },
    },
}

# Pip sizes for each MT5 symbol
# FIX: ETHUSD pip = 1.0 (price ~$1800-3500, so 10 pips = $10 movement, not $0.10)
PIP_SIZES = {
    "EURUSD.sc": 0.0001,
    "EURJPY.sc": 0.01,
    "GBPAUD.sc": 0.0001,
    "ADAUSD": 0.0001,
    "USDCHF.sc": 0.0001,
    "GBPUSD.sc": 0.0001,
    "ETHUSD": 1.0,
}

# MT5 error codes that are potentially fixable with a retry
RETRYABLE_ERRORS = {
    "MT5_10004",   # Requote
    "MT5_10006",   # Reject — transient
    "MT5_10007",   # Cancel by dealer
    "MT5_10014",   # Invalid volume
    "MT5_10015",   # Invalid price
    "MT5_10016",   # Invalid stops
}

# Track trailing stop levels and partial TP state per position
_position_state = {}

# Track which rejected signals we've already processed
_processed_rejections = {}  # signal_id -> timestamp (for TTL cleanup)

# ── HELPERS ──────────────────────────────────────────────────


def get_pip_size(symbol: str) -> float:
    return PIP_SIZES.get(symbol, 0.0001)


def _safe_tick(symbol: str):
    """Get tick data with symbol_select + retry. Returns None if unavailable."""
    mt5.symbol_select(symbol, True)
    tick = mt5.symbol_info_tick(symbol)
    if tick is not None and (tick.bid > 0 or tick.ask > 0):
        return tick
    # Retry after 0.5s
    time.sleep(0.5)
    mt5.symbol_select(symbol, True)
    tick = mt5.symbol_info_tick(symbol)
    if tick is not None and (tick.bid > 0 or tick.ask > 0):
        return tick
    logger.warning("No valid tick for %s after retry (bid=%.5f, ask=%.5f)",
                   symbol, tick.bid if tick else 0, tick.ask if tick else 0)
    return None


def _check_mt5_connected():
    """Check if MT5 is still connected, attempt reconnect if not."""
    acct = mt5.account_info()
    if acct is not None:
        return True
    logger.warning("MT5 connection lost, attempting reconnect...")
    mt5.shutdown()
    if not mt5.initialize():
        logger.error("MT5 reinitialize failed: %s", mt5.last_error())
        return False
    login = int(os.getenv("MT5_LOGIN", "0"))
    password = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "")
    if not mt5.login(login, password=password, server=server):
        logger.error("MT5 relogin failed: %s", mt5.last_error())
        return False
    logger.info("MT5 reconnected successfully.")
    return True


# ── POSITION MANAGEMENT ─────────────────────────────────────


def get_open_positions():
    """Get all open positions for our magic number."""
    positions = mt5.positions_get()
    if positions is None:
        return None  # Return None to distinguish from "no positions" (empty list)
    return [p for p in positions if p.magic == MAGIC_NUMBER]


def close_position(position, volume=None, comment="monitor_exit"):
    """Close a position (full or partial)."""
    symbol = position.symbol
    ticket = position.ticket
    close_volume = volume if volume else position.volume

    # Round volume to broker step
    mt5.symbol_select(symbol, True)
    info = mt5.symbol_info(symbol)
    if info and info.volume_step > 0:
        close_volume = round(int(close_volume / info.volume_step) * info.volume_step, 6)
        close_volume = max(close_volume, info.volume_min)
        # FIX: Cap at actual position volume to prevent over-closing
        close_volume = min(close_volume, position.volume)

    tick = _safe_tick(symbol)
    if tick is None:
        logger.error("No tick for %s, cannot close position %d", symbol, ticket)
        return False

    if position.type == mt5.ORDER_TYPE_BUY:
        close_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
    else:
        close_type = mt5.ORDER_TYPE_BUY
        price = tick.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": close_volume,
        "type": close_type,
        "position": ticket,
        "price": price,
        "deviation": 20,
        "magic": MAGIC_NUMBER,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _get_filling_mode(symbol),
    }

    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info("Closed %s position %d: vol=%.2f @ %.5f (%s)",
                     symbol, ticket, close_volume, price, comment)
        return True
    else:
        error = result.comment if result else mt5.last_error()
        logger.error("Failed to close position %d: %s", ticket, error)
        return False


def modify_sl(position, new_sl):
    """Modify the SL of an open position."""
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": position.symbol,
        "position": position.ticket,
        "sl": new_sl,
        "tp": position.tp,
        "magic": MAGIC_NUMBER,
    }
    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        return True
    else:
        error = result.comment if result else mt5.last_error()
        logger.error("Failed to modify SL for %d: %s", position.ticket, error)
        return False


def _get_filling_mode(symbol: str) -> int:
    info = mt5.symbol_info(symbol)
    if info is None:
        return mt5.ORDER_FILLING_IOC
    filling = info.filling_mode
    if filling & 1:
        return mt5.ORDER_FILLING_FOK
    if filling & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


# ── EXIT STRATEGIES ──────────────────────────────────────────


def check_time_exit(position, rules):
    """Close position if it's been open longer than max_minutes.
    Returns True if position was closed (or attempted)."""
    config = rules.get("time_exit", {})
    if not config.get("enabled"):
        return False

    max_minutes = config["max_minutes"]
    open_time = datetime.fromtimestamp(position.time, tz=timezone.utc)
    now = datetime.now(timezone.utc)
    elapsed = (now - open_time).total_seconds() / 60

    if elapsed >= max_minutes:
        logger.info("TIME EXIT: %s position %d open for %.0f min (max %d min)",
                     position.symbol, position.ticket, elapsed, max_minutes)
        close_position(position, comment=f"time_exit_{max_minutes}min")
        return True  # Signal that we attempted to close — skip other exit checks
    return False


def check_trailing_stop(position, rules):
    """Activate and update trailing stop."""
    config = rules.get("trailing_stop", {})
    if not config.get("enabled"):
        return

    ticket = position.ticket
    symbol = position.symbol
    pip = get_pip_size(symbol)
    activation_dist = config["activation_pips"] * pip
    trail_dist = config["trail_pips"] * pip

    # FIX: Use _safe_tick with symbol_select + retry + bid>0 check
    tick = _safe_tick(symbol)
    if tick is None:
        return

    entry = position.price_open
    is_buy = position.type == mt5.ORDER_TYPE_BUY
    current_price = tick.bid if is_buy else tick.ask

    # FIX: Double-check price is valid (not 0)
    if current_price <= 0:
        logger.warning("Invalid price %.5f for %s, skipping trail check", current_price, symbol)
        return

    if is_buy:
        profit_dist = current_price - entry
    else:
        profit_dist = entry - current_price

    if ticket not in _position_state:
        _position_state[ticket] = {
            "trail_active": False,
            "best_price": current_price,
        }

    state = _position_state[ticket]

    # FIX: Always update best_price to at least current_price on first valid read
    # This handles monitor restarts where best_price was initialized low
    if is_buy:
        if current_price > state["best_price"]:
            state["best_price"] = current_price
    else:
        if current_price < state["best_price"] or state["best_price"] <= 0:
            state["best_price"] = current_price

    if not state["trail_active"]:
        if profit_dist >= activation_dist:
            state["trail_active"] = True
            # FIX: Set best_price to current on activation to ensure accurate tracking
            state["best_price"] = current_price
            logger.info("TRAIL ACTIVATED: %s position %d at +%.1f pips",
                         symbol, ticket, profit_dist / pip)
        else:
            return

    if is_buy:
        new_sl = round(state["best_price"] - trail_dist, 6)
        if position.sl > 0 and new_sl <= position.sl:
            return
        if current_price <= new_sl:
            logger.info("TRAIL HIT: %s position %d, closing at %.5f",
                         symbol, ticket, current_price)
            close_position(position, comment="trailing_stop")
            return
    else:
        new_sl = round(state["best_price"] + trail_dist, 6)
        if position.sl > 0 and new_sl >= position.sl:
            return
        if current_price >= new_sl:
            logger.info("TRAIL HIT: %s position %d, closing at %.5f",
                         symbol, ticket, current_price)
            close_position(position, comment="trailing_stop")
            return

    if modify_sl(position, new_sl):
        logger.info("TRAIL SL: %s position %d SL moved to %.5f (best=%.5f)",
                     symbol, ticket, new_sl, state["best_price"])


def check_partial_tp(position, rules):
    """Close partial position when price reaches X% of TP distance."""
    config = rules.get("partial_tp", {})
    if not config.get("enabled"):
        return

    ticket = position.ticket
    symbol = position.symbol

    if ticket not in _position_state:
        _position_state[ticket] = {}
    state = _position_state[ticket]

    if state.get("partial_taken"):
        return

    entry = position.price_open
    tp = position.tp
    if tp == 0:
        return

    is_buy = position.type == mt5.ORDER_TYPE_BUY
    close_pct = config["close_pct"]
    tp_pct = config["tp_pct"]

    # FIX: Use _safe_tick with symbol_select
    tick = _safe_tick(symbol)
    if tick is None:
        return

    current_price = tick.bid if is_buy else tick.ask
    if current_price <= 0:
        return

    if is_buy:
        tp_dist = tp - entry
        partial_level = entry + tp_dist * tp_pct
        reached = current_price >= partial_level
    else:
        tp_dist = entry - tp
        partial_level = entry - tp_dist * tp_pct
        reached = current_price <= partial_level

    if reached:
        partial_volume = round(position.volume * close_pct, 2)
        # FIX: Ensure partial volume doesn't exceed position volume
        info = mt5.symbol_info(symbol)
        if info and info.volume_step > 0:
            partial_volume = round(int(partial_volume / info.volume_step) * info.volume_step, 6)
            partial_volume = max(partial_volume, info.volume_min)
            partial_volume = min(partial_volume, position.volume)

        # FIX: If partial_volume equals full volume (position too small to split), skip
        if partial_volume >= position.volume:
            logger.info("PARTIAL TP: %s position %d too small to split (vol=%.2f, min=%.2f), skipping",
                        symbol, ticket, position.volume, info.volume_min if info else 0)
            state["partial_taken"] = True  # Don't try again
            return

        pip = get_pip_size(symbol)
        logger.info(
            "PARTIAL TP: %s position %d reached %.1f%% of TP (%.5f), "
            "closing %.0f%% (%.2f lots)",
            symbol, ticket, tp_pct * 100, partial_level,
            close_pct * 100, partial_volume,
        )
        # FIX: Mark partial_taken BEFORE close attempt to prevent double-fire
        state["partial_taken"] = True
        if not close_position(position, volume=partial_volume,
                              comment=f"partial_{int(close_pct*100)}pct"):
            # Close failed — reset flag so we try again next cycle
            # But log a warning so we know
            logger.warning("Partial close failed for %d, will retry next cycle", ticket)
            state["partial_taken"] = False


def cleanup_state(open_tickets):
    """Remove state for positions that are no longer open."""
    closed = [t for t in _position_state if t not in open_tickets]
    for t in closed:
        del _position_state[t]


def _cleanup_processed_rejections():
    """Remove processed rejections older than 24 hours to prevent memory leak."""
    now = time.time()
    expired = [sid for sid, ts in _processed_rejections.items() if now - ts > 86400]
    for sid in expired:
        del _processed_rejections[sid]


# ── REJECTION MONITORING ─────────────────────────────────────


def get_symbol_map():
    """Load symbol map from env."""
    raw = os.getenv("MT5_SYMBOL_MAP", "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def scan_rejected_signals():
    """Scan DB for rejected/error executions that haven't been processed yet."""
    if not os.path.exists(DB_PATH):
        return []

    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT e.signal_id, e.status, e.error_code, e.error_message,
                   e.executed_at, e.raw_response,
                   s.action, s.symbol, s.normalized_symbol, s.meta,
                   s.raw_payload, s.received_at
            FROM executions e
            JOIN signals s ON e.signal_id = s.signal_id
            WHERE e.status IN ('rejected', 'error')
            AND e.executed_at > datetime('now', '-24 hours')
            ORDER BY e.executed_at DESC
        """)
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.debug("DB scan error: %s", exc)
        return []


def retry_rejected_trade(rejection):
    """Try to re-execute a rejected trade if the error is fixable."""
    signal_id = rejection["signal_id"]
    error_code = rejection.get("error_code", "")
    symbol_orig = rejection.get("symbol", "")
    action = rejection.get("action", "").upper()

    if error_code not in RETRYABLE_ERRORS:
        return False, f"Error {error_code} is not retryable"

    # Get the MT5 symbol
    symbol_map = get_symbol_map()
    mt5_symbol = symbol_map.get(symbol_orig, symbol_orig)

    # Parse meta for TP/SL
    try:
        meta = json.loads(rejection.get("meta", "{}") or "{}")
    except (json.JSONDecodeError, TypeError):
        meta = {}

    tp = float(meta.get("tp", 0))
    sl = float(meta.get("sl", 0))

    if not mt5_symbol or not action:
        return False, "Missing symbol or action"

    # FIX: Check if a position already exists for this symbol+direction (prevent dupes)
    existing = mt5.positions_get(symbol=mt5_symbol)
    if existing:
        for pos in existing:
            if pos.magic == MAGIC_NUMBER:
                is_same_dir = (
                    (action == "BUY" and pos.type == mt5.ORDER_TYPE_BUY) or
                    (action == "SELL" and pos.type == mt5.ORDER_TYPE_SELL)
                )
                if is_same_dir:
                    return False, f"Position already exists for {mt5_symbol} {action} (ticket={pos.ticket})"

    # Ensure symbol is selected and has data
    mt5.symbol_select(mt5_symbol, True)
    time.sleep(0.3)
    info = mt5.symbol_info(mt5_symbol)
    if info is None:
        return False, f"Symbol {mt5_symbol} not found in MT5"

    if not info.visible:
        mt5.symbol_select(mt5_symbol, True)
        time.sleep(0.5)
        info = mt5.symbol_info(mt5_symbol)
        if info is None or not info.visible:
            return False, f"Symbol {mt5_symbol} cannot be made visible"

    if info.trade_mode == 0:
        return False, f"Trading disabled for {mt5_symbol}"

    # Get fresh tick
    tick = _safe_tick(mt5_symbol)
    if tick is None:
        return False, f"No tick data for {mt5_symbol}"

    price = tick.ask if action == "BUY" else tick.bid

    # Validate TP/SL still makes sense with current price
    if action == "BUY":
        if tp > 0 and tp <= price:
            return False, f"TP {tp:.5f} already below current price {price:.5f}"
        if sl > 0 and sl >= price:
            return False, f"SL {sl:.5f} above current price {price:.5f}"
    else:
        if tp > 0 and tp >= price:
            return False, f"TP {tp:.5f} already above current price {price:.5f}"
        if sl > 0 and sl <= price:
            return False, f"SL {sl:.5f} below current price {price:.5f}"

    # Calculate volume using risk
    risk = float(os.getenv("RISK_PER_TRADE", "10"))
    if sl > 0:
        sl_dist = abs(price - sl)
        tick_size = info.trade_tick_size
        tick_value = info.trade_tick_value
        if tick_size > 0 and tick_value > 0:
            ticks_in_sl = sl_dist / tick_size
            loss_per_lot = ticks_in_sl * tick_value
            if loss_per_lot > 0:
                volume = risk / loss_per_lot
                volume = max(
                    round(int(volume / info.volume_step) * info.volume_step, 6),
                    info.volume_min,
                )
                # FIX: Hard cap on volume to prevent catastrophic orders
                volume = min(volume, info.volume_max, MAX_RETRY_VOLUME)
            else:
                return False, "Cannot calculate lot size (loss_per_lot=0)"
        else:
            return False, f"Invalid tick info for {mt5_symbol}"
    else:
        return False, "No SL in signal, cannot size position"

    order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": mt5_symbol,
        "volume": volume,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": MAGIC_NUMBER,
        "comment": f"retry_{signal_id[:8]}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _get_filling_mode(mt5_symbol),
    }

    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info("RETRY SUCCESS: %s %s %s vol=%.2f @ %.5f (was: %s)",
                     signal_id[:8], action, mt5_symbol, volume, price, error_code)
        return True, f"Filled: order={result.order}, vol={volume}, price={price}"
    else:
        err = result.comment if result else str(mt5.last_error())
        retcode = result.retcode if result else "N/A"
        return False, f"Retry failed: retcode={retcode}, {err}"


def write_rejection_file(rejection, retry_result=None):
    """Write a text file for a rejected order."""
    signal_id = rejection["signal_id"]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    symbol = rejection.get("symbol", "UNKNOWN")
    filename = f"{timestamp}_{symbol}_{signal_id[:8]}.txt"
    filepath = os.path.join(REJECTED_DIR, filename)

    try:
        meta = json.loads(rejection.get("meta", "{}") or "{}")
    except (json.JSONDecodeError, TypeError):
        meta = {}

    try:
        raw_payload = json.loads(rejection.get("raw_payload", "{}") or "{}")
    except (json.JSONDecodeError, TypeError):
        raw_payload = {}

    lines = [
        f"REJECTED ORDER REPORT",
        f"=====================",
        f"",
        f"Signal ID:    {signal_id}",
        f"Symbol:       {symbol}",
        f"MT5 Symbol:   {rejection.get('normalized_symbol', 'N/A')}",
        f"Action:       {rejection.get('action', 'N/A')}",
        f"Received:     {rejection.get('received_at', 'N/A')}",
        f"Executed:     {rejection.get('executed_at', 'N/A')}",
        f"",
        f"ERROR DETAILS",
        f"-------------",
        f"Status:       {rejection.get('status', 'N/A')}",
        f"Error Code:   {rejection.get('error_code', 'N/A')}",
        f"Error Msg:    {rejection.get('error_message', 'N/A')}",
        f"",
        f"SIGNAL META",
        f"-----------",
        f"TP:           {meta.get('tp', 'N/A')}",
        f"SL:           {meta.get('sl', 'N/A')}",
        f"C Level:      {meta.get('c_level', 'N/A')}",
        f"RR:           {meta.get('rr', 'N/A')}",
        f"Exit Strat:   {meta.get('exit_strategy', 'N/A')}",
        f"TP Mode:      {meta.get('tp_mode', 'N/A')}",
        f"",
    ]

    if retry_result:
        success, detail = retry_result
        lines += [
            f"RETRY ATTEMPT",
            f"-------------",
            f"Success:      {'YES' if success else 'NO'}",
            f"Detail:       {detail}",
            f"",
        ]

    lines += [
        f"RAW PAYLOAD",
        f"-----------",
        json.dumps(raw_payload, indent=2),
    ]

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info("Rejection logged to: %s", filename)
    return filepath


def process_rejections():
    """Scan for rejected trades, retry fixable ones, log all to files."""
    rejections = scan_rejected_signals()

    for rej in rejections:
        signal_id = rej["signal_id"]

        # Skip already processed
        if signal_id in _processed_rejections:
            continue

        error_code = rej.get("error_code", "")
        symbol = rej.get("symbol", "?")
        action = rej.get("action", "?")
        error_msg = rej.get("error_message", "?")

        logger.warning("REJECTED: %s %s %s -- %s: %s",
                        signal_id[:8], action, symbol, error_code, error_msg)

        retry_result = None

        # Try retry if error is fixable
        if error_code in RETRYABLE_ERRORS:
            logger.info("Attempting retry for %s %s %s...", signal_id[:8], action, symbol)
            success, detail = retry_rejected_trade(rej)
            retry_result = (success, detail)
            if success:
                logger.info("RETRY OK: %s -- %s", signal_id[:8], detail)
            else:
                logger.warning("RETRY FAILED: %s -- %s", signal_id[:8], detail)
        else:
            logger.info("Not retryable (error=%s), logging to file.", error_code)

        # Write rejection file
        write_rejection_file(rej, retry_result)

        # FIX: Store with timestamp for TTL-based cleanup
        _processed_rejections[signal_id] = time.time()


# ── MAIN LOOP ────────────────────────────────────────────────


def run_monitor():
    """Main monitoring loop."""
    if not mt5.initialize():
        logger.error("MT5 initialize failed: %s", mt5.last_error())
        sys.exit(1)

    login = int(os.getenv("MT5_LOGIN", "0"))
    password = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "")

    if not mt5.login(login, password=password, server=server):
        logger.error("MT5 login failed: %s", mt5.last_error())
        mt5.shutdown()
        sys.exit(1)

    info = mt5.account_info()
    logger.info("Position monitor started: account=%d, server=%s, balance=%.2f",
                 info.login, info.server, info.balance)
    logger.info("Monitoring symbols: %s", ", ".join(sorted(EXIT_RULES.keys())))
    logger.info("Check interval: %d seconds", CHECK_INTERVAL)

    cycle_count = 0

    try:
        while True:
            cycle_count += 1

            # FIX: Check MT5 connection every cycle
            positions = get_open_positions()
            if positions is None:
                # MT5 returned None — connection may be lost
                logger.warning("positions_get() returned None — checking MT5 connection")
                if not _check_mt5_connected():
                    logger.error("MT5 reconnect failed, waiting %ds...", CHECK_INTERVAL)
                    time.sleep(CHECK_INTERVAL)
                    continue
                positions = get_open_positions()
                if positions is None:
                    positions = []

            open_tickets = set()

            # ── EXIT BACKUP STRATEGIES ──
            for pos in positions:
                symbol = pos.symbol
                open_tickets.add(pos.ticket)

                rules = EXIT_RULES.get(symbol, {})
                if not rules:
                    continue

                # FIX: If time_exit closes the position, skip other checks for this position
                if check_time_exit(pos, rules):
                    continue
                check_trailing_stop(pos, rules)
                check_partial_tp(pos, rules)

            cleanup_state(open_tickets)

            # ── LOG POSITION STATUS ──
            if positions:
                for pos in positions:
                    pip = get_pip_size(pos.symbol)
                    if pip > 0:
                        pips_pnl = ((pos.price_current - pos.price_open) / pip
                                    if pos.type == mt5.ORDER_TYPE_BUY
                                    else (pos.price_open - pos.price_current) / pip)
                    else:
                        pips_pnl = 0
                    logger.info(
                        "  [POS] %s %s #%d | entry=%.5f | now=%.5f | pips=%+.1f | $%+.2f | SL=%.5f | TP=%.5f",
                        pos.symbol,
                        "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL",
                        pos.ticket,
                        pos.price_open,
                        pos.price_current,
                        pips_pnl,
                        pos.profit,
                        pos.sl,
                        pos.tp,
                    )
            else:
                if cycle_count % 10 == 1:
                    logger.info("  [POS] No open positions (magic=%d)", MAGIC_NUMBER)

            # ── REJECTION MONITORING ──
            process_rejections()

            # ── CLEANUP ──
            if cycle_count % 60 == 0:  # Every 30 minutes
                _cleanup_processed_rejections()

            # ── HEARTBEAT ──
            if cycle_count % 20 == 0:  # Every 10 minutes
                acct = mt5.account_info()
                if acct:
                    logger.info("HEARTBEAT: cycle=%d | balance=%.2f | equity=%.2f | positions=%d",
                                 cycle_count, acct.balance, acct.equity, len(positions))
                else:
                    logger.warning("HEARTBEAT: MT5 connection lost!")

            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        logger.info("Monitor stopped by user.")
    finally:
        mt5.shutdown()
        logger.info("MT5 connection closed.")


if __name__ == "__main__":
    print(f"\n  Position Monitor — Exit Backup + Rejection Handler")
    print(f"  ===================================================")
    print(f"  EURUSD.sc  : Partial TP (50% at 75% of TP)")
    print(f"  GBPAUD.sc  : Time exit at 120 min")
    print(f"  USDCHF.sc  : Time exit at 120 min")
    print(f"  GBPUSD.sc  : Time exit at 30 min")
    print(f"  ADAUSD     : Trailing stop (10pip activate, 10pip trail)")
    print(f"  ETHUSD     : Trailing stop (10pip activate, 10pip trail)")
    print(f"  EURJPY.sc  : No backup (1:1 RR via TP/SL)")
    print(f"  Check every {CHECK_INTERVAL}s | Magic: {MAGIC_NUMBER}")
    print(f"  Rejection logs: {REJECTED_DIR}")
    print(f"  DB: {DB_PATH}\n")
    run_monitor()
