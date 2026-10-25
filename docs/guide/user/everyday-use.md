# Everyday use

This page covers the handful of things you do with the program day to day: starting it with the right stocks and strategy, stopping it, restarting it, and choosing where orders go.

## Starting a session

Every run needs a data source and at least one stock symbol. Everything else has a default.

```bash
# watch two stocks with the default strategy (sma_cross on 1-minute bars)
tradesignals --data alpaca --symbols AAPL,MSFT

# the other strategy, on its default 5-minute bars
tradesignals --data alpaca --symbols NVDA --strategy rsi_reversion

# the default strategy on hourly bars
tradesignals --data alpaca --symbols AAPL --strategy sma_cross --timeframe 1h
```

- Symbols are comma-separated and case does not matter; `aapl` becomes `AAPL`.
- `--timeframe` changes the bar size the strategy looks at: `1m`, `5m`, `15m`, `1h` or `1d`. Bars are aligned to the clock in UTC, so an hourly bar starts on the hour UTC, not at the New York open.
- At startup the program fetches recent history so the strategy is ready at once. Pass `--no-warmup` to skip that; the strategy then stays silent until it has seen enough live bars (about 31 minutes for `sma_cross` on 1-minute bars).

## Choosing where orders go

Without `--execute` the program never sends an order, whatever broker you name. With `--execute` it needs a `--broker`:

| `--broker` | What it is | Starts with |
|---|---|---|
| `paper` | A pretend account inside the program. Fills instantly at the current price, no fees, forgets everything at exit. | 100,000 USD |
| `alpaca` | Your Alpaca account. Paper or live depends on the keys and the `ALPACA_PAPER` setting. | whatever the account holds |
| `xtb` | Your XTB account. Demo by default (`XTB_DEMO=true`). | whatever the account holds |

Executing against a live account is refused unless you also pass `--allow-live`. See [Trust and safety](trust-and-safety.md).

## Position size

`--qty` is the number of shares the program aims to hold when a strategy says LONG (default 1). It is a *target*, not an order size: if you already hold 1 share and the target is 1, nothing is sent (you will see a `no-op` line). The `rsi_reversion` strategy scales the target between 50% and 100% of `--qty` depending on how strong its signal is, which can produce fractional share quantities such as 0.75; keep `--qty` to whole numbers if your broker does not support fractional shares.

## Stopping

Press `Ctrl-C` (or send the process a termination signal). The program stops cleanly: it closes its connections and exits. It does **not** cancel open orders and it does **not** sell what you hold. On the pretend paper broker that does not matter, because the pretend account disappears with the program. On a broker account, whatever position the last signal created is still there; close it in the broker's own app if you do not want to keep it.

## Restarting

Nothing is remembered between runs. On start the program re-reads your positions from the broker (if you named one) and re-fetches history for the strategy. Two consequences:

- `sma_cross` will not re-announce the position you are already in. It waits for the *next* crossover. If you hold shares from a previous run and the next signal is FLAT, it sells them; if the next signal is LONG and you already hold the target amount, it does nothing.
- `rsi_reversion` can believe it is "in" a trade from the warm-up history even if you hold nothing, and then its next LONG signal does not fire until it has first said FLAT. If that happens, restart with `--no-warmup`.

## Platforms

macOS and Linux. On Windows the program cannot install its `Ctrl-C` handling and will not start; use WSL.
