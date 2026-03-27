"""CLI entry point for the live-market scanner.

Usage
-----
Scan and save catalogue to data/:

    python -m polymarket_ingestion.scan_cli

Custom output folder:

    python -m polymarket_ingestion.scan_cli --output-root /tmp/poly

Filter to markets accepting orders right now, then collect them:

    python -m polymarket_ingestion.scan_cli
    python -m polymarket_ingestion.cli --slugs-file data/all_live_market_slugs.txt

Show only the top-N by volume (preview):

    python -m polymarket_ingestion.scan_cli --top 20
"""
from __future__ import annotations

import argparse

from polymarket_ingestion.config import Settings
from polymarket_ingestion.market_scanner import MarketScanner


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan all live Polymarket markets and save a queryable CSV catalogue"
    )
    parser.add_argument(
        "--output-root",
        default="data",
        help="Root folder where all_live_markets.csv is written (default: data/)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        metavar="N",
        help="Print the top-N markets by 24h volume after scanning",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output",
    )
    args = parser.parse_args()

    scanner = MarketScanner.from_settings(Settings(), output_root=args.output_root)
    df = scanner.scan(verbose=not args.quiet)

    if not args.quiet:
        n = args.top or 10
        print(
            f"\nTop {n} markets by 24h volume:\n"
            + df[["market_name", "slug_name", "accepting_orders", "volume_24hr"]]
            .head(n)
            .to_string(index=False)
        )
        print(
            f"\nTo collect data for a single slug:\n"
            f"  python -m polymarket_ingestion.cli --slug <slug_name>\n"
            f"\nTo collect data for ALL live markets:\n"
            f"  python -m polymarket_ingestion.cli --slugs-file {scanner.slugs_txt_path}\n"
            f"\nTo collect only markets accepting orders right now, filter the CSV first:\n"
            f"  python -c \"\n"
            f"  import pandas as pd; df=pd.read_csv('{scanner.csv_path}');\n"
            f"  df[df.accepting_orders]['slug_name'].to_csv('active_slugs.txt',index=False,header=False)\n"
            f"  \"\n"
            f"  python -m polymarket_ingestion.cli --slugs-file active_slugs.txt"
        )


if __name__ == "__main__":
    main()
