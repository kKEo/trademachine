# tradesignals

Prototype trade-signal engine with a provider abstraction layer. First adapters: **Alpaca** and **XTB**.

```
Provider (Alpaca | XTB | Replay)
   └─ MarketDataProvider.stream()  → Tick / Bar
        └─ BarAggregator (per symbol × strategy timeframe)
             └─ Strategy.on_bar() → Signal
                  └─ SignalEngine → [execute] target position → RiskManager → Broker.submit_order()
```

## Documentation

- [User guide](docs/guide/user/README.md): quick start, everyday use, reading the output, safety, FAQ.
- [Operator guide](docs/guide/operator/README.md): architecture, configuration and command reference, monitoring, runbook, known gaps.
- [Tester guide](docs/guide/tester/README.md): running the tests, what blocks a merge, adding tests, exploratory sessions.

Build or check the docs with `make docs` / `make docs-check` (see `docs/README.md`).

## Layout

| Path | Role |
|---|---|
| `tradesignals/models.py` | Canonical `Tick`, `Bar`, `Signal`, `OrderRequest`, `Order`, `Position`, `Account` |
| `tradesignals/interfaces.py` | `MarketDataProvider`, `Broker`, `SymbolMapper` — the abstraction layer |
| `tradesignals/providers/alpaca.py` | `AlpacaData`, `AlpacaBroker` (alpaca-py, IEX feed + paper) |
| `tradesignals/providers/xtb.py` | `XtbSession`, `XtbData`, `XtbBroker` (raw xAPI JSON/WebSocket) |
| `tradesignals/providers/fake.py` | `ReplayProvider`, `PaperBroker` — no network, used by tests |
| `tradesignals/bars.py` | `BarAggregator`: ticks and finer bars become bars of the strategy's timeframe |
| `tradesignals/indicators.py` | Incremental `SMA`, `EMA`, `RSI` |
| `tradesignals/config.py` | `env_bool`: strict parsing of `ALPACA_PAPER` / `XTB_DEMO` |
| `tradesignals/strategies/` | `Strategy` base + `SmaCross`, `RsiReversion` |
| `tradesignals/engine.py` | `SignalEngine`: wiring, warm-up, execution, kill switch |
| `tradesignals/risk.py` | Pre-trade guards (notional, position, open positions, daily loss, stale quote) |
| `tradesignals/cli.py` | Command line runner |

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[alpaca,dev]"
cp .env.example .env   # fill in keys
pytest
```

## Run

```bash
# Signals only, Alpaca free IEX stream
tradesignals --data alpaca --symbols AAPL,MSFT --strategy sma_cross

# Alpaca data, execute against the in-memory paper broker
tradesignals --data alpaca --broker paper --symbols NVDA --strategy rsi_reversion --execute

# XTB demo account end to end
tradesignals --data xtb --broker xtb --symbols AAPL --strategy sma_cross --execute --qty 1
```

`--execute` against a live account (`ALPACA_PAPER=false` / `XTB_DEMO=false`) is refused unless `--allow-live` is also given.

## Adding a strategy

Subclass `Strategy`, implement `on_bar(bar) -> Signal | None`, set `timeframe` and `warmup_bars`,
register in `strategies/__init__.py`. Strategies are pure: no provider or broker access.

## Adding a provider

Implement `MarketDataProvider` and/or `Broker`, emit only canonical models, map symbols with
`SymbolMapper`, register factories in `providers/__init__.py`. `ReplayProvider`/`PaperBroker` show
the expected semantics.

## XTB notes

- Symbol suffix differs by account/instrument: CFDs are `AAPL.US`, real stocks are usually `AAPL.US_9`.
  Check with `getAllSymbols` and set `XTB_SYMBOL_SUFFIX`.
- Real stocks cannot be shorted; a SELL closes the existing position (`type=2`). The adapter handles this.
- xAPI enforces ~1 request / 200 ms on the main socket; `XtbSession.command` serialises and throttles.
- Order updates are polled every 2 s via `tradeTransactionStatus`.

## Alpaca notes

- Free plan = IEX feed, 30 symbols, 1 WebSocket connection. `ALPACA_FEED=sip` needs a paid data plan.
- Paper and live use the same API; `ALPACA_PAPER=true` by default.

## Not yet done (next steps)

- Backtester reusing `ReplayProvider` with historical bars + `PaperBroker`
- Persistence of signals/orders (SQLite) and a signal sink to Telegram/ntfy
- Reconnect/backoff in both live adapters
- XTB `getTrades` stream instead of polling; Alpaca extended-hours flag
- Position sizing by volatility / account equity instead of fixed qty
