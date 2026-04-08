import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

from config import AppConfig


VALID_ACTIONS = {
    "buy": "buy",
    "long": "buy",
    "sell": "sell",
    "short": "sell",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_timestamp(raw_value: Any) -> str:
    if raw_value is None:
        return _utc_now()
    if not isinstance(raw_value, str):
        raise ValueError("timestamp must be an ISO-8601 string.")
    parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _derive_signal_id(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def normalize_signal(payload: Dict[str, Any], config: AppConfig) -> Tuple[Dict[str, Any], str]:
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a JSON object.")

    indicator_id = str(payload.get("indicator_id", "")).strip()
    symbol = str(payload.get("symbol", "")).strip().upper()
    action_raw = str(payload.get("action", "")).strip().lower()
    strategy_id = payload.get("strategy_id")
    risk = payload.get("risk")
    timestamp = payload.get("timestamp")
    signal_id = payload.get("signal_id")

    if not indicator_id:
        raise ValueError("indicator_id is required.")
    if not symbol:
        raise ValueError("symbol is required.")
    if action_raw not in VALID_ACTIONS:
        raise ValueError("action must be one of: buy, sell, long, short.")
    if risk is not None:
        try:
            risk = float(risk)
        except (TypeError, ValueError) as exc:
            raise ValueError("risk must be numeric when provided.") from exc

    normalized_action = VALID_ACTIONS[action_raw]
    normalized_timestamp = _normalize_timestamp(timestamp)
    normalized_symbol = config.normalize_symbol(symbol)
    signal_id = str(signal_id).strip() if signal_id else _derive_signal_id(payload)

    signal = {
        "schema_version": "v1",
        "signal_id": signal_id,
        "indicator_id": indicator_id,
        "strategy_id": str(strategy_id).strip() if strategy_id else None,
        "symbol": symbol,
        "normalized_symbol": normalized_symbol,
        "action": normalized_action,
        "risk": risk if risk is not None else config.default_risk,
        "timestamp": normalized_timestamp,
        "received_at": _utc_now(),
        "volume_units": config.default_volume_units,
        "meta": payload.get("meta"),
    }
    return signal, signal_id

