"""Pricing layer for model-driven Polymarket trading signals."""

from polymarket_pricing.features import FeatureBuilder, default_feature_builder
from polymarket_pricing.backtesting import (
    AlwaysDownStrategy,
    AlwaysUpStrategy,
    BlackScholesProbabilityStrategy,
    LinearRegressionDirectionStrategy,
    backtest_slug,
    save_backtest_outputs,
)
from polymarket_pricing.models import LearningRule, LinearRegressionRule
from polymarket_pricing.signals import SignalConfig, SignalEngine, TradingSignal

__all__ = [
    "FeatureBuilder",
    "default_feature_builder",
    "LearningRule",
    "LinearRegressionRule",
    "AlwaysUpStrategy",
    "AlwaysDownStrategy",
    "BlackScholesProbabilityStrategy",
    "LinearRegressionDirectionStrategy",
    "backtest_slug",
    "save_backtest_outputs",
    "SignalConfig",
    "SignalEngine",
    "TradingSignal",
]
