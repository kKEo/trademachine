# Test debt and untested paths

The suite is green and covers the engine, the risk rules, the paper broker and the pure parts of both adapters. This page lists what it does **not** cover, ranked by what the gap protects, so that the next test is the one that matters most. Each row names the suggested test.

## Untested paths

| Priority | Path | Why it matters | Suggested test |
|---|---|---|---|
| 1 | Live connectivity of `AlpacaData`/`AlpacaBroker` and `XtbSession` (login, subscribe, stream parsing, keep-alive, throttle timing) | the only code that talks to money is exercised by hand only | a recorded-session test: feed `XtbData.stream()` a fake websocket yielding captured `tickPrices` JSON and assert the `Tick`s; time the 0.21 s throttle with a fake clock |
| 2 | Alpaca stream task ending silently (library loop returned) | the engine stays up and silent; nothing is logged | make `AlpacaData.stream()` watch `self._task` and raise when it finishes; test with a task that completes |
| 3 | Reconnect | not implemented; a test would define the contract | once implemented: a provider whose stream raises twice then succeeds, assert resubscribe |
| 4 | Kill switch end to end through a CLI trigger | there is no trigger; `kill_switch()` is tested in isolation only | once wired (signal or file): run the CLI's `_run()` with a replay and send the trigger |
| 5 | Risk policy on exits (halt and caps blocking a FLAT) | current behaviour is pinned by `tests/test_risk.py::test_halted_also_blocks_position_reducing_orders`; the desired behaviour is undecided | when the policy changes, replace that test with reduce-only expectations |
| 6 | Orders in flight between submit and terminal update | duplicates are possible with slow fills | a `PaperBroker` subclass that fills on the *next* tick; assert no second order is sent while one is pending (fails today, documents the gap) |
| 7 | Several strategies sharing one `(symbol, timeframe)` aggregator | second strategy never sees completed bars | two `Recorder` strategies on the same symbol; assert both receive every bar (fails today) |
| 8 | `on_signal` callback: coroutine awaited, Future not awaited, exception drops the order | embedding programs rely on it | three small engine tests with a replay |
| 9 | `_run()` in the CLI end to end (symbol parsing, strategy kwargs, paper-mark wiring, signal handlers) | only the pieces are tested | run `_run()` with `--data` pointing at a registered replay provider (needs a test-only registry entry) |
| 10 | Warm-up overlapping the live bucket | first live bar can duplicate history | replay with history ending inside the current minute; assert one bar per bucket (fails today) |
| 11 | `SMA(0)`, `SmaCross(fast >= slow)`, RSI on a flat series, `Signal.strength` outside 0..1 | silent nonsense instead of an error | parameter-validation tests once validation exists |
| 12 | Bad `RISK_*` values, `make_data()` with a missing extra | both raise with a traceback today | `pytest.raises` tests pinning the messages |
| 13 | Models: `Tick.price` preferring `last`, `OrderStatus.is_terminal`, `Timeframe.seconds` | cheap, and they anchor every other test | one parametrised file |

## Weak spots in existing tests

- `tests/test_engine.py::test_execute_mode_trades_paper_broker` asserts that buys and sells alternate and quantities equal 3; it does not assert the final position or the number of orders, so an extra duplicated order would not fail it.
- `tests/test_engine.py::test_risk_rejects_oversized_order` asserts `orders == []` without checking the reject reason; a rejection for an unrelated reason passes too. The direct rule tests in `tests/test_risk.py` cover the reasons.
- `tests/test_engine.py::test_warmup_uses_history` asserts on private strategy state (`_slow["AAPL"].ready`); a public "ready" property would make it honest.
- Adapter tests use hand-written fakes (`FakeSession`), not recorded protocol traffic; a drift in the real API would not be caught.
- Nothing enforces the architecture rule that the engine and strategies never import a provider module; a test walking `ast` imports under `tradesignals/` would take ten lines.

## Reading the gaps against the product

The operator guide's [Known gaps and roadmap](../../operator/develop/known-gaps.md) lists the product-level consequences of rows 2 to 7 above (H2 to H5, M3, M5). When one of those is fixed, write the test from this table first; it will fail, then pass, and the row can be deleted from both pages.
