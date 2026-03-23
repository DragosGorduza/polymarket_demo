from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


class LearningRule(Protocol):
    """Interface for pluggable learning rules."""

    def fit(self, X: np.ndarray, y: np.ndarray) -> "LearningRule":
        ...

    def predict_yes_probability(self, X: np.ndarray) -> np.ndarray:
        ...


@dataclass
class LinearRegressionRule:
    """
    Baseline learning rule.

    Uses linear regression to predict YES probability and clips to [0, 1].
    """

    clip_min: float = 0.001
    clip_max: float = 0.999

    def __post_init__(self) -> None:
        self.pipeline = Pipeline(
            [
                ("scaler", StandardScaler()),
                ("model", LinearRegression()),
            ]
        )
        self._is_fit = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> "LinearRegressionRule":
        y = np.asarray(y, dtype=float)
        self.pipeline.fit(np.asarray(X, dtype=float), y)
        self._is_fit = True
        return self

    def predict_yes_probability(self, X: np.ndarray) -> np.ndarray:
        if not self._is_fit:
            raise RuntimeError("Model is not fitted")
        pred = self.pipeline.predict(np.asarray(X, dtype=float))
        return np.clip(pred, self.clip_min, self.clip_max)
