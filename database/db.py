import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from config import AppConfig


def _conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(config: AppConfig) -> None:
    db_path = Path(config.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with _conn(config.db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS signals (
                signal_id TEXT PRIMARY KEY,
                indicator_id TEXT NOT NULL,
                strategy_id TEXT,
                symbol TEXT NOT NULL,
                normalized_symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                risk REAL,
                payload_json TEXT NOT NULL,
                source_timestamp TEXT,
                received_at TEXT NOT NULL,
                status TEXT NOT NULL,
                rejection_reason TEXT,
                dedupe_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL,
                broker_order_id TEXT,
                requested_price REAL,
                filled_price REAL,
                error_code TEXT,
                error_message TEXT,
                executed_at TEXT NOT NULL,
                latency_ms INTEGER,
                raw_response TEXT,
                FOREIGN KEY(signal_id) REFERENCES signals(signal_id),
                UNIQUE(signal_id, attempt)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                entry_price REAL,
                opened_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                FOREIGN KEY(signal_id) REFERENCES signals(signal_id)
            )
            """
        )

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_indicator ON signals(indicator_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(normalized_symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_received_at ON signals(received_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_executions_status ON executions(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_executions_signal ON executions(signal_id)")
        conn.commit()


def log_signal(config: AppConfig, signal: Dict[str, Any], raw_payload: Dict[str, Any], status: str) -> bool:
    with _conn(config.db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO signals (
                signal_id, indicator_id, strategy_id, symbol, normalized_symbol, action, risk,
                payload_json, source_timestamp, received_at, status, rejection_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal["signal_id"],
                signal["indicator_id"],
                signal.get("strategy_id"),
                signal["symbol"],
                signal["normalized_symbol"],
                signal["action"],
                signal.get("risk"),
                json.dumps(raw_payload, separators=(",", ":"), sort_keys=True),
                signal.get("timestamp"),
                signal["received_at"],
                status,
                signal.get("rejection_reason"),
            ),
        )
        conn.commit()
        return cursor.rowcount == 1


def increment_dedupe(config: AppConfig, signal_id: str) -> None:
    with _conn(config.db_path) as conn:
        conn.execute(
            "UPDATE signals SET dedupe_count = dedupe_count + 1 WHERE signal_id = ?",
            (signal_id,),
        )
        conn.commit()


def mark_signal_status(
    config: AppConfig, signal_id: str, status: str, rejection_reason: Optional[str] = None
) -> None:
    with _conn(config.db_path) as conn:
        conn.execute(
            "UPDATE signals SET status = ?, rejection_reason = ? WHERE signal_id = ?",
            (status, rejection_reason, signal_id),
        )
        conn.commit()


def log_execution(config: AppConfig, signal_id: str, result: Dict[str, Any], attempt: int = 1) -> None:
    with _conn(config.db_path) as conn:
        conn.execute(
            """
            INSERT INTO executions(
                signal_id, attempt, status, broker_order_id, requested_price, filled_price,
                error_code, error_message, executed_at, latency_ms, raw_response
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id,
                attempt,
                result.get("status", "error"),
                result.get("broker_order_id"),
                result.get("requested_price"),
                result.get("filled_price"),
                result.get("error_code"),
                result.get("error_message"),
                result.get("executed_at"),
                result.get("latency_ms"),
                json.dumps(result.get("raw_response"), separators=(",", ":"), sort_keys=True)
                if result.get("raw_response") is not None
                else None,
            ),
        )
        conn.commit()


def signal_exists(config: AppConfig, signal_id: str) -> bool:
    with _conn(config.db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM signals WHERE signal_id = ? LIMIT 1",
            (signal_id,),
        ).fetchone()
        return row is not None

