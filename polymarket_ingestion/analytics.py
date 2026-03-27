from __future__ import annotations

from typing import Any
import json

import numpy as np
import pandas as pd


def _parse_ts(value: Any) -> pd.Timestamp:
    if value is None:
        return pd.NaT

    if isinstance(value, (int, float)):
        v = float(value)
        av = abs(v)
        if av > 1e14:
            return pd.to_datetime(v, unit="ns", utc=True, errors="coerce")
        if av > 1e11:
            return pd.to_datetime(v, unit="ms", utc=True, errors="coerce")
        return pd.to_datetime(v, unit="s", utc=True, errors="coerce")

    if isinstance(value, str) and value.strip().isdigit():
        return _parse_ts(int(value.strip()))

    return pd.to_datetime(value, utc=True, errors="coerce")


def trades_to_price_history(trades: list[dict[str, Any]], token_map: dict[str, str]) -> pd.DataFrame:
    token_to_outcome = {v: k for k, v in token_map.items()}
    rows: list[dict[str, Any]] = []

    for t in trades:
        ts = t.get("timestamp") or t.get("ts") or t.get("created_at") or t.get("createdAt")
        token_id = str(t.get("token_id") or t.get("tokenId") or t.get("asset_id") or t.get("asset") or "")
        price = t.get("price")
        if ts is None or price is None:
            continue
        rows.append(
            {
                "ts": ts,
                "token_id": token_id,
                "outcome": token_to_outcome.get(token_id, t.get("outcome", "UNKNOWN")),
                "price": float(price),
            }
        )

    if not rows:
        return pd.DataFrame(columns=["ts", "token_id", "outcome", "price"])

    df = pd.DataFrame(rows)
    df["ts"] = df["ts"].apply(_parse_ts)
    return df.dropna(subset=["ts", "price"]).sort_values("ts").reset_index(drop=True)


def trades_to_activity(trades: list[dict[str, Any]], token_map: dict[str, str]) -> pd.DataFrame:
    token_to_outcome = {v: k for k, v in token_map.items()}
    rows: list[dict[str, Any]] = []

    for t in trades:
        ts = t.get("timestamp") or t.get("ts") or t.get("created_at") or t.get("createdAt")
        if ts is None:
            continue

        side = str(t.get("side", "")).lower()
        maker = t.get("maker_address") or t.get("maker") or t.get("makerAddress")
        taker = t.get("taker_address") or t.get("taker") or t.get("takerAddress")
        proxy_wallet = t.get("proxyWallet") or t.get("proxy_wallet")
        buyer = taker if side == "buy" else maker
        seller = maker if side == "buy" else taker

        if not buyer and side == "buy":
            buyer = proxy_wallet
        if not seller and side == "sell":
            seller = proxy_wallet

        token_id = str(t.get("token_id") or t.get("tokenId") or t.get("asset_id") or t.get("asset") or "")
        price = float(t.get("price", 0.0))
        size = float(t.get("size", 0.0))

        rows.append(
            {
                "ts": ts,
                "trade_id": t.get("trade_id") or t.get("id") or "",
                "token_id": token_id,
                "outcome": token_to_outcome.get(token_id, "UNKNOWN"),
                "buyer_address": buyer,
                "seller_address": seller,
                "actor_address": proxy_wallet,
                "side": side,
                "price": price,
                "size": size,
                "notional": price * size,
            }
        )

    if not rows:
        return pd.DataFrame(
            columns=[
                "ts",
                "trade_id",
                "token_id",
                "outcome",
                "buyer_address",
                "seller_address",
                "actor_address",
                "side",
                "price",
                "size",
                "notional",
            ]
        )

    df = pd.DataFrame(rows)
    df["ts"] = df["ts"].apply(_parse_ts)
    return df.dropna(subset=["ts"]).sort_values("ts").reset_index(drop=True)


def infer_volume_overtime(activity_df: pd.DataFrame, freq: str = "5s") -> pd.DataFrame:
    if activity_df.empty:
        return pd.DataFrame(columns=["bucket", "outcome", "volume_tokens", "volume_notional_usdc"])

    df = activity_df.copy()
    df["bucket"] = df["ts"].dt.floor(freq)

    agg = (
        df.groupby(["bucket", "outcome"], as_index=False)
        .agg(
            volume_tokens=("size", "sum"),
            volume_notional_usdc=("notional", "sum"),
        )
        .sort_values("bucket")
    )

    return agg


def reconstruct_historical_orderbook(
    trades: list[dict[str, Any]],
    token_map: dict[str, str],
    snapshot_freq: str = "5s",
    price_bucket: float = 0.01,
) -> pd.DataFrame:
    """
    Reconstruct an inferred order book from trade prints.

    Notes:
    - This is not true L2 reconstruction (no add/cancel events).
    - We approximate by aggregating BUY/Sell executed flow into ask/bid buckets.
    - Buckets are rounded to price_bucket and snapshots are time-bucketed.
    """
    cols = [
        "snapshot_ts",
        "outcome",
        "price_bucket",
        "bid_traded_size",
        "ask_traded_size",
        "total_traded_size",
        "net_aggressor_buy",
    ]

    activity_df = trades_to_activity(trades, token_map)
    if activity_df.empty:
        return pd.DataFrame(columns=cols)

    df = activity_df.copy()
    step = float(price_bucket)
    df["snapshot_ts"] = df["ts"].dt.floor(snapshot_freq)
    df["outcome"] = df["outcome"].astype(str).str.upper()
    df["price_bucket"] = (np.round(df["price"] / step) * step).round(4)

    df["bid_traded_size"] = np.where(df["side"] == "sell", df["size"], 0.0)
    df["ask_traded_size"] = np.where(df["side"] == "buy", df["size"], 0.0)

    out = (
        df.groupby(["snapshot_ts", "outcome", "price_bucket"], as_index=False)
        .agg(
            bid_traded_size=("bid_traded_size", "sum"),
            ask_traded_size=("ask_traded_size", "sum"),
        )
        .sort_values(["snapshot_ts", "outcome", "price_bucket"])
    )
    out["total_traded_size"] = out["bid_traded_size"] + out["ask_traded_size"]
    out["net_aggressor_buy"] = out["ask_traded_size"] - out["bid_traded_size"]
    return out[cols]


def compute_option_volatility_overtime(
    price_df: pd.DataFrame,
    interval: str = "5s",
    rolling_window_intervals: int = 60,
) -> pd.DataFrame:
    """Compute per-outcome return series and rolling volatility over time."""
    cols = [
        "bucket",
        "outcome",
        "mid_price",
        "log_return",
        "realized_volatility_rolling",
        "annualized_volatility_rolling",
    ]
    if price_df.empty or len(price_df) < 3:
        return pd.DataFrame(columns=cols)

    df = price_df.copy()
    df = df.dropna(subset=["ts", "price"])
    if "outcome" not in df.columns:
        df["outcome"] = "UNKNOWN"
    df["outcome"] = df["outcome"].astype(str).str.upper()
    if df.empty:
        return pd.DataFrame(columns=cols)

    interval_seconds = max(pd.to_timedelta(interval).total_seconds(), 1.0)
    periods_per_year = (365.0 * 24.0 * 3600.0) / interval_seconds

    out_frames: list[pd.DataFrame] = []
    for outcome, g in df.groupby("outcome"):
        series = (
            g.sort_values("ts")
            .set_index("ts")["price"]
            .resample(interval)
            .last()
            .ffill()
            .dropna()
        )
        if series.empty:
            continue

        frame = series.to_frame(name="mid_price")
        frame["log_return"] = np.log(frame["mid_price"]).diff()
        min_periods = min(max(2, rolling_window_intervals // 3), rolling_window_intervals)
        frame["realized_volatility_rolling"] = frame["log_return"].rolling(
            window=rolling_window_intervals,
            min_periods=min_periods,
        ).std(ddof=1)
        frame["annualized_volatility_rolling"] = (
            frame["realized_volatility_rolling"] * float(np.sqrt(periods_per_year))
        )
        frame["outcome"] = outcome
        out_frames.append(frame.reset_index().rename(columns={"ts": "bucket"}))

    if not out_frames:
        return pd.DataFrame(columns=cols)

    out = pd.concat(out_frames, ignore_index=True).sort_values(["bucket", "outcome"])
    return out[cols]


def compute_volatility(price_df: pd.DataFrame) -> dict[str, Any]:
    vol_df = compute_option_volatility_overtime(price_df, interval="5s", rolling_window_intervals=60)
    if vol_df.empty:
        return {
            "n_points": int(len(price_df)),
            "realized_volatility": None,
            "annualized_volatility": None,
            "sampling": "5s_rolling",
            "by_outcome": {},
        }

    by_outcome: dict[str, Any] = {}
    latest_annualized: list[float] = []
    latest_realized: list[float] = []

    for outcome, g in vol_df.groupby("outcome"):
        realized_series = g["realized_volatility_rolling"].dropna()
        annualized_series = g["annualized_volatility_rolling"].dropna()
        if realized_series.empty or annualized_series.empty:
            by_outcome[outcome] = {
                "realized_volatility": None,
                "annualized_volatility": None,
                "annualized_volatility_mean": None,
            }
            continue

        r_last = float(realized_series.iloc[-1])
        a_last = float(annualized_series.iloc[-1])
        by_outcome[outcome] = {
            "realized_volatility": r_last,
            "annualized_volatility": a_last,
            "annualized_volatility_mean": float(annualized_series.mean()),
        }
        latest_realized.append(r_last)
        latest_annualized.append(a_last)

    if not latest_realized or not latest_annualized:
        return {
            "n_points": int(len(price_df)),
            "realized_volatility": None,
            "annualized_volatility": None,
            "sampling": "5s_rolling",
            "by_outcome": by_outcome,
        }

    return {
        "n_points": int(len(price_df)),
        "realized_volatility": float(np.mean(latest_realized)),
        "annualized_volatility": float(np.mean(latest_annualized)),
        "sampling": "5s_rolling",
        "by_outcome": by_outcome,
    }


def parse_clob_snapshot(raw_books: dict[str, dict[str, Any]]) -> pd.DataFrame:
    """Parse raw CLOB order-book snapshots into a tidy DataFrame.

    Parameters
    ----------
    raw_books:
        Mapping ``{outcome_label: raw_api_response}`` as returned by
        ``ClobClient.get_order_books_for_market()``.  Each value is expected
        to contain:

        - ``bids``  – list of ``{"price": "0.50", "size": "100"}`` sorted
          descending (best bid first).
        - ``asks``  – list of ``{"price": "0.51", "size": "50"}`` sorted
          ascending (best ask first).
        - ``timestamp`` (optional) – exchange-side snapshot timestamp.

    Returns
    -------
    DataFrame with columns:
        ``ts_utc``, ``outcome``, ``bid_px``, ``ask_px``, ``bid_size``, ``ask_size``

    One row per price level per outcome (levels are paired by depth rank so
    level 1 = best bid + best ask, level 2 = second-best, etc.).  When one
    side has fewer levels the missing values are ``NaN``.
    """
    cols = ["ts_utc", "outcome", "bid_px", "ask_px", "bid_size", "ask_size"]
    if not raw_books:
        return pd.DataFrame(columns=cols)

    now_utc = pd.Timestamp.utcnow().floor("s")
    rows: list[dict[str, Any]] = []

    for outcome, book in raw_books.items():
        if not isinstance(book, dict):
            continue

        # Resolve snapshot timestamp (fall back to wall-clock now)
        raw_ts = book.get("timestamp")
        ts_utc = _parse_ts(raw_ts) if raw_ts is not None else now_utc
        if pd.isna(ts_utc):
            ts_utc = now_utc

        bids: list[dict[str, Any]] = list(book.get("bids") or [])
        asks: list[dict[str, Any]] = list(book.get("asks") or [])

        # Ensure canonical sort: bids descending, asks ascending
        bids = sorted(bids, key=lambda x: float(x.get("price", 0)), reverse=True)
        asks = sorted(asks, key=lambda x: float(x.get("price", 0)))

        n_levels = max(len(bids), len(asks))
        for i in range(n_levels):
            bid = bids[i] if i < len(bids) else None
            ask = asks[i] if i < len(asks) else None
            rows.append(
                {
                    "ts_utc": ts_utc,
                    "outcome": str(outcome).upper(),
                    "bid_px": float(bid["price"]) if bid else None,
                    "ask_px": float(ask["price"]) if ask else None,
                    "bid_size": float(bid["size"]) if bid else None,
                    "ask_size": float(ask["size"]) if ask else None,
                }
            )

    if not rows:
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame(rows, columns=cols)
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True, errors="coerce")
    return df.sort_values(["outcome", "bid_px"], ascending=[True, False]).reset_index(drop=True)


def to_pretty_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str)
