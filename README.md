# Polymarket Market Data Ingestion (MVP)

This codebase implements external connectivity for:

- Polymarket CLOB API
- Polymarket Gamma API
- Polymarket Data API
- Placeholder clients for Polygon + News APIs

Given a market slug, it exports into `data/<slug>/`:

- latest available outcome price series
- trade activity (buyer/seller and outcome)
- volume over time inferred from user position sizing (per outcome, 5s buckets)
- realized/annualized volatility over time (per outcome, 5s buckets)

## Quick start

1. Create a virtual environment and install dependencies:

```bash
pip install -r requirements.txt
```

2. Run collection:

```bash
python -m polymarket_ingestion.cli --slug <market-slug>
```

One-shot for many slugs:

```bash
python -m polymarket_ingestion.cli --slugs slug-a,slug-b,slug-c
```

Continuous updates every few minutes (ingestion listener):

```bash
python -m polymarket_ingestion.cli --slugs slug-a,slug-b --watch --interval-minutes 3
```

From file:

```bash
python -m polymarket_ingestion.cli --slugs-file slugs.txt --watch --interval-minutes 5
```

If CLOB `GET /trades` returns `401 Unauthorized`, the collector now automatically falls back to Data API trade/activity endpoints.

Optional env vars:

- `CLOB_API_KEY`
- `DATA_API_KEY`

3. Run tests:

```bash
pytest -q
```

## Output structure

```text
data/
  <slug>/
    market_metadata.json
    raw_trades.json
    outcomes_price_history.csv
    trade_activity.csv
    volume_overtime.csv
    historical_orderbook_5s_1c.csv
    volatility_overtime.csv
    volatility.json
```

  ## Pricing layer (linear-regression baseline)

  Package: `polymarket_pricing/`

  - `features.py`: pluggable feature builder
  - `models.py`: learning-rule interface + `LinearRegressionRule`
  - `signals.py`: signal engine producing `BUY_<OUTCOME>` / `PASS`

  Backtest strategies for one slug:

  ```bash
  python -m polymarket_pricing.backtest_cli --slug <market-slug>
  ```

  Outputs are written to:

  - `data/<slug>/backtests/strategy_performance.csv`
  - `data/<slug>/backtests/strategy_performance.json`
  - `data/<slug>/backtests/first_test_step_input.csv`
  - `data/<slug>/backtests/equity_curve_<strategy>.csv`

  Default strategies include:

  - `always_up`
  - `always_down`
  - `linear_regression_direction`
  - `black_scholes_probability` (simple baseline)

  ## Execution layer

  Package: `execution/`

  - `service.py`: orchestration (`generate signal -> risk checks -> submit passive -> monitor -> cancel/reprice`)
  - `risk_engine.py`: pre-trade risk constraints
  - `order_manager.py`: passive pricing inside spread + fill monitoring logic
  - `types.py`: order/venue interfaces

  Live one-shot trade (passive order inside spread):

  1. Copy `.env.example` to `.env` and set real values.
  2. Run:

  ```bash
  python -m execution.live_trade_cli --slug <market-slug> --direction UP --size-usd 25
  ```

  If `.env` still has dummy placeholders or malformed keys, the script raises an error and stops.

## Tracking layer

Package: `tracking/`

- real-time strategy PnL updates via `StrategyTrackingService.update_pnl()`
- hourly PnL updates for all active strategies via `publish_hourly_update()`
- max drawdown guardrails per strategy
- automatic strategy halt + critical alert when drawdown limit is breached

