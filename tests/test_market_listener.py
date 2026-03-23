from __future__ import annotations

from pathlib import Path

from polymarket_ingestion.cli import _parse_slugs
from polymarket_ingestion.collector import CollectionResult
from polymarket_ingestion.market_listener import MarketUpdateListener


def test_parse_slugs_from_single_csv_and_file(tmp_path: Path):
    f = tmp_path / "slugs.txt"
    f.write_text("alpha\n#comment\nbeta\n\nalpha\n", encoding="utf-8")

    slugs = _parse_slugs("one", "two,three , two", str(f))
    assert slugs == ["one", "two", "three", "alpha", "beta"]


class FakeCollector:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def collect(self, slug: str) -> CollectionResult:
        self.calls.append(slug)
        return CollectionResult(
            slug=slug,
            output_folder=Path("data") / slug,
            price_rows=10,
            trade_rows=12,
            volume_rows=3,
        )


def test_listener_runs_multiple_cycles_without_real_sleep():
    collector = FakeCollector()
    listener = MarketUpdateListener(collector=collector, interval_seconds=60)

    slept: list[int] = []

    def fake_sleep(seconds: int) -> None:
        slept.append(seconds)

    summary = listener.run(slugs=["s1", "s2"], cycles=3, sleep_fn=fake_sleep)

    assert summary.total_cycles == 3
    assert summary.total_success == 6
    assert summary.total_failed == 0
    assert collector.calls == ["s1", "s2", "s1", "s2", "s1", "s2"]
    assert slept == [60, 60]
