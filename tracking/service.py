from __future__ import annotations

from datetime import datetime, timezone
from threading import Event, Lock, Thread
from typing import Callable
import time

from tracking.alerts import AlertSink, PrintAlertSink
from tracking.models import HourlyPnLRow, StrategyConfig, StrategyState


class StrategyTrackingService:
    """
    Tracks strategies in real time.

    Features:
    - PnL updates per strategy
    - Hourly PnL report for all active strategies
    - Max drawdown monitoring
    - Auto-stop + alert on drawdown breach
    """

    def __init__(self, alert_sink: AlertSink | None = None) -> None:
        self.alert_sink = alert_sink or PrintAlertSink()
        self._states: dict[str, StrategyState] = {}
        self._lock = Lock()

        self._bg_thread: Thread | None = None
        self._bg_stop_event: Event | None = None

    def register_strategy(self, config: StrategyConfig) -> StrategyState:
        with self._lock:
            state = StrategyState(name=config.name, max_drawdown_usd=float(config.max_drawdown_usd))
            self._states[config.name] = state
            return state

    def list_states(self) -> list[StrategyState]:
        with self._lock:
            return list(self._states.values())

    def get_state(self, name: str) -> StrategyState:
        with self._lock:
            if name not in self._states:
                raise KeyError(f"Unknown strategy: {name}")
            return self._states[name]

    def update_pnl(self, strategy_name: str, pnl_delta_usd: float, ts: datetime | None = None) -> StrategyState:
        with self._lock:
            if strategy_name not in self._states:
                raise KeyError(f"Unknown strategy: {strategy_name}")

            state = self._states[strategy_name]
            if not state.active:
                return state

            state.total_pnl_usd += float(pnl_delta_usd)
            state.hourly_pnl_usd += float(pnl_delta_usd)
            state.peak_pnl_usd = max(state.peak_pnl_usd, state.total_pnl_usd)
            state.drawdown_usd = max(0.0, state.peak_pnl_usd - state.total_pnl_usd)
            state.updated_at = ts or datetime.now(timezone.utc)

            if state.drawdown_usd > state.max_drawdown_usd:
                self._stop_strategy_locked(
                    state,
                    reason=f"max_drawdown_breached_{state.drawdown_usd:.2f}_gt_{state.max_drawdown_usd:.2f}",
                )
            return state

    def stop_strategy(self, strategy_name: str, reason: str) -> StrategyState:
        with self._lock:
            if strategy_name not in self._states:
                raise KeyError(f"Unknown strategy: {strategy_name}")
            state = self._states[strategy_name]
            self._stop_strategy_locked(state, reason)
            return state

    def _stop_strategy_locked(self, state: StrategyState, reason: str) -> None:
        if not state.active:
            return
        state.active = False
        state.stopped_reason = reason
        state.updated_at = datetime.now(timezone.utc)
        self.alert_sink.send(
            level="CRITICAL",
            title="Strategy halted",
            message=f"{state.name} halted due to risk breach",
            payload={
                "strategy": state.name,
                "reason": reason,
                "total_pnl_usd": state.total_pnl_usd,
                "drawdown_usd": state.drawdown_usd,
                "max_drawdown_usd": state.max_drawdown_usd,
            },
        )

    def build_hourly_report(self) -> list[HourlyPnLRow]:
        with self._lock:
            rows = [
                HourlyPnLRow(
                    strategy=s.name,
                    active=s.active,
                    hourly_pnl_usd=s.hourly_pnl_usd,
                    total_pnl_usd=s.total_pnl_usd,
                    peak_pnl_usd=s.peak_pnl_usd,
                    drawdown_usd=s.drawdown_usd,
                )
                for s in self._states.values()
                if s.active
            ]
            return rows

    def publish_hourly_update(self) -> list[HourlyPnLRow]:
        with self._lock:
            rows = [
                HourlyPnLRow(
                    strategy=s.name,
                    active=s.active,
                    hourly_pnl_usd=s.hourly_pnl_usd,
                    total_pnl_usd=s.total_pnl_usd,
                    peak_pnl_usd=s.peak_pnl_usd,
                    drawdown_usd=s.drawdown_usd,
                )
                for s in self._states.values()
                if s.active
            ]

            self.alert_sink.send(
                level="INFO",
                title="Hourly strategy PnL update",
                message=f"active_strategies={len(rows)}",
                payload={"rows": [r.__dict__ for r in rows]},
            )

            for s in self._states.values():
                s.hourly_pnl_usd = 0.0
            return rows

    def start_hourly_scheduler(
        self,
        interval_seconds: int = 3600,
        tick_seconds: float = 1.0,
        time_fn: Callable[[], float] = time.time,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if self._bg_thread is not None:
            return

        stop_event = Event()
        self._bg_stop_event = stop_event

        def _run() -> None:
            next_run = time_fn() + interval_seconds
            while not stop_event.is_set():
                now = time_fn()
                if now >= next_run:
                    self.publish_hourly_update()
                    next_run = now + interval_seconds
                sleep_fn(tick_seconds)

        self._bg_thread = Thread(target=_run, daemon=True)
        self._bg_thread.start()

    def stop_hourly_scheduler(self) -> None:
        if self._bg_stop_event is not None:
            self._bg_stop_event.set()
        if self._bg_thread is not None:
            self._bg_thread.join(timeout=2.0)
        self._bg_thread = None
        self._bg_stop_event = None
