from __future__ import annotations

from typing import Any

import pandas as pd

from polymarket_ingestion.clients.base import BaseApiClient


class DataApiClient(BaseApiClient):
    """Client for historical market data. Endpoint names can vary by deployment."""

    def get_price_history(
        self,
        slug: str,
        condition_id: str,
        token_map: dict[str, str],
        max_points: int = 20000,
    ) -> pd.DataFrame:
        attempts: list[tuple[str, dict[str, Any]]] = []

        if condition_id:
            attempts.extend(
                [
                    ("/prices-history", {"market": condition_id, "limit": max_points}),
                    ("/price-history", {"market": condition_id, "limit": max_points}),
                ]
            )
        attempts.extend(
            [
                ("/prices-history", {"slug": slug, "limit": max_points}),
                ("/price-history", {"slug": slug, "limit": max_points}),
            ]
        )

        for path, params in attempts:
            try:
                payload = self.get_json(path, params=params)
                df = self._normalize_price_payload(payload, token_map)
                if not df.empty:
                    return df
            except Exception:
                continue

        return pd.DataFrame(columns=["ts", "token_id", "outcome", "price"])

    def get_trade_activity(
        self,
        slug: str,
        condition_id: str,
        max_pages: int = 20,
        page_size: int = 500,
    ) -> list[dict[str, Any]]:
        attempts: list[tuple[str, dict[str, Any]]] = []
        if condition_id:
            attempts.extend(
                [
                    ("/trades", {"market": condition_id, "limit": page_size}),
                    ("/activity", {"market": condition_id, "limit": page_size}),
                ]
            )
        attempts.extend(
            [
                ("/trades", {"slug": slug, "limit": page_size}),
                ("/activity", {"slug": slug, "limit": page_size}),
            ]
        )

        for path, base_params in attempts:
            try:
                rows = self._fetch_paginated_trades(path, base_params, max_pages=max_pages)
                if rows:
                    return rows
            except Exception:
                continue

        return []

    def _fetch_paginated_trades(
        self,
        path: str,
        base_params: dict[str, Any],
        max_pages: int,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        cursor: str | None = None

        for _ in range(max_pages):
            params = dict(base_params)
            if cursor:
                params["next_cursor"] = cursor

            payload = self.get_json(path, params=params)
            rows, next_cursor = self._extract_trade_rows(payload)
            if not rows:
                break

            out.extend(rows)
            cursor = next_cursor
            if not cursor:
                break

        return out

    @staticmethod
    def _extract_trade_rows(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)], None

        if isinstance(payload, dict):
            rows: list[dict[str, Any]] = []
            for key in ("trades", "activity", "data", "results"):
                maybe = payload.get(key)
                if isinstance(maybe, list):
                    rows = [x for x in maybe if isinstance(x, dict)]
                    break
            cursor = payload.get("next_cursor") or payload.get("cursor")
            return rows, cursor

        return [], None

    @staticmethod
    def _parse_ts(value: Any) -> pd.Timestamp:
        if value is None:
            return pd.NaT

        if isinstance(value, (int, float)):
            v = float(value)
            av = abs(v)
            if av > 1e14:
                return pd.to_datetime(v, unit="ns", utc=True, errors="coerce")
            if av > 1e11:
                return pd.to_datetime(v, unit="ms", utc=True, errors="coerce")
            return pd.to_datetime(v, unit="s", utc=True, errors="coerce")

        if isinstance(value, str) and value.strip().isdigit():
            return DataApiClient._parse_ts(int(value.strip()))

        return pd.to_datetime(value, utc=True, errors="coerce")

    @staticmethod
    def _normalize_price_payload(payload: Any, token_map: dict[str, str]) -> pd.DataFrame:
        token_to_outcome = {v: k for k, v in token_map.items()}

        rows: list[dict[str, Any]] = []

        def _add_row(item: dict[str, Any]) -> None:
            ts = item.get("timestamp") or item.get("ts") or item.get("time")
            price = item.get("price") or item.get("p")
            token_id = str(item.get("token_id") or item.get("tokenId") or item.get("asset_id") or "")
            outcome = item.get("outcome") or token_to_outcome.get(token_id, "UNKNOWN")
            if ts is None or price is None:
                return
            rows.append({"ts": ts, "token_id": token_id, "outcome": outcome, "price": float(price)})

        if isinstance(payload, list):
            for x in payload:
                if isinstance(x, dict):
                    _add_row(x)
        elif isinstance(payload, dict):
            for key in ("prices", "data", "results"):
                val = payload.get(key)
                if isinstance(val, list):
                    for x in val:
                        if isinstance(x, dict):
                            _add_row(x)
                    break

        if not rows:
            return pd.DataFrame(columns=["ts", "token_id", "outcome", "price"])

        df = pd.DataFrame(rows)
        df["ts"] = df["ts"].apply(DataApiClient._parse_ts)
        df = df.dropna(subset=["ts", "price"]).sort_values("ts")
        return df[["ts", "token_id", "outcome", "price"]].reset_index(drop=True)
