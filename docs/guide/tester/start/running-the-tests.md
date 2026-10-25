# Running the tests

This page gets the suite running and tells you what a healthy run looks like. It takes two minutes after the install.

## Run everything

```bash
source .venv/bin/activate
pip install -e ".[alpaca,dev]"     # once; installs pytest, pytest-asyncio and ruff
pytest -q
```

Expected tail of the output:

```text
........................................................................ [ 68%]
.................................                                        [100%]
105 passed in 0.8s
```

The count grows as tests are added; what matters is `passed` with no `failed`, `error` or `skipped` (one test, `tests/test_alpaca_mapping.py::test_timeframe_lookup_against_real_alpaca_enum`, skips itself when the `alpaca` extra is not installed; with the install line above it runs).

## Run part of it

```bash
pytest -q tests/test_risk.py                                   # one file
pytest -q tests/test_engine.py::test_warmup_uses_history        # one test
pytest -q -k "paper and limit"                                  # by name
pytest -q -x                                                    # stop at first failure
pytest --collect-only -q | tail -1                              # count without running
pytest -q -vv tests/test_xtb_broker.py                          # see each test name
```

## How the suite is set up

- `pyproject.toml` sets `asyncio_mode = "auto"` under `[tool.pytest.ini_options]`, so `async def` tests run without a marker, and `testpaths = ["tests"]`.
- No test touches the network. Engine tests use `ReplayProvider` and `PaperBroker` from `tradesignals/providers/fake.py`; adapter tests use fakes or pure helper functions.
- `tests/helpers.py` provides `ticks()`, `bars()`, `wave()`, `now_floor()` and the fixed epoch `T0`. There is no `conftest.py`; the helpers are plain functions.
- The whole suite runs in about a second. There are no sleeps and no time-dependent assertions: the one test that touches the wall clock (`tests/test_engine.py::test_warmup_uses_history`) builds its history relative to now.

## Traps

- **`ModuleNotFoundError: No module named 'tradesignals'`** when running bare `pytest`: the package is not installed. Run `pip install -e ".[dev]"` or use `python -m pytest`, which puts the current directory on the path.
- **Async tests fail with "async def functions are not natively supported"**: `pytest-asyncio` is missing. It is in the `dev` extra. On pytest 8.0 to 8.3 the same situation showed up as *skipped* tests and a misleadingly green run; pytest 8.4 and later fail instead.
- **`tests/test_cli.py::test_module_entry_point_prints_version` fails**: it runs `python -m tradesignals --version` in a subprocess with the same interpreter; it needs the editable install so that the version is readable from package metadata.
- **Lint is separate.** `pytest` does not run ruff; see [Gates and CI](../gates/gates-and-ci.md).
