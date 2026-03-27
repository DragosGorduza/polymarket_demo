from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from polymarket_ingestion.collector import MarketDataCollector
from polymarket_ingestion.config import Settings
from polymarket_ingestion.market_listener import MarketUpdateListener


def _slugs_from_scan_csv(csv_path: str, accepting_orders_only: bool = False) -> list[str]:
    """Read slug_name column from a all_live_markets.csv produced by scan_cli."""
    df = pd.read_csv(csv_path)
    if "slug_name" not in df.columns:
        raise SystemExit(f"--scan-csv: '{csv_path}' has no 'slug_name' column")
    if accepting_orders_only and "accepting_orders" in df.columns:
        df = df[df["accepting_orders"].astype(bool)]
    return df["slug_name"].dropna().astype(str).tolist()


def _parse_slugs(single_slug: str | None, csv_slugs: str | None, slugs_file: str | None) -> list[str]:
    out: list[str] = []

    if single_slug:
        out.append(single_slug.strip())

    if csv_slugs:
        out.extend(x.strip() for x in csv_slugs.split(",") if x.strip())

    if slugs_file:
        for line in Path(slugs_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append(line)

    deduped: list[str] = []
    seen = set()
    for slug in out:
        if slug not in seen:
            deduped.append(slug)
            seen.add(slug)
    return deduped


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect or watch Polymarket market data for one or many slugs")
    parser.add_argument("--slug", help="Single market slug")
    parser.add_argument("--slugs", help="Comma-separated list of market slugs")
    parser.add_argument("--slugs-file", help="Path to newline-separated slugs file")
    parser.add_argument(
        "--scan-csv",
        help="Path to all_live_markets.csv from scan_cli – collects every slug in it",
    )
    parser.add_argument(
        "--accepting-orders-only",
        action="store_true",
        help="When using --scan-csv, only collect markets with accepting_orders=True",
    )
    parser.add_argument("--output-root", default="data", help="Output root folder")
    parser.add_argument("--watch", action="store_true", help="Continuously refresh data for slugs")
    parser.add_argument("--interval-minutes", type=float, default=5.0, help="Polling interval in minutes")
    parser.add_argument("--cycles", type=int, default=None, help="Number of cycles in watch mode")
    args = parser.parse_args()

    slugs = _parse_slugs(args.slug, args.slugs, args.slugs_file)
    if args.scan_csv:
        csv_slugs = _slugs_from_scan_csv(args.scan_csv, accepting_orders_only=args.accepting_orders_only)
        seen = set(slugs)
        for s in csv_slugs:
            if s not in seen:
                slugs.append(s)
                seen.add(s)

    if not slugs:
        raise SystemExit(
            "Provide at least one slug via --slug, --slugs, --slugs-file, or --scan-csv"
        )

    collector = MarketDataCollector.from_settings(Settings(), output_root=args.output_root)

    if args.watch:
        listener = MarketUpdateListener(
            collector=collector,
            interval_seconds=max(1, int(args.interval_minutes * 60)),
        )
        summary = listener.run(slugs=slugs, cycles=args.cycles)
        print(
            f"Done watch: cycles={summary.total_cycles} "
            f"success={summary.total_success} failed={summary.total_failed}"
        )
        return

    total_success = 0
    total_failed = 0
    all_submarket_slugs: list[str] = []

    for slug in slugs:
        try:
            result = collector.collect(slug)
            total_success += 1
            sub_note = ""
            if result.submarket_slugs:
                all_submarket_slugs.extend(result.submarket_slugs)
                sub_note = f" | sub-markets={len(result.submarket_slugs)}"
            print(
                f"Done: slug={result.slug} folder={result.output_folder}"
                f" price_rows={result.price_rows} trade_rows={result.trade_rows}"
                f" volume_rows={result.volume_rows}{sub_note}"
            )
        except Exception as exc:
            total_failed += 1
            print(f"[ERROR] slug='{slug}' failed: {type(exc).__name__}: {exc}")
            # Still try to discover sub-markets so they can be re-queued
            try:
                sub_slugs = _discover_submarket_slugs(collector, slug, args.output_root)
                if sub_slugs:
                    all_submarket_slugs.extend(sub_slugs)
                    print(
                        f"  → found {len(sub_slugs)} sub-market(s) for '{slug}', "
                        f"saved to {args.output_root}/{slug}/submarket_slugs.json"
                    )
            except Exception as sub_exc:
                print(f"  → sub-market discovery also failed: {sub_exc}")

    # Write a consolidated pending-slugs file at the output root level
    if all_submarket_slugs:
        pending_path = Path(args.output_root) / "pending_submarket_slugs.json"
        existing: list[str] = []
        if pending_path.exists():
            try:
                existing = json.loads(pending_path.read_text(encoding="utf-8")).get("slugs", [])
            except Exception:
                pass
        merged = list(dict.fromkeys(existing + all_submarket_slugs))  # deduplicate, preserve order
        pending_path.write_text(
            json.dumps({"count": len(merged), "slugs": merged}, indent=2), encoding="utf-8"
        )
        print(
            f"\n{len(all_submarket_slugs)} sub-market slug(s) queued → {pending_path}\n"
            f"Re-run with:  python -m polymarket_ingestion.cli --slugs-file {pending_path}"
        )

    print(f"Completed one-shot updates: success={total_success} failed={total_failed}.")


def _discover_submarket_slugs(
    collector: MarketDataCollector, slug: str, output_root: str
) -> list[str]:
    """Best-effort sub-market discovery when the main collect() call has failed."""
    sub_slugs: list[str] = []

    # 1. Try Gamma market endpoint
    try:
        market = collector.gamma.get_market_by_slug(slug)
        sub_slugs = collector.gamma.extract_child_slugs(market)
    except Exception:
        pass

    # 2. Try Gamma event endpoint
    if not sub_slugs:
        try:
            event = collector.gamma.get_event_by_slug(slug)
            if event:
                sub_slugs = collector.gamma.extract_child_slugs(event)
        except Exception:
            pass

    if sub_slugs:
        folder = Path(output_root) / slug
        folder.mkdir(parents=True, exist_ok=True)
        payload = {"parent_slug": slug, "count": len(sub_slugs), "submarket_slugs": sub_slugs}
        (folder / "submarket_slugs.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    return sub_slugs


if __name__ == "__main__":
    main()
