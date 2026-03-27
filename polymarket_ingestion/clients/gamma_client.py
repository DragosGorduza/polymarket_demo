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

    def get_tags(self) -> list[dict[str, Any]]:
        """Return all available Gamma tags (id, slug, label) via GET /tags."""
        try:
            payload = self.get_json("/tags")
            if isinstance(payload, list):
                return payload
            if isinstance(payload, dict):
                for key in ("tags", "data", "results"):
                    val = payload.get(key)
                    if isinstance(val, list):
                        return val
        except Exception as exc:
            print(f"[GAMMA] /tags failed: {exc}")
        return []

    def get_tag_by_slug(self, slug: str) -> dict[str, Any] | None:
        """Fetch a single tag by its slug via GET /tags/slug/{slug}.

        Returns a dict with keys: id, label, slug, forceShow, isCarousel, …
        Returns None if the tag is not found or the request fails.
        """
        try:
            payload = self.get_json(f"/tags/slug/{slug}")
            if isinstance(payload, dict) and payload.get("id"):
                return payload
        except Exception as exc:
            print(f"[GAMMA] /tags/slug/{slug} failed: {exc}")
        return None

    def get_all_active_markets(
        self,
        limit: int = 100,
        max_pages: int = 300,
    ) -> list[dict[str, Any]]:
        """Paginate through Gamma /markets returning every active, non-closed market.

        Uses offset-based pagination and falls back to cursor-based if the API
        provides a ``next_cursor`` field.
        """
        out: list[dict[str, Any]] = []
        offset = 0
        cursor: str | None = None

        for page_num in range(max_pages):
            params: dict[str, Any] = {
                "active": "true",
                "closed": "false",
                "limit": limit,
            }
            if cursor:
                params["next_cursor"] = cursor
            else:
                params["offset"] = offset

            try:
                payload = self.get_json("/markets", params=params)
            except Exception as exc:
                print(f"[GAMMA] page fetch failed (page={page_num}, offset={offset}): {exc}")
                break

            markets = self._extract_markets_list(payload)
            if not markets:
                break

            out.extend(markets)

            # Advance pagination
            cursor = None
            if isinstance(payload, dict):
                cursor = payload.get("next_cursor") or payload.get("cursor") or None
            if cursor:
                pass  # cursor-based: loop will use it next iteration
            elif len(markets) < limit:
                break  # final page
            else:
                offset += limit

        return out

    @staticmethod
    def _extract_events_list(payload: Any) -> list[dict[str, Any]]:
        """Return a flat list of event dicts from any Gamma /events response shape."""
        if isinstance(payload, list):
            return [e for e in payload if isinstance(e, dict)]
        if isinstance(payload, dict):
            for key in ("events", "data", "results"):
                val = payload.get(key)
                if isinstance(val, list):
                    return [e for e in val if isinstance(e, dict)]
        return []

    @staticmethod
    def _extract_markets_list(payload: Any) -> list[dict[str, Any]]:
        """Return a flat list of market dicts from any Gamma response shape."""
        if isinstance(payload, list):
            return [m for m in payload if isinstance(m, dict)]
        if isinstance(payload, dict):
            for key in ("markets", "data", "results"):
                val = payload.get(key)
                if isinstance(val, list):
                    return [m for m in val if isinstance(m, dict)]
        return []

    def get_event_by_slug(self, slug: str) -> dict[str, Any] | None:
        """Try to fetch a Gamma *event* by slug (events contain multiple child markets)."""
        attempts: list[tuple[str, dict[str, Any] | None]] = [
            ("/events", {"slug": slug}),
            (f"/events/slug/{slug}", None),
        ]
        for path, params in attempts:
            try:
                payload = self.get_json(path, params=params)
                event = self._extract_event(payload, slug)
                if event:
                    return event
            except Exception:
                pass
        return None

    @staticmethod
    def _extract_event(payload: Any, slug: str) -> dict[str, Any] | None:
        """Pull a single event dict from various Gamma response shapes."""
        if isinstance(payload, dict):
            if payload.get("slug") == slug:
                return payload
            for key in ("events", "data", "results"):
                val = payload.get(key)
                if isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict) and item.get("slug") == slug:
                            return item
                    # If only one entry, return it regardless of slug match
                    if len(val) == 1 and isinstance(val[0], dict):
                        return val[0]
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict) and item.get("slug") == slug:
                    return item
            if len(payload) == 1 and isinstance(payload[0], dict):
                return payload[0]
        return None

    @staticmethod
    def extract_child_slugs(market_or_event: dict[str, Any]) -> list[str]:
        """Return child market slugs from a market or event payload.

        Checks several field shapes used by Gamma:
        - ``markets``       list of market dicts (event payload)
        - ``children``      list of market dicts or slug strings
        - ``subMarkets``    same
        - ``series``        same
        """
        slugs: list[str] = []
        for field in ("markets", "children", "subMarkets", "series"):
            entries = market_or_event.get(field)
            if not isinstance(entries, list) or not entries:
                continue
            for entry in entries:
                if isinstance(entry, str):
                    slugs.append(entry)
                elif isinstance(entry, dict):
                    s = entry.get("slug") or entry.get("market_slug") or entry.get("marketSlug")
                    if s:
                        slugs.append(str(s))
        # deduplicate preserving order
        seen: set[str] = set()
        return [s for s in slugs if not (s in seen or seen.add(s))]  # type: ignore[func-returns-value]

    @staticmethod
    def condition_id(market: dict[str, Any]) -> str:
        return str(
            market.get("condition_id")
            or market.get("conditionId")
            or market.get("id")
            or ""
        )
