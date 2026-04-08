import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import MetaTrader5 as mt5

from config import AppConfig

logger = logging.getLogger("mt5_executor")

# Hard cap on volume to prevent catastrophic sizing errors
MAX_VOLUME = 10.0

# Per-pair geometric TP multiplier (SL_distance * multiplier = geometric TP distance)
# Derived from pre-MFE signal analysis of ABC pattern projections
GEO_MULTIPLIER = {
    "EURUSD.sc": 3.0,
    "GBPUSD.sc": 4.5,
    "USDCHF.sc": 5.0,
    "GBPAUD.sc": 2.5,
    "NZDUSD.sc": 4.0,
    "AUDJPY.sc": 3.0,
    "EURAUD.sc": 4.0,
    "USDJPY.sc": 2.5,
    "EURJPY.sc": 3.0,
}


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


class MT5Executor(BaseExecutor):
    """Executes trades on MetaTrader 5 via the local terminal."""

    def __init__(self, config: AppConfig):
        self.config = config
        self._initialize()

    def _initialize(self):
        """Connect to the running MT5 terminal and log in."""
        # Initialize connection to MT5 terminal
        init_kwargs = {}
        if self.config.mt5_terminal_path:
            init_kwargs["path"] = self.config.mt5_terminal_path

        if not mt5.initialize(**init_kwargs):
            error = mt5.last_error()
            raise RuntimeError(
                f"MT5 initialize failed: {error}. "
                "Make sure MetaTrader 5 terminal is running."
            )
        logger.info("MT5 terminal connected.")

        # Log in to trading account
        if not mt5.login(
            self.config.mt5_login,
            password=self.config.mt5_password,
            server=self.config.mt5_server,
        ):
            error = mt5.last_error()
            mt5.shutdown()
            raise RuntimeError(f"MT5 login failed: {error}")

        # Verify connection
        info = mt5.account_info()
        if info is None:
            mt5.shutdown()
            raise RuntimeError("MT5 account_info() returned None after login.")

        logger.info(
            "MT5 logged in: account=%d, server=%s, balance=%.2f, leverage=%d",
            info.login, info.server, info.balance, info.leverage,
        )

    def _calculate_volume(self, symbol: str, sl_price: float, entry_price: float) -> float:
        """Calculate lot size based on risk amount and SL distance.

        Formula: volume = risk_amount / (sl_distance_in_ticks * tick_value)
        Falls back to DEFAULT_VOLUME_UNITS if SL is missing.
        """
        info = mt5.symbol_info(symbol)
        if info is None:
            logger.warning("No symbol info for %s, using fallback volume", symbol)
            return round(max(self.config.default_volume_units / 100_000, 0.01), 2)

        sl_distance = abs(entry_price - sl_price)
        if sl_distance == 0:
            logger.warning("SL distance is zero for %s, using minimum volume", symbol)
            return info.volume_min

        # tick_value = profit in account currency when price moves by tick_size
        tick_size = info.trade_tick_size
        tick_value = info.trade_tick_value
        if tick_size <= 0 or tick_value <= 0:
            # Retry once — MT5 sometimes needs a moment after symbol_select
            logger.info("tick_value=0 for %s, retrying after 0.5s...", symbol)
            time.sleep(0.5)
            mt5.symbol_select(symbol, True)
            info = mt5.symbol_info(symbol)
            if info is not None:
                tick_size = info.trade_tick_size
                tick_value = info.trade_tick_value
            if tick_size <= 0 or tick_value <= 0:
                logger.warning("Invalid tick info for %s (tick_size=%.8f, tick_value=%.4f), using fallback",
                               symbol, tick_size, tick_value)
                return info.volume_min if info else round(max(self.config.default_volume_units / 100_000, 0.01), 2)

        # Number of ticks in SL distance
        ticks_in_sl = sl_distance / tick_size
        # Loss per lot if SL hit = ticks_in_sl * tick_value
        loss_per_lot = ticks_in_sl * tick_value

        if loss_per_lot <= 0:
            logger.warning("Computed loss_per_lot <= 0 for %s, using minimum volume", symbol)
            return info.volume_min

        raw_volume = self.config.risk_per_trade / loss_per_lot

        # Round down to nearest volume_step
        step = info.volume_step
        if step > 0:
            raw_volume = int(raw_volume / step) * step

        # Clamp to broker min/max
        volume = max(raw_volume, info.volume_min)
        volume = min(volume, info.volume_max)

        # Hard cap to prevent catastrophic sizing errors
        if volume > MAX_VOLUME:
            logger.warning("Volume %.4f exceeds MAX_VOLUME %.1f for %s, capping", volume, MAX_VOLUME, symbol)
            volume = MAX_VOLUME

        # Round to avoid floating point issues
        volume = round(volume, 6)

        logger.info(
            "Risk sizing: %s | risk=$%.0f | SL dist=%.5f | loss/lot=%.2f | raw=%.4f | final=%.4f (min=%.4f, max=%.4f)",
            symbol, self.config.risk_per_trade, sl_distance, loss_per_lot,
            raw_volume, volume, info.volume_min, info.volume_max,
        )
        return volume

    def _validate_stops(self, symbol: str, price: float, tp: float, sl: float, is_buy: bool):
        """Adjust TP/SL if they violate the broker's minimum stop distance."""
        info = mt5.symbol_info(symbol)
        if info is None:
            return tp, sl

        # trade_stops_level is in points (e.g., 10 = 1 pip for 5-digit broker)
        min_distance = info.trade_stops_level * info.point
        if min_distance <= 0:
            return tp, sl

        # Add a small buffer (20%) to avoid edge-case rejections
        min_distance *= 1.2

        if is_buy:
            if tp and abs(tp - price) < min_distance:
                tp = round(price + min_distance, info.digits)
                logger.info("Adjusted BUY TP to %.5f (min distance %.5f)", tp, min_distance)
            if sl and abs(price - sl) < min_distance:
                sl = round(price - min_distance, info.digits)
                logger.info("Adjusted BUY SL to %.5f (min distance %.5f)", sl, min_distance)
        else:
            if tp and abs(price - tp) < min_distance:
                tp = round(price - min_distance, info.digits)
                logger.info("Adjusted SELL TP to %.5f (min distance %.5f)", tp, min_distance)
            if sl and abs(sl - price) < min_distance:
                sl = round(price + min_distance, info.digits)
                logger.info("Adjusted SELL SL to %.5f (min distance %.5f)", sl, min_distance)

        return tp, sl

    def _rejected_result(self, price: float, start: float,
                         error_code: str, error_message: str) -> Dict[str, Any]:
        """Return a rejection result for invalid/stale signals."""
        elapsed = int((time.perf_counter() - start) * 1000)
        return {
            "status": "rejected",
            "broker_order_id": None,
            "requested_price": price,
            "filled_price": None,
            "error_code": error_code,
            "error_message": error_message,
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "latency_ms": elapsed,
            "raw_response": None,
        }

    def _get_filling_mode(self, symbol: str) -> int:
        """Detect the correct filling mode for a symbol."""
        info = mt5.symbol_info(symbol)
        if info is None:
            return mt5.ORDER_FILLING_IOC

        filling = info.filling_mode
        # filling_mode is a bitmask: bit 0 = FOK, bit 1 = IOC, bit 2 = RETURN
        if filling & 1:  # ORDER_FILLING_FOK supported
            return mt5.ORDER_FILLING_FOK
        if filling & 2:  # ORDER_FILLING_IOC supported
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

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

    def _send_order(self, request: Dict[str, Any], label: str, start: float) -> Dict[str, Any]:
        """Send a single MT5 order and return a standardised result dict."""
        result = mt5.order_send(request)
        elapsed = int((time.perf_counter() - start) * 1000)

        if result is None:
            error = mt5.last_error()
            return {
                "status": "error",
                "broker_order_id": None,
                "requested_price": request.get("price"),
                "filled_price": None,
                "error_code": f"MT5_{error[0]}",
                "error_message": error[1] if len(error) > 1 else "order_send returned None",
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "latency_ms": elapsed,
                "raw_response": None,
                "label": label,
            }

        if result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(
                "%s order filled: order=%d, price=%.5f, volume=%.2f",
                label, result.order, result.price, result.volume,
            )
            return {
                "status": "filled",
                "broker_order_id": str(result.order),
                "requested_price": request.get("price"),
                "filled_price": result.price,
                "error_code": None,
                "error_message": None,
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "latency_ms": elapsed,
                "raw_response": {
                    "retcode": result.retcode,
                    "deal": result.deal,
                    "order": result.order,
                    "volume": result.volume,
                    "price": result.price,
                    "bid": result.bid,
                    "ask": result.ask,
                    "comment": result.comment,
                },
                "label": label,
            }
        else:
            logger.warning(
                "%s order rejected: retcode=%d, comment=%s",
                label, result.retcode, result.comment,
            )
            return {
                "status": "rejected",
                "broker_order_id": str(result.order) if result.order else None,
                "requested_price": request.get("price"),
                "filled_price": None,
                "error_code": f"MT5_{result.retcode}",
                "error_message": result.comment or f"Retcode {result.retcode}",
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "latency_ms": elapsed,
                "raw_response": {
                    "retcode": result.retcode,
                    "comment": result.comment,
                },
                "label": label,
            }

    def _execute_trade_inner(
        self, signal: Dict[str, Any], start: float
    ) -> Dict[str, Any]:
        symbol = signal["normalized_symbol"]
        action = signal["action"].upper()

        # Ensure symbol is visible in Market Watch
        if not mt5.symbol_select(symbol, True):
            raise ValueError(
                f"Symbol '{symbol}' not found or cannot be selected in MT5."
            )

        # Get current price (retry once if tick data is empty — MT5 needs time after symbol_select)
        tick = mt5.symbol_info_tick(symbol)
        if tick is None or (tick.bid == 0 and tick.ask == 0):
            logger.info("No tick data for %s, retrying after 0.5s...", symbol)
            time.sleep(0.5)
            tick = mt5.symbol_info_tick(symbol)
        if tick is None or (tick.bid == 0 and tick.ask == 0):
            raise ValueError(f"No tick data for '{symbol}'.")

        is_buy = action in ("BUY", "LONG")
        price = tick.ask if is_buy else tick.bid

        meta = signal.get("meta") or {}
        mfe_tp = float(meta["tp"]) if meta.get("tp") is not None else None
        original_sl = float(meta["sl"]) if meta.get("sl") is not None else None

        # ── GLOBAL SL OVERRIDE: 0.1% beyond C level ──
        c_level = None
        if meta.get("c_level") is not None:
            try:
                c_level = float(meta["c_level"])
            except (ValueError, TypeError):
                c_level = None

        if c_level is not None and c_level > 0:
            if is_buy:
                sl = round(c_level * 0.999, 6)  # 0.1% below C
            else:
                sl = round(c_level * 1.001, 6)  # 0.1% above C
            logger.info("SL override: C=%.5f -> SL=%.5f (0.1%% beyond C)", c_level, sl)
        else:
            sl = original_sl

        # ── MINIMUM SL DISTANCE GUARD ──
        if sl is not None:
            sl_distance = abs(price - sl)
            info = mt5.symbol_info(symbol)
            if info is not None:
                min_stop_dist = info.trade_stops_level * info.point * 1.2
                if min_stop_dist > 0 and sl_distance < min_stop_dist:
                    logger.warning(
                        "SL too close: distance=%.5f < min=%.5f for %s (C=%.5f, price=%.5f)",
                        sl_distance, min_stop_dist, symbol,
                        c_level if c_level else 0, price,
                    )
                    return self._rejected_result(price, start, "SL_TOO_CLOSE",
                        f"SL distance {sl_distance:.5f} < broker min {min_stop_dist:.5f}")

        # ── VALIDATE SL SIDE ──
        if sl is not None:
            if is_buy and sl >= price:
                logger.warning("Invalid: BUY SL (%.5f) >= price (%.5f)", sl, price)
                return self._rejected_result(price, start, "INVALID_SL",
                    f"BUY SL {sl:.5f} >= current price {price:.5f}")
            if not is_buy and sl <= price:
                logger.warning("Invalid: SELL SL (%.5f) <= price (%.5f)", sl, price)
                return self._rejected_result(price, start, "INVALID_SL",
                    f"SELL SL {sl:.5f} <= current price {price:.5f}")

        # Risk-based volume calculation (uses new SL from C level)
        if sl is not None:
            volume = self._calculate_volume(symbol, sl, price)
        else:
            volume = round(max(self.config.default_volume_units / 100_000, 0.01), 2)

        # ── COMPUTE GEOMETRIC 50% TP ──
        geo_mult = GEO_MULTIPLIER.get(symbol, 3.0)
        sl_dist = abs(price - sl) if sl is not None else 0
        geo_tp_dist = sl_dist * geo_mult * 0.50  # 50% of full geometric projection

        if is_buy:
            geo_tp = round(price + geo_tp_dist, 6) if geo_tp_dist > 0 else None
        else:
            geo_tp = round(price - geo_tp_dist, 6) if geo_tp_dist > 0 else None

        # ── VALIDATE TPs ──
        if mfe_tp is not None:
            if is_buy and mfe_tp <= price:
                logger.warning("Stale: BUY MFE TP (%.5f) <= price (%.5f)", mfe_tp, price)
                mfe_tp = None
            if not is_buy and mfe_tp is not None and mfe_tp >= price:
                logger.warning("Stale: SELL MFE TP (%.5f) >= price (%.5f)", mfe_tp, price)
                mfe_tp = None

        if geo_tp is not None:
            if is_buy and geo_tp <= price:
                geo_tp = None
            if not is_buy and geo_tp is not None and geo_tp >= price:
                geo_tp = None

        # Validate stops against broker minimum distance
        if geo_tp is not None or sl is not None:
            geo_tp, sl = self._validate_stops(symbol, price, geo_tp, sl, is_buy)
        mfe_tp_validated = mfe_tp
        if mfe_tp_validated is not None:
            mfe_tp_validated, _ = self._validate_stops(symbol, price, mfe_tp_validated, sl, is_buy)

        # ── SEND DUAL ORDERS ──
        signal_id_short = signal.get("signal_id", "")[:8]
        order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
        filling_mode = self._get_filling_mode(symbol)

        base_request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "deviation": self.config.mt5_deviation,
            "magic": self.config.mt5_magic,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_mode,
        }

        results = []

        # Order A: GEO position (50% geometric TP + trailing stop managed by position monitor)
        if geo_tp is not None:
            req_geo = dict(base_request)
            req_geo["comment"] = f"TV_{signal_id_short}_GEO"
            req_geo["tp"] = geo_tp
            if sl is not None:
                req_geo["sl"] = sl
            logger.info(
                "Placing GEO %s: %s vol=%.2f price=%.5f tp=%.5f sl=%s",
                action, symbol, volume, price, geo_tp, sl,
            )
            results.append(self._send_order(req_geo, "GEO", start))

        # Order B: MFE position (MFE TP from indicator, managed by time_exit / partial_tp)
        if mfe_tp_validated is not None:
            req_mfe = dict(base_request)
            req_mfe["comment"] = f"TV_{signal_id_short}_MFE"
            req_mfe["tp"] = mfe_tp_validated
            if sl is not None:
                req_mfe["sl"] = sl
            logger.info(
                "Placing MFE %s: %s vol=%.2f price=%.5f tp=%.5f sl=%s",
                action, symbol, volume, price, mfe_tp_validated, sl,
            )
            results.append(self._send_order(req_mfe, "MFE", start))

        # If neither TP was valid, send a single order with just SL
        if not results:
            req_fallback = dict(base_request)
            req_fallback["comment"] = f"TV_{signal_id_short}"
            if sl is not None:
                req_fallback["sl"] = sl
            logger.info(
                "Placing fallback %s (no valid TP): %s vol=%.2f price=%.5f sl=%s",
                action, symbol, volume, price, sl,
            )
            results.append(self._send_order(req_fallback, "SINGLE", start))

        # ── COMBINED RESULT ──
        # Return the first filled result as the primary; attach all sub-results
        filled = [r for r in results if r["status"] == "filled"]
        primary = filled[0] if filled else results[0]

        # Merge all order IDs into a combined response
        all_orders = [r.get("broker_order_id") for r in results if r.get("broker_order_id")]
        primary["raw_response"] = primary.get("raw_response") or {}
        primary["raw_response"]["dual_orders"] = [
            {"label": r.get("label"), "order_id": r.get("broker_order_id"), "status": r["status"]}
            for r in results
        ]
        if len(all_orders) > 1:
            primary["broker_order_id"] = ",".join(str(o) for o in all_orders)

        return primary

    def close(self):
        """Shutdown MT5 connection."""
        mt5.shutdown()
        logger.info("MT5 connection closed.")


def build_executor(config: AppConfig) -> BaseExecutor:
    if config.executor_mode == "simulated":
        return SimulatedExecutor()
    return MT5Executor(config)
