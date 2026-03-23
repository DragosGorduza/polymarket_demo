from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionRiskConfig:
    min_edge: float = 0.02
    min_confidence: float = 0.25
    max_spread: float = 0.05
    max_single_position_usd: float = 500.0
    max_total_exposure_usd: float = 10_000.0


class ExecutionRiskEngine:
    def __init__(self, config: ExecutionRiskConfig | None = None):
        self.config = config or ExecutionRiskConfig()

    def pre_trade_check(
        self,
        signal,
        size_usd: float,
        current_exposure_usd: float,
        best_bid: float,
        best_ask: float,
    ) -> tuple[bool, str]:
        spread = float(best_ask) - float(best_bid)

        if getattr(signal, "recommended_action", "PASS") == "PASS":
            return False, "signal_pass"
        if float(getattr(signal, "edge", 0.0)) < self.config.min_edge:
            return False, "insufficient_edge"
        if float(getattr(signal, "confidence", 0.0)) < self.config.min_confidence:
            return False, "low_confidence"
        if spread > self.config.max_spread:
            return False, "spread_too_wide"
        if size_usd > self.config.max_single_position_usd:
            return False, "single_position_limit"
        if current_exposure_usd + size_usd > self.config.max_total_exposure_usd:
            return False, "total_exposure_limit"
        return True, "approved"

    def available_capital(self, bankroll: float, current_exposure_usd: float, cap_fraction: float = 0.40) -> float:
        max_deploy = bankroll * cap_fraction
        return max(0.0, min(max_deploy, self.config.max_total_exposure_usd) - current_exposure_usd)
