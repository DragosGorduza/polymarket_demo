# Polymarket Trading System — Technical Specification

> **Status:** MVP specification  
> **Venue:** Polymarket (CLOB on Polygon)  
> **Stack:** Python 3.11+, PostgreSQL/TimescaleDB, Redis, Docker

---

## Table of Contents

1. [Infrastructure](#1-infrastructure)
2. [Pricing & Probability Model](#2-pricing--probability-model)
3. [Algorithmic Implementation](#3-algorithmic-implementation)
4. [Hedging & Risk Management](#4-hedging--risk-management)

---

## 1. Infrastructure

### 1.1 Overview

The infrastructure is divided into five layers: **data ingestion**, **storage**, **compute**, **execution**, and **observability**. All services are containerised with Docker and orchestrated via Docker Compose for the MVP, migrating to Kubernetes once live trading begins at scale.

```
┌─────────────────────────────────────────────────────────────────┐
│                        EXTERNAL FEEDS                           │
│  Polymarket CLOB API │ Polygon RPC │ News APIs │ Reference mkts │
└────────────────┬────────────────────────────────────────────────┘
                 │
┌────────────────▼──────────────────────────────────────────────┐
│                     INGESTION LAYER                           │
│         WebSocket listeners │ REST pollers │ On-chain listener │
└────────────────┬──────────────────────────────────────────────┘
                 │
┌────────────────▼──────────────────────────────────────────────┐
│                      STORAGE LAYER                            │
│   TimescaleDB (tick/OHLCV)  │  Redis (live book)  │  S3/Minio │
└────────────────┬──────────────────────────────────────────────┘
                 │
┌────────────────▼──────────────────────────────────────────────┐
│                      COMPUTE LAYER                            │
│       Signal engine │ Model server │ Backtester │ Scheduler    │
└────────────────┬──────────────────────────────────────────────┘
                 │
┌────────────────▼──────────────────────────────────────────────┐
│                     EXECUTION LAYER                           │
│     Order manager │ Risk engine │ Wallet manager │ EVM signer  │
└────────────────┬──────────────────────────────────────────────┘
                 │
┌────────────────▼──────────────────────────────────────────────┐
│                   OBSERVABILITY LAYER                         │
│          Grafana │ Prometheus │ AlertManager │ PnL dashboard   │
└───────────────────────────────────────────────────────────────┘
```

---

### 1.2 Polymarket API

Polymarket operates a **Central Limit Order Book (CLOB)** on the Polygon PoS network. All positions are settled in **USDC**.

| Resource | URL |
|---|---|
| CLOB API docs | https://docs.polymarket.com |
| REST base URL | `https://clob.polymarket.com` |
| WebSocket base URL | `wss://ws-subscriptions-clob.polymarket.com/ws/` |
| Gamma (markets metadata) | `https://gamma-api.polymarket.com` |
| Polygon RPC (Alchemy) | https://www.alchemy.com/polygon |
| Polymarket GitHub | https://github.com/Polymarket |
| py-clob-client | https://github.com/Polymarket/py-clob-client |

**Key REST endpoints:**

```
GET  /markets                        # all active markets
GET  /markets/{condition_id}         # single market metadata
GET  /order-book/{token_id}          # current L2 order book
GET  /trades?market={condition_id}   # trade history
POST /order                          # place limit/market order
DELETE /order/{order_id}             # cancel order
GET  /orders?owner={address}         # open orders for wallet
GET  /positions?user={address}       # current positions
```

**WebSocket channels:**

```python
# Subscribe to live order book updates
{"type": "subscribe", "channel": "order_book", "assets_ids": ["<token_id>"]}

# Subscribe to trade feed
{"type": "subscribe", "channel": "live_activity", "assets_ids": ["<token_id>"]}

# Subscribe to market status changes
{"type": "subscribe", "channel": "market", "assets_ids": ["<token_id>"]}
```

**Authentication:** Orders are signed with an EOA private key using EIP-712 typed data. The `py-clob-client` library handles signing. Never store private keys in code — use environment variables or a secrets manager.

```python
from py_clob_client.client import ClobClient

client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,  # Polygon mainnet
    private_key=os.environ["TRADING_PRIVATE_KEY"],
    signature_type=2,  # EOA
)
```

---

### 1.3 Blockchain & Wallet Infrastructure

Polymarket contracts live on **Polygon PoS** (chain ID 137). All collateral is USDC (native, not bridged).

| Resource | URL |
|---|---|
| Polygon docs | https://docs.polygon.technology |
| Alchemy (recommended RPC) | https://www.alchemy.com |
| Infura (alternative RPC) | https://www.infura.io |
| USDC on Polygon (Circle) | https://www.circle.com/en/usdc-multichain/polygon |
| web3.py | https://web3py.readthedocs.io |
| eth-account (signing) | https://eth-account.readthedocs.io |
| Polygon gas tracker | https://polygonscan.com/gastracker |

**Wallet setup:**

- Use a dedicated hot wallet for trading (EOA, not multisig, for latency).
- Keep a separate cold wallet for USDC reserves; top up the hot wallet programmatically.
- Maintain a MATIC balance for gas — Polygon transactions cost ~$0.001–0.01 per tx.
- Monitor USDC approval to the CTF Exchange contract (`0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E`).

```python
from web3 import Web3

w3 = Web3(Web3.HTTPProvider(os.environ["POLYGON_RPC_URL"]))

# Check USDC balance
usdc = w3.eth.contract(address=USDC_ADDRESS, abi=ERC20_ABI)
balance = usdc.functions.balanceOf(HOT_WALLET_ADDRESS).call()
```

---

### 1.4 Storage Layer

#### TimescaleDB (primary time-series store)

TimescaleDB is a PostgreSQL extension optimised for time-series data. Use it for all tick data, resolved market outcomes, and PnL history.

| Resource | URL |
|---|---|
| TimescaleDB docs | https://docs.timescale.com |
| Docker image | https://hub.docker.com/r/timescale/timescaledb |
| psycopg2 | https://www.psycopg.org/docs |
| SQLAlchemy | https://docs.sqlalchemy.org |

```sql
-- Core schema

CREATE TABLE markets (
    condition_id    TEXT PRIMARY KEY,
    question        TEXT NOT NULL,
    category        TEXT,
    end_date        TIMESTAMPTZ,
    resolution      NUMERIC(4,3),  -- NULL until resolved; 1.0=YES, 0.0=NO
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE orderbook_snapshots (
    ts              TIMESTAMPTZ NOT NULL,
    token_id        TEXT NOT NULL,
    side            CHAR(3),        -- 'bid' or 'ask'
    price           NUMERIC(6,4),   -- 0.0001 to 0.9999
    size            NUMERIC(18,6),
    PRIMARY KEY (ts, token_id, side, price)
);
SELECT create_hypertable('orderbook_snapshots', 'ts');

CREATE TABLE trades (
    ts              TIMESTAMPTZ NOT NULL,
    trade_id        TEXT UNIQUE,
    token_id        TEXT,
    side            CHAR(4),        -- 'buy' or 'sell'
    price           NUMERIC(6,4),
    size            NUMERIC(18,6),
    maker_address   TEXT,
    taker_address   TEXT
);
SELECT create_hypertable('trades', 'ts');

CREATE TABLE positions (
    ts              TIMESTAMPTZ NOT NULL,
    condition_id    TEXT,
    token_id        TEXT,
    size            NUMERIC(18,6),
    avg_entry_price NUMERIC(6,4),
    unrealised_pnl  NUMERIC(18,6),
    PRIMARY KEY (ts, token_id)
);
SELECT create_hypertable('positions', 'ts');
```

#### Redis (live data cache)

Redis stores live order book state and inter-process messaging. Use Redis Streams for the signal pipeline.

| Resource | URL |
|---|---|
| Redis docs | https://redis.io/docs |
| redis-py | https://redis-py.readthedocs.io |
| Redis Streams guide | https://redis.io/docs/data-types/streams |

```python
import redis

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

def update_book(token_id: str, best_bid: float, best_ask: float):
    r.hset(f"book:{token_id}", mapping={
        "bid": best_bid,
        "ask": best_ask,
        "mid": (best_bid + best_ask) / 2,
        "spread": best_ask - best_bid,
        "ts": time.time()
    })

def publish_signal(signal: dict):
    r.xadd("signals", signal)
```

#### Object storage (S3 / MinIO)

Use S3 or self-hosted MinIO for model artefacts, backtesting results, and raw data snapshots.

| Resource | URL |
|---|---|
| MinIO docs | https://min.io/docs |
| boto3 (AWS SDK) | https://boto3.amazonaws.com/v1/documentation/api/latest |

---

### 1.5 Compute & Scheduling

| Tool | Purpose | URL |
|---|---|---|
| APScheduler | Lightweight cron-style job scheduling | https://apscheduler.readthedocs.io |
| Celery | Distributed task queue for heavy compute | https://docs.celeryq.dev |
| FastAPI | Internal REST API for signal/order endpoints | https://fastapi.tiangolo.com |
| scikit-learn | ML model training | https://scikit-learn.org |
| LightGBM | Gradient boosting for probability models | https://lightgbm.readthedocs.io |
| pandas | Data manipulation | https://pandas.pydata.org |
| numpy | Numerical compute | https://numpy.org |

**Main loop schedule:**

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

# Fast loop: update live signals every 10 seconds
scheduler.add_job(update_signals, 'interval', seconds=10)

# Medium loop: refit short-horizon model every 5 minutes
scheduler.add_job(refit_model, 'interval', minutes=5)

# Slow loop: scan for new market opportunities every hour
scheduler.add_job(scan_markets, 'interval', hours=1)

# Daily: reconcile positions, compute daily PnL, retrain long-horizon model
scheduler.add_job(daily_reconciliation, 'cron', hour=0, minute=5)
```

---

### 1.6 External Signal Sources

| Source | Purpose | URL |
|---|---|---|
| NewsAPI | Real-time news for event context | https://newsapi.org |
| GDELT | Global event database (free) | https://www.gdeltproject.org |
| Metaculus API | Reference probability estimates | https://www.metaculus.com/api2 |
| Manifold Markets API | Alternative prediction market prices | https://docs.manifold.markets/api |
| FiveThirtyEight / Silver Bulletin | Polling data (elections) | https://www.natesilver.net |
| Twitter/X API | Social sentiment | https://developer.x.com |
| OpenAI / Anthropic API | LLM-based event probability extraction | https://docs.anthropic.com |

---

### 1.7 Observability

| Tool | Purpose | URL |
|---|---|---|
| Prometheus | Metrics collection | https://prometheus.io/docs |
| Grafana | Dashboards | https://grafana.com/docs |
| AlertManager | PagerDuty/Slack alerting | https://prometheus.io/docs/alerting/latest/alertmanager |
| structlog | Structured logging (Python) | https://www.structlog.org |
| Sentry | Error tracking | https://sentry.io |

**Critical metrics to track:**

```yaml
trading_pnl_daily_usd
trading_position_size_usd{market="...", direction="yes|no"}
trading_fill_rate_pct
trading_slippage_bps
model_edge_estimate{market="..."}
infra_ws_reconnect_count
infra_rpc_latency_ms
wallet_usdc_balance
wallet_matic_balance
```

---

### 1.8 Repository Structure

```
polymarket-trader/
├── docker-compose.yml
├── .env.example
├── requirements.txt
│
├── ingestion/
│   ├── polymarket_client.py      # REST + WS wrapper
│   ├── market_scanner.py         # discovers active markets
│   ├── onchain_listener.py       # Polygon event listener
│   └── data_store.py             # writes to TimescaleDB + Redis
│
├── alpha/
│   ├── probability_model.py      # main pricing model
│   ├── feature_engineering.py    # feature pipeline
│   ├── signal_aggregator.py      # combines signals → edge score
│   └── market_selector.py        # filters tradeable opportunities
│
├── execution/
│   ├── order_manager.py          # place/cancel/track
│   ├── risk_engine.py            # pre-trade checks & sizing
│   ├── wallet.py                 # USDC + MATIC management
│   └── position_tracker.py       # live position state
│
├── risk/
│   ├── kelly.py                  # Kelly criterion sizing
│   ├── exposure_limits.py        # hard position limits
│   ├── correlation_monitor.py    # cross-market correlation
│   └── drawdown_monitor.py       # circuit breakers
│
├── research/
│   ├── backtest.py               # event-driven backtester
│   ├── market_analysis.py        # exploratory tools
│   └── notebooks/
│
└── infra/
    ├── config.py                 # centralised config via pydantic
    ├── scheduler.py              # APScheduler main loop
    ├── monitoring.py             # Prometheus metrics
    └── logging.py                # structlog setup
```

---

## 2. Pricing & Probability Model

### 2.1 Problem Framing

Every binary market on Polymarket has a YES token and a NO token. At resolution, YES = 1.0 USDC and NO = 0.0 USDC. The market price `p` is the crowd's implied probability. Your edge is:

```
edge(market) = P_model(event = TRUE) - p_market
```

A trade is only initiated when `edge > threshold`, where threshold accounts for spread, slippage, and a minimum required return.

---

### 2.2 Skeleton Regression Model

The base model is a **logistic regression on cross-sectional features**, producing a probability estimate `p_hat ∈ (0, 1)`.

```
p_hat = σ( β₀ + β₁·x₁ + β₂·x₂ + ... + βₙ·xₙ )
```

where `σ(z) = 1 / (1 + exp(-z))` is the logistic (sigmoid) function.

The key challenge is **feature engineering** — mapping heterogeneous market contexts onto a comparable feature vector `X`.

#### Feature vector definition

```python
import numpy as np

def build_feature_vector(market: dict, signals: dict) -> np.ndarray:
    """
    Returns a feature vector X for a single binary market.

    Category groups
    ---------------
    X[0:4]   : Market microstructure
    X[4:8]   : Reference market signals
    X[8:12]  : External data signals
    X[12:16] : Temporal features
    X[16:20] : Historical base rates
    """

    # --- 1. Market microstructure features ---
    mid      = (signals["best_bid"] + signals["best_ask"]) / 2
    spread   = signals["best_ask"] - signals["best_bid"]
    volume   = signals.get("volume_24h", 0)
    oi       = signals.get("open_interest", 0)

    x_micro = np.array([
        mid,                            # current market implied probability
        spread / mid if mid > 0 else 0, # relative bid-ask spread (normalised)
        np.log1p(volume),               # log 24h volume (USDC)
        np.log1p(oi),                   # log open interest
    ])

    # --- 2. Reference market signals ---
    metaculus_p  = signals.get("metaculus_probability", mid)
    manifold_p   = signals.get("manifold_probability", mid)
    ref_spread   = abs(metaculus_p - manifold_p)
    poly_vs_meta = mid - metaculus_p

    x_ref = np.array([
        metaculus_p,
        manifold_p,
        ref_spread,                     # disagreement between reference markets
        poly_vs_meta,                   # Polymarket premium vs Metaculus
    ])

    # --- 3. External data signals ---
    news_sentiment  = signals.get("news_sentiment_score", 0.0)   # -1 to +1
    news_volume     = signals.get("news_article_count_24h", 0)
    social_momentum = signals.get("social_momentum_score", 0.0)  # z-score
    llm_prob        = signals.get("llm_probability_estimate", mid)

    x_external = np.array([
        news_sentiment,
        np.log1p(news_volume),
        social_momentum,
        llm_prob,
    ])

    # --- 4. Temporal features ---
    days_to_resolution = market.get("days_to_resolution", 30)
    hour_of_day        = signals.get("hour_utc", 12) / 24
    day_of_week        = signals.get("dow", 3) / 6
    recency_of_news    = signals.get("hours_since_last_news", 48) / 48

    x_temporal = np.array([
        np.log1p(days_to_resolution),
        hour_of_day,
        day_of_week,
        np.clip(recency_of_news, 0, 1),
    ])

    # --- 5. Historical base rates (category-level priors) ---
    category_base_rate = signals.get("category_base_rate_yes", 0.5)
    category_avg_vol   = signals.get("category_avg_volume", 0.0)
    similar_market_p   = signals.get("similar_resolved_market_mean", 0.5)
    n_similar_markets  = signals.get("n_similar_resolved_markets", 0)

    x_hist = np.array([
        category_base_rate,
        np.log1p(category_avg_vol),
        similar_market_p,
        np.log1p(n_similar_markets),
    ])

    return np.concatenate([x_micro, x_ref, x_external, x_temporal, x_hist])
```

#### Training the model

```python
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
import lightgbm as lgb

# ---- Option A: Logistic regression (interpretable, fast) ----
lr_pipeline = Pipeline([
    ("scaler", StandardScaler()),
    ("model", LogisticRegression(
        C=1.0,
        solver="lbfgs",
        max_iter=500,
        class_weight="balanced"
    ))
])

# ---- Option B: LightGBM (higher capacity, requires more data) ----
lgbm_pipeline = Pipeline([
    ("scaler", StandardScaler()),
    ("model", CalibratedClassifierCV(
        lgb.LGBMClassifier(
            n_estimators=200,
            learning_rate=0.05,
            max_depth=4,
            min_child_samples=20,
            subsample=0.8,
            colsample_bytree=0.8,
        ),
        cv=5,
        method="sigmoid"
    ))
])

# Training data: each row = one resolved market observation
# y = 1 if market resolved YES, 0 if resolved NO
X_train, y_train = load_resolved_markets()
model = lgbm_pipeline.fit(X_train, y_train)
```

---

### 2.3 Bayesian Update Layer

The regression gives a **prior**. As new information arrives during a market's lifetime, update the estimate using Bayes' rule:

```
P(YES | new_evidence) = P(new_evidence | YES) · P_prior(YES)
                        ─────────────────────────────────────
                                   P(new_evidence)
```

In practice, implement this as a weighted log-odds update:

```python
def bayesian_update(
    p_prior: float,
    likelihood_ratio: float,  # P(evidence|YES) / P(evidence|NO)
    weight: float = 0.3       # how much to trust this evidence update
) -> float:
    """
    Update probability given new evidence via likelihood ratio.
    weight controls how strongly the update shifts the prior.
    """
    log_odds_prior     = np.log(p_prior / (1 - p_prior))
    log_lr             = np.log(likelihood_ratio) * weight
    log_odds_posterior = log_odds_prior + log_lr
    return 1 / (1 + np.exp(-log_odds_posterior))


# Example: breaking news strongly suggests YES
p_prior = 0.42          # model's base estimate
lr = 3.5                # news ~3.5x more likely given YES than NO
p_posterior = bayesian_update(p_prior, lr, weight=0.4)
# p_posterior ≈ 0.60
```

---

### 2.4 Edge Calculation & Trade Signal

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class TradeSignal:
    condition_id: str
    token_id: str
    direction: str          # "YES" or "NO"
    p_model: float          # model probability of YES
    p_market: float         # market mid price (= implied prob of YES)
    edge: float             # p_model - p_market (for YES) or inverted for NO
    confidence: float       # model uncertainty score [0, 1]
    recommended_action: str # "BUY_YES", "BUY_NO", "PASS"


def compute_signal(
    market: dict,
    signals: dict,
    model,
    min_edge: float = 0.05,
    min_confidence: float = 0.6,
    max_spread: float = 0.04,
) -> TradeSignal:

    X = build_feature_vector(market, signals).reshape(1, -1)
    p_model = float(model.predict_proba(X)[0, 1])

    best_bid = signals["best_bid"]
    best_ask = signals["best_ask"]
    spread   = best_ask - best_bid
    mid      = (best_bid + best_ask) / 2

    # Edge on each side: you pay the ask to buy YES, pay (1-bid) to buy NO
    edge_yes = p_model - best_ask
    edge_no  = (1 - p_model) - (1 - best_bid)

    confidence = min(
        1.0,
        (1 - spread / 0.10) *
        min(1.0, signals.get("volume_24h", 0) / 5000)
    )

    if spread > max_spread or confidence < min_confidence:
        return TradeSignal(
            condition_id=market["condition_id"],
            token_id=signals["yes_token_id"],
            direction="NONE", p_model=p_model, p_market=mid,
            edge=max(edge_yes, edge_no), confidence=confidence,
            recommended_action="PASS"
        )

    if edge_yes > edge_no and edge_yes > min_edge:
        return TradeSignal(
            condition_id=market["condition_id"],
            token_id=signals["yes_token_id"],
            direction="YES", p_model=p_model, p_market=mid,
            edge=edge_yes, confidence=confidence,
            recommended_action="BUY_YES"
        )
    elif edge_no > min_edge:
        return TradeSignal(
            condition_id=market["condition_id"],
            token_id=signals["no_token_id"],
            direction="NO", p_model=p_model, p_market=mid,
            edge=edge_no, confidence=confidence,
            recommended_action="BUY_NO"
        )
    else:
        return TradeSignal(
            condition_id=market["condition_id"],
            token_id=signals["yes_token_id"],
            direction="NONE", p_model=p_model, p_market=mid,
            edge=max(edge_yes, edge_no), confidence=confidence,
            recommended_action="PASS"
        )
```

---

### 2.5 Model Calibration & Validation

Calibration is the most important model property for a prediction market system. A model that outputs `p = 0.70` should be correct ~70% of the time.

```python
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.calibration import calibration_curve

def evaluate_model(model, X_test, y_test):
    p_hat = model.predict_proba(X_test)[:, 1]

    print(f"Brier Score : {brier_score_loss(y_test, p_hat):.4f}")  # lower = better; random=0.25
    print(f"Log Loss    : {log_loss(y_test, p_hat):.4f}")
    print(f"Mean edge   : {(p_hat - 0.5).mean():.4f}")             # should be near 0

    # Calibration curve: fraction_pos should equal mean_pred
    fraction_pos, mean_pred = calibration_curve(y_test, p_hat, n_bins=10)
    # Plot these — any systematic deviation is a calibration error you are paying for
```

---

## 3. Algorithmic Implementation

### 3.1 Position Sizing: Kelly Criterion

The Kelly criterion determines the optimal fraction of bankroll to bet on a single market, maximising long-run wealth growth.

**Full Kelly formula for binary bets:**

```
f* = (b·p - q) / b

where:
  p = probability of winning (model estimate)
  q = 1 - p (probability of losing)
  b = net odds = (1 - price) / price
  f* = fraction of bankroll to bet
```

For prediction markets, `b` is determined by the price you pay:

```python
def kelly_fraction(
    p_model: float,       # model probability of YES
    price: float,         # ask price you pay to buy YES token
    fraction: float = 0.25  # use fractional Kelly to reduce variance
) -> float:
    """
    Compute Kelly fraction for a prediction market position.

    b = (1 - price) / price  (net profit per dollar staked if correct)

    Full Kelly is theoretically optimal but highly volatile in practice.
    Standard practice: use 0.25 to 0.50 of full Kelly.
    """
    if price <= 0 or price >= 1:
        return 0.0

    b = (1 - price) / price
    q = 1 - p_model

    full_kelly = max(0.0, (b * p_model - q) / b)

    return full_kelly * fraction


# Example
# p_model = 0.65, ask = 0.55
# b = 0.818
# full_kelly = (0.818*0.65 - 0.35) / 0.818 = 0.222
# fractional_kelly (0.25x) = 0.056 → bet 5.6% of bankroll
```

**Dollar sizing:**

```python
def compute_position_size(
    signal: TradeSignal,
    bankroll: float,
    kelly_fraction_param: float = 0.25,
    max_position_usd: float = 500.0,
    min_position_usd: float = 20.0,
) -> float:
    """Returns position size in USDC."""

    price      = signal.p_market + signal.edge / 2  # approximate fill price
    f          = kelly_fraction(signal.p_model, price, kelly_fraction_param)
    raw_size   = bankroll * f
    scaled     = raw_size * signal.confidence       # confidence scaling

    return float(np.clip(scaled, min_position_usd, max_position_usd))
```

---

### 3.2 Order Execution

#### Order types

Polymarket CLOB supports **limit orders** and **market orders (FOK)**. Limit orders are strongly preferred — market orders in thin books incur severe slippage.

```python
from py_clob_client.clob_types import OrderArgs
from py_clob_client.order_builder.constants import BUY

class OrderManager:

    def __init__(self, client, risk_engine):
        self.client      = client
        self.risk        = risk_engine
        self.open_orders = {}   # order_id -> order metadata

    def place_limit_order(
        self,
        signal: TradeSignal,
        size_usd: float,
        limit_price: Optional[float] = None,
        slippage_tolerance: float = 0.005,
    ) -> Optional[str]:
        """
        Place a passive limit order priced slightly inside the spread.
        Returns order_id or None if risk check fails.
        """
        if not self.risk.pre_trade_check(signal, size_usd):
            return None

        if limit_price is None:
            book = get_live_book(signal.token_id)
            if signal.direction == "YES":
                limit_price = round(book["best_ask"] - slippage_tolerance, 4)
            else:
                limit_price = round(1 - book["best_bid"] - slippage_tolerance, 4)

        token_qty  = size_usd / limit_price
        order_args = OrderArgs(
            price=limit_price,
            size=round(token_qty, 2),
            side=BUY,
            token_id=signal.token_id,
        )

        try:
            resp     = self.client.create_and_post_order(order_args)
            order_id = resp.get("orderID")
            if order_id:
                self.open_orders[order_id] = {
                    "signal": signal, "size_usd": size_usd,
                    "limit_price": limit_price, "placed_at": time.time(),
                    "status": "OPEN"
                }
            return order_id
        except Exception as e:
            logger.error("order_placement_failed", error=str(e))
            return None

    def cancel_stale_orders(self, max_age_seconds: int = 300):
        """Cancel unfilled orders older than max_age_seconds."""
        now = time.time()
        for order_id, o in list(self.open_orders.items()):
            if now - o["placed_at"] > max_age_seconds and o["status"] == "OPEN":
                try:
                    self.client.cancel(order_id)
                    self.open_orders[order_id]["status"] = "CANCELLED"
                except Exception as e:
                    logger.warning("cancel_failed", order_id=order_id, error=str(e))

    def reprice_on_model_change(self, signal: TradeSignal, drift_threshold: float = 0.03):
        """Cancel and replace open orders if model estimate drifted > threshold."""
        for order_id, o in list(self.open_orders.items()):
            if (o["signal"].condition_id == signal.condition_id
                    and o["status"] == "OPEN"
                    and abs(o["limit_price"] - signal.p_model) > drift_threshold):
                self.client.cancel(order_id)
                self.open_orders[order_id]["status"] = "CANCELLED"
                self.place_limit_order(signal, o["size_usd"])
```

#### Order flow

```
signal arrives
      │
      ▼
pre-trade risk check ──► FAIL → log and skip
      │ PASS
      ▼
compute passive limit price (inside spread)
      │
      ▼
submit order to CLOB
      │
      ├── filled? → update position tracker, record fill
      │
      └── unfilled after N seconds?
              ├── model price unchanged → cancel (opportunity gone)
              └── model price moved > 3¢ → reprice and resubmit
```

---

### 3.3 Position Tracker

```python
from dataclasses import dataclass, field

@dataclass
class Position:
    condition_id:   str
    token_id:       str
    direction:      str     # "YES" or "NO"
    size:           float   # token quantity held
    avg_entry:      float   # average price paid
    current_price:  float   # latest mid price
    cost_basis:     float   # total USDC spent
    unrealised_pnl: float = 0.0
    fills:          list    = field(default_factory=list)

    @property
    def market_value(self) -> float:
        return self.size * self.current_price

    @property
    def return_pct(self) -> float:
        return (self.market_value - self.cost_basis) / self.cost_basis if self.cost_basis else 0.0


class PositionTracker:

    def __init__(self):
        self.positions: dict[str, Position] = {}
        self.closed_pnl: float = 0.0

    @property
    def total_exposure_usd(self) -> float:
        return sum(p.cost_basis for p in self.positions.values())

    @property
    def unrealised_pnl(self) -> float:
        return sum(p.unrealised_pnl for p in self.positions.values())

    def on_fill(self, fill: dict):
        tid = fill["token_id"]
        price, qty = fill["price"], fill["size"]

        if tid not in self.positions:
            self.positions[tid] = Position(
                condition_id=fill["condition_id"], token_id=tid,
                direction=fill["direction"], size=qty, avg_entry=price,
                current_price=price, cost_basis=price * qty, fills=[fill]
            )
        else:
            p = self.positions[tid]
            new_cost  = p.cost_basis + price * qty
            new_size  = p.size + qty
            p.avg_entry = new_cost / new_size
            p.size, p.cost_basis = new_size, new_cost
            p.fills.append(fill)

    def on_resolution(self, token_id: str, outcome: float):
        """outcome: 1.0 = YES wins, 0.0 = NO wins."""
        if token_id not in self.positions:
            return
        p = self.positions.pop(token_id)
        realised = p.size * outcome - p.cost_basis
        self.closed_pnl += realised
        logger.info("position_resolved", token_id=token_id,
                    outcome=outcome, realised_pnl=realised)

    def update_prices(self, prices: dict[str, float]):
        for tid, mid in prices.items():
            if tid in self.positions:
                p = self.positions[tid]
                p.current_price  = mid
                p.unrealised_pnl = p.market_value - p.cost_basis
```

---

### 3.4 Main Trading Loop

```python
import asyncio

async def trading_loop(
    scanner, model, order_manager: OrderManager,
    position_tracker: PositionTracker, risk_engine,
    bankroll: float, interval_seconds: int = 15,
):
    while True:
        try:
            # 1. Discover liquid, active markets
            markets = scanner.get_tradeable_markets(
                min_volume_24h=1000,
                max_spread=0.04,
                min_days_to_resolution=1,
                max_days_to_resolution=60,
            )

            # 2. Compute signals
            signals = [
                s for m in markets
                if (s := compute_signal(m, get_live_signals(m), model)).recommended_action != "PASS"
            ]
            signals.sort(key=lambda s: s.edge * s.confidence, reverse=True)

            # 3. Deploy capital within portfolio limits
            available    = risk_engine.available_capital(bankroll, position_tracker.total_exposure_usd)
            deployed     = 0.0

            for signal in signals:
                if deployed >= available:
                    break
                if signal.token_id in position_tracker.positions:
                    continue   # already have a position in this market

                size_usd = min(compute_position_size(signal, bankroll), available - deployed)
                order_id = order_manager.place_limit_order(signal, size_usd)
                if order_id:
                    deployed += size_usd

            # 4. Housekeeping
            order_manager.cancel_stale_orders(max_age_seconds=300)
            position_tracker.update_prices(get_all_mid_prices(position_tracker.positions))

            # 5. Log snapshot
            logger.info("portfolio_snapshot",
                        exposure=position_tracker.total_exposure_usd,
                        unrealised_pnl=position_tracker.unrealised_pnl,
                        closed_pnl=position_tracker.closed_pnl,
                        n_positions=len(position_tracker.positions))

        except Exception as e:
            logger.error("trading_loop_error", error=str(e), exc_info=True)

        await asyncio.sleep(interval_seconds)
```

---

## 4. Hedging & Risk Management

### 4.1 Risk Philosophy

Prediction markets present unique risks versus traditional financial markets:

1. **Binary resolution risk** — every position goes to exactly 0 or 1. There is no partial outcome.
2. **Correlation clusters** — many markets (e.g. 20 markets on the same election) resolve simultaneously, creating concentrated tail exposure.
3. **Liquidity discontinuity** — thin order books can make large positions impossible to exit before resolution.
4. **Model calibration error** — systematic miscalibration, especially near 0 or 1, can be catastrophic.

Risk management must address all four.

---

### 4.2 Pre-Trade Risk Engine

```python
class RiskEngine:

    def __init__(self, config: dict):
        self.max_single_position_usd   = config["max_single_position_usd"]   # e.g. 500
        self.max_total_exposure_usd    = config["max_total_exposure_usd"]     # e.g. 10_000
        self.max_category_exposure_usd = config["max_category_exposure_usd"]  # e.g. 3_000
        self.min_edge                  = config["min_edge"]                    # e.g. 0.05
        self.max_spread                = config["max_spread"]                  # e.g. 0.04
        self.daily_loss_limit_usd      = config["daily_loss_limit_usd"]        # e.g. 1_000
        self.is_halted                 = False
        self.daily_pnl                 = 0.0

    def pre_trade_check(
        self,
        signal: TradeSignal,
        size_usd: float,
        position_tracker,
        market_metadata: dict,
    ) -> tuple[bool, str]:
        """Returns (approved: bool, reason: str)."""

        if self.is_halted:
            return False, "system_halted"

        if self.daily_pnl < -self.daily_loss_limit_usd:
            self.is_halted = True
            logger.critical("daily_loss_limit_breached", pnl=self.daily_pnl)
            return False, "daily_loss_limit"

        if signal.edge < self.min_edge:
            return False, f"insufficient_edge_{signal.edge:.4f}"

        if signal.confidence < 0.5:
            return False, f"low_confidence_{signal.confidence:.2f}"

        if size_usd > self.max_single_position_usd:
            return False, f"position_too_large_{size_usd:.0f}"

        if position_tracker.total_exposure_usd + size_usd > self.max_total_exposure_usd:
            return False, "total_exposure_limit"

        category          = market_metadata.get("category", "unknown")
        category_exposure = sum(
            p.cost_basis for p in position_tracker.positions.values()
            if p.condition_id in get_markets_by_category(category)
        )
        if category_exposure + size_usd > self.max_category_exposure_usd:
            return False, f"category_concentration_{category}"

        book_depth = market_metadata.get("book_depth_at_5pct", 0)
        if book_depth < size_usd * 1.5:
            return False, "insufficient_liquidity"

        return True, "approved"

    def available_capital(self, bankroll: float, current_exposure: float) -> float:
        max_deploy = bankroll * 0.40   # never deploy more than 40% of bankroll
        return max(0.0, min(max_deploy, self.max_total_exposure_usd) - current_exposure)
```

---

### 4.3 Portfolio Exposure Limits

| Limit | Rule | Rationale |
|---|---|---|
| Single market | max 2% of bankroll | Binary loss cannot exceed 2% |
| Single category | max 20% of bankroll | Election night / sports day concentration |
| Correlated cluster | max 15% of bankroll | Markets resolving on the same underlying event |
| Total deployed | max 40% of bankroll | Reserve capital for new opportunities |
| Days to resolution | max 60 days | Avoid locking capital too long |
| Minimum liquidity | 1.5× position size in book | Must be able to exit if model changes |

---

### 4.4 Correlation Risk

Many Polymarket markets are highly correlated — "Will Trump win Iowa?", "Will Trump win Michigan?", and "Will Trump win the presidency?" all resolve together. Treat such markets as a **correlated cluster** and apply a group exposure limit.

```python
def compute_cluster_exposure(
    positions: dict[str, "Position"],
    market_graph: dict,
    new_condition_id: str,
    new_size_usd: float,
) -> float:
    """Returns total cluster exposure after adding new_size_usd."""
    cluster = get_cluster(market_graph, new_condition_id)
    cluster_exposure = sum(
        p.cost_basis for p in positions.values()
        if p.condition_id in cluster
    )
    return cluster_exposure + new_size_usd


def build_market_correlation_graph(markets: list[dict]) -> dict:
    """
    Heuristic: markets in the same category resolving within 2 days
    of each other are treated as correlated.
    Improve with LLM-extracted topic similarity for production.
    """
    graph = defaultdict(set)
    for i, m1 in enumerate(markets):
        for m2 in markets[i+1:]:
            if (m1.get("category") == m2.get("category")
                    and abs((m1["end_date"] - m2["end_date"]).days) <= 2):
                graph[m1["condition_id"]].add(m2["condition_id"])
                graph[m2["condition_id"]].add(m1["condition_id"])
    return dict(graph)
```

---

### 4.5 Hedging Strategies

#### Cross-market hedge

If holding a large YES position on market A, and a highly correlated market B exists with cheap NO tokens, buy NO on B as a partial hedge.

```python
def find_hedge_opportunity(
    position: "Position",
    correlation_graph: dict,
    min_hedge_edge: float = 0.02,
) -> Optional[TradeSignal]:
    """
    For a given position, find the cheapest correlated hedge.
    Returns a hedge TradeSignal or None.
    """
    correlated = correlation_graph.get(position.condition_id, set())
    best_hedge, best_cost = None, 1.0

    for cid in correlated:
        hedge_market = get_market(cid)
        book         = get_live_book(hedge_market["no_token_id"])
        no_ask       = 1 - book["best_bid"]  # cost to buy NO = 1 - YES_bid

        if no_ask < best_cost:
            best_cost  = no_ask
            best_hedge = TradeSignal(
                condition_id=cid,
                token_id=hedge_market["no_token_id"],
                direction="NO",
                p_model=1 - position.current_price,
                p_market=no_ask,
                edge=min_hedge_edge,
                confidence=0.9,
                recommended_action="BUY_NO"
            )

    # Only hedge if the cost is reasonable relative to position value
    return best_hedge if (best_hedge and best_cost < position.avg_entry * 0.5) else None
```

#### Time-decay exit

As a market approaches resolution without a clear signal, unwind positions whose edge has decayed:

```python
def check_exit_conditions(
    position: "Position",
    signal: TradeSignal,
    days_to_resolution: float,
) -> tuple[bool, str]:
    """Returns (should_exit: bool, reason: str)."""

    if abs(signal.edge) < 0.02:
        return True, "edge_decayed"

    if signal.direction not in ("PASS", position.direction):
        return True, "model_direction_flipped"

    if position.return_pct > 0.15 and days_to_resolution < 3:
        return True, "lock_in_profit_near_resolution"

    if position.return_pct < -0.10 and days_to_resolution < 1:
        return True, "cut_loss_at_resolution"

    return False, "hold"
```

---

### 4.6 Drawdown Controls & Circuit Breakers

```python
class DrawdownMonitor:

    def __init__(
        self,
        daily_loss_limit_usd: float,
        weekly_loss_limit_usd: float,
        max_drawdown_pct: float = 0.15,   # halt at 15% drawdown from peak
    ):
        self.daily_loss_limit  = daily_loss_limit_usd
        self.weekly_loss_limit = weekly_loss_limit_usd
        self.max_drawdown_pct  = max_drawdown_pct
        self.peak_bankroll     = None
        self.daily_pnl_series  = []

    def check(self, current_bankroll: float, daily_pnl: float) -> tuple[bool, str]:
        """Returns (should_halt: bool, reason: str). Call once per main loop."""
        if self.peak_bankroll is None:
            self.peak_bankroll = current_bankroll

        self.peak_bankroll = max(self.peak_bankroll, current_bankroll)

        drawdown = (self.peak_bankroll - current_bankroll) / self.peak_bankroll
        if drawdown > self.max_drawdown_pct:
            return True, f"max_drawdown_{drawdown:.1%}"

        if daily_pnl < -self.daily_loss_limit:
            return True, f"daily_loss_{daily_pnl:.0f}"

        self.daily_pnl_series.append(daily_pnl)
        if sum(self.daily_pnl_series[-7:]) < -self.weekly_loss_limit:
            return True, f"weekly_loss_{sum(self.daily_pnl_series[-7:]):.0f}"

        return False, "ok"
```

**Halt procedure:**

```python
async def emergency_halt(
    order_manager: OrderManager,
    position_tracker: PositionTracker,
    reason: str,
):
    """
    On circuit breaker trigger:
    1. Cancel all open orders immediately.
    2. Alert operations team.
    3. Do NOT force-liquidate positions — thin books produce terrible fills.
       Let positions run to resolution unless a specific exit is warranted.
    """
    logger.critical("circuit_breaker_triggered", reason=reason)

    for order_id in list(order_manager.open_orders.keys()):
        try:
            order_manager.client.cancel(order_id)
        except Exception as e:
            logger.error("cancel_failed_during_halt", order_id=order_id, error=str(e))

    send_alert(
        level="CRITICAL",
        message=f"Trading halted: {reason}",
        pnl_snapshot={
            "unrealised":  position_tracker.unrealised_pnl,
            "realised":    position_tracker.closed_pnl,
            "exposure":    position_tracker.total_exposure_usd,
            "n_positions": len(position_tracker.positions),
        }
    )
```

---

### 4.7 Risk Reporting

```python
def generate_risk_report(
    position_tracker: PositionTracker,
    drawdown_monitor: DrawdownMonitor,
) -> dict:
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "bankroll_summary": {
            "total_exposure_usd":   position_tracker.total_exposure_usd,
            "unrealised_pnl_usd":   position_tracker.unrealised_pnl,
            "closed_pnl_usd":       position_tracker.closed_pnl,
            "peak_bankroll_usd":    drawdown_monitor.peak_bankroll,
        },
        "positions": [
            {
                "condition_id":   p.condition_id,
                "direction":      p.direction,
                "cost_basis":     p.cost_basis,
                "market_value":   p.market_value,
                "unrealised_pnl": p.unrealised_pnl,
                "return_pct":     f"{p.return_pct:.1%}",
            }
            for p in position_tracker.positions.values()
        ],
    }
```

---

## Appendix: Key Libraries & References

| Library | Version | URL |
|---|---|---|
| py-clob-client | latest | https://github.com/Polymarket/py-clob-client |
| web3.py | 6.x | https://web3py.readthedocs.io |
| eth-account | 0.10.x | https://eth-account.readthedocs.io |
| scikit-learn | 1.4.x | https://scikit-learn.org |
| lightgbm | 4.x | https://lightgbm.readthedocs.io |
| pandas | 2.x | https://pandas.pydata.org |
| numpy | 1.26.x | https://numpy.org |
| APScheduler | 3.x | https://apscheduler.readthedocs.io |
| FastAPI | 0.110.x | https://fastapi.tiangolo.com |
| TimescaleDB | 2.x | https://docs.timescale.com |
| redis-py | 5.x | https://redis-py.readthedocs.io |
| structlog | 24.x | https://www.structlog.org |
| Prometheus client | 0.20.x | https://github.com/prometheus/client_python |
| pydantic | 2.x | https://docs.pydantic.dev |

---

*This document is a living specification. Update after each sprint.*
