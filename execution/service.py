from __future__ import annotations

from dataclasses import dataclass

from execution.order_manager import ExecutionOrderManager
from execution.risk_engine import ExecutionRiskEngine


@dataclass
class ExecutionResult:
    accepted: bool
    reason: str
    signal_action: str
    order_id: str | None = None
    monitor_status: str | None = None


class ExecutionService:
    """
    End-to-end execution flow:
    1) Generate signal
    2) Perform risk checks
    3) Place passive order inside spread
    4) Monitor fill status
    5) Cancel/reprice if needed
    """

    def __init__(self, signal_engine, risk_engine: ExecutionRiskEngine, order_manager: ExecutionOrderManager):
        self.signal_engine = signal_engine
        self.risk_engine = risk_engine
        self.order_manager = order_manager

    def run_once(
        self,
        market: dict,
        signals: dict,
        size_usd: float,
        current_exposure_usd: float,
        refresh_signal_fn=None,
    ) -> ExecutionResult:
        signal = self.signal_engine.compute_signal(market, signals)

        approved, reason = self.risk_engine.pre_trade_check(
            signal=signal,
            size_usd=size_usd,
            current_exposure_usd=current_exposure_usd,
            best_bid=float(signals["best_bid"]),
            best_ask=float(signals["best_ask"]),
        )
        if not approved:
            return ExecutionResult(
                accepted=False,
                reason=reason,
                signal_action=str(getattr(signal, "recommended_action", "PASS")),
            )

        order_id = self.order_manager.place_passive_order_inside_spread(signal, signals, size_usd)
        monitor = self.order_manager.monitor_fill_status(
            order_id=order_id,
            refresh_signal_fn=refresh_signal_fn,
        )
        return ExecutionResult(
            accepted=True,
            reason="submitted",
            signal_action=str(getattr(signal, "recommended_action", "PASS")),
            order_id=monitor.order_id,
            monitor_status=monitor.status,
        )
