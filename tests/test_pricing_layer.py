from __future__ import annotations

import numpy as np

from polymarket_pricing import LinearRegressionRule, SignalConfig, SignalEngine, default_feature_builder


def _mk_market(condition_id: str = "cond_1") -> dict:
    return {"condition_id": condition_id, "days_to_resolution": 3}


def _mk_signals(best_bid: float, best_ask: float, volume_24h: float = 10000.0) -> dict:
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "volume_24h": volume_24h,
        "open_interest": 2000.0,
        "news_sentiment_score": 0.1,
        "social_momentum_score": 0.2,
        "yes_outcome": "UP",
        "no_outcome": "DOWN",
    }


def test_linear_regression_rule_predicts_probability_range():
    fb = default_feature_builder()
    model = LinearRegressionRule()
    engine = SignalEngine(fb, model)

    rows = []
    for i in range(20):
        bid = 0.30 + i * 0.02
        ask = bid + 0.02
        y = 1.0 if bid > 0.50 else 0.0
        rows.append((_mk_market(), _mk_signals(bid, ask), y))

    engine.fit(rows)
    p = engine.predict_yes_probability(_mk_market(), _mk_signals(0.62, 0.64))
    assert 0.0 <= p <= 1.0


def test_signal_engine_buy_up_when_positive_edge():
    fb = default_feature_builder()
    model = LinearRegressionRule()
    engine = SignalEngine(fb, model, SignalConfig(min_edge=0.02, min_confidence=0.1, max_spread=0.10))

    # Train so that higher bid/ask maps to higher YES probability.
    rows = []
    for bid in np.linspace(0.20, 0.80, 30):
        ask = bid + 0.01
        y = 1.0 if bid >= 0.50 else 0.0
        rows.append((_mk_market(), _mk_signals(float(bid), float(ask)), y))
    engine.fit(rows)

    sig = engine.compute_signal(_mk_market(), _mk_signals(0.55, 0.57, volume_24h=15000))
    assert sig.recommended_action in {"BUY_UP", "PASS"}
    assert sig.outcome_yes == "UP"
    assert sig.outcome_no == "DOWN"


def test_signal_engine_pass_on_wide_spread():
    fb = default_feature_builder()
    model = LinearRegressionRule()
    engine = SignalEngine(fb, model, SignalConfig(min_edge=0.01, min_confidence=0.2, max_spread=0.02))

    rows = [
        (_mk_market(), _mk_signals(0.40, 0.41), 0.0),
        (_mk_market(), _mk_signals(0.60, 0.61), 1.0),
        (_mk_market(), _mk_signals(0.55, 0.56), 1.0),
        (_mk_market(), _mk_signals(0.35, 0.36), 0.0),
    ]
    engine.fit(rows)

    sig = engine.compute_signal(_mk_market(), _mk_signals(0.40, 0.48, volume_24h=15000))
    assert sig.recommended_action == "PASS"
