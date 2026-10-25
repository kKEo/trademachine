# Adding a provider

A provider is one or two classes behind the interfaces in `tradesignals/interfaces.py`, registered as zero-argument factories. This chapter lists what to implement, what the reference semantics are, and the tests that keep an adapter honest without touching the network. Budget: a day for a data adapter, more for a broker.

## Implement the interfaces

**`MarketDataProvider`**

| Member | Do this |
|---|---|
| `name` | short lowercase id, used in `Tick.source`/`Bar.source` and logs |
| `pushes_bars` | set `True` if the provider streams bars itself; the engine then never builds bars from ticks for you (`tradesignals/engine.py:75`) |
| `connect()` / `close()` | open and close sockets; `close()` must be safe to call after a failed `connect()` |
| `subscribe(symbols)` | subscribe once; the engine calls it once with the union of all strategies' symbols |
| `stream()` | async generator of canonical `Tick` and `Bar` events; let exceptions propagate (the engine logs them and stops) rather than swallowing them |
| `get_bars(symbol, timeframe, start, end=None)` | oldest first; treat `end` as inclusive; raise on failure (the engine downgrades warm-up failures to a warning) |

**`Broker`**

| Member | Do this |
|---|---|
| `name` | as above |
| `paper` | set from your configuration; **leave it `False` unless you know the account is paper/demo**, because the CLI's live guard reads it (`tradesignals/cli.py:83`) |
| `submit_order(req)` | translate `OrderRequest` (market or limit, DAY or GTC, `client_order_id`) and return an `Order` with the best status you can know at return time |
| `cancel_order(id)` / `cancel_all()` | cancel pending orders; document the scope (own orders vs account-wide) |
| `get_order(id)` | fresh status |
| `get_positions()` | one `Position` per symbol, signed qty, average price; `market_value`/`unrealized_pnl` optional |
| `get_account()` | equity, cash, buying power, currency |
| `order_updates()` | async generator of `Order` changes; poll if the API has no push channel (see `XtbBroker.order_updates()`) |

## Boundary rules

1. Emit only canonical models from `models.py`; put the provider's raw object in `Order.raw` if you need it for debugging.
2. Convert timestamps to **aware UTC** at the boundary. Naive datetimes break `bucket_start()` (treated as local time) and `RiskManager.check()` (`TypeError`).
3. Translate symbols through `SymbolMapper` only (`tradesignals/interfaces.py:15`); the engine and strategies see canonical symbols. Read the suffix from an environment variable, as `XtbData` does.
4. Read configuration from environment variables in the constructor and fail fast with a clear `RuntimeError`/`ValueError`; the registries call factories with no arguments. Use `env_bool()` from `tradesignals/config.py` for any boolean.
5. Import optional third-party SDKs inside the constructor or the methods that use them, so that the package imports without the extra (see `providers/alpaca.py`).
6. Log with `logging.getLogger("tradesignals.<name>")`; never log credentials, and never rely on the root logger being at INFO.
7. Do not set `last` on `Tick` unless it is a real trade price; `Tick.price` falls back to the mid.

## Reference semantics

`ReplayProvider` and `PaperBroker` in `tradesignals/providers/fake.py` are the behaviours the engine is written against: `stream()` ends when the data ends, `get_bars()` honours timeframe/start/inclusive end, market orders fill at once, limit orders fill only when marketable, positions net per symbol with cost basis kept on reduce. Where a real API cannot match them, document the difference in [Provider adapters](../internals/providers.md) (the comparison table is the place).

The contract leaves some things open on purpose; decide and document them for your adapter: whether `stream()` ends, whether `subscribe()` is idempotent, which exceptions you raise, whether `submit_order()` can return a terminal status, and whether `order_updates()` re-emits terminal orders.

## Register

Add a factory to `DATA_PROVIDERS` and/or `BROKERS` in `tradesignals/providers/__init__.py`. The name appears in `--data`/`--broker` choices automatically; `scripts/docs-check.py` requires it to be mentioned in [Commands and flags](../install/commands.md). Add the environment variables to `.env.example` and to the [Configuration reference](../install/configuration.md); the same script checks both.

## Conformance tests (offline)

Tests never use the network (`CLAUDE.md`). Test the pure parts directly and the protocol parts against a fake transport, as the existing suites do:

- Pure mapping helpers (status and side enums, timeframe tables, timestamp conversion, price decoding): `tests/test_alpaca_mapping.py`, `tests/test_xtb_broker.py::test_get_bars_decodes_digits_and_deltas`.
- Order construction and status handling against a fake session that records every request: `tests/test_xtb_broker.py` (`FakeSession`).
- Strict configuration parsing: `tests/test_alpaca_mapping.py::test_creds_paper_flag_is_strict`.
- Engine integration with your data events: build a `ReplayProvider` with the exact `Tick`/`Bar` sequence your adapter would emit (including out-of-order provider bars) and assert one bar per bucket reaches the strategy: `tests/test_engine_robustness.py::test_provider_that_declares_bars_never_builds_them_from_ticks`.
- Tests that need the optional SDK use `pytest.importorskip("<module>")` so the suite stays green without the extra.

A smoke test against a real demo account is a manual, documented step ([Exploratory testing by hand](../../tester/writing/exploratory.md)), not part of the suite.
