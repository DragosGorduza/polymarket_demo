from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
import json
from math import erf, sqrt, log

import numpy as np
import pandas as pd


class Strategy(Protocol):
    name: str

    def fit(self, frame: pd.DataFrame) -> "Strategy":
        ...

    def positions(self, frame: pd.DataFrame) -> pd.Series:
        ...


@dataclass
class StrategyPerformance:
    slug: str
    strategy: str
    total_pnl: float
    mean_pnl_per_step: float
    pnl_std_per_step: float
    sharpe_like: float
    n_steps: int


class AlwaysUpStrategy:
    name = "always_up"

    def fit(self, frame: pd.DataFrame) -> "AlwaysUpStrategy":
        _ = frame
        return self

    def positions(self, frame: pd.DataFrame) -> pd.Series:
        return pd.Series(1.0, index=frame.index)


class AlwaysDownStrategy:
    name = "always_down"

    def fit(self, frame: pd.DataFrame) -> "AlwaysDownStrategy":
        _ = frame
        return self

    def positions(self, frame: pd.DataFrame) -> pd.Series:
        return pd.Series(-1.0, index=frame.index)


class LinearRegressionDirectionStrategy:
    """Linear-regression strategy on lagged microstructure features."""

    name = "linear_regression_direction"

    def __init__(self, threshold: float = 0.0) -> None:
        self.threshold = threshold
        self.coef_: np.ndarray | None = None

    @staticmethod
    def _features(frame: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame(index=frame.index)
        out["up"] = frame["up_price"]
        out["down"] = frame["down_price"]
        out["spread_ud"] = frame["up_price"] - frame["down_price"]
        out["ret_up_1"] = frame["up_price"].pct_change().fillna(0.0)
        out["ret_down_1"] = frame["down_price"].pct_change().fillna(0.0)
        out["ret_up_3"] = frame["up_price"].pct_change(3).fillna(0.0)
        out["ret_down_3"] = frame["down_price"].pct_change(3).fillna(0.0)
        return out

    def fit(self, frame: pd.DataFrame) -> "LinearRegressionDirectionStrategy":
        feat = self._features(frame)
        y = frame["up_price"].shift(-1) - frame["up_price"]
        X = feat.iloc[:-1].to_numpy(dtype=float)
        yy = y.iloc[:-1].to_numpy(dtype=float)
        if len(X) == 0:
            self.coef_ = np.zeros(feat.shape[1] + 1, dtype=float)
            return self

        X_design = np.c_[np.ones(len(X)), X]
        beta, *_ = np.linalg.lstsq(X_design, yy, rcond=None)
        self.coef_ = beta
        return self

    def positions(self, frame: pd.DataFrame) -> pd.Series:
        if self.coef_ is None:
            raise RuntimeError("Strategy not fit")
        feat = self._features(frame).to_numpy(dtype=float)
        X_design = np.c_[np.ones(len(feat)), feat]
        y_hat = X_design @ self.coef_
        pos = np.where(y_hat > self.threshold, 1.0, np.where(y_hat < -self.threshold, -1.0, 0.0))
        return pd.Series(pos, index=frame.index)


class BlackScholesProbabilityStrategy:
    """
    Simple Black-Scholes-inspired baseline.

    We estimate a short-horizon sigma from rolling UP returns, compute a
    risk-neutral call probability proxy N(d2), and trade when that implied
    probability diverges from market UP price.
    """

    name = "black_scholes_probability"

    def __init__(self, edge_threshold: float = 0.01):
        self.edge_threshold = edge_threshold

    @staticmethod
    def _norm_cdf(x: float) -> float:
        return 0.5 * (1.0 + erf(x / sqrt(2.0)))

    def fit(self, frame: pd.DataFrame) -> "BlackScholesProbabilityStrategy":
        _ = frame
        return self

    def positions(self, frame: pd.DataFrame) -> pd.Series:
        up = frame["up_price"].clip(1e-6, 1 - 1e-6)
        ret = np.log(up).diff().fillna(0.0)
        sigma = ret.rolling(24, min_periods=5).std(ddof=1).fillna(ret.std(ddof=1))
        sigma = sigma.replace(0.0, 1e-4).fillna(1e-4)

        dt_years = 5.0 / (365.0 * 24.0 * 3600.0)
        sqrt_t = sqrt(dt_years)

        # Synthetic underlying/strike setup for a simple binary proxy.
        S = up
        K = 0.5
        r = 0.0

        d2 = (np.log(np.maximum(S, 1e-6) / K) + (r - 0.5 * sigma**2) * dt_years) / (sigma * sqrt_t)
        bs_prob_up = d2.apply(self._norm_cdf).clip(0.001, 0.999)
        edge = bs_prob_up - up

        pos = np.where(edge > self.edge_threshold, 1.0, np.where(edge < -self.edge_threshold, -1.0, 0.0))
        return pd.Series(pos, index=frame.index)


def load_slug_price_frame(slug: str, data_root: str | Path = "data") -> pd.DataFrame:
    p = Path(data_root) / slug / "outcomes_price_history.csv"
    if not p.exists():
        raise FileNotFoundError(f"Missing price history for slug={slug}: {p}")

    df = pd.read_csv(p)
    if df.empty:
        raise ValueError(f"No rows in {p}")

    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    df["outcome"] = df["outcome"].astype(str).str.upper()
    df = df.dropna(subset=["ts", "price"]).sort_values("ts")

    outcomes = list(df["outcome"].dropna().unique())
    if len(outcomes) < 2:
        raise ValueError("Need at least two outcomes for binary backtest")

    # Prefer common naming if available.
    up_name = "UP" if "UP" in outcomes else outcomes[0]
    down_name = "DOWN" if "DOWN" in outcomes else [o for o in outcomes if o != up_name][0]

    pivot = (
        df.pivot_table(index="ts", columns="outcome", values="price", aggfunc="last")
        .sort_index()
        .resample("5s")
        .last()
        .ffill()
        .dropna(subset=[up_name, down_name])
    )

    out = pd.DataFrame(
        {
            "up_price": pivot[up_name].astype(float),
            "down_price": pivot[down_name].astype(float),
        },
        index=pivot.index,
    )
    out.index.name = "ts"
    return out


def _evaluate_positions(frame: pd.DataFrame, positions: pd.Series) -> tuple[pd.DataFrame, dict[str, float]]:
    aligned_pos = positions.reindex(frame.index).fillna(0.0)

    d_up = frame["up_price"].diff().fillna(0.0)
    d_down = frame["down_price"].diff().fillna(0.0)

    # position=+1 -> long UP, position=-1 -> long DOWN
    step_pnl = np.where(aligned_pos >= 0.0, aligned_pos * d_up, (-aligned_pos) * d_down)
    curve = pd.DataFrame({"position": aligned_pos, "step_pnl": step_pnl}, index=frame.index)
    curve["cum_pnl"] = curve["step_pnl"].cumsum()

    std = float(curve["step_pnl"].std(ddof=1)) if len(curve) > 1 else 0.0
    mean = float(curve["step_pnl"].mean())
    sharpe_like = mean / std if std > 0 else 0.0
    metrics = {
        "total_pnl": float(curve["cum_pnl"].iloc[-1]) if not curve.empty else 0.0,
        "mean_pnl_per_step": mean,
        "pnl_std_per_step": std,
        "sharpe_like": float(sharpe_like),
        "n_steps": int(len(curve)),
    }
    return curve, metrics


def backtest_slug(
    slug: str,
    data_root: str | Path = "data",
    strategies: list[Strategy] | None = None,
    train_ratio: float = 0.7,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], pd.DataFrame]:
    frame = load_slug_price_frame(slug, data_root=data_root)
    n_train = max(10, int(len(frame) * train_ratio))
    train = frame.iloc[:n_train]
    test = frame.iloc[n_train:]
    if test.empty:
        raise ValueError("Not enough data after train/test split")

    if strategies is None:
        strategies = [
            AlwaysUpStrategy(),
            AlwaysDownStrategy(),
            LinearRegressionDirectionStrategy(),
            BlackScholesProbabilityStrategy(),
        ]

    rows: list[dict[str, float | str | int]] = []
    curves: dict[str, pd.DataFrame] = {}

    for strat in strategies:
        strat.fit(train)
        pos = strat.positions(test)
        curve, metrics = _evaluate_positions(test, pos)
        curves[strat.name] = curve
        rows.append(
            {
                "slug": slug,
                "strategy": strat.name,
                **metrics,
            }
        )

    report = pd.DataFrame(rows).sort_values("total_pnl", ascending=False).reset_index(drop=True)
    first_test_step = test.head(1).copy()
    first_test_step["step"] = "first_test_input"
    first_test_step = first_test_step.reset_index().rename(columns={"index": "ts"})
    return report, curves, first_test_step


def save_backtest_outputs(
    slug: str,
    report: pd.DataFrame,
    curves: dict[str, pd.DataFrame],
    first_test_step: pd.DataFrame,
    data_root: str | Path = "data",
) -> Path:
    out_dir = Path(data_root) / slug / "backtests"
    out_dir.mkdir(parents=True, exist_ok=True)

    report.to_csv(out_dir / "strategy_performance.csv", index=False)
    first_test_step.to_csv(out_dir / "first_test_step_input.csv", index=False)
    for name, curve in curves.items():
        curve.to_csv(out_dir / f"equity_curve_{name}.csv")

    payload = {
        "slug": slug,
        "first_test_step_input": first_test_step.to_dict(orient="records"),
        "strategies": report.to_dict(orient="records"),
    }
    (out_dir / "strategy_performance.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return out_dir
