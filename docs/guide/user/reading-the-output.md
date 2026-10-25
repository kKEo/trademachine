# Reading the output

Every line the program prints has the same shape: a timestamp, a level, the part of the program that wrote it, and the message. This page explains each message you can meet.

```text
2026-10-23 15:52:00,480 INFO tradesignals.engine: SIGNAL sma_cross AAPL long strength=1.00 fast=184.12 slow=183.90
└── when (your local time) ─┘ └─level┘ └── who ──────────┘ └── what ───────────────────────────────────────────┘
```

Two things to know up front: the timestamps are in **your computer's local time**, and everything is written to the terminal's *error* stream, so to keep a copy use `2> session.log` at the end of the command. Nothing is saved anywhere else.

## Startup

| Line | Meaning |
|---|---|
| `alpaca broker connected (paper=True)` | The broker connection is up. `paper=True` is what you want. |
| `XTB logged in (wss://ws.xtb.com/demo, demo=True)` | Same, for XTB. |
| `warmup sma_cross/AAPL: 93 bars` | Recent history was fetched and fed to the strategy, which is ready now. |
| `warmup sma_cross/AAPL: only 7 of 31 bars available; strategy starts cold` | Not enough history (typically right after the open or after a weekend). The strategy will be silent until it has seen enough live bars. |
| `warmup sma_cross/AAPL failed: ...` | History could not be fetched at all; the program carries on, starting cold. |
| `alpaca data: feed=iex symbols=['AAPL']` | Subscribed to live prices. |
| `engine running: symbols=['AAPL'] strategies=['sma_cross'] execute=False qty=1.0` | Everything is up. `execute=False` means signal-only. |
| `EXECUTE mode: broker=paper paper=True` | You passed `--execute`; orders will be sent to the named broker. |

## While running

| Line | Meaning |
|---|---|
| `SIGNAL sma_cross AAPL long strength=1.00 fast=184.12 slow=183.90` | The strategy changed its mind. `long` means "be in", `flat` means "be out". The rest is the reason. |
| `no-op: AAPL already at 1.00` | A signal arrived but you already hold the target amount. Nothing sent. |
| `RISK REJECT buy 1.0 AAPL: notional 2051 > 2000.0` | A signal wanted an order but a risk limit said no. The text after the colon is the rule that fired. See [Trust and safety](trust-and-safety.md). |
| `ORDER paper buy 1.0000 AAPL -> filled (1f3a9c2e)` | An order was sent. The word after `->` is the broker's answer: `filled`, `accepted`, `new`, or `rejected`. |
| `ORDER UPDATE AAPL buy filled filled=1.0000 @ 184.2` | The broker reported progress on an order (Alpaca pushes these; XTB polls every two seconds). |
| `xtb order 1234: ...` | A message from XTB about an order, printed as received. |
| `broker state refresh failed: ...` | Positions or account balance could not be read this time. The last known values are used. |
| `error handling Tick(...)` followed by a traceback | One price update caused an error inside the program. It is skipped and the program keeps running. |

## Stopping

| Line | Meaning |
|---|---|
| `engine stopped` | Clean exit after `Ctrl-C`, or the data stream ended. |
| `data stream failed; stopping engine` followed by a traceback | The connection to the provider broke. The program stops; restart it. |
| `order update stream failed; continuing without push updates` | The broker's order-update feed broke. Trading continues; positions are still re-read before every order. |
| `KILL SWITCH: halting risk, cancelling open orders, stopping` | Only appears if a program embedding the engine triggered the kill switch; the command-line program never does. |

## Levels

`INFO` is normal operation. `WARNING` is something you should read but that did not stop the program (risk rejects, warm-up shortfalls, failed refreshes). `ERROR` comes with a traceback and usually means the program is stopping or skipped something. `-v` adds `DEBUG` lines from the program itself; libraries stay quiet, so `-v` is safe to use even with real keys.
