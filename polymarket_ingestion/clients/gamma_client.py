from __future__ import annotations

from typing import Any
import json

from polymarket_ingestion.clients.base import BaseApiClient


class GammaClient(BaseApiClient):
    """Client for market metadata from Gamma API."""

    def get_market_by_slug(self, slug: str) -> dict[str, Any]:
        attempts: list[tuple[str, dict[str, Any] | None]] = [
            ("/markets", {"slug": slug}),
            (f"/markets/slug/{slug}", None),
        ]

        last_error: Exception | None = None
        for path, params in attempts:
            try:
                payload = self.get_json(path, params=params)
                market = self._extract_market(payload, slug)
                if market:
                    return market
            except Exception as exc:  # pragma: no cover - error handling path
                last_error = exc

        if last_error:
            raise RuntimeError(f"Unable to fetch market by slug '{slug}': {last_error}") from last_error
        raise RuntimeError(f"Unable to fetch market by slug '{slug}'")

    @staticmethod
    def _extract_market(payload: Any, slug: str) -> dict[str, Any] | None:
        if isinstance(payload, dict):
            if payload.get("slug") == slug:
                return payload
            for key in ("markets", "data", "results"):
                val = payload.get(key)
                if isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict) and item.get("slug") == slug:
                            return item
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict) and item.get("slug") == slug:
                    return item
        return None

    @staticmethod
    def extract_token_map(market: dict[str, Any]) -> dict[str, str]:
        token_map: dict[str, str] = {}
        tokens = market.get("tokens", [])
        if isinstance(tokens, list):
            for t in tokens:
                if not isinstance(t, dict):
                    continue
                outcome = str(t.get("outcome", t.get("name", "UNKNOWN"))).upper()
                token_id = t.get("token_id") or t.get("id") or t.get("asset_id")
                if token_id:
                    token_map[outcome] = str(token_id)

        # Fallback format seen in Gamma payloads:
        # outcomes='["Up","Down"]', clobTokenIds='["...","..."]'
        if not token_map:
            outcomes_raw = market.get("outcomes", [])
            token_ids_raw = market.get("clobTokenIds", [])

            if isinstance(outcomes_raw, str):
                try:
                    outcomes_raw = json.loads(outcomes_raw)
                except Exception:
                    outcomes_raw = []
            if isinstance(token_ids_raw, str):
                try:
                    token_ids_raw = json.loads(token_ids_raw)
                except Exception:
                    token_ids_raw = []

            if isinstance(outcomes_raw, list) and isinstance(token_ids_raw, list):
                for outcome, token_id in zip(outcomes_raw, token_ids_raw):
                    if token_id is None:
                        continue
                    token_map[str(outcome).upper()] = str(token_id)
        return token_map

    @staticmethod
    def condition_id(market: dict[str, Any]) -> str:
        return str(
            market.get("condition_id")
            or market.get("conditionId")
            or market.get("id")
            or ""
        )
