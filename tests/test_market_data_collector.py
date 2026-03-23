from __future__ import annotations

import json

import pandas as pd

from polymarket_ingestion.analytics import infer_volume_overtime, trades_to_activity, trades_to_price_history
from polymarket_ingestion.collector import MarketDataCollector


class FakeGammaClient:
    def get_market_by_slug(self, slug: str) -> dict:
        return {
            "slug": slug,
            "condition_id": "cond_123",
            "tokens": [
                {"outcome": "YES", "token_id": "yes_token"},
                {"outcome": "NO", "token_id": "no_token"},
            ],
        }

    @staticmethod
    def extract_token_map(market: dict) -> dict[str, str]:
        return {t["outcome"]: t["token_id"] for t in market["tokens"]}

    @staticmethod
    def condition_id(market: dict) -> str:
        return market["condition_id"]


class FakeClobClient:
    def get_trades_by_market(self, condition_id: str) -> list[dict]:
        assert condition_id == "cond_123"
        return [
            {
                "trade_id": "t1",
                "timestamp": "2026-03-22T10:00:00Z",
                "token_id": "yes_token",
                "side": "buy",
                "price": 0.52,
                "size": 100,
                "maker_address": "0xmaker1",
                "taker_address": "0xtaker1",
            },
            {
                "trade_id": "t2",
                "timestamp": "2026-03-22T11:00:00Z",
                "token_id": "yes_token",
                "side": "sell",
                "price": 0.55,
                "size": 80,
                "maker_address": "0xmaker2",
                "taker_address": "0xtaker2",
            },
            {
                "trade_id": "t3",
                "timestamp": "2026-03-22T12:00:00Z",
                "token_id": "no_token",
                "side": "buy",
                "price": 0.46,
                "size": 75,
                "maker_address": "0xmaker3",
                "taker_address": "0xtaker3",
            },
        ]


class FakeDataApiClient:
    def get_price_history(self, slug: str, condition_id: str, token_map: dict[str, str], max_points: int = 20000) -> pd.DataFrame:
        _ = slug, condition_id, token_map, max_points
        return pd.DataFrame(
            [
                {"ts": "2026-03-22T10:00:00Z", "token_id": "yes_token", "outcome": "YES", "price": 0.52},
                {"ts": "2026-03-22T11:00:00Z", "token_id": "yes_token", "outcome": "YES", "price": 0.55},
                {"ts": "2026-03-22T12:00:00Z", "token_id": "no_token", "outcome": "NO", "price": 0.46},
                {"ts": "2026-03-22T13:00:00Z", "token_id": "yes_token", "outcome": "YES", "price": 0.57},
            ]
        )


def test_collect_market_slug_exports_expected_outputs(tmp_path):
    slug = "test-market-slug"
    collector = MarketDataCollector(
        gamma_client=FakeGammaClient(),
        clob_client=FakeClobClient(),
        data_client=FakeDataApiClient(),
        output_root=tmp_path / "data",
    )

    result = collector.collect(slug)

    out = result.output_folder
    assert out.exists()
    assert (out / "outcomes_price_history.csv").exists()
    assert (out / "trade_activity.csv").exists()
    assert (out / "volume_overtime.csv").exists()
    assert (out / "historical_orderbook_5s_1c.csv").exists()
    assert (out / "volatility_overtime.csv").exists()
    assert (out / "volatility.json").exists()

    prices = pd.read_csv(out / "outcomes_price_history.csv")
    activity = pd.read_csv(out / "trade_activity.csv")
    volume = pd.read_csv(out / "volume_overtime.csv")
    orderbook = pd.read_csv(out / "historical_orderbook_5s_1c.csv")
    vol_ts = pd.read_csv(out / "volatility_overtime.csv")
    vol = json.loads((out / "volatility.json").read_text(encoding="utf-8"))

    assert not prices.empty
    assert set(["ts", "token_id", "outcome", "price"]).issubset(prices.columns)

    assert not activity.empty
    assert set(["buyer_address", "seller_address", "outcome", "price", "size"]).issubset(activity.columns)

    assert not volume.empty
    assert set(["bucket", "outcome", "volume_tokens", "volume_notional_usdc"]).issubset(volume.columns)
    vol_buckets = pd.to_datetime(volume["bucket"], utc=True, errors="coerce").dropna().sort_values()
    if len(vol_buckets) >= 2:
        min_step = vol_buckets.diff().dropna().dt.total_seconds().min()
        assert min_step <= 5.0

    assert not vol_ts.empty
    assert set(
        [
            "bucket",
            "outcome",
            "mid_price",
            "log_return",
            "realized_volatility_rolling",
            "annualized_volatility_rolling",
        ]
    ).issubset(vol_ts.columns)
    assert vol_ts["outcome"].nunique() >= 1

    assert not orderbook.empty
    assert set(
        [
            "snapshot_ts",
            "outcome",
            "price_bucket",
            "bid_traded_size",
            "ask_traded_size",
            "total_traded_size",
            "net_aggressor_buy",
        ]
    ).issubset(orderbook.columns)
    assert (orderbook["price_bucket"] * 100).round(6).mod(1).eq(0).all()

    assert vol["realized_volatility"] is not None
    assert vol["annualized_volatility"] is not None
    assert "by_outcome" in vol


def test_unix_seconds_and_asset_field_are_parsed_correctly():
    token_map = {
        "UP": "70511961689427886849251125331075177577040674831404454735628437740418162211411",
        "DOWN": "93818154076666686896621053885958896092305047141716153924461852974244812585066",
    }
    trades = [
        {
            "timestamp": 1774280977,
            "asset": token_map["UP"],
            "outcome": "Up",
            "side": "BUY",
            "price": 0.5,
            "size": 20,
            "proxyWallet": "0xabc",
        },
        {
            "timestamp": 1774281300,
            "asset": token_map["DOWN"],
            "outcome": "Down",
            "side": "BUY",
            "price": 0.51,
            "size": 10,
            "proxyWallet": "0xdef",
        },
    ]

    prices = trades_to_price_history(trades, token_map)
    activity = trades_to_activity(trades, token_map)
    volume = infer_volume_overtime(activity)

    assert not prices.empty
    # Guard against erroneous ns parsing that yields 1970.
    assert prices["ts"].dt.year.min() >= 2026

    assert not activity.empty
    assert set(activity["outcome"].str.upper().unique()) == {"UP", "DOWN"}
    assert activity["actor_address"].notna().all()

    assert not volume.empty
    assert volume["bucket"].dt.year.min() >= 2026
