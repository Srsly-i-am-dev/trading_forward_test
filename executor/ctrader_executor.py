import json
import logging
import ssl
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import websocket

from config import AppConfig

logger = logging.getLogger("ctrader_executor")

# ── Payload type IDs (cTrader Open API) ─────────────────────
PT_APP_AUTH_REQ = 2100
PT_APP_AUTH_RES = 2101
PT_ACCOUNT_AUTH_REQ = 2102
PT_ACCOUNT_AUTH_RES = 2103
PT_NEW_ORDER_REQ = 2106
PT_SYMBOLS_LIST_REQ = 2114
PT_SYMBOLS_LIST_RES = 2115
PT_EXECUTION_EVENT = 2126
PT_ORDER_ERROR_EVENT = 2132
PT_GET_ACCOUNTS_REQ = 2149
PT_GET_ACCOUNTS_RES = 2150
PT_HEARTBEAT = 51


class BaseExecutor:
    def execute_trade(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError


class SimulatedExecutor(BaseExecutor):
    def execute_trade(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        meta = signal.get("meta") or {}
        raw = {
            "mode": "simulated",
            "action": signal.get("action"),
            "symbol": signal.get("normalized_symbol") or signal.get("symbol"),
        }
        if meta.get("tp") is not None:
            raw["tp"] = float(meta["tp"])
        if meta.get("sl") is not None:
            raw["sl"] = float(meta["sl"])
        return {
            "status": "filled",
            "broker_order_id": f"sim-{signal['signal_id'][:12]}",
            "requested_price": None,
            "filled_price": None,
            "error_code": None,
            "error_message": None,
            "executed_at": now,
            "latency_ms": 0,
            "raw_response": raw,
        }


class CTraderExecutor(BaseExecutor):
    """Connects to cTrader Open API via WebSocket (JSON on port 5036)."""

    def __init__(self, config: AppConfig):
        self.config = config
        self._ws: Optional[websocket.WebSocket] = None
        self._symbol_cache: Dict[str, int] = {}
        self._ctid_trader_account_id: Optional[int] = None
        self._lock = threading.Lock()
        self._msg_id_counter = 0
        self._connect_and_auth()

    # ── Connection & Auth ───────────────────────────────────

    def _next_msg_id(self) -> str:
        self._msg_id_counter += 1
        return f"msg_{self._msg_id_counter}"

    def _send(self, payload_type: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Send a JSON message and wait for the response."""
        msg_id = self._next_msg_id()
        envelope = {
            "clientMsgId": msg_id,
            "payloadType": payload_type,
            "payload": payload,
        }
        raw = json.dumps(envelope)
        logger.debug("WS SEND: %s", raw[:500])
        self._ws.send(raw)

        # Read responses until we get one matching our clientMsgId
        deadline = time.time() + self.config.request_timeout_seconds
        while time.time() < deadline:
            self._ws.settimeout(max(0.1, deadline - time.time()))
            try:
                resp_raw = self._ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not resp_raw:
                continue
            resp = json.loads(resp_raw)
            logger.info("WS RECV: payloadType=%s resp=%s", resp.get("payloadType"), str(resp)[:500])
            # Return any response that matches our msg ID or is an execution event
            if resp.get("clientMsgId") == msg_id:
                return resp
            # Store execution events for later pickup
            if resp.get("payloadType") == PT_EXECUTION_EVENT:
                return resp
        raise TimeoutError(f"No response within {self.config.request_timeout_seconds}s")

    def _send_and_check(self, payload_type: int, payload: Dict[str, Any], expected_type: int) -> Dict[str, Any]:
        """Send and verify response payloadType matches expected."""
        resp = self._send(payload_type, payload)
        resp_type = resp.get("payloadType")
        resp_payload = resp.get("payload", {})
        if resp_type != expected_type:
            error_msg = resp_payload.get("errorCode", resp_payload.get("description", f"Unexpected response type {resp_type}"))
            raise RuntimeError(f"cTrader API error: {error_msg}")
        return resp_payload

    def _connect_and_auth(self):
        """Connect via WebSocket and authenticate."""
        host = self.config.ctrader_host
        port = self.config.ctrader_port
        url = f"wss://{host}:{port}"
        logger.info("Connecting to cTrader at %s ...", url)

        sslopt = {"cert_reqs": ssl.CERT_NONE}
        self._ws = websocket.WebSocket(sslopt=sslopt)
        self._ws.connect(url)
        logger.info("WebSocket connected.")

        # Step 1: Application auth
        self._send_and_check(
            PT_APP_AUTH_REQ,
            {
                "clientId": self.config.ctrader_client_id,
                "clientSecret": self.config.ctrader_client_secret,
            },
            PT_APP_AUTH_RES,
        )
        logger.info("Application authenticated.")

        # Step 2: Get account list
        accounts_resp = self._send_and_check(
            PT_GET_ACCOUNTS_REQ,
            {"accessToken": self.config.ctrader_access_token},
            PT_GET_ACCOUNTS_RES,
        )

        # Find the matching ctidTraderAccountId
        target_id = str(self.config.ctrader_account_id).strip()
        ctid = None
        for acct in accounts_resp.get("ctidTraderAccount", []):
            if str(acct.get("ctidTraderAccountId")) == target_id or str(acct.get("traderLogin")) == target_id:
                ctid = int(acct["ctidTraderAccountId"])
                break
        if ctid is None:
            available = [str(a.get("ctidTraderAccountId")) for a in accounts_resp.get("ctidTraderAccount", [])]
            raise RuntimeError(
                f"Account {target_id} not found. Available accounts: {available}"
            )
        self._ctid_trader_account_id = ctid
        logger.info("Found account ctidTraderAccountId=%d", ctid)

        # Step 3: Account auth
        self._send_and_check(
            PT_ACCOUNT_AUTH_REQ,
            {
                "ctidTraderAccountId": ctid,
                "accessToken": self.config.ctrader_access_token,
            },
            PT_ACCOUNT_AUTH_RES,
        )
        logger.info("Account authenticated.")

        # Step 4: Load symbols
        self._load_symbols()

    def _load_symbols(self):
        """Query symbol list and build name->id cache."""
        resp = self._send_and_check(
            PT_SYMBOLS_LIST_REQ,
            {"ctidTraderAccountId": self._ctid_trader_account_id},
            PT_SYMBOLS_LIST_RES,
        )
        for sym in resp.get("symbol", []):
            name = sym.get("symbolName", "").upper()
            sym_id = sym.get("symbolId")
            if name and sym_id is not None:
                self._symbol_cache[name] = int(sym_id)
        logger.info("Loaded %d symbols.", len(self._symbol_cache))

    def _get_symbol_id(self, symbol_name: str) -> int:
        """Resolve symbol name to numeric ID."""
        upper = symbol_name.upper().strip()
        if upper in self._symbol_cache:
            return self._symbol_cache[upper]
        # Try refreshing cache once
        self._load_symbols()
        if upper in self._symbol_cache:
            return self._symbol_cache[upper]
        raise ValueError(f"Symbol '{upper}' not found in cTrader. Available: {list(self._symbol_cache.keys())[:20]}")

    # ── Trade Execution ─────────────────────────────────────

    def execute_trade(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        start = time.perf_counter()
        try:
            return self._execute_trade_inner(signal, start)
        except Exception as exc:
            elapsed = int((time.perf_counter() - start) * 1000)
            logger.exception("Trade execution failed: %s", exc)
            return {
                "status": "error",
                "broker_order_id": None,
                "requested_price": None,
                "filled_price": None,
                "error_code": "EXECUTOR_EXCEPTION",
                "error_message": str(exc),
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "latency_ms": elapsed,
                "raw_response": None,
            }

    def _execute_trade_inner(self, signal: Dict[str, Any], start: float) -> Dict[str, Any]:
        symbol_name = signal["normalized_symbol"]
        symbol_id = self._get_symbol_id(symbol_name)
        action = signal["action"].upper()

        # Build order request
        # Volume: cTrader uses "cents" — 1 lot = 100000 units, volume in cents = units * 100
        volume_units = signal.get("volume_units", self.config.default_volume_units)
        volume_cents = volume_units * 100

        order_payload: Dict[str, Any] = {
            "ctidTraderAccountId": self._ctid_trader_account_id,
            "symbolId": symbol_id,
            "orderType": "MARKET",
            "tradeSide": "BUY" if action in ("BUY", "LONG") else "SELL",
            "volume": volume_cents,
            "comment": f"ABC_{signal.get('signal_id', '')[:12]}",
        }

        # Add TP/SL from meta (absolute prices)
        meta = signal.get("meta") or {}
        if meta.get("tp") is not None:
            order_payload["takeProfit"] = float(meta["tp"])
        if meta.get("sl") is not None:
            order_payload["stopLoss"] = float(meta["sl"])

        logger.info(
            "Placing %s order: symbol=%s (id=%d), volume=%d, tp=%s, sl=%s",
            action, symbol_name, symbol_id, volume_cents,
            meta.get("tp"), meta.get("sl"),
        )

        # Send order and wait for execution event
        msg_id = self._next_msg_id()
        envelope = {
            "clientMsgId": msg_id,
            "payloadType": PT_NEW_ORDER_REQ,
            "payload": order_payload,
        }
        self._ws.send(json.dumps(envelope))

        # Wait for execution event or error
        deadline = time.time() + self.config.request_timeout_seconds
        while time.time() < deadline:
            self._ws.settimeout(max(0.1, deadline - time.time()))
            try:
                resp_raw = self._ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not resp_raw:
                continue

            resp = json.loads(resp_raw)
            resp_type = resp.get("payloadType")
            resp_payload = resp.get("payload", {})

            if resp_type == PT_EXECUTION_EVENT:
                return self._parse_execution_event(resp_payload, start)

            if resp_type == PT_ORDER_ERROR_EVENT:
                elapsed = int((time.perf_counter() - start) * 1000)
                return {
                    "status": "rejected",
                    "broker_order_id": None,
                    "requested_price": None,
                    "filled_price": None,
                    "error_code": resp_payload.get("errorCode", "UNKNOWN"),
                    "error_message": resp_payload.get("description", "Order rejected"),
                    "executed_at": datetime.now(timezone.utc).isoformat(),
                    "latency_ms": elapsed,
                    "raw_response": resp_payload,
                }

            # Check for generic error response
            if resp.get("clientMsgId") == msg_id and resp_type not in (PT_NEW_ORDER_REQ,):
                error_code = resp_payload.get("errorCode")
                if error_code:
                    elapsed = int((time.perf_counter() - start) * 1000)
                    return {
                        "status": "rejected",
                        "broker_order_id": None,
                        "requested_price": None,
                        "filled_price": None,
                        "error_code": error_code,
                        "error_message": resp_payload.get("description", "Order error"),
                        "executed_at": datetime.now(timezone.utc).isoformat(),
                        "latency_ms": elapsed,
                        "raw_response": resp_payload,
                    }

        # Timeout
        elapsed = int((time.perf_counter() - start) * 1000)
        return {
            "status": "error",
            "broker_order_id": None,
            "requested_price": None,
            "filled_price": None,
            "error_code": "TIMEOUT",
            "error_message": f"No execution event within {self.config.request_timeout_seconds}s",
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "latency_ms": elapsed,
            "raw_response": None,
        }

    def _parse_execution_event(self, payload: Dict[str, Any], start: float) -> Dict[str, Any]:
        """Parse ProtoOAExecutionEvent into standard result dict."""
        elapsed = int((time.perf_counter() - start) * 1000)
        exec_type = payload.get("executionType", "")
        order = payload.get("order", {})
        position = payload.get("position", {})
        deal = payload.get("deal", {})

        order_id = str(order.get("orderId", ""))
        filled_price = deal.get("executionPrice") or position.get("price")

        if exec_type in ("ORDER_FILLED", "FILL"):
            return {
                "status": "filled",
                "broker_order_id": order_id,
                "requested_price": None,
                "filled_price": float(filled_price) if filled_price else None,
                "error_code": None,
                "error_message": None,
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "latency_ms": elapsed,
                "raw_response": payload,
            }
        elif exec_type in ("ORDER_REJECTED", "REJECT"):
            return {
                "status": "rejected",
                "broker_order_id": order_id,
                "requested_price": None,
                "filled_price": None,
                "error_code": order.get("closingOrder", exec_type),
                "error_message": order.get("comment", "Order rejected by broker"),
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "latency_ms": elapsed,
                "raw_response": payload,
            }
        else:
            # ORDER_ACCEPTED or other intermediate states — wait more or return as-is
            return {
                "status": "filled" if filled_price else "accepted",
                "broker_order_id": order_id,
                "requested_price": None,
                "filled_price": float(filled_price) if filled_price else None,
                "error_code": None,
                "error_message": None,
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "latency_ms": elapsed,
                "raw_response": payload,
            }


def build_executor(config: AppConfig) -> BaseExecutor:
    if config.executor_mode == "simulated":
        return SimulatedExecutor()
    return CTraderExecutor(config)
