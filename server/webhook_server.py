import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv(override=True)

from app_logging import SignalLoggerAdapter, configure_logging
from config import AppConfig
from database.db import increment_dedupe, init_db, log_execution, log_signal, mark_signal_status
from executor.mt5_executor import BaseExecutor, build_executor
from server.signal_schema import normalize_signal


def _json_response(status_code: int, **payload: Any):
    return jsonify(payload), status_code


# In-memory store for latest C levels per symbol+direction.
# Key: "SYMBOL_bullish" or "SYMBOL_bearish"
# Value: {"c_level": float, "active": bool, "updated_at": str}
_latest_c_levels: Dict[str, Dict[str, Any]] = {}


def create_app(config: Optional[AppConfig] = None, executor: Optional[BaseExecutor] = None) -> Flask:
    cfg = config or AppConfig.from_env()
    configure_logging(cfg.log_level)
    logger = logging.getLogger("webhook")
    init_db(cfg)

    app = Flask(__name__)
    app.config["APP_CONFIG"] = cfg
    app.config["EXECUTOR"] = executor or build_executor(cfg)

    @app.route("/health", methods=["GET"])
    def health():
        return _json_response(
            200,
            status="ok",
            executor_mode=cfg.executor_mode,
            db_path=cfg.db_path,
        )

    @app.route("/webhook", methods=["POST"])
    def webhook():
        auth_token = request.headers.get("X-Webhook-Token", "") or request.args.get("token", "")
        if auth_token != cfg.webhook_token:
            return _json_response(401, status="rejected", reason="unauthorized")

        payload: Dict[str, Any] = request.get_json(silent=True) or {}

        # Handle pattern state updates (C-level freshness tracking)
        if str(payload.get("action", "")).strip().lower() == "pattern_update":
            meta = payload.get("meta") or {}
            symbol = str(payload.get("symbol", "")).strip().upper()
            pattern = meta.get("pattern", "")
            c_level = float(meta.get("c_level", 0))
            active = bool(meta.get("active", False))
            key = f"{symbol}_{pattern}"
            _latest_c_levels[key] = {
                "c_level": c_level,
                "active": active,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            logger.info(
                "Pattern update: %s %s c_level=%.5f active=%s",
                symbol, pattern, c_level, active,
            )
            return _json_response(200, status="pattern_updated", symbol=symbol, pattern=pattern)

        try:
            signal, signal_id = normalize_signal(payload, cfg)
        except ValueError as exc:
            return _json_response(400, status="rejected", reason=str(exc))

        slogger = SignalLoggerAdapter(logger, {"signal_id": signal_id})

        inserted = log_signal(cfg, signal, payload, status="accepted")
        if not inserted:
            increment_dedupe(cfg, signal_id)
            slogger.info("Duplicate signal ignored.")
            return _json_response(
                200,
                status="duplicate",
                signal_id=signal_id,
                reason="already_processed",
            )

        slogger.info("Signal accepted for processing.")

        # Validate C-level freshness: reject if pattern's C level has changed
        signal_meta = signal.get("meta") or {}
        signal_c_level = signal_meta.get("c_level")
        if signal_c_level is not None:
            direction = "bullish" if signal["action"] == "buy" else "bearish"
            c_key = f"{signal['symbol']}_{direction}"
            latest = _latest_c_levels.get(c_key)
            if latest is not None:
                if not latest["active"]:
                    mark_signal_status(
                        cfg, signal_id, "rejected",
                        rejection_reason="Pattern no longer active",
                    )
                    slogger.info(
                        "Rejected: %s pattern no longer active for %s",
                        direction, signal["symbol"],
                    )
                    return _json_response(
                        200, status="rejected", signal_id=signal_id,
                        reason="pattern_no_longer_active",
                    )
                if abs(latest["c_level"] - float(signal_c_level)) > 1e-8:
                    reason = (
                        f"C-level changed: signal={signal_c_level}, "
                        f"latest={latest['c_level']}"
                    )
                    mark_signal_status(
                        cfg, signal_id, "rejected",
                        rejection_reason=reason,
                    )
                    slogger.info(
                        "Rejected: C-level changed from %.5f to %.5f for %s",
                        float(signal_c_level), latest["c_level"],
                        signal["symbol"],
                    )
                    return _json_response(
                        200, status="rejected", signal_id=signal_id,
                        reason="c_level_changed",
                    )

        try:
            result = app.config["EXECUTOR"].execute_trade(signal)
        except Exception as exc:  # defensive boundary for external executor failures
            slogger.exception("Executor raised an unhandled exception.")
            result = {
                "status": "error",
                "broker_order_id": None,
                "requested_price": None,
                "filled_price": None,
                "error_code": "EXECUTOR_EXCEPTION",
                "error_message": str(exc),
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "latency_ms": None,
                "raw_response": None,
            }
        log_execution(cfg, signal_id, result)

        execution_status = result.get("status", "error")
        if execution_status == "filled":
            mark_signal_status(cfg, signal_id, "executed")
        elif execution_status == "rejected":
            mark_signal_status(
                cfg,
                signal_id,
                "rejected",
                rejection_reason=result.get("error_message"),
            )
        else:
            mark_signal_status(
                cfg,
                signal_id,
                "error",
                rejection_reason=result.get("error_message"),
            )

        slogger.info("Execution completed with status=%s.", execution_status)
        return _json_response(
            200,
            status="accepted",
            signal_id=signal_id,
            execution=result,
        )

    return app


if __name__ == "__main__":
    cfg = AppConfig.from_env()
    cfg.validate(require_real_executor=cfg.executor_mode == "real")
    app = create_app(cfg)
    app.run(host=cfg.server_host, port=cfg.server_port)
