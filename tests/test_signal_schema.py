from config import AppConfig
from server.signal_schema import normalize_signal


def test_normalize_signal_generates_id_and_defaults():
    cfg = AppConfig.from_env()
    payload = {"indicator_id": "EMA_RSI_V1", "symbol": "eurusd", "action": "long"}
    signal, signal_id = normalize_signal(payload, cfg)

    assert signal["indicator_id"] == "EMA_RSI_V1"
    assert signal["normalized_symbol"] == "EURUSD"
    assert signal["action"] == "buy"
    assert signal["signal_id"] == signal_id
    assert len(signal_id) == 24
    assert signal["risk"] == cfg.default_risk


def test_normalize_signal_rejects_bad_action():
    cfg = AppConfig.from_env()
    payload = {"indicator_id": "X", "symbol": "EURUSD", "action": "hold"}

    try:
        normalize_signal(payload, cfg)
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "action must be one of" in str(exc)

