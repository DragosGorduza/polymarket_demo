from __future__ import annotations

import argparse

from polymarket_pricing.backtesting import backtest_slug, save_backtest_outputs


def main() -> None:
    parser = argparse.ArgumentParser(description="Run pricing strategies backtest for a slug")
    parser.add_argument("--slug", required=True, help="Market slug")
    parser.add_argument("--data-root", default="data", help="Root folder containing slug data")
    parser.add_argument("--train-ratio", type=float, default=0.7, help="Train split ratio")
    args = parser.parse_args()

    report, curves, first_test_step = backtest_slug(slug=args.slug, data_root=args.data_root, train_ratio=args.train_ratio)
    out_dir = save_backtest_outputs(
        slug=args.slug,
        report=report,
        curves=curves,
        first_test_step=first_test_step,
        data_root=args.data_root,
    )

    print("First test-step input:")
    print(first_test_step.to_string(index=False))
    print(report.to_string(index=False))
    print(f"Saved backtest outputs to: {out_dir}")


if __name__ == "__main__":
    main()
