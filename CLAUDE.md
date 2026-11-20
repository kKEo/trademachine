# tradesignals — project guide for Claude Code

Provider-agnostic trade-signal engine. Python 3.11+, asyncio, no framework.
Adapters: Alpaca (alpaca-py) and XTB (raw xAPI over websockets). Read README.md first.

## Commands
- Install: `pip install -e ".[alpaca,console,tui,dev]"`
- Tests: `pytest -q` (must stay green; add a test for every behaviour change)
- Lint/format: `ruff check . && ruff format .` (add ruff if missing)
- Run signal-only: `tradesignals --data alpaca --symbols AAPL --strategy sma_cross`
- Run on demo broker: `tradesignals --data xtb --broker xtb --symbols AAPL --execute --qty 1`
- Admin console, offline demo: `python scripts/console-demo.py` (http://127.0.0.1:8788; `CONSOLE_TOKEN=demo` enables actions)
- Admin console, real feed: add `--console` to a run; terminal view: `tradesignals-tui`
- Docs gate: `python scripts/docs-check.py` (flags, env vars and cited test names must match the code)

## Architecture rules (do not break)
- `interfaces.py` (`MarketDataProvider`, `Broker`, `SymbolMapper`) is the abstraction layer.
  The engine and strategies import ONLY from `models.py`, `interfaces.py`, `strategies/`, `risk.py`,
  `bars.py`, `indicators.py` and (engine only) `events.py`.
  Never import a provider module from `engine.py` or a strategy.
- Adapters emit only canonical models (`Tick`, `Bar`, `Order`, `Position`, `Account`) with canonical
  symbols. Provider symbol translation goes through `SymbolMapper`, nowhere else.
- Strategies are pure: `on_bar(bar) -> Signal | None`. No I/O, no broker access, no provider knowledge.
- `engine.py` is the only place that submits orders. Every order passes `RiskManager.check()` first,
  through `SignalEngine._submit()`: strategy orders, `flatten()` and `submit_manual()` alike.
- Orders that only reduce a position always pass risk (`risk.reduces_position`); keep exits exempt
  when adding a rule.
- `providers/fake.py` (`ReplayProvider`, `PaperBroker`) is the reference semantics; new adapters
  must behave the same way from the engine's point of view. Tests use these, never the network.
- New provider: implement the interfaces, register in `providers/__init__.py`. New strategy:
  subclass `Strategy`, register in `strategies/__init__.py`; expose constructor parameters as
  public attributes of the same name so the console can show them.

## Console rules (do not break)
- The engine never imports `store.py`, `analytics.py`, `console/` or `tui.py`. It publishes typed
  events to an optional `EventBus` (`events.py`, stdlib only); publishing never blocks or raises.
- The console never touches a `Broker` or a provider module. Reads: `Store` + the engine's public
  state. Writes: `SignalEngine` methods, called from `ConsoleService`, each one audited.
- `analytics.py` is pure (no I/O). `Store` is synchronous; call it via `asyncio.to_thread` from
  the loop (`ConsoleService._db`, `StoreWriter`).
- Console failures are logged, never raised into the engine. The server binds to 127.0.0.1 only;
  do not add a host option. State-changing requests need `CONSOLE_TOKEN`; no token = view only.
- The console trades paper/demo only: keep `ConsoleService.capabilities()` refusing trading
  actions when `broker.paper` is false (halt and kill switch stay allowed).
- Pages use no inline script (CSP `default-src 'self'`); browser libraries are vendored under
  `console/static/vendor/`, no CDN, no Node build.
- UI changes: check them in a browser with `scripts/console-demo.py`; tests use
  `tests/console_helpers.py` (`Rig`, `finished_run`) and open no socket (only
  `tests/test_cli_console.py` does, on a free loopback port).

## Safety
- Default is signal-only. `--execute` against `alpaca`/`xtb` must only ever target paper/demo
  accounts in this phase; never change `ALPACA_PAPER` / `XTB_DEMO` defaults to false.
- Never log or commit API keys. `.env` is gitignored; `.env.example` holds the schema.
  The console shows secrets only as set / not set (`SECRET_SETTINGS` in `console/service.py`).
- Keep the kill switch (`SignalEngine.kill_switch`) working; cancel-all must not be removed.
  It is reachable from the console (Risk page) and the TUI (`k`), both with typed confirmation.

## Conventions
- Dataclasses over pydantic. Type hints everywhere. `from __future__ import annotations`.
- UTC-aware datetimes only (`models.utcnow()`); provider timestamps converted at the adapter boundary.
- Prices are floats; quantities are shares (XTB "volume" == shares for stocks).
- Log with the module logger (`tradesignals.<module>`); no print.
- Small commits, one concern each; run `pytest` before every commit.

## Known gaps / roadmap (pick from here)
1. Backtester: `ReplayProvider` fed with `get_bars()` history + `PaperBroker`, report PnL/drawdown.
2. Signal sink (Telegram/ntfy) as another `EventBus` subscriber. (SQLite persistence is done: `--db`.)
3. Reconnect with backoff in `AlpacaData`, `XtbSession` (resubscribe after reconnect).
4. XTB: use the `getTrades` stream for order updates instead of polling.
5. Position sizing by volatility / fraction of equity instead of fixed `--qty`.
6. Verify XTB symbol suffix via `getAllSymbols` at startup and fail fast if a symbol is unknown.

## XTB protocol reminders
- Main socket: max 1 request / 200 ms, serialised through `XtbSession.command`.
- `getChartLastRequest` prices are scaled by `digits`; high/low/close are deltas from open.
- Real stocks can't be shorted: SELL = close existing position (`type=2`, `order=position`).
7. Console: persist limit/strategy changes across restarts; headless-browser smoke test; metrics endpoint.
   See `docs/guide/operator/develop/known-gaps.md` (C1 to C10) for the console's own gaps.
