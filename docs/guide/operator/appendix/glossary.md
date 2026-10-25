# Glossary

Terms as used in this guide and in the code.

**Adapter.** A provider-specific implementation of `MarketDataProvider` and/or `Broker` (`providers/alpaca.py`, `providers/xtb.py`, `providers/fake.py`).

**Aggregator.** A `BarAggregator`, one per `(symbol, timeframe)`, that turns ticks or finer bars into bars of the strategy's timeframe.

**Bucket.** The time span of one bar, aligned to UTC epoch multiples of the timeframe by `bucket_start()`.

**Canonical symbol.** The provider-independent ticker (`AAPL`); provider symbols (`AAPL.US_9`) exist only inside adapters, translated by `SymbolMapper`.

**Cold start.** A strategy that begins live operation without enough warm-up bars and stays silent until it has seen `warmup_bars` live bars.

**Delta.** `target − held`, the signed share quantity the engine must trade to reach the target position.

**Halt / halt latch.** `RiskManager.halted`, set by the loss rule or the kill switch; every later order is rejected until the process restarts.

**Kill switch.** `SignalEngine.kill_switch()`: halt, cancel pending orders, stop. Not wired to the CLI.

**Live guard.** `check_live_guard()` in the CLI: refuses `--execute` when `broker.paper` is false unless `--allow-live` is given.

**Mark.** The last price fed to `PaperBroker.mark()`, used to fill paper orders and value positions.

**Netting.** Keeping one signed position per symbol (paper, Alpaca) as opposed to XTB's one record per trade.

**Notional.** `qty × price`, the money value of an order; capped by `RISK_MAX_ORDER_NOTIONAL`.

**Pass-through bar.** A provider bar whose timeframe equals the strategy's; the aggregator returns it unchanged.

**Pending order.** On XTB, a limit or stop order resting on the book (cmd 2 to 5); the only kind `cancel_order()` can delete.

**Provider bars win.** The rule that a provider declaring `pushes_bars = True` is never double-fed from ticks.

**Reference semantics.** The behaviour of `ReplayProvider` and `PaperBroker`; real adapters are expected to match it from the engine's point of view.

**Refcounted session.** `XtbSession`, shared by the XTB data and broker adapters and closed when the last user closes it.

**Signal-only.** A run without `--execute` (or without a broker): signals are logged, nothing is sent.

**Stale quote.** A last tick older than `RISK_MAX_QUOTE_AGE_SEC`; blocks orders.

**Stream session id.** The token XTB returns at login, sent with every stream-socket subscription.

**Strict boolean.** `env_bool()` parsing: only explicit true/false spellings, anything else raises.

**Target position.** `qty × strength` for LONG, its negative for SHORT, 0 for FLAT.

**Terminal status.** `filled`, `cancelled` or `rejected`; triggers a broker-state refresh when seen in `order_updates()`.

**Tick-built bar.** A bar assembled from quote mids, with volume 0.

**Warm-up window.** `timeframe.seconds × warmup_bars × 3` seconds before now; the span of history requested at startup.
