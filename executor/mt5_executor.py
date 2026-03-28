import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import MetaTrader5 as mt5

from config import AppConfig

logger = logging.getLogger("mt5_executor")


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
            logger.warning("Invalid tick info for %s (tick_size=%.8f, tick_value=%.4f), using fallback",
                           symbol, tick_size, tick_value)
            return info.volume_min

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

        # Get current price
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise ValueError(f"No tick data for '{symbol}'.")

        is_buy = action in ("BUY", "LONG")
        price = tick.ask if is_buy else tick.bid

        # TP/SL from meta (absolute prices)
        meta = signal.get("meta") or {}
        tp = float(meta["tp"]) if meta.get("tp") is not None else None
        sl = float(meta["sl"]) if meta.get("sl") is not None else None

        # Risk-based volume calculation (uses SL distance)
        if sl is not None:
            volume = self._calculate_volume(symbol, sl, price)
        else:
            # Fallback to default volume units if no SL
            volume = round(max(self.config.default_volume_units / 100_000, 0.01), 2)

        # Validate TP/SL are on the correct side of current price
        if tp is not None:
            if is_buy and tp <= price:
                logger.warning(
                    "Stale signal: BUY TP (%.5f) is at or below current price (%.5f) — target already reached",
                    tp, price,
                )
                return self._rejected_result(price, start, "STALE_TP",
                    f"BUY TP {tp:.5f} <= current price {price:.5f} — target already reached")
            if not is_buy and tp >= price:
                logger.warning(
                    "Stale signal: SELL TP (%.5f) is at or above current price (%.5f) — target already reached",
                    tp, price,
                )
                return self._rejected_result(price, start, "STALE_TP",
                    f"SELL TP {tp:.5f} >= current price {price:.5f} — target already reached")

        if sl is not None:
            if is_buy and sl >= price:
                logger.warning(
                    "Invalid signal: BUY SL (%.5f) is at or above current price (%.5f)",
                    sl, price,
                )
                return self._rejected_result(price, start, "INVALID_SL",
                    f"BUY SL {sl:.5f} >= current price {price:.5f}")
            if not is_buy and sl <= price:
                logger.warning(
                    "Invalid signal: SELL SL (%.5f) is at or below current price (%.5f)",
                    sl, price,
                )
                return self._rejected_result(price, start, "INVALID_SL",
                    f"SELL SL {sl:.5f} <= current price {price:.5f}")

        # Validate stops against broker minimum distance
        if tp is not None and sl is not None:
            tp, sl = self._validate_stops(symbol, price, tp, sl, is_buy)

        # Build order request
        request: Dict[str, Any] = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
            "price": price,
            "deviation": self.config.mt5_deviation,
            "magic": self.config.mt5_magic,
            "comment": f"TV_{signal.get('signal_id', '')[:8]}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._get_filling_mode(symbol),
        }

        if tp is not None:
            request["tp"] = tp
        if sl is not None:
            request["sl"] = sl

        logger.info(
            "Placing %s order: symbol=%s, volume=%.2f lots, price=%.5f, tp=%s, sl=%s, risk=$%.0f",
            action, symbol, volume, price, tp, sl, self.config.risk_per_trade,
        )

        # Send order
        result = mt5.order_send(request)
        elapsed = int((time.perf_counter() - start) * 1000)

        if result is None:
            error = mt5.last_error()
            return {
                "status": "error",
                "broker_order_id": None,
                "requested_price": price,
                "filled_price": None,
                "error_code": f"MT5_{error[0]}",
                "error_message": error[1] if len(error) > 1 else "order_send returned None",
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "latency_ms": elapsed,
                "raw_response": None,
            }

        # Parse result
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(
                "Order filled: order=%d, price=%.5f, volume=%.2f",
                result.order, result.price, result.volume,
            )
            return {
                "status": "filled",
                "broker_order_id": str(result.order),
                "requested_price": price,
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
            }
        else:
            logger.warning(
                "Order rejected: retcode=%d, comment=%s",
                result.retcode, result.comment,
            )
            return {
                "status": "rejected",
                "broker_order_id": str(result.order) if result.order else None,
                "requested_price": price,
                "filled_price": None,
                "error_code": f"MT5_{result.retcode}",
                "error_message": result.comment or f"Retcode {result.retcode}",
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "latency_ms": elapsed,
                "raw_response": {
                    "retcode": result.retcode,
                    "comment": result.comment,
                },
            }

    def close(self):
        """Shutdown MT5 connection."""
        mt5.shutdown()
        logger.info("MT5 connection closed.")


def build_executor(config: AppConfig) -> BaseExecutor:
    if config.executor_mode == "simulated":
        return SimulatedExecutor()
    return MT5Executor(config)
