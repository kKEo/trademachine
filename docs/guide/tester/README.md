# Tester guide

This guide is for people checking that a change to tradesignals is safe to merge: reviewers, contributors writing tests, and anyone probing the program by hand. It assumes you can run `pytest` and read a Python test; it does not assume you know the trading domain, which the [Operator guide](../operator/) covers.

The suite is small and fast (about one second, no network) and it is the only gate the project has besides lint. Every page here names the command or the test file it talks about.

| Part | Read it when you need to... |
|---|---|
| [Part I: Getting started](start/README.md) | run the suite once, read its output, and find the tests for a product area |
| [Part II: Gates](gates/README.md) | know what blocks a merge, run the same checks locally, and see what is still untested |
| [Part III: Writing tests](writing/README.md) | add a test for a change, follow the conventions, and probe the program by hand on a paper account |
| [Appendix](appendix/README.md) | look up testing vocabulary |

## Conventions

- Commands run from the repository root with the virtual environment active (`source .venv/bin/activate`), after `pip install -e ".[alpaca,dev]"`.
- Tests are named as `tests/test_engine.py::test_warmup_uses_history`; `scripts/docs-check.py` fails if a name on these pages stops existing.
- "Green" means `pytest -q` ends with `passed` and nothing else; "red" means at least one failure.
- Test helpers live in `tests/helpers.py` and are imported with `from helpers import ...`.

> **Looking for how to use the program, or how it works inside?** The [User guide](../user/) is the plain-language introduction. The [Operator guide](../operator/) has the architecture, the configuration reference and the ranked list of known gaps.
