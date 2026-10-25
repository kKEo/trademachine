# Use cases and limits

This page helps you decide whether tradesignals fits what you want to do. It is honest about the edges.

## Good fits

- **Watching a handful of US stocks for a simple rule.** Start it in the morning with two or three symbols and see when the moving averages cross. Free with an Alpaca paper account.
- **Paper-trading a crossover or mean-reversion rule.** The pretend paper broker gives you a feel for how often the rule trades and how the position changes, with zero risk.
- **Trying a demo broker end to end.** With Alpaca paper or an XTB demo account you see real order acknowledgements, real fills and real rejections without money at stake.
- **A starting point for your own rule.** If you can write a few lines of Python, a new strategy is one small class. The [Operator guide](../operator/) shows how.

## Poor fits

- **Real money.** The front page says prototype and means it. The [Trust and safety](trust-and-safety.md) page lists the reasons: no reconnect, halts that block exits, no awareness of orders in flight, no record of what happened.
- **Many symbols.** Alpaca's free data feed allows 30 symbols on one connection. The program does not enforce that; the feed simply refuses beyond it.
- **Backtesting.** There is no way to run a strategy over last month's prices yet. It is on the roadmap.
- **Unattended running.** Nothing restarts the program if the feed drops, and nothing tells your phone. If you leave it running, wrap it in a service that restarts it, and check on it.
- **Anything outside US stocks.** The symbol handling assumes US tickers, and the XTB side assumes the `.US` or `.US_9` suffixes.
- **Precise fills or costs.** Bars built from quotes have no volume, orders are always market orders, and the pretend paper broker ignores spread and fees.

## A note on XTB

Third-party client libraries report that XTB switched off its public API (the "xAPI" this program speaks) on 14 March 2025, and the official documentation site no longer resolves. This project could not confirm either way. Before you plan around XTB, log in to your XTB account and check whether API access is still offered to you; if it is not, use Alpaca.
