# Known gaps and roadmap

This chapter is the consolidated, severity-ranked list of what the code base gets wrong or does not do, found while reading every file for this documentation. Each entry has a status tag and a code pointer so that it can be checked, fixed and ticked off. Items fixed during the documentation pass are listed at the end so that nobody hunts for them.

Status tags: **verified in code**, **library** (verified against library source), **spec** (from third-party copies of the xAPI spec), **unverified**.

## High: can lose money or hide a failure

| # | Finding | Where | Status |
|---|---|---|---|
| H1 | Every risk rule applies to position-reducing orders. After a loss halt or a kill switch, the FLAT that would close a position is rejected; the notional cap blocks a sale once the shares are worth more than the cap; a stale quote blocks exits. Nothing flattens automatically. | `tradesignals/risk.py:49-58` | verified in code (`tests/test_risk.py::test_halted_also_blocks_position_reducing_orders`) |
| H2 | Orders in flight are invisible to sizing and risk. A second signal before a market order fills is sized from the stale position; a duplicate order or a wrong-direction order can follow. | `tradesignals/engine.py:223-235`, `tradesignals/risk.py:60-66` | verified in code |
| H3 | The kill switch is not reachable from the CLI; `SIGINT`/`SIGTERM` are a plain stop. Its `cancel_all()` is a no-op on paper, account-wide on Alpaca (cancels orders the engine never placed), pending-only on XTB; it never flattens. | `tradesignals/engine.py:119-128`, `tradesignals/cli.py:135`, `tradesignals/providers/alpaca.py:223`, `tradesignals/providers/xtb.py:348` | verified in code |
| H4 | No reconnect. `XtbSession` logs `xtb ping failed` forever after a drop; the engine stops on a data-stream exception. Alpaca relies on alpaca-py's internal loop, and if that loop returns (for example after an "insufficient subscription" error) nothing notices: the engine stays up and silent. | `tradesignals/providers/xtb.py:137-143`, `tradesignals/providers/alpaca.py:135`, `tradesignals/engine.py:156-171` | verified in code; alpaca-py behaviour: library |
| H5 | The engine treats any position in a traded symbol as its own and corrects it towards the target, including positions opened by hand or by another program. The open-positions cap counts every position on the account. | `tradesignals/engine.py:226-233`, `tradesignals/risk.py:66` | verified in code |
| H6 | XTB's public xAPI is reported discontinued (14 March 2025) by third-party client libraries; the official documentation host no longer resolves. The adapter is tested offline only. | external | unverified |

## Medium: wrong numbers or surprising behaviour

| # | Finding | Where | Status |
|---|---|---|---|
| M1 | "Daily" loss is loss since the first check of this process; the baseline never resets by date and the halt is a latch cleared only by restart. | `tradesignals/risk.py:69-72` | verified in code |
| M2 | The loss rule is skipped while the account is unknown, and broker-state refresh failures keep stale positions and account (fail open). | `tradesignals/risk.py:67`, `tradesignals/engine.py:266-272` | verified in code |
| M3 | Warm-up is anchored to the wall clock, so history is short near the open; warm-up may include the bucket in progress and the first live bar can be partial or duplicate it (no de-duplication by `start`). | `tradesignals/engine.py:135` | verified in code |
| M4 | Bars are emitted only when a later event arrives; the last bar of a session comes out with the next session's first event, carrying yesterday's `Signal.ts` and executing at today's price. Gaps produce no bars. | `tradesignals/bars.py:44-62` | verified in code |
| M5 | Aggregators are shared across strategies with the same `(symbol, timeframe)`; only the first strategy receives the completed bar, and rolled-up volume is added once per strategy. CLI runs are unaffected (one strategy). | `tradesignals/engine.py:179-210` | verified in code |
| M6 | XTB: a pending limit/stop order is reported `ACCEPTED` and polled every 2 s, but `tradeTransactionStatus` never reflects its later fill. Market fills are reported at the price sent, because the status reply carries no fill price. A close acts on the first matching trade only; any remainder is neither closed nor opened. | `tradesignals/providers/xtb.py:300-317`, `tradesignals/providers/xtb.py:241-249` | spec, verified in code |
| M7 | XTB session robustness: no receive timeout; replies matched by order, not by `customTag`; stream-socket sends unthrottled; session cache is process-global across event loops; an order whose status maps to `UNKNOWN` is polled forever; one consumer per stream. | `tradesignals/providers/xtb.py:117-135`, `tradesignals/providers/xtb.py:61`, `tradesignals/providers/xtb.py:389-401` | verified in code |
| M8 | Alpaca: the adapter drives alpaca-py's private `_run_forever()` and does not watch the task; `subscribe()` twice starts two tasks; statuses outside the mapping (held, replaced, pending_cancel, ...) map to non-terminal `UNKNOWN`. | `tradesignals/providers/alpaca.py:133-135`, `tradesignals/providers/alpaca.py:188`, `tradesignals/providers/alpaca.py:44-53` | verified in code |
| M9 | Currency: order notional is checked in the quote currency, the loss limit in the account currency; `buying_power` and `cash` are never read. | `tradesignals/risk.py:57`, `tradesignals/risk.py:69` | verified in code |
| M10 | No order throttle, cooldown or hysteresis; a strategy that flips every bar trades every bar. | `tradesignals/engine.py:213-221` | verified in code |
| M11 | `on_signal` callbacks returning a Future (not a coroutine) are not awaited; an exception in a callback or strategy drops the order for that event. | `tradesignals/engine.py:216-221`, `tradesignals/engine.py:156-171` | verified in code |
| M12 | Quotes are not validated: a zero or crossed bid/ask produces a garbage mid that flows into bars, marks and the notional check. Naive datetimes are not rejected at the engine boundary. | `tradesignals/providers/alpaca.py:113-116`, `tradesignals/providers/xtb.py:170-177`, `tradesignals/bars.py:17` | verified in code |
| M13 | `engine.signals`, `engine.orders` and the adapters' queues grow without bound. | `tradesignals/engine.py:72-73`, `tradesignals/providers/alpaca.py:100`, `tradesignals/providers/fake.py:88` | verified in code |
| M14 | With a non-editable install, `.env` in the working directory is not found: python-dotenv searches upward from the calling module's directory. | `tradesignals/cli.py:143` | library |
| M15 | Windows is unsupported (`loop.add_signal_handler`). | `tradesignals/cli.py:135` | verified in code |

## Low: documentation, tooling and tidiness

| # | Finding | Where | Status |
|---|---|---|---|
| L1 | No persistence, metrics, health endpoint or notification sink; signals live in memory only. | engine | verified in code |
| L2 | No backtester; `PaperBroker` needs tick marks, so a bar-only replay cannot fill orders. | `tradesignals/providers/fake.py:90-91` | verified in code |
| L3 | No configuration file; strategy parameters, mapper overrides, paper cash, poll interval and hosts are not configurable. | `tradesignals/providers/__init__.py:40-48` | verified in code |
| L4 | Alpaca bypasses `SymbolMapper`; `Order.raw` carries provider objects upward despite the models docstring. | `tradesignals/providers/alpaca.py:72-84` | verified in code |
| L5 | Unused by the built-in strategies: `EMA`, `SignalKind.SHORT`, `OrderType.LIMIT`, `TimeInForce.GTC`, `Strategy.on_tick`. | `tradesignals/indicators.py:30` | verified in code |
| L6 | `Signal.strength` is unvalidated; `SMA(0)` divides by zero; `SmaCross` does not check `fast < slow`; RSI on a flat series reads 100 and triggers FLAT. | `tradesignals/models.py:149`, `tradesignals/indicators.py:69-70` | verified in code |
| L7 | `CLAUDE.md`'s import allow-list omits `bars.py` and `indicators.py`, which the engine and strategies import; nothing enforces the rule. The README's pipeline diagram lists "Replay" as a provider although it is not a CLI data source. | `tradesignals/engine.py:23`, `README.md` | verified in code |
| L8 | Dependencies are unpinned and there is no lock file; the paper flags are trusted without asking the broker for the account type. | `pyproject.toml` | verified in code |

## Roadmap items from `CLAUDE.md` and their status

| Item | Status |
|---|---|
| 1. Backtester (`ReplayProvider` + history + `PaperBroker`, P&L and drawdown) | not started; blocked by L2 (marks come from ticks) |
| 2. Persist signals and orders to SQLite; Telegram/ntfy sink | not started (L1) |
| 3. Reconnect with backoff in `AlpacaData` and `XtbSession`, resubscribe after reconnect | not started (H4); Alpaca's library reconnects by itself but the adapter does not watch it |
| 4. XTB `getTrades` stream instead of polling | not started (M6) |
| 5. Position sizing by volatility or fraction of equity | not started; `--qty` × strength only |
| 6. Verify the XTB suffix via `getAllSymbols` at startup and fail fast | not started; a wrong suffix is silent |

Suggested order: H1 (reduce-only exemption from halts and caps, or an explicit flatten command), H2 (track open orders between submit and terminal update), H3 (wire the kill switch to a signal or file trigger and define cancel scope per broker), H4 (reconnect with backoff, watch the Alpaca task), then M1 to M3.

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
