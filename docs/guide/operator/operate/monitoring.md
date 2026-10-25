# Monitoring and logs

The engine has no metrics endpoint, no health check and no persistence; the log is the only window into it. This chapter lists every line it can write, what it means, and how to watch a process from outside.

## Logging setup

`configure_logging()` (`tradesignals/cli.py:76-80`) calls `logging.basicConfig(level=INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")`, which writes to **stderr** with **local-time** timestamps, and sets the `tradesignals` logger to DEBUG when `-v` is given. Third-party loggers (`websockets`, `alpaca`) inherit the root's INFO. That split is deliberate: at DEBUG the websockets library logs every frame it sends, which for XTB includes the login message with the password (verified against websockets source). `-v` is therefore safe to use with real credentials.

Loggers in use: `tradesignals.cli`, `tradesignals.engine`, `tradesignals.alpaca`, `tradesignals.xtb`. There is no file handler and no rotation; redirect stderr (`2> session.log`) or let the service manager capture it.

## Every log line

| Logger | Level | Message | Meaning |
|---|---|---|---|
| cli | WARNING | `EXECUTE mode: broker=<name> paper=True` | `--execute` against a paper/demo broker |
| cli | WARNING | `EXECUTE mode against a LIVE <name> account (--allow-live given)` | real money |
| cli | WARNING | `--execute given without --broker: running signal-only` | flag combination downgraded |
| engine | INFO | `warmup <strategy>/<symbol>: N bars` | warm-up delivered at least `warmup_bars` bars |
| engine | WARNING | `warmup <strategy>/<symbol>: only N of M bars available; strategy starts cold` | not enough history; strategy silent until it has seen M live bars |
| engine | WARNING | `warmup <strategy>/<symbol> failed: <error>` | `get_bars()` raised; cold start |
| engine | INFO | `engine running: symbols=[...] strategies=[...] execute=<bool> qty=<float>` | subscribed; event loop running (`tradesignals/engine.py:93`) |
| engine | DEBUG | `<symbol>: provider pushes bars; ticks no longer build bars for it` | safety-net switch for an undeclared bar-pushing provider |
| engine | INFO | `SIGNAL <strategy> <symbol> <kind> strength=<f> <reason>` | a signal (`tradesignals/engine.py:215`) |
| engine | INFO | `no-op: <symbol> already at <qty>` | target equals held position (`tradesignals/engine.py:235`) |
| engine | WARNING | `RISK REJECT <side> <qty> <symbol>: <reason>` | a risk rule fired (`tradesignals/engine.py:248`) |
| engine | ERROR | `submit failed for <OrderRequest>` + traceback | broker raised on submit; signal dropped (`tradesignals/engine.py:253`) |
| engine | INFO | `ORDER <source> <side> <qty> <symbol> -> <status> (<id>)` | order accepted by the adapter (`tradesignals/engine.py:257`) |
| engine | INFO | `ORDER UPDATE <symbol> <side> <status> filled=<qty> @ <price>` | push or polled update |
| engine | WARNING | `broker state refresh failed: <error>` | positions/account not re-read; stale values in use (`tradesignals/engine.py:272`) |
| engine | ERROR | `error handling <event>` + traceback | one event raised inside a strategy, callback or aggregator; skipped |
| engine | ERROR | `data stream failed; stopping engine` + traceback | provider stream raised; shutdown follows (`tradesignals/engine.py:169`) |
| engine | ERROR | `order update stream failed; continuing without push updates` + traceback | broker update stream raised; trading continues (`tradesignals/engine.py:292`) |
| engine | WARNING | `KILL SWITCH: halting risk, cancelling open orders, stopping` | `kill_switch()` was called by an embedding program |
| engine | INFO | `engine stopped` | shutdown complete (`tradesignals/engine.py:114`) |
| alpaca | INFO | `alpaca data: feed=<iex\|sip> symbols=[...]` | stream subscribed |
| alpaca | INFO | `alpaca broker connected (paper=<bool>)` | trade-update stream started |
| xtb | INFO | `XTB logged in (<url>, demo=<bool>)` | login done, stream socket open |
| xtb | WARNING | `xtb ping failed: <error>` | keep-alive failed; repeats every 10 s while the socket is dead |
| xtb | INFO | `xtb order <id>: <message>` | the broker's own message for a transaction |
| xtb | WARNING | `cancel <id> failed: <error>` | `cancel_all()` could not delete one pending order |
| xtb | WARNING | `refresh <id> failed: <error>` | status poll of a pending order failed; retried next poll |

Lines with a traceback are the ones to alert on. `RISK REJECT` is expected in normal operation; a *burst* of them (every signal rejected) usually means a limit is mis-set or the manager is halted.

## What is not observable

- Startup configuration (risk limits, feed, qty) is only partly logged; `engine running` carries symbols, strategies, execute and qty, the adapters log feed and paper mode, risk limits are not logged.
- Signals and orders are not persisted; `engine.signals` and `engine.orders` are in-memory lists.
- There is no heartbeat. A silent process is indistinguishable from a healthy one outside market hours.
- The number of late events dropped by an aggregator (`BarAggregator.dropped_late`) is counted but not logged.

## Watching from outside

Run the process under a service manager that captures stderr and restarts it, since the engine stops on a data-stream failure rather than reconnecting:

```ini
# ~/.config/systemd/user/tradesignals.service (Linux); use launchd on macOS
[Service]
WorkingDirectory=/path/to/tradesignals
EnvironmentFile=/path/to/tradesignals/.env   # strip inline '#' comments first; systemd keeps them
ExecStart=/path/to/tradesignals/.venv/bin/tradesignals --data alpaca --broker paper --symbols AAPL --execute
Restart=on-failure
RestartSec=30
```

Then watch the journal:

```bash
journalctl --user -u tradesignals -f
journalctl --user -u tradesignals --since today | grep -E "RISK REJECT|error handling|stream failed"
```

A useful external check during market hours: alert if no `SIGNAL`, `ORDER` or `ORDER UPDATE` line has appeared for N minutes *and* the market is open. The engine itself cannot tell you that.
