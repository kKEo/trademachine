# Glossary

Words used in this guide, in the order you are likely to meet them.

**Signal.** The strategy's decision for one stock: LONG (be in) or FLAT (be out). Printed as a `SIGNAL` line.

**LONG / FLAT.** LONG means hold the target number of shares. FLAT means hold none. The built-in strategies never go short (betting on a fall).

**Tick / quote.** One price update from the provider: the best price someone will pay (bid) and the best price someone will sell at (ask). The program uses the midpoint.

**Bar.** A summary of a time span: open (first price), high, low and close (last price). Strategies work on bars.

**Timeframe / bar size.** How long one bar lasts: `1m`, `5m`, `15m`, `1h` or `1d`. Bars are aligned to the clock in UTC.

**Warm-up.** Fetching recent history at startup and feeding it to the strategy so it is ready immediately.

**Data provider.** Where prices come from: Alpaca or XTB.

**Broker.** Where orders go and where positions live: the pretend paper broker inside the program, Alpaca, or XTB.

**Paper account / demo account.** A broker account with pretend money. Alpaca calls it paper, XTB calls it demo. The program targets these by default.

**Live account.** A broker account with real money. The program refuses to execute against one unless you pass `--allow-live`.

**Execute mode.** Started with `--execute`: signals become orders. Without it, nothing is ever sent.

**Market order.** An order to buy or sell now at whatever the current price is. The only order type the program sends.

**Position.** How many shares of a stock you hold. Negative means a short position.

**Target position.** What a signal wants you to hold: `--qty` shares times the signal strength for LONG, zero for FLAT. The program sends the difference between target and actual.

**Strength.** A number between 0 and 1 attached to a signal. `sma_cross` always says 1; `rsi_reversion` says between 0.5 and 1.

**Order value / notional.** Shares times price. One of the risk limits caps it.

**Risk limit.** One of five rules checked before every order: order value, position size, number of open positions, loss since start, and age of the last price.

**Halt.** The state after the loss limit fires or the kill switch is pulled: every order is blocked until restart, including exits.

**Kill switch.** A function inside the engine that halts risk, cancels open orders and stops. The command-line program does not expose it; `Ctrl-C` is a plain stop.

**Stale quote.** A last price older than the limit (15 seconds by default). Orders are blocked while the quote is stale.

**Symbol suffix (XTB).** The ending XTB adds to a ticker: `.US` for CFDs, `.US_9` usually for real stocks.

**CFD.** Contract for difference: a leveraged bet on a price that can be sold short. XTB's default instruments for US tickers.

**IEX / SIP.** Alpaca's two data feeds. IEX is free and covers one exchange; SIP is paid and covers all of them.
