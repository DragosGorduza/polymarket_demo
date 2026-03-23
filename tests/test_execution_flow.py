from __future__ import annotations

from dataclasses import dataclass

from execution.order_manager import ExecutionOrderManager
from execution.risk_engine import ExecutionRiskConfig, ExecutionRiskEngine
from execution.service import ExecutionService
from execution.types import OrderStatus
from polymarket_pricing.signals import TradingSignal


@dataclass
class FakeSignalEngine:
    signal: TradingSignal

    def compute_signal(self, market: dict, signals: dict) -> TradingSignal:
        _ = market, signals
        return self.signal


class FakeVenue:
    def __init__(self, statuses: list[str] | None = None):
        self.statuses = statuses or ["FILLED"]
        self.idx = 0
        self.placed: list[dict] = []
        self.cancelled: list[str] = []

    def place_limit_order(self, token_id: str, side: str, price: float, size: float) -> str:
        oid = f"o{len(self.placed)+1}"
        self.placed.append({"order_id": oid, "token_id": token_id, "side": side, "price": price, "size": size})
        return oid

    def get_order_status(self, order_id: str) -> OrderStatus:
        state = self.statuses[min(self.idx, len(self.statuses) - 1)]
        self.idx += 1
        return OrderStatus(order_id=order_id, state=state)

    def cancel_order(self, order_id: str) -> None:
        self.cancelled.append(order_id)


def _mk_signal(action: str = "BUY_UP", edge: float = 0.05, confidence: float = 0.8, direction: str = "UP") -> TradingSignal:
    return TradingSignal(
        condition_id="cond1",
        direction=direction,
        p_model=0.60,
        p_market=0.55,
        edge=edge,
        confidence=confidence,
        recommended_action=action,
        outcome_yes="UP",
        outcome_no="DOWN",
    )


def _mk_signals(best_bid: float = 0.49, best_ask: float = 0.51) -> dict:
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "yes_outcome": "UP",
        "no_outcome": "DOWN",
        "up_token_id": "up_tok",
        "down_token_id": "down_tok",
    }


def test_execution_rejects_on_risk():
    signal_engine = FakeSignalEngine(_mk_signal(edge=0.001))
    risk = ExecutionRiskEngine(ExecutionRiskConfig(min_edge=0.01))
    venue = FakeVenue(statuses=["FILLED"])
    om = ExecutionOrderManager(venue)
    svc = ExecutionService(signal_engine, risk, om)

    res = svc.run_once(
        market={"condition_id": "cond1"},
        signals=_mk_signals(),
        size_usd=50.0,
        current_exposure_usd=0.0,
    )
    assert not res.accepted
    assert res.order_id is None
    assert len(venue.placed) == 0


def test_execution_places_and_fills():
    signal_engine = FakeSignalEngine(_mk_signal())
    risk = ExecutionRiskEngine(ExecutionRiskConfig(min_edge=0.01, max_spread=0.10))
    venue = FakeVenue(statuses=["OPEN", "FILLED"])
    om = ExecutionOrderManager(venue)
    svc = ExecutionService(signal_engine, risk, om)

    res = svc.run_once(
        market={"condition_id": "cond1"},
        signals=_mk_signals(),
        size_usd=100.0,
        current_exposure_usd=0.0,
        refresh_signal_fn=None,
    )
    assert res.accepted
    assert res.monitor_status == "filled"
    assert len(venue.placed) == 1


def test_execution_reprices_when_market_drifts():
    signal_engine = FakeSignalEngine(_mk_signal())
    risk = ExecutionRiskEngine(ExecutionRiskConfig(min_edge=0.01, max_spread=0.10))
    venue = FakeVenue(statuses=["OPEN", "OPEN", "OPEN"])
    om = ExecutionOrderManager(venue)

    first = _mk_signals(best_bid=0.49, best_ask=0.51)
    second = _mk_signals(best_bid=0.55, best_ask=0.57)

    def refresh_signal_fn():
        return _mk_signal(), second

    oid = om.place_passive_order_inside_spread(_mk_signal(), first, size_usd=100.0)
    result = om.monitor_fill_status(
        order_id=oid,
        refresh_signal_fn=refresh_signal_fn,
        drift_threshold=0.01,
        max_checks=4,
        poll_seconds=0.0,
        sleep_fn=lambda _x: None,
    )

    assert result.status == "repriced"
    assert len(venue.cancelled) >= 1
    assert len(venue.placed) == 2
