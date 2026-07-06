# trading-bot

A Python trading bot for **US stocks and ETFs through Interactive Brokers (IBKR)**,
built around one idea:

> **The same strategy code runs unchanged in backtest, paper trading, and live
> trading.** Only the data source and the broker adapter are swapped underneath.

There is **no real trading strategy in this repo yet** -- only a clearly-labeled
placeholder (`BuyAndHoldPlaceholder`) that proves the whole pipeline works:
data -> signal -> risk -> execution -> logging, in all three modes.

## How the parity trick works

```
                 +--------------------+       +------------------+
   DataProvider  |   TradingEngine    |       |   RiskManager    |
  (an adapter)   |   (ONE shared loop)|       | (gate for EVERY  |
        |        |                    |       |     order)       |
        v        |  snapshot          |       +------------------+
  MarketSnapshot --> Strategy.generate_signal --> risk.evaluate --> Broker
                 |  (pure function!)  |                        (an adapter)
                 +--------------------+
```

- **`DataProvider`** (`src/trading_bot/data/base.py`) yields `MarketSnapshot`s.
  - `ReplayDataProvider` replays historical bars (backtest).
  - `IBKRDataProvider` streams live bars from IB Gateway/TWS (paper + live).
- **`Strategy`** (`src/trading_bot/strategy/base.py`) is a *pure function*:
  snapshot in, target weights out. No network, no orders, no side effects --
  so it literally cannot behave differently between modes.
- **`RiskManager`** (`src/trading_bot/risk/risk_manager.py`) turns weights into
  sized orders and enforces position caps, exposure caps, stop-losses, and a
  daily-loss circuit breaker. No order reaches a broker without passing it.
- **`Broker`** (`src/trading_bot/execution/base.py`) executes orders.
  - `SimBroker` simulates fills with slippage + commission (backtest).
  - `IBKRBroker` submits real orders via Gateway/TWS (paper + live -- same
    class, no `if paper:` anywhere; the port/account decides).

Backtest, paper, and live are just three different ways of wiring adapters
into the same `TradingEngine` (`src/trading_bot/engine/core.py`).

## Setup

### 1. Install dependencies

Dependency management uses [uv](https://docs.astral.sh/uv/) (installed via
`curl -LsSf https://astral.sh/uv/install.sh | sh` if you don't have it):

```bash
make install        # = uv sync; creates .venv and installs everything
```

### 2. Configure

```bash
cp .env.example .env    # then edit .env
```

| Env var | Meaning |
|---|---|
| `IBKR_HOST` | Where IB Gateway/TWS runs (default `127.0.0.1`) |
| `IBKR_PORT` | `7497` TWS paper, `7496` TWS live, `4002` Gateway paper, `4001` Gateway live |
| `IBKR_CLIENT_ID` | Any integer, unique per simultaneous API connection |
| `IBKR_ACCOUNT` | Your account ID (paper accounts start with `DU`); optional |
| `TRADING_MODE` | `paper` (default) or `live` -- see safety section |
| `GEMINI_API_KEY` | Only for the optional sentiment module (free tier is fine) |
| `SENTIMENT_PROVIDER` | `gemini` (default) or `ollama` (local, keyless) |
| `ALERT_WEBHOOK_URL` | Optional Slack/Telegram-style webhook for alerts |

Non-secret settings (symbols, timeframe, risk limits, fees, backtest window)
live in `config/config.yaml`. Both files load into one validated `Settings`
object at startup; a missing or invalid key fails immediately with a message
naming the field.

### 3. IBKR prerequisites (for paper/live only -- backtests don't need IBKR)

IBKR's API has **no API keys**. The bot connects to a locally running
**IB Gateway** or **Trader Workstation (TWS)**, which is where you log in:

1. Create an IBKR account and enable paper trading (Canada-friendly; this repo
   assumes IBKR precisely because Alpaca isn't available there).
2. Install IB Gateway (lighter) or TWS, log into your **paper** account.
3. Enable the API: Configure -> API -> Settings -> check
   *Enable ActiveX and Socket Clients*; note the socket port; keep
   *Read-Only API* unchecked if the bot should place (paper) orders.

## Running each mode

### Backtest (no IBKR needed)

```bash
make backtest       # = uv run python scripts/run_backtest.py
```

Uses cached real bars from `data/` if present, otherwise falls back to
deterministic **synthetic data** (with a loud warning) so the pipeline always
runs. Prints a PnL + trade-count summary. To cache real IBKR data first
(needs Gateway/TWS running):

```bash
uv run python scripts/fetch_data.py
```

### Paper trading

```bash
make paper          # DRY-RUN: logs intended orders, sends nothing
make paper-execute  # actually places PAPER orders
```

If Gateway/TWS isn't running you get a checklist error telling you what to
start and which port to check.

### Live trading (guarded)

```bash
make live
```

Refuses to run unless **all** of these hold:

1. `TRADING_MODE=live` set explicitly in `.env`,
2. you type `LIVE` (exactly) at the interactive prompt,
3. the connected account/port actually are live (`verify_mode` cross-checks
   the account prefix and port against `TRADING_MODE` in both directions),
4. and even then, orders are only sent with `--execute` -- without it, live
   mode is a dry-run.

### Kill switch

```bash
make kill           # = uv run python scripts/kill.py
```

Cancels **all** open orders, then closes **all** positions, then exits.
`Ctrl-C` / `SIGTERM` on a running bot triggers the same flatten logic (in
dry-run mode it only halts -- it never touches positions the bot didn't open).
The daily-loss circuit breaker fires it automatically.

## The sentiment module (optional)

`news -> LLM -> per-symbol score in [-1, 1]` -- an *input* a future strategy
may read from `snapshot.sentiment`; it is **not** a strategy. Disabled by
default (`sentiment.enabled: false` in config.yaml). The placeholder runs
fine without it.

- Headlines come from Yahoo Finance's free per-ticker RSS (no key), behind a
  swappable `NewsProvider` interface. (IBKR news needs paid subscriptions.)
- Scoring goes through `SentimentProvider`, selected by `SENTIMENT_PROVIDER`:
  - `gemini` (default): Google's `google-genai` SDK, model configurable via
    `sentiment.gemini_model` (default `gemini-2.0-flash`). Free-tier friendly.
  - `ollama`: a local model via [Ollama](https://ollama.com) -- no API key.
    `ollama pull llama3.2 && ollama serve`, then flip the env var.
- The model must answer strict JSON; replies are parsed defensively (retry
  once, then neutral-with-logged-error). Scores are cached by
  (symbol, article-id) in `data/sentiment_cache.json` to avoid re-scoring.

Sanity-check the whole sentiment path (no IBKR needed):

```bash
uv run python scripts/check_sentiment.py
```

## Safety model (read this before going live)

- **Paper by default.** Every default -- env, config, scripts -- points at
  paper. Live requires the three explicit steps listed above.
- **Dry-run by default.** `run_paper.py` and `run_live.py` both log intended
  orders unless you pass `--execute`.
- **Risk manager before every order**, with per-position caps, total-exposure
  caps, stop-losses, a minimum order value (anti-churn), and a sticky
  daily-loss breaker that flattens and halts.
- **Idempotent orders.** Every order carries a deterministic client order ID
  (IBKR `orderRef`); a retry after a timeout can never double-submit.
- **Retries with backoff** on flaky API calls; **rotating file logs** of every
  signal, risk decision, order, and fill in `logs/trading_bot.log`.

## Development

```bash
make test   # pytest
make lint   # ruff check + format check
make fmt    # auto-fix
```

Project layout mirrors the architecture: one directory per adapter boundary
(`data/`, `strategy/`, `risk/`, `execution/`, `sentiment/`), the shared loop
in `engine/`, thin mode wiring in `backtest/engine.py` and
`engine/live_engine.py`, and entry points in `scripts/`.

### Writing your real strategy (later)

Subclass `Strategy`, implement `generate_signal(snapshot) -> Signal`, keep it
pure, and inject it where `BuyAndHoldPlaceholder` is constructed
(`backtest/engine.py`'s `run_backtest(..., strategy=...)` and
`engine/live_engine.py`). Everything else -- risk, execution, logging, all
three modes -- already works.
