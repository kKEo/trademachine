# Exploratory testing by hand

Automated tests never touch the network. This page is the manual session that covers what they cannot: a real paper account, real latency, real rejections, and the operator-facing behaviour around start, stop and failure. Plan 45 minutes during US market hours with an Alpaca paper account.

## Preparation

```bash
source .venv/bin/activate
cp .env.example .env            # fill ALPACA_API_KEY / ALPACA_SECRET_KEY from the PAPER dashboard
export RISK_MAX_ORDER_NOTIONAL=500 RISK_MAX_POSITION_QTY=2 RISK_MAX_OPEN_POSITIONS=1
```

Keep the broker's web dashboard open in a browser to compare what the engine believes with what the account shows.

## Session plan

1. **Signal-only, one symbol.** `tradesignals --data alpaca --symbols AAPL 2> session.log`. Expect `warmup sma_cross/AAPL: N bars` with N ≥ 31 if the session is older than half an hour, then `engine running`, then silence until a crossover. Confirm the timestamps are local time and that nothing is written to stdout.
2. **Paper broker execution.** Add `--broker paper --execute --qty 1`. Expect `EXECUTE mode: broker=paper paper=True`. On the first LONG expect an `ORDER paper buy 1.0000 AAPL -> filled` line; on the next FLAT a sell. Compare with the dashboard: nothing should appear there (the paper broker is internal).
3. **Alpaca paper execution.** Switch to `--broker alpaca`. Expect `alpaca broker connected (paper=True)`. After an order, expect `ORDER alpaca buy ... -> accepted` (or `new`), then one or more `ORDER UPDATE ... filled` lines, and the position in the dashboard.
4. **Stop mid-position.** `Ctrl-C` while long. Expect `engine stopped`, exit 0, and the position still open in the dashboard. Close it there.
5. **Restart with a position.** Open a 1-share position by hand in the dashboard, start the engine with `--execute`. On the next FLAT it sells your share; on the next LONG it logs `no-op`. This is the "engine owns every position" behaviour.

## Probes

Each probe has an expected outcome. A different outcome is a finding for the operator guide's known-gaps page.

| Probe | Command or action | Expected |
|---|---|---|
| Live guard | `ALPACA_PAPER=false tradesignals --data alpaca --broker alpaca --symbols AAPL --execute` (paper keys are fine; nothing is sent) | `refusing --execute: broker 'alpaca' is configured for a LIVE account ...`, exit 1, no network order |
| Bad boolean | `ALPACA_PAPER=maybe tradesignals --data alpaca --symbols AAPL` | `ValueError: ALPACA_PAPER='maybe' is not a boolean; use true or false`, exit 1 |
| Verbose with secrets | add `-v` to any run | DEBUG lines from `tradesignals.*` only; grep the log for the secret: no match |
| `--execute` without broker | `tradesignals --data alpaca --symbols AAPL --execute` | WARNING `--execute given without --broker: running signal-only`; `engine running: ... execute=False` |
| SIP without a plan | `ALPACA_FEED=sip` | after `engine running`, silence; nothing logged (known gap H4) |
| Unknown symbol | `--symbols ZZZZ` | warm-up `only 0 of 31 bars`, then silence; no error |
| Hourly bars | `--timeframe 1h` | first `SIGNAL`, if any, carries a bar start on the UTC hour |
| Notional cap on exit | hold 1 share, set `RISK_MAX_ORDER_NOTIONAL=1` and restart; wait for FLAT | `RISK REJECT sell 1.0 AAPL: notional ...`; position remains (known gap H1) |
| Pull the network | disconnect Wi-Fi for 30 s during a run | Alpaca: alpaca-py reconnects by itself (watch for a gap, then events resume) or the run goes silent; XTB: `xtb ping failed` every 10 s, then restart by hand |
| Module entry point | `python -m tradesignals --version` | `tradesignals 0.1.0` |
| Windows | run on native Windows | fails at startup on signal handlers (unsupported) |
| XTB demo login | `--data xtb --symbols AAPL` with demo credentials | either `XTB logged in (wss://ws.xtb.com/demo, demo=True)` and ticks, or a connection/login failure confirming the API is unavailable to your account; record which |

## Recording findings

Add anything unexpected to the operator guide's [Known gaps and roadmap](../../operator/develop/known-gaps.md) with the command, the expected and the actual outcome, and the log lines. If it can be reproduced offline, write the test from [Adding tests](adding-tests.md) and let it fail until fixed.
