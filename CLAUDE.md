# tradesignals — project guide for Claude Code

Provider-agnostic trade-signal engine. Python 3.11+, asyncio, no framework.
Adapters: Alpaca (alpaca-py) and XTB (raw xAPI over websockets). Read README.md first.

## Commands
- Install: `pip install -e ".[alpaca,dev]"`
- Tests: `pytest -q` (must stay green; add a test for every behaviour change)
- Lint/format: `ruff check . && ruff format .` (add ruff if missing)
- Run signal-only: `tradesignals --data alpaca --symbols AAPL --strategy sma_cross`
- Run on demo broker: `tradesignals --data xtb --broker xtb --symbols AAPL --execute --qty 1`

## Architecture rules (do not break)
- `interfaces.py` (`MarketDataProvider`, `Broker`, `SymbolMapper`) is the abstraction layer.
  The engine and strategies import ONLY from `models.py`, `interfaces.py`, `strategies/`, `risk.py`.
  Never import a provider module from `engine.py` or a strategy.
- Adapters emit only canonical models (`Tick`, `Bar`, `Order`, `Position`, `Account`) with canonical
  symbols. Provider symbol translation goes through `SymbolMapper`, nowhere else.
- Strategies are pure: `on_bar(bar) -> Signal | None`. No I/O, no broker access, no provider knowledge.
- `engine.py` is the only place that submits orders. Every order passes `RiskManager.check()` first.
- `providers/fake.py` (`ReplayProvider`, `PaperBroker`) is the reference semantics; new adapters
  must behave the same way from the engine's point of view. Tests use these, never the network.
- New provider: implement the interfaces, register in `providers/__init__.py`. New strategy:
  subclass `Strategy`, register in `strategies/__init__.py`.

## Safety
- Default is signal-only. `--execute` against `alpaca`/`xtb` must only ever target paper/demo
  accounts in this phase; never change `ALPACA_PAPER` / `XTB_DEMO` defaults to false.
- Never log or commit API keys. `.env` is gitignored; `.env.example` holds the schema.
- Keep the kill switch (`SignalEngine.kill_switch`) working; cancel-all must not be removed.

## Conventions
- Dataclasses over pydantic. Type hints everywhere. `from __future__ import annotations`.
- UTC-aware datetimes only (`models.utcnow()`); provider timestamps converted at the adapter boundary.
- Prices are floats; quantities are shares (XTB "volume" == shares for stocks).
- Log with the module logger (`tradesignals.<module>`); no print.
- Small commits, one concern each; run `pytest` before every commit.

## Known gaps / roadmap (pick from here)
1. Backtester: `ReplayProvider` fed with `get_bars()` history + `PaperBroker`, report PnL/drawdown.
2. Persist signals and orders to SQLite; add a signal sink (Telegram/ntfy).
3. Reconnect with backoff in `AlpacaData`, `XtbSession` (resubscribe after reconnect).
4. XTB: use the `getTrades` stream for order updates instead of polling.
5. Position sizing by volatility / fraction of equity instead of fixed `--qty`.
6. Verify XTB symbol suffix via `getAllSymbols` at startup and fail fast if a symbol is unknown.

## XTB protocol reminders
- Main socket: max 1 request / 200 ms, serialised through `XtbSession.command`.
- `getChartLastRequest` prices are scaled by `digits`; high/low/close are deltas from open.
- Real stocks can't be shorted: SELL = close existing position (`type=2`, `order=position`).
