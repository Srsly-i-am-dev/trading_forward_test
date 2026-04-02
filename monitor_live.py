"""
Continuous monitor for live trading alerts.
Polls the live DB every 15 seconds for new signals/executions.
Also checks MT5 open positions periodically.

Usage: python -X utf8 monitor_live.py
"""
import sqlite3
import time
import os
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "trades_live.db")
ERROR_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "monitor_errors.txt")
POLL_INTERVAL = 15  # seconds
MT5_CHECK_INTERVAL = 60  # check MT5 every 60 seconds

def get_latest_signal_count():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM signals")
    count = c.fetchone()[0]
    conn.close()
    return count

def get_recent_signals(since_rowid):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT rowid, * FROM signals WHERE rowid > ? ORDER BY rowid", (since_rowid,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_recent_executions(since_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM executions WHERE id > ? ORDER BY id", (since_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_max_execution_id():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COALESCE(MAX(id), 0) FROM executions")
    val = c.fetchone()[0]
    conn.close()
    return val

def get_max_signal_rowid():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COALESCE(MAX(rowid), 0) FROM signals")
    val = c.fetchone()[0]
    conn.close()
    return val

def check_mt5_positions():
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            return None, "MT5 initialize failed"
        if not mt5.login(24017565, server="PUPrime-Live2"):
            mt5.shutdown()
            return None, f"MT5 login failed: {mt5.last_error()}"

        info = mt5.account_info()
        positions = mt5.positions_get()
        mt5.shutdown()

        if info is None:
            return None, "account_info returned None"

        return {
            "balance": info.balance,
            "equity": info.equity,
            "profit": info.profit,
            "positions": positions if positions else [],
        }, None
    except Exception as e:
        return None, str(e)

def log_error(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}\n"
    print(f"  \u26a0 ERROR: {msg}")
    with open(ERROR_LOG, "a", encoding="utf-8") as f:
        f.write(line)

def main():
    print("=" * 70)
    print("  LIVE TRADING MONITOR")
    print(f"  DB: {DB_PATH}")
    print(f"  Error log: {ERROR_LOG}")
    print(f"  Poll interval: {POLL_INTERVAL}s | MT5 check: {MT5_CHECK_INTERVAL}s")
    print("=" * 70)

    # Get current counts to only show NEW signals
    last_signal_rowid = get_max_signal_rowid()
    last_exec_id = get_max_execution_id()
    last_mt5_check = 0

    signal_count = get_latest_signal_count()
    print(f"  Existing signals in DB: {signal_count}")
    print(f"  Starting rowid: {last_signal_rowid}, exec_id: {last_exec_id}")
    print(f"  Monitoring started at {datetime.now().strftime('%H:%M:%S')}...")
    print("-" * 70)

    errors_found = []

    while True:
        try:
            now = time.time()

            # Check for new signals
            new_signals = get_recent_signals(last_signal_rowid)
            for s in new_signals:
                rowid = s["rowid"]
                last_signal_rowid = max(last_signal_rowid, rowid)

                ts = datetime.now().strftime("%H:%M:%S")
                symbol = s["symbol"]
                action = s["action"]
                status = s["status"]
                reason = s["rejection_reason"] or ""

                if status == "executed":
                    print(f"\n  [{ts}] \u2705 TRADE EXECUTED: {action.upper()} {symbol}")
                elif status == "rejected":
                    print(f"\n  [{ts}] \u274c REJECTED: {action.upper()} {symbol} - {reason}")
                    log_error(f"Signal rejected: {action.upper()} {symbol} - {reason} (signal_id={s['signal_id'][:12]})")
                    errors_found.append(f"{action.upper()} {symbol}: {reason}")
                elif status == "error":
                    print(f"\n  [{ts}] \u26a0 ERROR: {action.upper()} {symbol} - {reason}")
                    log_error(f"Signal error: {action.upper()} {symbol} - {reason} (signal_id={s['signal_id'][:12]})")
                    errors_found.append(f"{action.upper()} {symbol}: {reason}")
                elif status == "accepted":
                    print(f"\n  [{ts}] \u23f3 ACCEPTED: {action.upper()} {symbol} (awaiting execution)")
                else:
                    print(f"\n  [{ts}] \u2753 {status.upper()}: {action.upper()} {symbol}")

            # Check for new executions
            new_execs = get_recent_executions(last_exec_id)
            for e in new_execs:
                last_exec_id = max(last_exec_id, e["id"])

                ts = datetime.now().strftime("%H:%M:%S")
                status = e["status"]
                sig_id = e["signal_id"][:12]

                if status == "filled":
                    price = e["filled_price"] or e["requested_price"]
                    order_id = e["broker_order_id"]
                    latency = e["latency_ms"]
                    print(f"         Filled @ {price} | order={order_id} | latency={latency}ms")
                elif status == "rejected":
                    code = e["error_code"]
                    msg = e["error_message"]
                    print(f"         Rejected: [{code}] {msg}")
                elif status == "error":
                    code = e["error_code"]
                    msg = e["error_message"]
                    print(f"         Error: [{code}] {msg}")
                    log_error(f"Execution error: [{code}] {msg} (signal={sig_id})")

            # Periodic MT5 check
            if now - last_mt5_check > MT5_CHECK_INTERVAL:
                last_mt5_check = now
                mt5_data, mt5_err = check_mt5_positions()
                if mt5_err:
                    log_error(f"MT5 check failed: {mt5_err}")
                elif mt5_data:
                    pos_count = len(mt5_data["positions"])
                    ts = datetime.now().strftime("%H:%M:%S")
                    if pos_count > 0:
                        print(f"\n  [{ts}] MT5: {pos_count} open position(s) | equity=${mt5_data['equity']:.2f} | P&L=${mt5_data['profit']:.2f}")
                        for p in mt5_data["positions"]:
                            side = "BUY" if p.type == 0 else "SELL"
                            print(f"         {p.symbol} {side} {p.volume:.2f} @ {p.price_open:.5f} -> {p.price_current:.5f} = ${p.profit:.2f}")
                    # Silent if no positions (don't spam)

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("\n\n" + "=" * 70)
            print("  MONITOR STOPPED")
            if errors_found:
                print(f"\n  Errors found during session ({len(errors_found)}):")
                for e in errors_found:
                    print(f"    - {e}")
            else:
                print("  No errors found during session.")
            print("=" * 70)
            break
        except Exception as ex:
            log_error(f"Monitor exception: {ex}")
            time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
