from pathlib import Path

from config import AppConfig
from server.webhook_server import create_app


class StubExecutor:
    def execute_trade(self, signal):
        return {
            "status": "filled",
            "broker_order_id": "stub-order-1",
            "requested_price": None,
            "filled_price": None,
            "error_code": None,
            "error_message": None,
            "executed_at": "2026-03-11T00:00:00+00:00",
            "latency_ms": 5,
            "raw_response": {"ok": True},
        }


def _cfg(tmp_path: Path) -> AppConfig:
    return AppConfig(
        db_path=str(tmp_path / "trades.db"),
        webhook_token="test-token",
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


def test_webhook_accepts_and_dedupes(tmp_path):
    app = create_app(_cfg(tmp_path), executor=StubExecutor())
    client = app.test_client()
    payload = {
        "indicator_id": "EMA_RSI_V1",
        "symbol": "EURUSD",
        "action": "buy",
        "signal_id": "fixed-id-1",
    }
    headers = {"X-Webhook-Token": "test-token"}

    first = client.post("/webhook", json=payload, headers=headers)
    assert first.status_code == 200
    assert first.json["status"] == "accepted"

    second = client.post("/webhook", json=payload, headers=headers)
    assert second.status_code == 200
    assert second.json["status"] == "duplicate"


def test_webhook_rejects_unauthorized(tmp_path):
    app = create_app(_cfg(tmp_path), executor=StubExecutor())
    client = app.test_client()
    resp = client.post("/webhook", json={"indicator_id": "X", "symbol": "EURUSD", "action": "buy"})
    assert resp.status_code == 401

