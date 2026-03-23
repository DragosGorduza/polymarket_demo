from __future__ import annotations

from typing import Any

from polymarket_ingestion.clients.base import BaseApiClient


class ClobClient(BaseApiClient):
    """Client for CLOB order book and trade feed."""

    def get_order_book(self, token_id: str) -> dict[str, Any]:
        return self.get_json(f"/order-book/{token_id}")

    def get_trades_by_market(
        self,
        condition_id: str,
        max_pages: int = 30,
        page_size: int = 500,
    ) -> list[dict[str, Any]]:
        if not condition_id:
            return []

        out: list[dict[str, Any]] = []
        cursor: str | None = None

        for _ in range(max_pages):
            params: dict[str, Any] = {"market": condition_id, "limit": page_size}
            if cursor:
                params["next_cursor"] = cursor
            payload = self.get_json("/trades", params=params)

            trades: list[dict[str, Any]] = []
            if isinstance(payload, dict):
                for key in ("trades", "data", "results"):
                    maybe = payload.get(key)
                    if isinstance(maybe, list):
                        trades = [x for x in maybe if isinstance(x, dict)]
                        break
                cursor = payload.get("next_cursor") or payload.get("cursor")
            elif isinstance(payload, list):
                trades = [x for x in payload if isinstance(x, dict)]
                cursor = None

            if not trades:
                break

            out.extend(trades)

            if not cursor:
                break

        return out
