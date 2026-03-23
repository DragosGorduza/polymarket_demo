from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from polymarket_pricing.features import FeatureBuilder
from polymarket_pricing.models import LearningRule


@dataclass(frozen=True)
class SignalConfig:
    min_edge: float = 0.03
    min_confidence: float = 0.40
    max_spread: float = 0.05


@dataclass(frozen=True)
class TradingSignal:
    condition_id: str
    direction: str
    p_model: float
    p_market: float
    edge: float
    confidence: float
    recommended_action: str
    outcome_yes: str = "UP"
    outcome_no: str = "DOWN"


class SignalEngine:
    """Model + features + decision logic => trading signal."""

    def __init__(self, feature_builder: FeatureBuilder, learning_rule: LearningRule, config: SignalConfig | None = None):
        self.feature_builder = feature_builder
        self.learning_rule = learning_rule
        self.config = config or SignalConfig()

    def fit(self, rows: list[tuple[dict, dict, float]]) -> "SignalEngine":
        """
        rows: list of (market, signals, y_yes)
        where y_yes is 1.0 for YES/UP resolution, 0.0 otherwise.
        """
        if not rows:
            raise ValueError("Training rows are empty")

        X = np.vstack([self.feature_builder.build(m, s) for (m, s, _y) in rows])
        y = np.asarray([float(y_yes) for (_m, _s, y_yes) in rows], dtype=float)
        self.learning_rule.fit(X, y)
        return self

    def predict_yes_probability(self, market: dict, signals: dict) -> float:
        X = self.feature_builder.build(market, signals).reshape(1, -1)
        return float(self.learning_rule.predict_yes_probability(X)[0])

    def compute_signal(self, market: dict, signals: dict) -> TradingSignal:
        p_model = self.predict_yes_probability(market, signals)

        best_bid = float(signals["best_bid"])
        best_ask = float(signals["best_ask"])
        mid = (best_bid + best_ask) / 2.0
        spread = best_ask - best_bid

        edge_yes = p_model - best_ask
        edge_no = (1.0 - p_model) - (1.0 - best_bid)

        liq_score = min(1.0, float(signals.get("volume_24h", 0.0)) / 5000.0)
        confidence = float(max(0.0, (1.0 - spread / 0.10) * liq_score))

        yes_label = str(signals.get("yes_outcome", "UP")).upper()
        no_label = str(signals.get("no_outcome", "DOWN")).upper()

        if spread > self.config.max_spread or confidence < self.config.min_confidence:
            return TradingSignal(
                condition_id=str(market.get("condition_id", market.get("conditionId", ""))),
                direction="NONE",
                p_model=p_model,
                p_market=mid,
                edge=max(edge_yes, edge_no),
                confidence=confidence,
                recommended_action="PASS",
                outcome_yes=yes_label,
                outcome_no=no_label,
            )

        if edge_yes > edge_no and edge_yes > self.config.min_edge:
            return TradingSignal(
                condition_id=str(market.get("condition_id", market.get("conditionId", ""))),
                direction=yes_label,
                p_model=p_model,
                p_market=mid,
                edge=edge_yes,
                confidence=confidence,
                recommended_action=f"BUY_{yes_label}",
                outcome_yes=yes_label,
                outcome_no=no_label,
            )

        if edge_no > self.config.min_edge:
            return TradingSignal(
                condition_id=str(market.get("condition_id", market.get("conditionId", ""))),
                direction=no_label,
                p_model=p_model,
                p_market=mid,
                edge=edge_no,
                confidence=confidence,
                recommended_action=f"BUY_{no_label}",
                outcome_yes=yes_label,
                outcome_no=no_label,
            )

        return TradingSignal(
            condition_id=str(market.get("condition_id", market.get("conditionId", ""))),
            direction="NONE",
            p_model=p_model,
            p_market=mid,
            edge=max(edge_yes, edge_no),
            confidence=confidence,
            recommended_action="PASS",
            outcome_yes=yes_label,
            outcome_no=no_label,
        )
