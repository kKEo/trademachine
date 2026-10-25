# Commands and flags

This chapter lists every flag of the `tradesignals` command, how flags combine, what happens at startup, and the exit codes. `scripts/docs-check.py` fails when a flag exists in the parser but not on this page.

```bash
tradesignals --data {alpaca,xtb} --symbols AAPL[,MSFT...] [options]
```

## Flags

| Flag | Default | Values | Meaning |
|---|---|---|---|
| `--data` | required | `alpaca`, `xtb` | market data provider (`DATA_PROVIDERS` registry) |
| `--broker` | none | `alpaca`, `paper`, `xtb` | broker to read positions from and, with `--execute`, to send orders to (`BROKERS` registry) |
| `--symbols` | required | comma-separated | canonical symbols; whitespace trimmed, upper-cased |
| `--strategy` | `sma_cross` | `sma_cross`, `rsi_reversion` | strategy name (`REGISTRY`) |
| `--timeframe` | the strategy's own | `1m`, `5m`, `15m`, `1h`, `1d` | bar size passed to the strategy constructor as `timeframe=` |
| `--execute` | off | flag | send orders; without it the run is signal-only whatever `--broker` says |
| `--allow-live` | off | flag | required to `--execute` against a broker whose `paper` attribute is false |
| `--qty` | `1.0` | float | target position in shares for a LONG signal, multiplied by signal strength |
| `--no-warmup` | off | flag | skip fetching history into the strategy at startup |
| `-v`, `--verbose` | off | flag | DEBUG level for `tradesignals.*` loggers only; third-party loggers stay at INFO |
| `--version` | | | print `tradesignals <version>` and exit |
| `-h`, `--help` | | | usage, flag help and examples |

Strategy parameters other than the timeframe cannot be set from the command line.

## How flags combine

| Combination | Effect |
|---|---|
| no `--broker`, no `--execute` | signal-only; no credentials needed beyond the data provider's |
| `--broker X`, no `--execute` | connects to the broker (credentials required), logs positions and `ORDER UPDATE` lines, sends nothing |
| `--execute`, no `--broker` | silently signal-only (`tradesignals/engine.py:64`); the CLI logs `--execute given without --broker: running signal-only` |
| `--execute --broker paper` | trades the in-memory paper broker; logs `EXECUTE mode: broker=paper paper=True` |
| `--execute --broker alpaca` with `ALPACA_PAPER=true` | trades the Alpaca paper account |
| `--execute --broker alpaca` with `ALPACA_PAPER=false` | refused: `refusing --execute: broker 'alpaca' is configured for a LIVE account ...`, exit 1 |
| `--execute --allow-live --broker alpaca` with `ALPACA_PAPER=false` | trades live; logs `EXECUTE mode against a LIVE alpaca account (--allow-live given)` at WARNING |

The same applies to `xtb` with `XTB_DEMO`.

## Startup sequence

`main()` and `_run()` in `tradesignals/cli.py`:

1. `load_dotenv()` (`tradesignals/cli.py:143`), then argument parsing.
2. `configure_logging()`: root at INFO, `tradesignals` at DEBUG if `-v` (`tradesignals/cli.py:76-80`).
3. Symbols are split, trimmed and upper-cased; the strategy is built as `REGISTRY[name](symbols, timeframe=...)`.
4. `make_data()` and, if given, `make_broker()` construct the adapters. Constructors read credentials and may raise `RuntimeError` (missing) or `ValueError` (bad boolean); Alpaca constructors build client objects without touching the network.
5. `check_live_guard()` (`tradesignals/cli.py:83`) refuses live execution without `--allow-live`.
6. `RiskManager(RiskLimits.from_env())` is created only when a broker is given (`tradesignals/cli.py:118`).
7. The engine is built with the paper-mark callback when the broker has a `mark()` method (`tradesignals/cli.py:98-108`).
8. `SIGINT` and `SIGTERM` are mapped to `engine.stop()` (`tradesignals/cli.py:135`).
9. `engine.run()`: data connect, broker connect and state refresh, warm-up, subscribe, then the event loop until stopped.

## Exit codes

| Code | When |
|---|---|
| 0 | clean stop (`Ctrl-C`, `SIGTERM`) or the data stream ended on its own (the replay provider, or an XTB socket that closed normally) |
| 1 | the live guard refused (`refusing --execute: ...` on stderr, no traceback), or an uncaught exception: missing credentials, a bad boolean, an unparseable `RISK_*` value, a provider error during connect or warm-up (traceback on stderr) |
| 2 | argparse usage error (unknown flag, bad choice, missing required flag) |

A data-stream failure while running logs `data stream failed; stopping engine` with a traceback and exits 0 after a clean shutdown; use the log, not the exit code, to detect it.

## Examples

```bash
# signals only, two symbols, default strategy on 1-minute bars
tradesignals --data alpaca --symbols AAPL,MSFT

# hourly crossover, signals only
tradesignals --data alpaca --symbols AAPL --strategy sma_cross --timeframe 1h

# paper-trade with the in-memory broker, 2 shares per signal
tradesignals --data alpaca --broker paper --symbols NVDA --strategy rsi_reversion --execute --qty 2

# XTB demo end to end, keep a log
tradesignals --data xtb --broker xtb --symbols AAPL --execute --qty 1 2> session.log
```
