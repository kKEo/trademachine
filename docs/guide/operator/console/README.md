# Part V: The admin console

The admin console is a local web page, served by the engine process itself, for managing the paper account, watching prices and trends, and reading and analysing signals. A thin terminal view shows the essentials without a browser. This part explains how to run it, how it is built, how it is secured and what every page and endpoint does.

- [Architecture of the console](architecture.md): the event bus, the SQLite store, the service layer and the rules that keep the engine independent of all three.
- [Security model](security.md): who can reach the console, what the token unlocks, what is refused on a live account, what is recorded.
- [Pages, API and database](reference.md): every page, every JSON endpoint and action, the tables, retention, and the terminal UI.

## Start it

```bash
pip install -e ".[alpaca,console]"          # FastAPI, uvicorn, Jinja2
tradesignals --data alpaca --broker paper --symbols AAPL,MSFT --execute --console
# console: http://127.0.0.1:8788 (read-only, CONSOLE_TOKEN not set)
```

`--console` starts an HTTP server on `127.0.0.1` inside the engine process and turns persistence on (`data/tradesignals.db` unless `--db` or `TRADESIGNALS_DB` names another file). Without `CONSOLE_TOKEN` the console can only show things. To enable actions, put a long random value in `.env` and restart:

```bash
CONSOLE_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
```

then type that value into the "Console token" box at the bottom of the menu. The browser keeps it in a cookie until you choose "Lock actions".

To try the console without any account or network, run the demo: a synthetic market, two strategies and the in-memory paper broker.

```bash
python scripts/console-demo.py                      # http://127.0.0.1:8788, view only
CONSOLE_TOKEN=demo python scripts/console-demo.py   # with actions
```

The terminal view needs the `tui` extra and a running console:

```bash
pip install -e ".[tui]"
tradesignals-tui                    # reads CONSOLE_PORT and CONSOLE_TOKEN from the environment or .env
```

## What you can do from it

| Goal | Where |
|---|---|
| See equity, today's result, drawdown, positions, latest signals and orders | Overview |
| Close one position or all, cancel an open order, place a manual order, read the trade history with realised P&L | Portfolio |
| Watch each symbol's trend and momentum, and a candlestick chart with averages, RSI, signal and fill markers | Markets |
| Filter signals, compare strategies, see what the price did after each signal, export CSV | Signals |
| Read the order history | Orders |
| Pause, resume, add or remove strategies | Strategies |
| See limit usage, halt and resume trading, change limits, pull the kill switch | Risk |
| Check health, read the log tail, the settings in effect, past sessions and the audit trail | System |

## What it deliberately does not do

- It does not listen on any address but `127.0.0.1`, and there is no flag to change that. For access from another machine use an SSH tunnel (`ssh -L 8788:127.0.0.1:8788 host`).
- It does not trade a live account. On a live broker the console offers halt and the kill switch and refuses every other action.
- It does not keep changes across restarts: limits and strategies set from the console apply to the running engine only. The audit log records them.
- It never bypasses risk: every order it sends goes through `RiskManager.check()` like a strategy's.

> **Warning.** A halt stops new and larger positions; it does not sell anything. The kill switch also stops the engine and therefore the console. Neither closes positions. Use "Close every position" for that, and check the broker's own app afterwards.
