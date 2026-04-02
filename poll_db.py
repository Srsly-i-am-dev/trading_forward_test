"""Simple DB poller - outputs new signals/executions as JSON lines."""
import sqlite3, json, sys, time, os

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "trades_live.db")

last_sig = int(sys.argv[1]) if len(sys.argv) > 1 else 0
last_exec = int(sys.argv[2]) if len(sys.argv) > 2 else 0

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

c = conn.cursor()
c.execute("SELECT rowid, * FROM signals WHERE rowid > ? ORDER BY rowid", (last_sig,))
signals = c.fetchall()

c.execute("SELECT * FROM executions WHERE id > ? ORDER BY id", (last_exec,))
execs = c.fetchall()

if signals:
    for s in signals:
        print(json.dumps({"type": "signal", "rowid": s["rowid"], "signal_id": s["signal_id"][:12],
              "symbol": s["symbol"], "action": s["action"], "status": s["status"],
              "rejection_reason": s["rejection_reason"], "received_at": s["received_at"]}))

if execs:
    for e in execs:
        print(json.dumps({"type": "execution", "id": e["id"], "signal_id": e["signal_id"][:12],
              "status": e["status"], "filled_price": e["filled_price"],
              "requested_price": e["requested_price"], "error_code": e["error_code"],
              "error_message": e["error_message"], "latency_ms": e["latency_ms"]}))

if not signals and not execs:
    print("NO_NEW")

conn.close()
