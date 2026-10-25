# Install

This chapter installs tradesignals for development or for running it on a paper account. It takes about five minutes.

## Requirements

- Python 3.11 or newer (`requires-python = ">=3.11"` in `pyproject.toml`); the suite runs on 3.11 and 3.12 in CI.
- macOS or Linux. The CLI installs `SIGINT`/`SIGTERM` handlers with `loop.add_signal_handler()` (`tradesignals/cli.py:135`), which is not available on Windows.
- Network access to Alpaca or XTB for live runs. Tests need no network.

## Steps

```bash
git clone https://github.com/OWNER/tradesignals.git
cd tradesignals
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[alpaca,dev]"
cp .env.example .env            # then fill in the keys you need
tradesignals --version          # tradesignals 0.1.0
pytest -q                       # the suite must be green
```

Extras:

| Extra | Installs | Needed for |
|---|---|---|
| `alpaca` | `alpaca-py>=0.30` | `--data alpaca`, `--broker alpaca`. Imported lazily, so the package imports without it; selecting `alpaca` without the extra fails with `ModuleNotFoundError: No module named 'alpaca'`. |
| `dev` | `pytest`, `pytest-asyncio`, `ruff` | tests and lint |

Core dependencies are `websockets>=12` (XTB) and `python-dotenv>=1.0`. Nothing is pinned and there is no lock file; the versions used while writing this guide were alpaca-py 0.44, websockets 17.3, python-dotenv 1.2.

## Why the editable install matters

`main()` calls `load_dotenv()` with no arguments (`tradesignals/cli.py:143`). python-dotenv then searches for `.env` starting from the directory of the file that called it and walking up (verified against python-dotenv source), **not** from the current working directory. With the editable install above the calling file lives in the repository, so the repository's `.env` is found from any working directory. With a regular (non-editable) install the search starts inside `site-packages` and a `.env` in your project directory is **not** found; export the variables in the shell or in the service unit instead.

Values already present in the environment win over `.env` (`override=False`). Inline comments such as `ALPACA_FEED=iex  # iex | sip` in `.env.example` are stripped by python-dotenv but not by other loaders (`docker --env-file`, systemd `EnvironmentFile`); remove them if you reuse the file elsewhere.

## Invocation forms

- `tradesignals ...` (console script from `pyproject.toml`).
- `python -m tradesignals ...` (via `tradesignals/__main__.py`).
- `python -m tradesignals.cli ...` also works.

## Upgrading

Pull, then reinstall so that new dependencies and the console script are refreshed:

```bash
git pull
pip install -e ".[alpaca,dev]"
pytest -q
```

Nothing persists between versions: there is no database, no state file and no migration to run.
