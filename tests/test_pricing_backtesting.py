from __future__ import annotations

from pathlib import Path

import pandas as pd

from polymarket_pricing.backtesting import backtest_slug, save_backtest_outputs


def _make_slug_price_history(base: Path, slug: str) -> Path:
    slug_dir = base / slug
    slug_dir.mkdir(parents=True, exist_ok=True)

    ts = pd.date_range("2026-03-23T15:55:00Z", periods=120, freq="5s")
    up = pd.Series(0.45 + (pd.Series(range(len(ts))) * 0.001)).clip(upper=0.75)
    down = 1.0 - up

    rows = []
    for t, p in zip(ts, up):
        rows.append({"ts": t.isoformat(), "token_id": "up_tok", "outcome": "UP", "price": float(p)})
    for t, p in zip(ts, down):
        rows.append({"ts": t.isoformat(), "token_id": "down_tok", "outcome": "DOWN", "price": float(p)})

    df = pd.DataFrame(rows).sort_values("ts")
    out_path = slug_dir / "outcomes_price_history.csv"
    df.to_csv(out_path, index=False)
    return out_path


def test_backtest_slug_generates_strategy_report_and_curves(tmp_path: Path):
    slug = "test-slug"
    _make_slug_price_history(tmp_path / "data", slug)

    report, curves, first_test_step = backtest_slug(slug=slug, data_root=tmp_path / "data")

    assert not report.empty
    assert set(["slug", "strategy", "total_pnl", "sharpe_like"]).issubset(report.columns)
    assert len(curves) >= 4
    assert "black_scholes_probability" in report["strategy"].values
    assert not first_test_step.empty
    assert set(["ts", "up_price", "down_price", "step"]).issubset(first_test_step.columns)
    for name, curve in curves.items():
        assert not curve.empty, name
        assert set(["position", "step_pnl", "cum_pnl"]).issubset(curve.columns), name


def test_save_backtest_outputs_writes_files(tmp_path: Path):
    slug = "test-slug-2"
    _make_slug_price_history(tmp_path / "data", slug)
    report, curves, first_test_step = backtest_slug(slug=slug, data_root=tmp_path / "data")

    out_dir = save_backtest_outputs(
        slug=slug,
        report=report,
        curves=curves,
        first_test_step=first_test_step,
        data_root=tmp_path / "data",
    )
    assert (out_dir / "strategy_performance.csv").exists()
    assert (out_dir / "strategy_performance.json").exists()
    assert (out_dir / "first_test_step_input.csv").exists()
    assert any(p.name.startswith("equity_curve_") for p in out_dir.iterdir())
