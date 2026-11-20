# Known gaps and roadmap

This chapter is the consolidated, severity-ranked list of what the code base gets wrong or does not do, found while reading every file for this documentation. Each entry has a status tag and a code pointer so that it can be checked, fixed and ticked off. Items fixed during the documentation pass are listed at the end so that nobody hunts for them.

Status tags: **verified in code**, **library** (verified against library source), **spec** (from third-party copies of the xAPI spec), **unverified**.

## High: can lose money or hide a failure

| # | Finding | Where | Status |
|---|---|---|---|
| H1 | **Fixed.** Orders that only reduce a position (without flipping it) now bypass every risk rule, so a halt, the notional cap or a stale quote can no longer block an exit; the console can flatten one or all positions. Nothing flattens automatically. | `reduces_position()` in `tradesignals/risk.py` | fixed (`tests/test_risk.py::test_halt_blocks_new_risk_but_never_an_exit`) |
| H2 | **Fixed.** The engine tracks its orders from submit to a terminal update and counts their unfilled part in sizing and in the positions risk judges. An open order the broker stops reporting is re-read after 30 seconds and forgotten after ten minutes. | `tradesignals/engine.py:536-584` | fixed (`tests/test_controls.py::test_second_signal_before_the_fill_does_not_duplicate_the_order`) |
| H3 | **Mostly fixed.** The kill switch is reachable from the admin console and the terminal UI (typed confirmation, audited); there is still no CLI flag or signal for it, and `SIGINT`/`SIGTERM` remain a plain stop. Its `cancel_all()` is a no-op on paper, account-wide on Alpaca (cancels orders the engine never placed), pending-only on XTB; it never flattens (the console has a separate flatten). | `tradesignals/engine.py:174-184`, `tradesignals/providers/alpaca.py:223`, `tradesignals/providers/xtb.py:348` | verified in code (`tests/test_controls.py::test_console_kill_switch_needs_the_typed_word_and_stops_the_engine`) |
| H4 | No reconnect. `XtbSession` logs `xtb ping failed` forever after a drop; the engine stops on a data-stream exception. Alpaca relies on alpaca-py's internal loop, and if that loop returns (for example after an "insufficient subscription" error) nothing notices: the engine stays up and silent. | `tradesignals/providers/xtb.py:137-143`, `tradesignals/providers/alpaca.py:135`, `tradesignals/engine.py:429-444` | verified in code; alpaca-py behaviour: library |
| H5 | The engine treats any position in a traded symbol as its own and corrects it towards the target, including positions opened by hand or by another program. The open-positions cap counts every position on the account. | `tradesignals/engine.py:514-523`, `tradesignals/risk.py:74` | verified in code |
| H6 | XTB's public xAPI is reported discontinued (14 March 2025) by third-party client libraries; the official documentation host no longer resolves. The adapter is tested offline only. | external | unverified |

## Medium: wrong numbers or surprising behaviour

| # | Finding | Where | Status |
|---|---|---|---|
| M1 | "Daily" loss is loss since the first check of this process; the baseline never resets by date. The halt is a latch cleared by a restart or by resuming from the console, which restarts the baseline. | `tradesignals/risk.py:76-84` | verified in code |
| M2 | The loss rule is skipped while the account is unknown, and broker-state refresh failures keep stale positions and account (fail open). | `tradesignals/risk.py:75`, `tradesignals/engine.py:627-633` | verified in code |
| M3 | Warm-up is anchored to the wall clock, so history is short near the open; warm-up may include the bucket in progress and the first live bar can be partial or duplicate it (no de-duplication by `start`). | `tradesignals/engine.py:408` | verified in code |
| M4 | Bars are emitted only when a later event arrives; the last bar of a session comes out with the next session's first event, carrying yesterday's `Signal.ts` and executing at today's price. Gaps produce no bars. | `tradesignals/bars.py:44-62` | verified in code |
| M5 | **Fixed.** Each `(symbol, timeframe)` aggregator is fed once per event and its completed bar goes to every strategy on that timeframe; rolled-up volume is counted once. | `tradesignals/engine.py:452-494` | fixed (`tests/test_management.py::test_two_strategies_on_one_symbol_and_timeframe_both_get_every_bar`) |
| M6 | XTB: a pending limit/stop order is reported `ACCEPTED` and polled every 2 s, but `tradeTransactionStatus` never reflects its later fill. Market fills are reported at the price sent, because the status reply carries no fill price. A close acts on the first matching trade only; any remainder is neither closed nor opened. | `tradesignals/providers/xtb.py:300-317`, `tradesignals/providers/xtb.py:241-249` | spec, verified in code |
| M7 | XTB session robustness: no receive timeout; replies matched by order, not by `customTag`; stream-socket sends unthrottled; session cache is process-global across event loops; an order whose status maps to `UNKNOWN` is polled forever; one consumer per stream. | `tradesignals/providers/xtb.py:117-135`, `tradesignals/providers/xtb.py:61`, `tradesignals/providers/xtb.py:389-401` | verified in code |
| M8 | Alpaca: the adapter drives alpaca-py's private `_run_forever()` and does not watch the task; `subscribe()` twice starts two tasks; statuses outside the mapping (held, replaced, pending_cancel, ...) map to non-terminal `UNKNOWN`. | `tradesignals/providers/alpaca.py:133-135`, `tradesignals/providers/alpaca.py:188`, `tradesignals/providers/alpaca.py:44-53` | verified in code |
| M9 | Currency: order notional is checked in the quote currency, the loss limit in the account currency; `buying_power` and `cash` are never read. | `tradesignals/risk.py:65`, `tradesignals/risk.py:76` | verified in code |
| M10 | No order throttle, cooldown or hysteresis; a strategy that flips every bar trades every bar. | `tradesignals/engine.py:496-507` | verified in code |
| M11 | `on_signal` callbacks returning a Future (not a coroutine) are not awaited; an exception in a callback or strategy drops the order for that event. | `tradesignals/engine.py:502-507`, `tradesignals/engine.py:429-444` | verified in code |
| M12 | Quotes are not validated: a zero or crossed bid/ask produces a garbage mid that flows into bars, marks and the notional check. Naive datetimes are not rejected at the engine boundary. | `tradesignals/providers/alpaca.py:113-116`, `tradesignals/providers/xtb.py:170-177`, `tradesignals/bars.py:17` | verified in code |
| M13 | **Partly fixed.** `engine.signals` and `engine.orders` keep the latest 1000 entries (the database keeps everything). The adapters' queues still grow without bound. | `tradesignals/engine.py:68`, `tradesignals/providers/alpaca.py:100`, `tradesignals/providers/fake.py:88` | verified in code (`tests/test_events_store.py::test_engine_without_bus_is_unchanged_and_history_is_bounded`) |
| M14 | With a non-editable install, `.env` in the working directory is not found: python-dotenv searches upward from the calling module's directory. | `tradesignals/cli.py:224` | library |
| M15 | Windows is unsupported (`loop.add_signal_handler`). | `tradesignals/cli.py:189` | verified in code |

## Low: documentation, tooling and tidiness

| # | Finding | Where | Status |
|---|---|---|---|
| L1 | **Mostly fixed.** Signals, outcomes, orders, bars and equity are persisted to SQLite (`--db`); the admin console shows them with health figures and a log tail. Still missing: a metrics endpoint for external monitoring and a notification sink. | `tradesignals/store.py`, `tradesignals/console/` | verified in code |
| L2 | No backtester; `PaperBroker` needs tick marks, so a bar-only replay cannot fill orders. | `tradesignals/providers/fake.py:90-91` | verified in code |
| L3 | No configuration file; strategy parameters, mapper overrides, paper cash, poll interval and hosts are not configurable. | `tradesignals/providers/__init__.py:40-48` | verified in code |
| L4 | Alpaca bypasses `SymbolMapper`; `Order.raw` carries provider objects upward despite the models docstring. | `tradesignals/providers/alpaca.py:72-84` | verified in code |
| L5 | Unused by the built-in strategies: `EMA`, `SignalKind.SHORT`, `OrderType.LIMIT`, `TimeInForce.GTC`, `Strategy.on_tick`. | `tradesignals/indicators.py:30` | verified in code |
| L6 | `Signal.strength` is unvalidated; `SMA(0)` divides by zero; `SmaCross` does not check `fast < slow`; RSI on a flat series reads 100 and triggers FLAT. | `tradesignals/models.py:149`, `tradesignals/indicators.py:69-70` | verified in code |
| L7 | `CLAUDE.md`'s import allow-list omits `bars.py` and `indicators.py`, which the engine and strategies import; nothing enforces the rule. The README's pipeline diagram lists "Replay" as a provider although it is not a CLI data source. | `tradesignals/engine.py:30`, `README.md` | verified in code |
| L8 | Dependencies are unpinned and there is no lock file; the paper flags are trusted without asking the broker for the account type. | `pyproject.toml` | verified in code |

## Admin console: what it does not do

Found while building the console (`tradesignals/console/`, `tradesignals/tui.py`); all verified in code.

| # | Finding | Where |
|---|---|---|
| C1 | No user accounts and no HTTPS: one shared token, loopback only. Anyone with a shell on the machine and the token can act. Remote access needs an SSH tunnel; exposing the port through a proxy is unsupported and unsafe. | `tradesignals/console/security.py`, `tradesignals/console/server.py` |
| C2 | Changes made from the console (risk limits, added, paused or removed strategies, a halt) last until the engine restarts. Only the audit log remembers them. | `tradesignals/console/service.py` |
| C3 | The console trades paper and demo accounts only; on a live account it offers halt and the kill switch and refuses everything else, even with `--allow-live`. | `ConsoleService.capabilities()` |
| C4 | A strategy can be added only on symbols the engine subscribed to at startup, because subscribing twice is not safe on every adapter (see M8). | `SignalEngine.add_strategy()` |
| C5 | A paused strategy keeps receiving bars, so its private state can drift from the account (`RsiReversion` may believe it is in a position it never opened). After a resume, its next exit signal is then a harmless no-op. | `SignalEngine._deliver()` |
| C6 | The quote tape's change is measured from the first price seen in the session, not from the previous close; "Today" compares with the first equity snapshot of the UTC day. | `ConsoleService._quote()`, `ConsoleService.kpis()` |
| C7 | Forward returns, slippage and chart markers need stored bars: signals older than the bar retention (one year for intraday bars) lose them. Latency is reported only when plausible, so replays show none. | `tradesignals/analytics.py`, `Store.prune()` |
| C8 | Round trips are rebuilt from every stored fill on each request; fine for thousands of orders, slow for hundreds of thousands. Fills made outside the engine appear only if the broker pushes them. | `ConsoleService.trips()` |
| C9 | The terminal UI polls every two seconds and offers halt, resume and the kill switch only. | `tradesignals/tui.py` |
| C10 | The narrow-screen layout is defined in CSS but has not been checked on a phone-sized viewport. | `tradesignals/console/static/app.css` |

## Roadmap items from `CLAUDE.md` and their status

| Item | Status |
|---|---|
| 1. Backtester (`ReplayProvider` + history + `PaperBroker`, P&L and drawdown) | not started; blocked by L2 (marks come from ticks) |
| 2. Persist signals and orders to SQLite; Telegram/ntfy sink | persistence done (`--db`, `tradesignals/store.py`); the notification sink is not started, and would be one more event-bus subscriber |
| 3. Reconnect with backoff in `AlpacaData` and `XtbSession`, resubscribe after reconnect | not started (H4); Alpaca's library reconnects by itself but the adapter does not watch it |
| 4. XTB `getTrades` stream instead of polling | not started (M6) |
| 5. Position sizing by volatility or fraction of equity | not started; `--qty` × strength only |
| 6. Verify the XTB suffix via `getAllSymbols` at startup and fail fast | not started; a wrong suffix is silent |

Suggested order: H4 (reconnect with backoff, watch the Alpaca task), then M1 to M3, then the console gaps below. H1, H2, H3 and M5 were closed while building the admin console.

## Resolved during the documentation pass

These were found by the same read and fixed, with tests, before the docs were written; they are listed so that older notes are not mistaken for current behaviour.

- `ALPACA_PAPER`/`XTB_DEMO` accepted only the literal `true` and treated `1`, `yes` or an empty value as live; now parsed strictly, and the CLI refuses live execution without `--allow-live` (`tests/test_config.py`, `tests/test_cli.py`).
- `-v` set the root logger to DEBUG, which made the websockets library log the XTB login frame including the password; `-v` now affects `tradesignals.*` loggers only.
- Alpaca reported every order as SELL (`str()` of a `(str, Enum)` is `'OrderSide.BUY'`) and failed warm-up for minute timeframes (`TimeFrameUnit("Minute")` raises; the value is `"Min"`); both mappings fixed (`tests/test_alpaca_mapping.py`).
- Alpaca quotes and 1-minute bars were both fed into one aggregator, handing every bucket to the strategy twice; providers now declare `pushes_bars` and ticks stop building bars (`tests/test_engine_robustness.py`).
- A late event for a closed bucket resurrected the old bucket and emitted it twice; late events are now dropped and counted (`tests/test_bars.py`).
- A failing data or order-update stream left the engine hanging silently; it now logs at ERROR and stops (data) or continues (updates).
- XTB `cancel_order()` sent transaction type 1 (PENDING) instead of 4 (DELETE) with empty symbol and cmd 0, so cancel-all could not delete anything; limit orders were reported FILLED on acceptance.
- `PaperBroker` reset the cost basis on a partial reduce and filled limit orders at the limit price regardless of the market.
- The warm-up test was pinned to a fixed date and failed from 21 October 2026 onwards; the kill switch skipped `stop()` when cancelling raised; `python -m tradesignals` did not exist; ruff was referenced but not declared; no CI, no LICENSE.
