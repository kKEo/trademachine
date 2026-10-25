# Operations runbook

This chapter is the procedure list: how to start, stop and restart safely, what to expect at the open, what to do when the stream dies, and a troubleshooting table with code pointers. Nothing here needs more than a terminal.

## Start

1. `source .venv/bin/activate`, confirm `.env` (or the service environment) holds the right keys and `ALPACA_PAPER=true` / `XTB_DEMO=true`.
2. Start signal-only first and watch for `warmup ...` and `engine running`. If warm-up reports `starts cold`, decide whether to wait or restart later.
3. For execution, add `--broker` and `--execute`, confirm the `EXECUTE mode: broker=... paper=True` line, and check the broker's own app once the first `ORDER` line appears.

## Stop

`Ctrl-C` or `systemctl --user stop tradesignals`. The engine cancels its tasks, closes the data provider, then the broker, and logs `engine stopped`. It does **not** cancel pending orders (there are none from the engine itself, which sends market orders only) and does **not** flatten. If you do not want to hold the position overnight, close it in the broker's app after stopping.

If the process hangs on shutdown, the likely spot is the XTB logout waiting for a reply on a dead socket (`XtbSession.close()`); send `SIGKILL`. Nothing is lost because nothing is persisted.

## Restart

Every restart is a cold process:

- Positions and account are re-read from the broker at start and before every order; the engine will act on positions it finds, including ones you opened by hand.
- Strategy state is rebuilt from warm-up history with the signals discarded. `sma_cross` waits for the next crossover; if you hold shares and the next signal is FLAT, it sells them. `rsi_reversion` can come out of warm-up believing it is "in" a trade; restart with `--no-warmup` if its first LONG never fires.
- The loss halt and its baseline are reset (`RiskManager.start_equity`). A restart is therefore also the only way to clear a halt.

## At the open

The warm-up window is three times the strategy's need, measured backwards from the wall clock (`tradesignals/engine.py:135`). A 1-minute `sma_cross` started within 31 minutes of the open has fewer than 31 bars of today and starts cold; it begins signalling about half an hour later. A 5-minute `rsi_reversion` reaches into the previous session and is ready at once. The first live bar after warm-up may be partial because warm-up history can include the bucket in progress.

## When the data stream dies

You see `data stream failed; stopping engine` with a traceback, then `engine stopped`, exit code 0. Neither adapter reconnects. Restart the process (or let the service manager do it); positions are untouched. On Alpaca, if the stream task ended silently (for example after an "insufficient subscription" error on `ALPACA_FEED=sip` without the plan), there is no log line at all: the symptom is silence during market hours.

When only the order-update stream dies (`order update stream failed; continuing ...`), trading continues; positions are still re-read before every order. Restart at a quiet moment.

## XTB session notes

- One session per `(user, demo)` per process; the data and broker adapters share it.
- No receive timeout: a lost reply blocks every later command, including the keep-alive pings, and you see `xtb ping failed` every 10 s. Restart.
- Replies are matched by order, not by tag; cancelling a command between send and receive would offset every later reply. The engine does not do that, but an embedding program could.
- Pending (limit/stop) orders submitted by an embedding program are polled every 2 s and never progress past `ACCEPTED` through polling.

## Upgrade

```bash
git pull && pip install -e ".[alpaca,dev]" && pytest -q
```

No data migration exists because no data is stored.

## Backup and restore

Nothing to back up: `.env` is the only state, and the broker holds the positions and order history.

## Troubleshooting

| Symptom | Likely cause | Where to look | Action |
|---|---|---|---|
| `RuntimeError: ALPACA_API_KEY / ALPACA_SECRET_KEY not set` | `.env` not found (non-editable install) or variables empty | `tradesignals/providers/alpaca.py:66-68`, [Install](../install/install.md) | export the variables or reinstall editable |
| `ValueError: XTB_DEMO='...' is not a boolean` | bad boolean spelling | `tradesignals/config.py:15` | use `true`/`false` |
| `refusing --execute: ... LIVE account` | flags point at live | `tradesignals/cli.py:83-95` | set the paper flag, or pass `--allow-live` on purpose |
| `warmup ... failed: ...` on Alpaca | feed or plan issue, or history endpoint error | `tradesignals/providers/alpaca.py:150` | check `ALPACA_FEED`; the run continues cold |
| `engine running`, then silence in market hours | market closed, wrong symbol/suffix, or Alpaca stream task ended | `tradesignals/providers/xtb.py:162` (suffix), `tradesignals/providers/alpaca.py:135` (stream task) | verify symbol; on Alpaca check the feed; restart |
| Every signal is `RISK REJECT ... halted` | loss halt latched | `tradesignals/risk.py:69-72` | close positions in the app, restart |
| `RISK REJECT sell ... notional` | sale blocked by the notional cap | `tradesignals/risk.py:57` | raise the cap for the exit, restart, or close in the app |
| `RISK REJECT ... stale quote` persistently | feed stalled or thin instrument | `tradesignals/risk.py:55` | restart; consider a larger `RISK_MAX_QUOTE_AGE_SEC` |
| Duplicate order after a slow fill | orders in flight are invisible | `tradesignals/engine.py:223-235` | reduce signal frequency (larger timeframe); known gap |
| `TypeError` from Alpaca positions | `None` numeric fields | `tradesignals/providers/alpaca.py:232-236` | fixed by `_opt_float`; report if seen again |
| `xtb ping failed` every 10 s | main socket dead | `tradesignals/providers/xtb.py:137-143` | restart; no reconnect exists |
| Process will not exit on `Ctrl-C` | XTB logout blocked on `recv` | `XtbSession.close()` | `SIGKILL`; nothing is lost |
| Windows: error about signal handlers at start | unsupported platform | `tradesignals/cli.py:135` | use WSL |
