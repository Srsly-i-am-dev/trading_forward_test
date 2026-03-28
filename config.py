import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


def _to_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc


def _to_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a float.") from exc


def _to_json_dict(name: str) -> Dict[str, str]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} must be a JSON object.")
    return {str(k).upper(): str(v) for k, v in parsed.items()}


@dataclass(frozen=True)
class AppConfig:
    db_path: str
    webhook_token: str
    server_host: str
    server_port: int
    log_level: str
    default_volume_units: int
    default_risk: float
    risk_per_trade: float
    executor_mode: str
    mt5_login: int
    mt5_password: str
    mt5_server: str
    mt5_terminal_path: str
    mt5_magic: int
    mt5_deviation: int
    mt5_symbol_map: Dict[str, str]
    request_timeout_seconds: int
    max_execution_retries: int

    @staticmethod
    def from_env() -> "AppConfig":
        return AppConfig(
            db_path=os.getenv("DB_PATH", "logs/trades.db"),
            webhook_token=os.getenv("WEBHOOK_SHARED_TOKEN", "change-me"),
            server_host=os.getenv("SERVER_HOST", "0.0.0.0"),
            server_port=_to_int("SERVER_PORT", 5000),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            default_volume_units=_to_int("DEFAULT_VOLUME_UNITS", 1000),
            default_risk=_to_float("DEFAULT_RISK", 1.0),
            risk_per_trade=_to_float("RISK_PER_TRADE", 100.0),
            executor_mode=os.getenv("EXECUTOR_MODE", "real").lower(),
            mt5_login=_to_int("MT5_LOGIN", 0),
            mt5_password=os.getenv("MT5_PASSWORD", ""),
            mt5_server=os.getenv("MT5_SERVER", ""),
            mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH", ""),
            mt5_magic=_to_int("MT5_MAGIC", 234000),
            mt5_deviation=_to_int("MT5_DEVIATION", 20),
            mt5_symbol_map=_to_json_dict("MT5_SYMBOL_MAP"),
            request_timeout_seconds=_to_int("REQUEST_TIMEOUT_SECONDS", 10),
            max_execution_retries=_to_int("MAX_EXECUTION_RETRIES", 2),
        )

    def db_path_obj(self) -> Path:
        return Path(self.db_path)

    def normalize_symbol(self, symbol: str) -> str:
        upper = symbol.upper().strip()
        return self.mt5_symbol_map.get(upper, upper)

    def validate(self, require_real_executor: Optional[bool] = None) -> None:
        mode = self.executor_mode
        if mode not in {"real", "simulated"}:
            raise ValueError("EXECUTOR_MODE must be either 'real' or 'simulated'.")

        if not self.webhook_token or self.webhook_token == "change-me":
            raise ValueError("WEBHOOK_SHARED_TOKEN must be set to a non-default value.")

        require_real = require_real_executor if require_real_executor is not None else mode == "real"
        if not require_real:
            return

        required = {
            "MT5_LOGIN": self.mt5_login,
            "MT5_PASSWORD": self.mt5_password,
            "MT5_SERVER": self.mt5_server,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise ValueError(
                "Missing required MT5 settings for real executor: "
                + ", ".join(missing)
            )

