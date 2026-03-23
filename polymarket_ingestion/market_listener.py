from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time

from polymarket_ingestion.collector import CollectionResult, MarketDataCollector


@dataclass
class ListenerRunSummary:
    total_cycles: int
    total_success: int
    total_failed: int


class MarketUpdateListener:
    """Periodic polling listener for one slug or a list of slugs."""

    def __init__(self, collector: MarketDataCollector, interval_seconds: int = 300) -> None:
        self.collector = collector
        self.interval_seconds = max(1, int(interval_seconds))

    def update_once(self, slugs: list[str]) -> tuple[list[CollectionResult], list[tuple[str, Exception]]]:
        successes: list[CollectionResult] = []
        failures: list[tuple[str, Exception]] = []

        for slug in slugs:
            try:
                result = self.collector.collect(slug)
                successes.append(result)
            except Exception as exc:  # pragma: no cover - runtime/network path
                failures.append((slug, exc))

        return successes, failures

    def run(
        self,
        slugs: list[str],
        cycles: int | None = None,
        sleep_fn=time.sleep,
    ) -> ListenerRunSummary:
        if not slugs:
            raise ValueError("At least one slug is required")

        cycle = 0
        total_success = 0
        total_failed = 0

        while True:
            cycle += 1
            now = datetime.now(timezone.utc).isoformat()
            print(f"[{now}] update cycle={cycle} slugs={len(slugs)}")

            successes, failures = self.update_once(slugs)
            total_success += len(successes)
            total_failed += len(failures)

            for s in successes:
                print(
                    f"  ok slug={s.slug} price_rows={s.price_rows} "
                    f"trade_rows={s.trade_rows} volume_rows={s.volume_rows}"
                )
            for slug, exc in failures:
                print(f"  fail slug={slug} error={exc}")

            if cycles is not None and cycle >= cycles:
                break

            sleep_fn(self.interval_seconds)

        return ListenerRunSummary(
            total_cycles=cycle,
            total_success=total_success,
            total_failed=total_failed,
        )
