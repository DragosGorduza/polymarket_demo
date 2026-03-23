from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class OrderStatus:
    order_id: str
    state: str  # OPEN | PARTIALLY_FILLED | FILLED | CANCELLED | REJECTED
    filled_size: float = 0.0


@dataclass
class OrderInfo:
    order_id: str
    token_id: str
    side: str
    price: float
    size: float
    size_usd: float
    status: str = "OPEN"


class VenueClient(Protocol):
    def place_limit_order(self, token_id: str, side: str, price: float, size: float) -> str:
        ...

    def get_order_status(self, order_id: str) -> OrderStatus:
        ...

    def cancel_order(self, order_id: str) -> None:
        ...
