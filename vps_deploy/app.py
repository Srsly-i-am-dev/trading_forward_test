"""
Live Trading Webhook Server — Standalone VPS Deployment

Receives TradingView webhook signals and executes dual-position trades on MT5.
Runs on port 80 (configurable via .env.live SERVER_PORT).

Usage:
    python -X utf8 app.py
    OR double-click start.bat
"""
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

from dotenv import load_dotenv

# Load .env.live from the same directory as this script
_app_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_app_dir, ".env.live"), override=True)

from app_logging import SignalLoggerAdapter, configure_logging
from config import AppConfig
from database.db import increment_dedupe, init_db, log_execution, log_signal, mark_signal_status
from executor.mt5_executor import BaseExecutor, build_executor
from server.signal_schema import normalize_signal

from flask import Flask, jsonify, request


def _json_response(status_code: int, **payload: Any):
    return jsonify(payload), status_code


_latest_c_levels: Dict[str, Dict[str, Any]] = {}
_c_levels_lock = threading.Lock()


def _load_allowed_symbols() -> Optional[Set[str]]:
    raw = os.getenv("ALLOWED_SYMBOLS", "")
    if not raw.strip():
        return None
    return {s.strip().upper() for s in raw.split(",") if s.strip()}


def create_app(config: Optional[AppConfig] = None,
               executor: Optional[BaseExecutor] = None) -> Flask:
    cfg = config or AppConfig.from_env()
    configure_logging(cfg.log_level)
    logger = logging.getLogger("webhook_live")
    init_db(cfg)

    allowed_symbols = _load_allowed_symbols()
    min_rr_ratio = float(os.getenv("MIN_RR_RATIO", "0.75"))
    if allowed_symbols:
        logger.info("LIVE server -- allowed symbols: %s", ", ".join(sorted(allowed_symbols)))
    logger.info("LIVE server -- minimum RR ratio: %.2f:1", min_rr_ratio)

    app = Flask(__name__)
    app.config["APP_CONFIG"] = cfg
    app.config["EXECUTOR"] = executor or build_executor(cfg)

    @app.route("/health", methods=["GET"])
    def health():
        return _json_response(
            200,
            status="ok",
            mode="LIVE",
            executor_mode=cfg.executor_mode,
            account=cfg.mt5_login,
            allowed_symbols=sorted(allowed_symbols) if allowed_symbols else "all",
        )

    @app.route("/webhook", methods=["POST"])
    def webhook():
        auth_token = (request.headers.get("X-Webhook-Token", "")
                      or request.args.get("token", ""))
        if auth_token != cfg.webhook_token:
            return _json_response(401, status="rejected", reason="unauthorized")

        payload: Dict[str, Any] = request.get_json(silent=True) or {}

        # Handle pattern state updates
        if str(payload.get("action", "")).strip().lower() == "pattern_update":
            meta = payload.get("meta") or {}
            symbol = str(payload.get("symbol", "")).strip().upper()
            pattern = meta.get("pattern", "")
            try:
                c_level = float(meta.get("c_level", 0))
            except (ValueError, TypeError):
                logger.warning("Invalid c_level in pattern_update: %s", meta.get("c_level"))
                return _json_response(400, status="rejected", reason="invalid_c_level")
            active = bool(meta.get("active", False))
            key = f"{symbol}_{pattern}"
            with _c_levels_lock:
                _latest_c_levels[key] = {
                    "c_level": c_level,
                    "active": active,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            logger.info("Pattern update: %s %s c=%.5f active=%s",
                         symbol, pattern, c_level, active)
            return _json_response(200, status="pattern_updated",
                                   symbol=symbol, pattern=pattern)

        try:
            signal, signal_id = normalize_signal(payload, cfg)
        except ValueError as exc:
            return _json_response(400, status="rejected", reason=str(exc))

        slogger = SignalLoggerAdapter(logger, {"signal_id": signal_id})

        # Symbol filter
        sym = signal.get("symbol", "").upper()
        if allowed_symbols and sym not in allowed_symbols:
            slogger.info("Rejected: %s not in allowed symbols", sym)
            return _json_response(
                200, status="rejected", signal_id=signal_id,
                reason=f"symbol_{sym}_not_allowed_on_live",
            )

        # Minimum RR filter
        signal_meta = signal.get("meta") or {}
        rr_str = str(signal_meta.get("rr", "")).strip()
        if rr_str and min_rr_ratio > 0:
            try:
                rr_value = float(rr_str.split(":")[0])
                if rr_value < min_rr_ratio:
                    slogger.info("Rejected: RR %.2f:1 below minimum %.2f:1", rr_value, min_rr_ratio)
                    return _json_response(
                        200, status="rejected", signal_id=signal_id,
                        reason=f"rr_{rr_value:.2f}_below_min_{min_rr_ratio:.2f}",
                    )
            except (ValueError, IndexError):
                slogger.warning("Could not parse RR value: %s", rr_str)

        inserted = log_signal(cfg, signal, payload, status="accepted")
        if not inserted:
            increment_dedupe(cfg, signal_id)
            slogger.info("Duplicate signal ignored.")
            return _json_response(
                200, status="duplicate", signal_id=signal_id,
                reason="already_processed",
            )

        slogger.info("LIVE signal accepted: %s %s", signal["action"], sym)

        # C-level freshness validation
        signal_meta = signal.get("meta") or {}
        signal_c_level = signal_meta.get("c_level")
        if signal_c_level is not None:
            try:
                signal_c_level = float(signal_c_level)
            except (ValueError, TypeError):
                slogger.warning("Invalid c_level in signal: %s", signal_c_level)
                signal_c_level = None
        if signal_c_level is not None:
            direction = "bullish" if signal["action"] == "buy" else "bearish"
            c_key = f"{signal['symbol']}_{direction}"
            with _c_levels_lock:
                latest = _latest_c_levels.get(c_key)
            if latest is not None:
                if not latest["active"]:
                    mark_signal_status(cfg, signal_id, "rejected",
                                       rejection_reason="Pattern no longer active")
                    slogger.info("Rejected: %s pattern inactive for %s",
                                 direction, signal["symbol"])
                    return _json_response(
                        200, status="rejected", signal_id=signal_id,
                        reason="pattern_no_longer_active",
                    )
                if abs(latest["c_level"] - signal_c_level) > 1e-8:
                    reason = (f"C-level changed: signal={signal_c_level}, "
                              f"latest={latest['c_level']}")
                    mark_signal_status(cfg, signal_id, "rejected",
                                       rejection_reason=reason)
                    slogger.info("Rejected: C-level stale for %s", signal["symbol"])
                    return _json_response(
                        200, status="rejected", signal_id=signal_id,
                        reason="c_level_changed",
                    )

        try:
            result = app.config["EXECUTOR"].execute_trade(signal)
        except Exception as exc:
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
        try:
            log_execution(cfg, signal_id, result)
        except Exception:
            slogger.exception("Failed to log execution to DB (trade still executed)")

        execution_status = result.get("status", "error")
        if execution_status == "filled":
            mark_signal_status(cfg, signal_id, "executed")
        elif execution_status == "rejected":
            mark_signal_status(cfg, signal_id, "rejected",
                               rejection_reason=result.get("error_message"))
        else:
            mark_signal_status(cfg, signal_id, "error",
                               rejection_reason=result.get("error_message"))

        slogger.info("LIVE execution: status=%s", execution_status)
        return _json_response(
            200, status="accepted", signal_id=signal_id, execution=result,
        )

    return app


if __name__ == "__main__":
    cfg = AppConfig.from_env()
    cfg.validate(require_real_executor=cfg.executor_mode == "real")
    app = create_app(cfg)

    # Print startup info
    try:
        import requests as req
        public_ip = req.get("https://api.ipify.org", timeout=5).text.strip()
    except Exception:
        public_ip = "<VPS_IP>"

    port = cfg.server_port
    token = cfg.webhook_token
    if port == 80:
        webhook_url = f"http://{public_ip}/webhook?token={token}"
    else:
        webhook_url = f"http://{public_ip}:{port}/webhook?token={token}"

    print(f"\n{'='*60}")
    print(f"  LIVE Webhook Server")
    print(f"  Port:    {port}")
    print(f"  Account: {cfg.mt5_login} @ {cfg.mt5_server}")
    print(f"  Symbols: {os.getenv('ALLOWED_SYMBOLS', 'all')}")
    print(f"  Risk:    ${cfg.risk_per_trade}")
    print(f"{'='*60}")
    print(f"  TradingView Webhook URL:")
    print(f"  {webhook_url}")
    print(f"{'='*60}\n")

    app.run(host=cfg.server_host, port=port)
