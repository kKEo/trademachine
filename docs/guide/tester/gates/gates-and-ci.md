# Gates and CI

This page says what blocks a merge and why, and how to run exactly the same checks on your machine before pushing. The gates are defined in `.github/workflows/ci.yml` and `.github/workflows/docs.yml`; the Makefile mirrors them.

## The checks

| Gate | Command | Blocks a merge when | Why it exists |
|---|---|---|---|
| Lint | `ruff check .` | any rule in `E`, `F`, `I`, `W`, `BLE` fires (`[tool.ruff.lint]` in `pyproject.toml`, line length 120) | unused imports and blind `except Exception` without a `noqa` are how silent failures start |
| Format | `ruff format --check .` | any file would be reformatted | one layout, no style debates in review |
| Tests | `pytest -q` | any test fails or errors | the behaviour contract; `CLAUDE.md` requires a test for every behaviour change |
| Docs drift | `python scripts/docs-check.py` | an environment variable, CLI flag, registry name or test name in the docs no longer matches the code, or a link, image or SUMMARY entry is broken | documentation that lies is worse than none |

`ci.yml` runs all four on Python 3.11 and 3.12 for every push to `master`/`main` and every pull request. `docs.yml` runs the docs check again, builds the three books with a pinned, checksum-verified mdBook, and on pushes to the default branch publishes them to GitHub Pages.

There is no coverage threshold, no type checker and no pytest timeout. A hang in a test would block CI until the runner's own limit.

## Run the gates locally

```bash
source .venv/bin/activate
make lint          # ruff check . && ruff format --check .
make test          # pytest -q
make docs-check    # python scripts/docs-check.py
make docs          # docs-check + mdbook build of all three books into site/
```

Or without make:

```bash
ruff check . && ruff format --check . && pytest -q && python scripts/docs-check.py
```

`ruff format .` (without `--check`) rewrites files; run it before committing, then `ruff check . --fix` for import order.

## What `CLAUDE.md` asks for beyond the gates

- A test for every behaviour change, and `pytest` before every commit. CI enforces the first only in the sense that a behaviour change without a test still passes; reviewers must ask for the test.
- Never commit API keys: `.env` is ignored by git, but nothing scans a commit for secrets. Review diffs of `.env.example` and any test fixtures with that in mind.
- Keep the kill switch and cancel-all working: `tests/test_engine_robustness.py::test_kill_switch_stops_even_if_cancel_all_fails` and `tests/test_xtb_broker.py::test_cancel_all_deletes_every_pending_order_but_leaves_positions` are the guards.
- Never change the paper/demo defaults: `tests/test_alpaca_mapping.py::test_creds_paper_flag_is_strict` and `tests/test_config.py::test_env_bool_unset_uses_default` pin them.

## Pull-request checklist

- [ ] `make lint test docs-check` is green locally.
- [ ] Every behaviour change has a test, and the test fails without the change.
- [ ] If an environment variable, flag, strategy or provider was added, the operator reference pages were updated (the docs check will say which).
- [ ] If a defect from the operator guide's known-gaps page was fixed, its row was moved to the resolved list.
- [ ] No credentials in the diff; `.env` untouched.
