# Glossary

**Gate.** A check that must pass before a merge: lint, format, tests, docs drift.

**Green / red.** A run of `pytest -q` with no failures / with at least one.

**Replay.** Running the engine on a fixed list of events through `ReplayProvider`, with no network and no clock.

**Fake.** A stand-in that records calls and returns canned data (`FakeSession`, `Recorder`), as opposed to a mock library.

**Helper.** A plain function in `tests/helpers.py` that builds test data (`ticks`, `bars`, `wave`).

**Pinning test.** A test that documents current behaviour, including behaviour known to be wrong, so that a change is deliberate.

**Time bomb.** A test whose outcome depends on the calendar; the old warm-up test was one, anchored to a fixed date.

**Flaky.** A test that passes or fails without a code change; this suite has none because it uses no sleeps, clock or network.

**Exploratory session.** A manual run on a paper account following a plan and a list of probes.

**Probe.** One manual action with an expected outcome.

**Coverage map.** The table in [What is tested where](../start/test-map.md) from product area to test names.

**Drift check.** `scripts/docs-check.py`: verifies that names in the docs still exist in the code and tests.

**Importorskip.** `pytest.importorskip("module")`: skips a test when an optional dependency is absent.

**Terminal status.** An order status after which nothing changes: filled, cancelled, rejected.
