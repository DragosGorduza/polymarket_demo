"""Scan all active Polymarket markets and persist a queryable CSV catalogue.

Output files (written to ``output_root/``):
    all_live_markets.csv      – full catalogue, one row per market
    all_live_market_slugs.txt – plain slug list, compatible with --slugs-file

CSV columns
-----------
market_name       Human-readable question / title
slug_name         URL slug – feed this directly to ``--slug``
condition_id      On-chain condition ID
clob_token_ids    JSON list of CLOB token IDs (one per outcome)
active            bool
accepting_orders  bool – True means the order book is open right now
outcomes          JSON list of outcome labels (e.g. ["Up","Down"])
neg_risk          bool
end_date          ISO-8601 resolution date
volume_24hr       USD volume in the last 24 h
tags              JSON list of tag label strings  (e.g. ["Crypto","Bitcoin"])
tag_slugs         JSON list of tag slug strings   (e.g. ["crypto","bitcoin"])
tag_ids           JSON list of tag id integers
event_slug        Slug of the parent event
series_slug       Recurring series slug if applicable
last_updated_utc  Timestamp of this scan run
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from polymarket_ingestion.clients.gamma_client import GammaClient
from polymarket_ingestion.config import Settings

CSV_COLUMNS = [
    "market_name",
    "slug_name",
    "condition_id",
    "clob_token_ids",
    "active",
    "accepting_orders",
    "outcomes",
    "neg_risk",
    "end_date",
    "volume_24hr",
    "tags",
    "tag_slugs",
    "tag_ids",
    "event_slug",
    "series_slug",
    "last_updated_utc",
]


# ── helpers ────────────────────────────────────────────────────────────────

def _parse_json_list(value: Any) -> list:
    """Safely coerce a value to a Python list (handles stringified JSON)."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
    return []


def _extract_tags(raw_tags: Any) -> tuple[list[str], list[str], list[int]]:
    """Parse a raw tags value into (labels, slugs, ids) lists.

    Gamma returns tags as a list of dicts::

        [{"id": 100381, "slug": "crypto", "label": "Crypto"}, ...]
    """
    if not isinstance(raw_tags, list):
        return [], [], []
    labels, slugs, ids = [], [], []
    for t in raw_tags:
        if not isinstance(t, dict):
            continue
        label = t.get("label") or t.get("name") or t.get("slug") or ""
        slug = t.get("slug") or ""
        tid = t.get("id")
        if label:
            labels.append(str(label))
        if slug:
            slugs.append(str(slug))
        if tid is not None:
            try:
                ids.append(int(tid))
            except (TypeError, ValueError):
                pass
    return labels, slugs, ids


def normalise_market_row(
    market: dict[str, Any],
    updated_at: str,
    event_tags: list | None = None,
    event_slug: str = "",
    series_slug: str = "",
) -> dict[str, Any]:
    """Convert a raw Gamma market dict into one CSV row.

    Parameters
    ----------
    market:
        Raw market dict from Gamma.
    updated_at:
        ISO timestamp string for ``last_updated_utc``.
    event_tags:
        Tags from the *parent event* (tags live at event level, not market level).
    event_slug:
        Slug of the parent event.
    series_slug:
        Recurring series slug (e.g. ``btc-up-or-down-5m``).
    """
    outcomes = _parse_json_list(market.get("outcomes", []))
    clob_token_ids = _parse_json_list(market.get("clobTokenIds", []))
    condition_id = str(
        market.get("conditionId")
        or market.get("condition_id")
        or market.get("id")
        or ""
    )

    # Tags: prefer market-level if present, fall back to parent event tags
    raw_tags = market.get("tags") or event_tags or []
    tag_labels, tag_slugs, tag_ids = _extract_tags(raw_tags)

    # series_slug: check market itself, then fall back to caller-supplied value
    resolved_series_slug = str(
        market.get("seriesSlug") or market.get("series_slug") or series_slug or ""
    )

    return {
        "market_name": str(
            market.get("question")
            or market.get("title")
            or market.get("slug")
            or ""
        ),
        "slug_name": str(market.get("slug") or ""),
        "condition_id": condition_id,
        "clob_token_ids": json.dumps(clob_token_ids),
        "active": bool(market.get("active", False)),
        "accepting_orders": bool(market.get("acceptingOrders", False)),
        "outcomes": json.dumps(outcomes),
        "neg_risk": bool(market.get("negRisk", False)),
        "end_date": str(market.get("endDate") or market.get("endDateIso") or ""),
        "volume_24hr": _safe_float(market.get("volume24hr") or market.get("volume24hrClob")),
        "tags": json.dumps(tag_labels),
        "tag_slugs": json.dumps(tag_slugs),
        "tag_ids": json.dumps(tag_ids),
        "event_slug": event_slug,
        "series_slug": resolved_series_slug,
        "last_updated_utc": updated_at,
    }


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# ── scanner ────────────────────────────────────────────────────────────────

class MarketScanner:
    """Fetches all active Polymarket markets and saves a queryable CSV catalogue."""

    def __init__(
        self,
        gamma_client: GammaClient,
        output_root: str | Path = "data",
    ) -> None:
        self.gamma = gamma_client
        self.output_root = Path(output_root)
        self.csv_path = self.output_root / "all_live_markets.csv"
        self.slugs_txt_path = self.output_root / "all_live_market_slugs.txt"

    @classmethod
    def from_settings(
        cls, settings: Settings, output_root: str | Path = "data"
    ) -> "MarketScanner":
        return cls(
            gamma_client=GammaClient(
                settings.gamma_base_url,
                timeout_seconds=settings.request_timeout_seconds,
                user_agent=settings.user_agent,
            ),
            output_root=output_root,
        )

    def _build_tag_lookup(self) -> dict[str, dict[str, Any]]:
        """Fetch all tags and return two merged lookups keyed by both id and slug.

        Result: ``{str(id): tag_dict, slug: tag_dict, ...}``
        so callers can resolve tags regardless of whether they have an id or slug.
        """
        raw_tags = self.gamma.get_tags()
        lookup: dict[str, dict[str, Any]] = {}
        for tag in raw_tags:
            if not isinstance(tag, dict):
                continue
            tid = tag.get("id")
            tslug = tag.get("slug")
            if tid is not None:
                lookup[str(tid)] = tag
            if tslug:
                lookup[str(tslug)] = tag
        return lookup

    def scan(self, verbose: bool = True) -> pd.DataFrame:
        """Fetch every active market, build the catalogue, overwrite CSV files.

        Tags are resolved via ``GET /tags`` (fetched once) and then matched
        against each market's embedded tag references (id or slug).
        Returns the full DataFrame so callers can filter / groupby freely.
        """
        updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if verbose:
            print("[SCANNER] Fetching all tags from Gamma API …")
        tag_lookup = self._build_tag_lookup()   # id/slug → {id, label, slug}
        if verbose:
            distinct = {v["slug"] for v in tag_lookup.values() if v.get("slug")}
            print(f"[SCANNER] {len(distinct)} distinct tags loaded")

        if verbose:
            print("[SCANNER] Fetching all active markets from Gamma API …")

        raw_markets = self.gamma.get_all_active_markets()

        if verbose:
            print(f"[SCANNER] {len(raw_markets)} raw market records received")

        rows: list[dict[str, Any]] = []
        for market in raw_markets:
            # Pull event context from the first embedded event
            event_list = market.get("events") or []
            first_event: dict[str, Any] = (
                event_list[0]
                if isinstance(event_list, list) and event_list and isinstance(event_list[0], dict)
                else {}
            )
            event_slug = str(first_event.get("slug") or market.get("slug") or "")

            # series_slug from the first series entry inside the event
            series_list = first_event.get("series") or []
            series_slug = ""
            if isinstance(series_list, list) and series_list:
                first_series = series_list[0]
                if isinstance(first_series, dict):
                    series_slug = str(first_series.get("slug") or "")

            # Resolve tags: event may carry [{"id":"123","slug":"crypto",...}],
            # or just ids, or nothing — hydrate everything through the lookup.
            raw_event_tags = first_event.get("tags") or market.get("tags") or []
            resolved_tags: list[dict[str, Any]] = []
            if isinstance(raw_event_tags, list):
                for t in raw_event_tags:
                    if isinstance(t, dict):
                        # Try to hydrate via lookup (lookup may have richer data)
                        key = str(t.get("id") or t.get("slug") or "")
                        hydrated = tag_lookup.get(key, t)
                        resolved_tags.append(hydrated)
                    elif isinstance(t, (str, int)):
                        # bare id or slug reference
                        hydrated = tag_lookup.get(str(t))
                        if hydrated:
                            resolved_tags.append(hydrated)

            rows.append(
                normalise_market_row(
                    market,
                    updated_at,
                    event_tags=resolved_tags,
                    event_slug=event_slug,
                    series_slug=series_slug,
                )
            )

        # Deduplicate by slug_name, keeping the last occurrence
        seen: dict[str, dict[str, Any]] = {}
        for row in rows:
            slug = row["slug_name"]
            if slug:
                seen[slug] = row

        df = pd.DataFrame(list(seen.values()), columns=CSV_COLUMNS)
        df = (
            df.sort_values(
                ["accepting_orders", "active", "volume_24hr"],
                ascending=[False, False, False],
            )
            .reset_index(drop=True)
        )

        self.output_root.mkdir(parents=True, exist_ok=True)

        # ── Write full CSV ──────────────────────────────────────────────────
        df.to_csv(self.csv_path, index=False)

        # ── Write plain slug list (compatible with --slugs-file) ────────────
        slugs_for_txt = df["slug_name"].dropna().tolist()
        self.slugs_txt_path.write_text(
            "\n".join(slugs_for_txt) + "\n", encoding="utf-8"
        )

        if verbose:
            n_accepting = int(df["accepting_orders"].sum())
            n_active = int(df["active"].sum())
            tag_counts = (
                df["tags"].dropna()
                .apply(lambda x: json.loads(x) if isinstance(x, str) else x)
                .explode()
                .value_counts()
            )
            top_tags = tag_counts.head(8).to_dict()
            print(
                f"[SCANNER] Saved {len(df)} markets → {self.csv_path}\n"
                f"          accepting_orders={n_accepting}  active={n_active}\n"
                f"          top tags: {top_tags}\n"
                f"          slug list        → {self.slugs_txt_path}"
            )

        return df
