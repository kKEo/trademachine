# Trust and safety

This page says what you can rely on and what you cannot. Read it before you connect the program to any broker account, including a paper one.

## What protects you

- **Signal-only by default.** Without `--execute` the program never sends an order. It can connect to a broker to show your positions, but it only reads.
- **Paper and demo by default.** `ALPACA_PAPER=true` and `XTB_DEMO=true` are the defaults, and the program understands only clear yes/no values for them (`true`, `false`, `1`, `0`, `yes`, `no`, `on`, `off`). A typo such as `ALPACA_PAPER=ture` stops the program with an error instead of quietly picking the live account. An empty value means the default.
- **Live needs an extra flag.** If the settings do point at a live account, `--execute` is refused with a clear message unless you also pass `--allow-live`. You cannot trade real money by accident, only on purpose.
- **Risk limits before every order.** Each order passes five checks, with these default limits (all changeable in `.env`): the order's value may not exceed 2,000 (in the stock's currency); the resulting position may not exceed 50 shares; you may not hold more than 5 different stocks; once the account has lost more than 200 since the program started, all further orders are blocked for the rest of the run; and an order is blocked if the last price the program saw is older than 15 seconds. A blocked order is printed as `RISK REJECT` with the rule that fired.
- **Your keys stay on your machine.** They are read from `.env`, sent only to the provider you chose, and never printed. The `-v` option prints extra detail from the program itself only; the networking libraries stay quiet, so passwords and secrets cannot leak into the log.
- **Tests.** Every behaviour on this page has an automated test; see the [Tester guide](../tester/).

## What does not protect you

- **A halt also blocks the exit.** Once the loss limit has fired (or the kill switch inside the engine was pulled), *every* order is blocked, including the one that would close your position. You then close it in the broker's app. The same is true of the order-value limit: it can block a sale if the shares are now worth more than the limit. Raise the limit in `.env` and restart, or close in the app.
- **"Loss since start" is not "loss today".** The loss limit counts from the moment the program first checked your account in this run. Restarting resets it.
- **Stopping does not flatten.** `Ctrl-C` cancels nothing and sells nothing. Whatever position the last signal created stays open at the broker.
- **No reconnect.** If the price feed drops, the program stops with an error message and leaves your positions as they are. It does not retry.
- **Positions are not tagged.** The program treats any position in a symbol as its own, including ones you opened by hand or that another program opened. If you run it on a stock you also trade yourself, it will "correct" your position towards its own target.
- **Slow fills are invisible.** On a real broker a market order can take a moment to fill. If a second signal arrives in that moment, the program sizes it from the positions it can see, not from the order still in flight, and can send a duplicate.
- **The pretend paper broker is optimistic.** It fills at the last quoted mid-price, with no spread, no slippage and no fees. Results on it are an upper bound.
- **XTB default is CFDs, not shares.** The default XTB symbol suffix (`.US`) selects contracts for difference, which can be sold short with leverage. If a custom strategy ever says SELL without an existing position, XTB opens a short. The built-in strategies never do that, but check the suffix setting before executing on XTB (see the operator guide's accounts page).

## Sensible habits

1. Use the pretend paper broker (`--broker paper`) until you have watched the strategy for a few days.
2. Keep `--qty` small and the order-value limit low; both are per order, so small numbers cost nothing.
3. After each session, look at the broker's own app and close anything you do not want to keep overnight.
4. Never type live keys into `.env` while the project says "paper only" on its front page.
