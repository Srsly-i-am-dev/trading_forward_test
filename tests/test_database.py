from pathlib import Path

from config import AppConfig
from database.db import init_db, log_execution, log_signal, mark_signal_status, signal_exists


def _cfg(tmp_path: Path) -> AppConfig:
    return AppConfig(
        db_path=str(tmp_path / "trades.db"),
        webhook_token="token",
        server_host="127.0.0.1",
        server_port=5000,
        log_level="INFO",
        default_volume_units=1000,
        default_risk=1.0,
        executor_mode="simulated",
        ctrader_client_id="x",
        ctrader_client_secret="y",
        ctrader_access_token="",
        ctrader_refresh_token="",
        ctrader_token_url="https://example.com/token",
        ctrader_order_url="https://example.com/order",
        ctrader_account_id="acc",
        ctrader_environment="demo",
        ctrader_symbol_map={},
        request_timeout_seconds=5,
        max_execution_retries=1,
    )


def test_signal_and_execution_roundtrip(tmp_path):
    cfg = _cfg(tmp_path)
    init_db(cfg)
    signal = {
        "signal_id": "abc123",
        "indicator_id": "EMA",
        "strategy_id": "EMA_EURUSD",
        "symbol": "EURUSD",
        "normalized_symbol": "EURUSD",
        "action": "buy",
        "risk": 1.0,
        "timestamp": "2026-03-11T00:00:00+00:00",
        "received_at": "2026-03-11T00:00:01+00:00",
        "volume_units": 1000,
    }
    inserted = log_signal(cfg, signal, {"k": "v"}, status="accepted")
    assert inserted
    assert signal_exists(cfg, "abc123")

    log_execution(
        cfg,
        "abc123",
        {
            "status": "filled",
            "broker_order_id": "ord1",
            "executed_at": "2026-03-11T00:00:02+00:00",
            "latency_ms": 12,
            "raw_response": {"ok": True},
        },
    )
    mark_signal_status(cfg, "abc123", "executed")

