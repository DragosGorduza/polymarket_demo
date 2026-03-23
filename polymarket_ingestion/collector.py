from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

from polymarket_ingestion.analytics import (
    compute_option_volatility_overtime,
    compute_volatility,
    infer_volume_overtime,
    reconstruct_historical_orderbook,
    to_pretty_json,
    trades_to_activity,
    trades_to_price_history,
)
from polymarket_ingestion.clients.clob_client import ClobClient
from polymarket_ingestion.clients.data_client import DataApiClient
from polymarket_ingestion.clients.base import ApiUnauthorizedError
from polymarket_ingestion.clients.gamma_client import GammaClient
from polymarket_ingestion.config import Settings


@dataclass
class CollectionResult:
    slug: str
    output_folder: Path
    price_rows: int
    trade_rows: int
    volume_rows: int


class MarketDataCollector:
    def __init__(
        self,
        gamma_client: GammaClient,
        clob_client: ClobClient,
        data_client: DataApiClient,
        output_root: str | Path = "data",
    ) -> None:
        self.gamma = gamma_client
        self.clob = clob_client
        self.data = data_client
        self.output_root = Path(output_root)

    @classmethod
    def from_settings(cls, settings: Settings, output_root: str | Path = "data") -> "MarketDataCollector":
        clob_headers = {"Authorization": f"Bearer {settings.clob_api_key}"} if settings.clob_api_key else None
        data_headers = {"Authorization": f"Bearer {settings.data_api_key}"} if settings.data_api_key else None
        return cls(
            gamma_client=GammaClient(
                settings.gamma_base_url,
                timeout_seconds=settings.request_timeout_seconds,
                user_agent=settings.user_agent,
            ),
            clob_client=ClobClient(
                settings.clob_base_url,
                timeout_seconds=settings.request_timeout_seconds,
                user_agent=settings.user_agent,
                extra_headers=clob_headers,
            ),
            data_client=DataApiClient(
                settings.data_base_url,
                timeout_seconds=settings.request_timeout_seconds,
                user_agent=settings.user_agent,
                extra_headers=data_headers,
            ),
            output_root=output_root,
        )

    def collect(self, slug: str) -> CollectionResult:
        market = self.gamma.get_market_by_slug(slug)
        token_map = self.gamma.extract_token_map(market)
        condition_id = self.gamma.condition_id(market)

        try:
            trades = self.clob.get_trades_by_market(condition_id)
        except ApiUnauthorizedError:
            trades = self.data.get_trade_activity(slug=slug, condition_id=condition_id)

        price_df = self.data.get_price_history(slug, condition_id, token_map)
        if price_df.empty:
            price_df = trades_to_price_history(trades, token_map)

        activity_df = trades_to_activity(trades, token_map)
        volume_df = infer_volume_overtime(activity_df, freq="5s")
        orderbook_df = reconstruct_historical_orderbook(
            trades=trades,
            token_map=token_map,
            snapshot_freq="5s",
            price_bucket=0.01,
        )
        option_vol_df = compute_option_volatility_overtime(price_df, interval="5s", rolling_window_intervals=60)
        vol = compute_volatility(price_df)

        folder = self.output_root / slug
        folder.mkdir(parents=True, exist_ok=True)

        (folder / "market_metadata.json").write_text(to_pretty_json(market), encoding="utf-8")
        (folder / "raw_trades.json").write_text(json.dumps(trades, indent=2, default=str), encoding="utf-8")
        price_df.to_csv(folder / "outcomes_price_history.csv", index=False)
        activity_df.to_csv(folder / "trade_activity.csv", index=False)
        volume_df.to_csv(folder / "volume_overtime.csv", index=False)
        orderbook_df.to_csv(folder / "historical_orderbook_5s_1c.csv", index=False)
        option_vol_df.to_csv(folder / "volatility_overtime.csv", index=False)
        (folder / "volatility.json").write_text(to_pretty_json(vol), encoding="utf-8")

        return CollectionResult(
            slug=slug,
            output_folder=folder,
            price_rows=len(price_df),
            trade_rows=len(activity_df),
            volume_rows=len(volume_df),
        )
