from __future__ import annotations

import argparse
from pathlib import Path

from polymarket_ingestion.collector import MarketDataCollector
from polymarket_ingestion.config import Settings
from polymarket_ingestion.market_listener import MarketUpdateListener


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
    parser.add_argument("--output-root", default="data", help="Output root folder")
    parser.add_argument("--watch", action="store_true", help="Continuously refresh data for slugs")
    parser.add_argument("--interval-minutes", type=float, default=5.0, help="Polling interval in minutes")
    parser.add_argument("--cycles", type=int, default=None, help="Number of cycles in watch mode")
    args = parser.parse_args()

    slugs = _parse_slugs(args.slug, args.slugs, args.slugs_file)
    if not slugs:
        raise SystemExit("Provide at least one slug via --slug, --slugs or --slugs-file")

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
    for slug in slugs:
        result = collector.collect(slug)
        total_success += 1
        print(
            f"Done: slug={result.slug} folder={result.output_folder} "
            f"price_rows={result.price_rows} trade_rows={result.trade_rows} volume_rows={result.volume_rows}"
        )
    print(f"Completed one-shot updates for {total_success} slug(s).")


if __name__ == "__main__":
    main()
