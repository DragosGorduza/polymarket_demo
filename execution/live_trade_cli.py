from __future__ import annotations

import argparse
from dataclasses import dataclass

import requests

from execution.live_config import LiveTradingConfig
from execution.order_manager import ExecutionOrderManager
from execution.polymarket_venue import PolymarketVenueClient
from polymarket_ingestion.clients.gamma_client import GammaClient
from polymarket_ingestion.clients.clob_client import ClobClient


@dataclass
class ManualSignal:
    direction: str
    recommended_action: str


def _best_prices(order_book: dict) -> tuple[float, float]:
    bids = order_book.get("bids") or []
    asks = order_book.get("asks") or []
    if not bids or not asks:
        raise RuntimeError(f"Order book is empty: {order_book}")

    def _px(level):
        if isinstance(level, dict):
            return float(level.get("price") or level.get("px") or 0.0)
        if isinstance(level, (list, tuple)) and level:
            return float(level[0])
        return 0.0

    best_bid = max(_px(x) for x in bids)
    best_ask = min(_px(x) for x in asks)
    if best_bid <= 0 or best_ask <= 0 or best_bid >= best_ask:
        raise RuntimeError(f"Invalid best bid/ask from order book: bid={best_bid} ask={best_ask}")
    return best_bid, best_ask


def _preflight_api_check(cfg: LiveTradingConfig) -> None:
    # Gamma access check (requires API key in this setup).
    resp = requests.get(
        f"{cfg.gamma_base_url.rstrip('/')}/markets",
        params={"limit": 1},
        headers={"Authorization": f"Bearer {cfg.gamma_api_key}"},
        timeout=15,
    )
    if resp.status_code == 401:
        raise RuntimeError("Gamma API key is invalid (401 Unauthorized)")
    resp.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute one live passive order on Polymarket")
    parser.add_argument("--slug", required=True, help="Market slug")
    parser.add_argument("--direction", required=True, choices=["UP", "DOWN"], help="Outcome direction")
    parser.add_argument("--size-usd", required=True, type=float, help="Order notional in USDC")
    parser.add_argument("--env-file", default=".env", help="Path to env file with credentials")
    args = parser.parse_args()

    cfg = LiveTradingConfig.from_env(args.env_file)
    _preflight_api_check(cfg)

    gamma = GammaClient(cfg.gamma_base_url, extra_headers={"Authorization": f"Bearer {cfg.gamma_api_key}"})
    market = gamma.get_market_by_slug(args.slug)
    token_map = gamma.extract_token_map(market)

    up_token = token_map.get("UP")
    down_token = token_map.get("DOWN")
    if not up_token or not down_token:
        raise RuntimeError(f"Could not resolve UP/DOWN token ids for slug={args.slug}. token_map={token_map}")

    token_id = up_token if args.direction.upper() == "UP" else down_token

    clob = ClobClient(cfg.clob_base_url)
    order_book = clob.get_order_book(token_id)
    best_bid, best_ask = _best_prices(order_book)

    venue = PolymarketVenueClient(host=cfg.clob_base_url, chain_id=cfg.chain_id, private_key=cfg.private_key)
    om = ExecutionOrderManager(venue=venue)

    signal = ManualSignal(direction=args.direction.upper(), recommended_action=f"BUY_{args.direction.upper()}")
    signals = {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "yes_outcome": "UP",
        "no_outcome": "DOWN",
        "up_token_id": up_token,
        "down_token_id": down_token,
    }

    order_id = om.place_passive_order_inside_spread(signal=signal, signals=signals, size_usd=args.size_usd)
    print(f"Order submitted. order_id={order_id} slug={args.slug} direction={args.direction} size_usd={args.size_usd}")


if __name__ == "__main__":
    main()
