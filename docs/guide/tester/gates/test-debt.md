# Test debt and untested paths

The suite is green and covers the engine, the risk rules, the paper broker and the pure parts of both adapters. This page lists what it does **not** cover, ranked by what the gap protects, so that the next test is the one that matters most. Each row names the suggested test.

## Untested paths

| Priority | Path | Why it matters | Suggested test |
|---|---|---|---|
| 1 | Live connectivity of `AlpacaData`/`AlpacaBroker` and `XtbSession` (login, subscribe, stream parsing, keep-alive, throttle timing) | the only code that talks to money is exercised by hand only | a recorded-session test: feed `XtbData.stream()` a fake websocket yielding captured `tickPrices` JSON and assert the `Tick`s; time the 0.21 s throttle with a fake clock |
| 2 | Alpaca stream task ending silently (library loop returned) | the engine stays up and silent; nothing is logged | make `AlpacaData.stream()` watch `self._task` and raise when it finishes; test with a task that completes |
| 3 | Reconnect | not implemented; a test would define the contract | once implemented: a provider whose stream raises twice then succeeds, assert resubscribe |
| 4 | Kill switch end to end | closed: the console and the terminal UI trigger it and tests drive both (`tests/test_controls.py::test_console_kill_switch_needs_the_typed_word_and_stops_the_engine`, `tests/test_tui.py::test_kill_switch_needs_the_typed_word`) | nothing owed; there is still no CLI flag or signal for it |
| 5 | Risk policy on exits | settled: orders that only reduce a position always pass risk, pinned by `tests/test_risk.py::test_halt_blocks_new_risk_but_never_an_exit` | nothing owed; extend that test when a new rule is added so that exits stay exempt |
| 6 | Orders in flight between submit and terminal update | closed: `RestingBroker` in `tests/test_controls.py` keeps orders open until the test fills them (`tests/test_controls.py::test_second_signal_before_the_fill_does_not_duplicate_the_order`) | partial fills delivered as several updates are still untested |
| 7 | Several strategies sharing one `(symbol, timeframe)` aggregator | closed (`tests/test_management.py::test_two_strategies_on_one_symbol_and_timeframe_both_get_every_bar`) | nothing owed |
| 8 | `on_signal` callback: coroutine awaited, Future not awaited, exception drops the order | embedding programs rely on it | three small engine tests with a replay |
| 9 | `_run()` in the CLI end to end (symbol parsing, strategy kwargs, paper-mark wiring, signal handlers, and now the `--db` / `--console` wiring in `_start_console()`) | mostly closed: `tests/test_cli_console.py::test_db_flag_persists_a_run` and `tests/test_cli_console.py::test_console_flag_serves_the_console_while_the_engine_runs` run `_run()` with a replay feed registered for the test; signal handlers and the live adapters' wiring remain untested | run `_run()` with `--data` pointing at a registered replay provider (needs a test-only registry entry) |
| 10 | Warm-up overlapping the live bucket | first live bar can duplicate history | replay with history ending inside the current minute; assert one bar per bucket (fails today) |
| 11 | `SMA(0)`, `SmaCross(fast >= slow)`, RSI on a flat series, `Signal.strength` outside 0..1 | silent nonsense instead of an error | parameter-validation tests once validation exists |
| 12 | Bad `RISK_*` values, `make_data()` with a missing extra | both raise with a traceback today | `pytest.raises` tests pinning the messages |
| 13 | Models: `Tick.price` preferring `last`, `OrderStatus.is_terminal`, `Timeframe.seconds` | cheap, and they anchor every other test | one parametrised file |
| 14 | Browser scripts of the console (`app.js`, `charts.js`) | live updates, the action forms and the charts run only in a browser; a broken script leaves pages static but server tests green | a headless-browser smoke test against `scripts/console-demo.py`: open each page, assert no console error and that a quote updates the tape |
| 15 | The console server on a real socket | closed: one test starts uvicorn on a free loopback port, another takes the port first (`tests/test_cli_console.py::test_console_flag_serves_the_console_while_the_engine_runs`, `tests/test_cli_console.py::test_console_port_in_use_does_not_stop_the_engine`) | long-lived event streams across a shutdown are untested |
| 16 | Console actions against Alpaca paper and XTB demo | flatten, cancel and manual orders are tested on the in-memory brokers only | recorded-session tests once the adapters have them (see row 1) |

## Weak spots in existing tests

- `tests/test_engine.py::test_execute_mode_trades_paper_broker` asserts that buys and sells alternate and quantities equal 3; it does not assert the final position or the number of orders, so an extra duplicated order would not fail it.
- `tests/test_engine.py::test_risk_rejects_oversized_order` asserts `orders == []` without checking the reject reason; a rejection for an unrelated reason passes too. The direct rule tests in `tests/test_risk.py` cover the reasons.
- `tests/test_engine.py::test_warmup_uses_history` asserts on private strategy state (`_slow["AAPL"].ready`); a public "ready" property would make it honest.
- Adapter tests use hand-written fakes (`FakeSession`), not recorded protocol traffic; a drift in the real API would not be caught.
- Nothing enforces the architecture rule that the engine and strategies never import a provider module; a test walking `ast` imports under `tradesignals/` would take ten lines.

## Reading the gaps against the product

The operator guide's [Known gaps and roadmap](../../operator/develop/known-gaps.md) lists the product-level consequences of rows 2 to 7 above (H2 to H5, M3, M5). When one of those is fixed, write the test from this table first; it will fail, then pass, and the row can be deleted from both pages.
