from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np


FeatureFunction = Callable[[dict, dict], float]


@dataclass(frozen=True)
class FeatureBuilder:
    """Composable feature builder for arbitrary feature pipelines."""

    feature_names: tuple[str, ...]
    feature_functions: tuple[FeatureFunction, ...]

    def build(self, market: dict, signals: dict) -> np.ndarray:
        values = [fn(market, signals) for fn in self.feature_functions]
        return np.asarray(values, dtype=float)


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


def default_feature_builder() -> FeatureBuilder:
    """Default feature set. Extend or replace for more complex learning rules."""

    def best_bid(_m: dict, s: dict) -> float:
        return float(s.get("best_bid", 0.5))

    def best_ask(_m: dict, s: dict) -> float:
        return float(s.get("best_ask", 0.5))

    def mid(_m: dict, s: dict) -> float:
        bid = float(s.get("best_bid", 0.5))
        ask = float(s.get("best_ask", 0.5))
        return (bid + ask) / 2

    def spread(_m: dict, s: dict) -> float:
        return float(s.get("best_ask", 0.5)) - float(s.get("best_bid", 0.5))

    def rel_spread(_m: dict, s: dict) -> float:
        m = mid(_m, s)
        return _safe_div(spread(_m, s), m)

    def log_volume(_m: dict, s: dict) -> float:
        return float(np.log1p(float(s.get("volume_24h", 0.0))))

    def log_oi(_m: dict, s: dict) -> float:
        return float(np.log1p(float(s.get("open_interest", 0.0))))

    def news_sentiment(_m: dict, s: dict) -> float:
        return float(s.get("news_sentiment_score", 0.0))

    def social_momentum(_m: dict, s: dict) -> float:
        return float(s.get("social_momentum_score", 0.0))

    def days_to_resolution(m: dict, _s: dict) -> float:
        return float(np.log1p(float(m.get("days_to_resolution", 30.0))))

    names = (
        "best_bid",
        "best_ask",
        "mid",
        "spread",
        "relative_spread",
        "log_volume_24h",
        "log_open_interest",
        "news_sentiment",
        "social_momentum",
        "log_days_to_resolution",
    )
    fns = (
        best_bid,
        best_ask,
        mid,
        spread,
        rel_spread,
        log_volume,
        log_oi,
        news_sentiment,
        social_momentum,
        days_to_resolution,
    )
    return FeatureBuilder(feature_names=names, feature_functions=fns)
