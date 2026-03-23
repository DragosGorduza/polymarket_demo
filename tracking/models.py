from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    max_drawdown_usd: float


@dataclass
class StrategyState:
    name: str
    max_drawdown_usd: float
    active: bool = True
    stopped_reason: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    # Equity/PnL tracking
    total_pnl_usd: float = 0.0
    hourly_pnl_usd: float = 0.0
    peak_pnl_usd: float = 0.0
    drawdown_usd: float = 0.0


@dataclass(frozen=True)
class HourlyPnLRow:
    strategy: str
    active: bool
    hourly_pnl_usd: float
    total_pnl_usd: float
    peak_pnl_usd: float
    drawdown_usd: float
