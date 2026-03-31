"""
Heartbeat Monitor — sends dummy trade signals every 15 minutes to verify
the webhook server + MT5 connection are alive.

Signals use intentionally stale TP values so they are safely rejected
(STALE_TP) without placing any real trades.

Usage:
    python heartbeat_monitor.py
"""

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

WEBHOOK_URL = f"http://localhost:{os.getenv('SERVER_PORT', '5000')}/webhook"
WEBHOOK_TOKEN = os.getenv("WEBHOOK_SHARED_TOKEN", "")
INTERVAL_SECONDS = 15 * 60  # 15 minutes
TIMEOUT_SECONDS = 15
LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "heartbeat_log.txt"

# Dummy signals with stale TP — guaranteed to be rejected safely
HEARTBEAT_SIGNALS = [
    {
        "symbol": "BTCUSD",
        "action": "BUY",
        "meta": {"tp": 1.00, "sl": 0.50, "c_level": 0.80, "rr": "1:1"},
    },
    {
        "symbol": "EURUSD",
        "action": "BUY",
        "meta": {"tp": 0.50, "sl": 0.40, "c_level": 0.45, "rr": "1:1"},
    },
]


def _now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _log_to_file(message: str):
    LOG_DIR.mkdir(exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{_now_str()} | {message}\n")


def send_heartbeat(signal_template: dict) -> str:
    """Send a single heartbeat signal. Returns status string."""
    # Unique signal_id per attempt to avoid deduplication
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    signal_id = f"heartbeat_{signal_template['symbol']}_{ts}"

    payload = {
        "indicator_id": "heartbeat_monitor",
        "strategy_id": "heartbeat",
        "symbol": signal_template["symbol"],
        "action": signal_template["action"],
        "risk": 1.0,
        "signal_id": signal_id,
        "meta": signal_template["meta"],
    }

    try:
        resp = requests.post(
            WEBHOOK_URL,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Token": WEBHOOK_TOKEN,
            },
            timeout=TIMEOUT_SECONDS,
        )
        data = resp.json()
    except requests.ConnectionError:
        return "DOWN"
    except requests.Timeout:
        return "TIMEOUT"
    except Exception as e:
        return f"ERROR:{e}"

    # STALE_TP means server received signal, validated with MT5, and correctly
    # rejected it — the full pipeline is working.
    execution = data.get("execution") or {}
    error_code = execution.get("error_code", "")

    if error_code == "STALE_TP":
        return "HEALTHY"
    elif resp.status_code == 200:
        return f"WARNING:unexpected_response({execution.get('status', 'unknown')}:{error_code})"
    else:
        return f"WARNING:http_{resp.status_code}"


def run_cycle():
    """Run one heartbeat cycle (check all symbols)."""
    print(f"\n{'='*60}")
    print(f"  HEARTBEAT CHECK  |  {_now_str()}")
    print(f"{'='*60}")

    for sig in HEARTBEAT_SIGNALS:
        symbol = sig["symbol"]
        status = send_heartbeat(sig)

        if status == "HEALTHY":
            print(f"  [OK]   {symbol:10s} | Server + MT5 connected")
        elif status == "DOWN":
            msg = f"{symbol} | Server DOWN — connection refused"
            print(f"  [FAIL] {msg}")
            _log_to_file(f"DOWN | {msg}")
        elif status == "TIMEOUT":
            msg = f"{symbol} | Server TIMEOUT — no response in {TIMEOUT_SECONDS}s"
            print(f"  [FAIL] {msg}")
            _log_to_file(f"TIMEOUT | {msg}")
        else:
            msg = f"{symbol} | {status}"
            print(f"  [WARN] {msg}")
            _log_to_file(f"WARNING | {msg}")

    print(f"{'='*60}")
    print(f"  Next check in {INTERVAL_SECONDS // 60} minutes...")


if __name__ == "__main__":
    print("Heartbeat Monitor started.")
    print(f"Target: {WEBHOOK_URL}")
    print(f"Interval: {INTERVAL_SECONDS // 60} minutes")
    print(f"Symbols: {', '.join(s['symbol'] for s in HEARTBEAT_SIGNALS)}")
    print(f"Log file: {LOG_FILE.resolve()}")

    # Run first check immediately
    run_cycle()

    while True:
        time.sleep(INTERVAL_SECONDS)
        run_cycle()
