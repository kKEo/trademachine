# Configuration reference

This chapter lists every environment variable the code reads, with its default and exactly how the value is interpreted. The repository's `scripts/docs-check.py` fails when a variable is added to the code or to `.env.example` without appearing here.

All variables are read from the process environment after `load_dotenv()` has loaded `.env` (see [Install](install.md) for where `.env` is looked for and why shell variables win).

## Alpaca

| Variable | Default | Parsed as | Read at |
|---|---|---|---|
| `ALPACA_API_KEY` | none, required | string | `tradesignals/providers/alpaca.py:66` |
| `ALPACA_SECRET_KEY` | none, required | string | `tradesignals/providers/alpaca.py:66` |
| `ALPACA_PAPER` | `true` | strict boolean (below) | `tradesignals/providers/alpaca.py:69` |
| `ALPACA_FEED` | `iex` | `sip` (case-insensitive) selects the SIP feed; **any other value** means IEX. Applies to the live stream and to warm-up history. | `tradesignals/providers/alpaca.py:97` |

Missing keys raise `RuntimeError: ALPACA_API_KEY / ALPACA_SECRET_KEY not set` when the adapter is constructed, before any network call. Keys are needed for signal-only runs too, because the data stream authenticates.

## XTB

| Variable | Default | Parsed as | Read at |
|---|---|---|---|
| `XTB_USER_ID` | none, required | string | `tradesignals/providers/xtb.py:80` |
| `XTB_PASSWORD` | none, required | string | `tradesignals/providers/xtb.py:80` |
| `XTB_DEMO` | `true` | strict boolean (below) | `tradesignals/providers/xtb.py:83` |
| `XTB_SYMBOL_SUFFIX` | `.US` | string appended to every canonical symbol (`AAPL` → `AAPL.US`) and stripped on the way back. `.US` are CFDs, `.US_9` are real stocks on most accounts. | `tradesignals/providers/xtb.py:152`, `tradesignals/providers/xtb.py:220` |

## Risk limits

Read only when `--broker` is given (`tradesignals/cli.py:118`), through `RiskLimits.from_env()` (`tradesignals/risk.py:21-29`). A value that does not parse raises at startup with a traceback.

| Variable | Default | Parsed as | Rule |
|---|---|---|---|
| `RISK_MAX_ORDER_NOTIONAL` | `2000` | float | reject when `qty × price > limit` (price = limit price if any, else the last mid) |
| `RISK_MAX_POSITION_QTY` | `50` | float | reject when the resulting absolute position would exceed the limit |
| `RISK_MAX_OPEN_POSITIONS` | `5` | int | reject a *new* symbol when this many positions already exist on the account |
| `RISK_MAX_DAILY_LOSS` | `200` | float | halt every further order once equity has fallen this much below the equity seen at the first check of this run |
| `RISK_MAX_QUOTE_AGE_SEC` | `15` | float | reject when the last tick for the symbol is older than this (wall clock) |

The order in which the rules run, and their reject strings, are in [Execution and risk](../internals/execution-and-risk.md).

## Strict booleans

`ALPACA_PAPER` and `XTB_DEMO` go through `env_bool()` (`tradesignals/config.py:15`):

| Value (case-insensitive, trimmed) | Result |
|---|---|
| unset, or empty | the default (`true`) |
| `1`, `true`, `yes`, `on` | paper / demo |
| `0`, `false`, `no`, `off` | live / real |
| anything else | `ValueError: ALPACA_PAPER='...' is not a boolean; use true or false` at startup |

The point is that no spelling fails open to a live account. Setting a flag to `false` is still not enough to trade live: the CLI refuses `--execute` against a broker whose `paper` attribute is false unless `--allow-live` is given (`tradesignals/cli.py:83-95`).

## `.env.example`

The repository ships `.env.example` with every variable above and its default; `scripts/docs-check.py` verifies that the set of names there equals the set the code reads. Copy it to `.env` and fill in only what you use. `.env` is ignored by git; never commit keys.

## Not configurable

The following are hard-coded. Changing them means editing the code (all locations are in `tradesignals/providers/`):

| Setting | Value | Where |
|---|---|---|
| Paper broker starting cash | `100_000.0` | `tradesignals/providers/fake.py:83` |
| XTB hosts | `wss://ws.xtb.com/demo`, `/real`, plus `Stream` | `tradesignals/providers/xtb.py:68` |
| XTB request throttle | 0.21 s between main-socket requests | `tradesignals/providers/xtb.py:120` |
| XTB keep-alive | ping both sockets every 10 s | `tradesignals/providers/xtb.py:139` |
| XTB tick subscription | `minArrivalTime=200`, `maxLevel=0` | `tradesignals/providers/xtb.py:162` |
| XTB order-status poll | `poll_sec=2.0` | `tradesignals/providers/xtb.py:217` |
| XTB websocket message cap | `max_size=2**24` | `tradesignals/providers/xtb.py:95`, `tradesignals/providers/xtb.py:98` |
| XTB account currency fallback | `PLN` | `tradesignals/providers/xtb.py:387` |
| Alpaca stream bars | always 1-minute | `tradesignals/providers/alpaca.py:122` |
| Symbol mapper overrides | none | `tradesignals/interfaces.py:15` |
| Strategy parameters (SMA lengths, RSI thresholds) | class defaults | `strategies/` |
| Log format, level, destination | `%(asctime)s %(levelname)s %(name)s: %(message)s` to stderr, INFO (`-v` for DEBUG) | `tradesignals/cli.py:76-80` |

There is no configuration file and no `--config` flag.
