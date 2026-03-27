from __future__ import annotations

from typing import Any

from polymarket_ingestion.clients.base import BaseApiClient


class ClobClient(BaseApiClient):
    """Client for CLOB order book and trade feed."""

    def get_order_book(self, token_id: str) -> dict[str, Any]:
        url = "/book"
        full_url = f"{self.base_url}{url}"
        print(f"[CLOB DEBUG] get_order_book  URL = {full_url}?token_id={token_id}")
        resp = self.session.get(full_url, params={"token_id": token_id}, timeout=self.timeout_seconds)
        print(f"[CLOB DEBUG] HTTP status = {resp.status_code}")
        if resp.status_code != 200:
            print(f"[CLOB DEBUG] response body = {resp.text[:600]}")
            resp.raise_for_status()
        raw = resp.json()
        print(f"[CLOB DEBUG] raw response type = {type(raw).__name__}")
        if isinstance(raw, dict):
            print(f"[CLOB DEBUG] response keys = {list(raw.keys())}")
            for side in ("bids", "asks"):
                levels = raw.get(side)
                if isinstance(levels, list):
                    print(f"[CLOB DEBUG]   {side}: {len(levels)} levels  first={levels[:2]}")
                else:
                    print(f"[CLOB DEBUG]   {side}: {levels!r}  (not a list)")
        else:
            print(f"[CLOB DEBUG] response (non-dict) = {str(raw)[:300]}")
        return raw

    def get_clob_market(self, condition_id: str) -> dict[str, Any] | None:
        """Fetch market info directly from the CLOB to inspect its token IDs."""
        try:
            raw = self.get_json(f"/markets/{condition_id}")
            print(f"[CLOB DEBUG] /markets/{condition_id} → keys={list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__}")
            if isinstance(raw, dict):
                tokens = raw.get("tokens", [])
                print(f"[CLOB DEBUG] CLOB market tokens = {tokens}")
            return raw if isinstance(raw, dict) else None
        except Exception as exc:
            print(f"[CLOB DEBUG] /markets/{condition_id} failed: {type(exc).__name__}: {exc}")
            return None

    def get_order_books_for_market(
        self,
        token_map: dict[str, str],
        condition_id: str = "",
    ) -> dict[str, dict]:
        """Fetch a live L2 order-book snapshot for every outcome in *token_map*.

        Returns a dict keyed by outcome label, e.g. ``{"YES": {...}, "NO": {...}}``.
        Debug prints are active – remove them once the response shape is confirmed.
        """
        print(f"[CLOB DEBUG] get_order_books_for_market  token_map = {token_map}")

        # If all token_ids 404, cross-check what the CLOB itself knows about this market
        if condition_id:
            print(f"[CLOB DEBUG] cross-checking CLOB market for condition_id={condition_id}")
            self.get_clob_market(condition_id)

        result: dict[str, dict] = {}
        for outcome, token_id in token_map.items():
            print(f"[CLOB DEBUG] fetching book for outcome={outcome!r}  token_id={token_id!r}")
            try:
                book = self.get_order_book(token_id)
                if isinstance(book, dict):
                    result[outcome] = book
                    print(f"[CLOB DEBUG] stored book for {outcome!r}")
                else:
                    print(f"[CLOB DEBUG] SKIP {outcome!r}: response is not a dict ({type(book).__name__})")
            except Exception as exc:
                print(f"[CLOB DEBUG] ERROR fetching {outcome!r} (token_id={token_id!r}): {type(exc).__name__}: {exc}")
        print(f"[CLOB DEBUG] get_order_books_for_market done  outcomes_with_book={list(result.keys())}")
        return result

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
