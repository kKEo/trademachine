# FAQ and troubleshooting

This page is where to look when something is not working. Each entry starts with what you see, then the likely cause, then what to do.

## It will not start

**`RuntimeError: ALPACA_API_KEY / ALPACA_SECRET_KEY not set`.** The keys were not found. Check that `.env` is in the project folder (next to `pyproject.toml`), that the two lines have no spaces around `=`, and that you installed with `pip install -e ".[alpaca,dev]"` as in the [Quick start](quick-start.md). If you installed differently, the program looks for `.env` relative to where it is installed, not where you run it; in that case export the two variables in your shell instead.

**`ValueError: ALPACA_PAPER='ture' is not a boolean`.** A yes/no setting has a value the program does not understand. Use `true` or `false`.

**`ModuleNotFoundError: No module named 'alpaca'`.** The Alpaca library is not installed. Run `pip install -e ".[alpaca,dev]"` again inside the virtual environment.

**`refusing --execute: broker 'alpaca' is configured for a LIVE account`.** Your settings point at a live account. Either set `ALPACA_PAPER=true` (recommended) or, if you really mean it, add `--allow-live`.

**`command not found: tradesignals`.** The virtual environment is not active. Run `source .venv/bin/activate` in the project folder.

**`python -m tradesignals` says there is no module.** You are not in the virtual environment, or the package is not installed. Both `tradesignals` and `python -m tradesignals` work after the quick-start install.

**On Windows it stops immediately with an error about signals.** Windows is not supported; use WSL.

## It starts but prints nothing

**Nothing after `engine running`.** Usually the market is closed (US regular hours are 9:30 to 16:00 New York time) or the symbol is wrong. With XTB a wrong symbol suffix produces silence rather than an error: check the operator guide's accounts page.

**`warmup ... only 7 of 31 bars available; strategy starts cold`.** It is early in the session and there is not enough history yet. The strategy will speak once it has seen enough live bars (31 minutes on 1-minute bars). Not an error.

**`warmup ... failed`.** History could not be fetched. On Alpaca's free plan that can happen for the most recent minutes of SIP data; the program carries on cold. If it happens every time, check your keys and `ALPACA_FEED=iex`.

**No signal for an hour.** Normal. `sma_cross` only speaks when the averages cross. Try a smaller bar size to see more action, with the understanding that more signals is not better.

## It prints things I did not expect

**`RISK REJECT ... notional 2051 > 2000.0`.** The order would be worth more than the limit. Lower `--qty` or raise `RISK_MAX_ORDER_NOTIONAL` in `.env`. The same rule can block a *sale* if the shares have gone up; see [Trust and safety](trust-and-safety.md).

**`RISK REJECT ... stale quote (23s)`.** The last price the program saw is older than 15 seconds. Thin stock or a stalling feed. Nothing to do unless it persists; then restart.

**`RISK REJECT ... halted: daily loss ...`.** The loss limit fired. All further orders are blocked for this run, including exits. Close positions in the broker app, then restart (which resets the counter).

**`no-op: AAPL already at 1.00`.** The signal wanted what you already hold. Nothing was sent.

**Fractional quantities such as `0.75`.** `rsi_reversion` scales the target by signal strength. See [Strategies explained](strategies.md).

**`data stream failed; stopping engine`.** The connection dropped. Restart the program. Positions are untouched.

## After stopping

**I pressed Ctrl-C and I still hold shares.** Expected: stopping cancels and sells nothing. Close the position in the broker's app.

**Where are my signals saved?** Nowhere. Redirect the output to a file next time (`2> session.log` at the end of the command).

**Why do I need keys for signals only?** Alpaca's price stream requires authentication even for the free feed.

## XTB-specific

**Login fails or the connection is refused.** XTB may have discontinued its public API (reports say March 2025). Check with your XTB account whether API access is still available to you. See [Use cases and limits](use-cases.md).

**Prices arrive but nothing trades, or a SELL opened a short.** Check `XTB_SYMBOL_SUFFIX`: `.US` is CFDs, `.US_9` is usually real stocks. The operator guide explains how to verify.
