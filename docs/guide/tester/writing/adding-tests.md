# Adding tests

This page tells you which kind of test to write for which kind of change, the conventions the suite follows, and the checklist a reviewer will apply. Budget: a new test usually takes less time than the change it covers.

## Which test for which change

| You changed... | Write... | Template |
|---|---|---|
| a model, indicator or pure helper | a plain unit test with literal inputs and exact expected outputs | `tests/test_bars.py::test_bucket_start_floors_to_utc_multiples` |
| a strategy | a bar series with hand-computed signal positions (`[(index, kind), ...]`) | `tests/test_strategies.py::test_sma_cross_emits_long_on_golden_cross_and_flat_on_death_cross` |
| a risk rule | a direct `RiskManager.check()` test with `now=T0` and the exact reject string | `tests/test_risk.py::test_rejects_stale_quote` |
| engine behaviour | a replay run: `ReplayProvider` + (optionally) `PaperBroker` with `on_tick=lambda t: broker.mark(...)`, `warmup=False`, `max_quote_age_sec=10**9`; assert on `engine.signals`, `engine.orders`, broker positions or `caplog` | `tests/test_engine_robustness.py::test_stream_failure_stops_engine_and_logs` |
| tick/bar handling for a provider | a replay whose events mimic the provider (including out-of-order bars) and a `Recorder` strategy; assert one bar per bucket | `tests/test_engine_robustness.py::test_provider_that_declares_bars_never_builds_them_from_ticks` |
| the paper broker | a sequence of `submit_order()` calls and exact `(qty, avg_price, cash)` tuples | `tests/test_paper_broker.py::test_partial_reduce_keeps_the_cost_basis` |
| an adapter's mapping or request building | a test against a stub object or `FakeSession` that records the request and returns canned replies | `tests/test_xtb_broker.py::test_cancel_order_sends_delete_type_4_with_the_orders_own_fields` |
| anything that needs an optional SDK | the same, guarded by `pytest.importorskip("alpaca.data.timeframe")` | `tests/test_alpaca_mapping.py::test_timeframe_lookup_against_real_alpaca_enum` |
| the CLI | parser tests with `parse_args([...])`, guard tests with fake broker objects, logging tests on effective levels; one subprocess test is enough | `tests/test_cli.py::test_live_guard_refuses_execute_on_live_account_without_allow_live` |
| configuration parsing | parametrised tests with `monkeypatch.setenv` | `tests/test_config.py::test_env_bool_rejects_anything_else_instead_of_failing_open` |

## Conventions

- **No network, ever.** Tests use `tradesignals/providers/fake.py`, stub objects, or `FakeSession`. A test that needs a live account is a manual probe ([Exploratory testing by hand](exploratory.md)), not a test.
- **No sleeps, no wall-clock assertions.** Use the fixed `T0` from `tests/helpers.py` and pass `now=` to the risk manager. If you must touch wall-clock code (warm-up), anchor your data to `now_floor()` as `tests/test_engine.py::test_warmup_uses_history` does, so the test never expires.
- **Async tests are bare `async def`** (`asyncio_mode = "auto"`). Wrap engine runs that could hang in `asyncio.wait_for(..., timeout=5)`.
- **Timestamps are aware UTC.** Build them with the helpers or `datetime(..., tzinfo=timezone.utc)`.
- **One behaviour per test, named as a sentence**: `test_late_tick_is_dropped_instead_of_resurrecting_old_bucket`. The name is the documentation; it is also what the tester guide's coverage map cites.
- **Assert exact values**, not just "something happened": the index of each signal, the reject string, the tuple of position fields.
- **Log assertions** use `caplog.at_level(level, logger="tradesignals.<module>")` and match on `r.getMessage()`.
- **Helpers go in `tests/helpers.py`**; fakes that only one file needs stay in that file.
- **Pin a known gap** when you cannot fix it: a test that documents current (wrong) behaviour with a docstring pointing at the known-gaps page is better than silence (`tests/test_risk.py::test_halted_also_blocks_position_reducing_orders`).

## Adding a file

New test files are `tests/test_<area>.py` and are collected automatically (`testpaths = ["tests"]`). Add the area and the new test names to [What is tested where](../start/test-map.md); `scripts/docs-check.py` verifies the names exist, so a typo there fails CI rather than going stale.

## Pull-request checklist for tests

- [ ] The new test fails when the change is reverted (try it: `git stash`, run, `git stash pop`).
- [ ] It runs in well under a second and touches no network, clock or file system.
- [ ] Its name says what behaviour it pins.
- [ ] `make lint test docs-check` is green.
- [ ] The coverage map lists it.
