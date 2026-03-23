from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable

from execution.types import OrderInfo, VenueClient


@dataclass
class MonitorResult:
    status: str
    order_id: str
    details: str = ""


class ExecutionOrderManager:
    def __init__(self, venue: VenueClient, tick_size: float = 0.01):
        self.venue = venue
        self.tick_size = tick_size
        self.open_orders: dict[str, OrderInfo] = {}

    def _resolve_token_id(self, signal, signals: dict) -> str:
        direction = str(getattr(signal, "direction", "")).upper()

        up_label = str(signals.get("yes_outcome", "UP")).upper()
        down_label = str(signals.get("no_outcome", "DOWN")).upper()

        if direction == up_label:
            return str(signals.get("yes_token_id") or signals.get("up_token_id") or "")
        if direction == down_label:
            return str(signals.get("no_token_id") or signals.get("down_token_id") or "")
        return ""

    def _passive_buy_price(self, best_bid: float, best_ask: float) -> float:
        spread = best_ask - best_bid
        if spread <= self.tick_size:
            return round(best_bid, 4)
        price = min(best_ask - self.tick_size, best_bid + self.tick_size)
        return round(max(0.001, min(0.999, price)), 4)

    def place_passive_order_inside_spread(self, signal, signals: dict, size_usd: float) -> str:
        token_id = self._resolve_token_id(signal, signals)
        if not token_id:
            raise ValueError("Missing token_id for signal direction")

        best_bid = float(signals["best_bid"])
        best_ask = float(signals["best_ask"])
        limit_price = self._passive_buy_price(best_bid, best_ask)
        size_tokens = round(size_usd / max(limit_price, 1e-6), 4)

        order_id = self.venue.place_limit_order(
            token_id=token_id,
            side="BUY",
            price=limit_price,
            size=size_tokens,
        )
        self.open_orders[order_id] = OrderInfo(
            order_id=order_id,
            token_id=token_id,
            side="BUY",
            price=limit_price,
            size=size_tokens,
            size_usd=float(size_usd),
            status="OPEN",
        )
        return order_id

    def monitor_fill_status(
        self,
        order_id: str,
        refresh_signal_fn: Callable[[], tuple[object, dict]] | None = None,
        drift_threshold: float = 0.03,
        max_checks: int = 10,
        poll_seconds: float = 0.5,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> MonitorResult:
        if order_id not in self.open_orders:
            raise KeyError(f"Unknown order_id={order_id}")

        original_info = self.open_orders[order_id]
        baseline_price = original_info.price

        for i in range(1, max_checks + 1):
            status = self.venue.get_order_status(order_id)
            self.open_orders[order_id].status = status.state

            if status.state == "FILLED":
                return MonitorResult(status="filled", order_id=order_id)
            if status.state in ("CANCELLED", "REJECTED"):
                return MonitorResult(status=status.state.lower(), order_id=order_id)

            if refresh_signal_fn is not None and i % 2 == 0:
                new_signal, new_signals = refresh_signal_fn()
                new_price = self._passive_buy_price(float(new_signals["best_bid"]), float(new_signals["best_ask"]))
                if abs(new_price - baseline_price) >= drift_threshold:
                    self.venue.cancel_order(order_id)
                    self.open_orders[order_id].status = "CANCELLED"
                    new_id = self.place_passive_order_inside_spread(new_signal, new_signals, original_info.size_usd)
                    return MonitorResult(status="repriced", order_id=new_id, details=f"replaced {order_id}")

            if i < max_checks:
                sleep_fn(poll_seconds)

        self.venue.cancel_order(order_id)
        self.open_orders[order_id].status = "CANCELLED"
        return MonitorResult(status="cancelled_stale", order_id=order_id)
