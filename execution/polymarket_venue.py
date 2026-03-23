from __future__ import annotations

from execution.types import OrderStatus


class PolymarketVenueClient:
    def __init__(self, host: str, chain_id: int, private_key: str):
        from py_clob_client.client import ClobClient

        self.client = ClobClient(
            host=host,
            chain_id=chain_id,
            private_key=private_key,
            signature_type=2,
        )

    def place_limit_order(self, token_id: str, side: str, price: float, size: float) -> str:
        from py_clob_client.clob_types import OrderArgs
        from py_clob_client.order_builder.constants import BUY, SELL

        side_val = BUY if side.upper() == "BUY" else SELL
        order_args = OrderArgs(
            token_id=token_id,
            side=side_val,
            price=round(float(price), 4),
            size=round(float(size), 4),
        )
        resp = self.client.create_and_post_order(order_args)
        order_id = resp.get("orderID") or resp.get("orderId") or resp.get("id")
        if not order_id:
            raise RuntimeError(f"Order placement failed: {resp}")
        return str(order_id)

    def get_order_status(self, order_id: str) -> OrderStatus:
        # API variants across client versions.
        raw = None
        if hasattr(self.client, "get_order"):
            raw = self.client.get_order(order_id)
        elif hasattr(self.client, "get_orders"):
            maybe = self.client.get_orders()
            if isinstance(maybe, list):
                raw = next((o for o in maybe if str(o.get("id") or o.get("orderID")) == order_id), None)

        if not raw:
            return OrderStatus(order_id=order_id, state="OPEN", filled_size=0.0)

        state = str(raw.get("status") or raw.get("state") or "OPEN").upper()
        filled = float(raw.get("filled") or raw.get("filled_size") or 0.0)
        return OrderStatus(order_id=order_id, state=state, filled_size=filled)

    def cancel_order(self, order_id: str) -> None:
        if hasattr(self.client, "cancel"):
            self.client.cancel(order_id)
            return
        if hasattr(self.client, "cancel_order"):
            self.client.cancel_order(order_id)
            return
        raise RuntimeError("No cancel method available on py-clob-client instance")
