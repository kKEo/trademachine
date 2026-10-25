# Accounts, feeds and symbols

This chapter covers what you need from each provider before a run: the right kind of account, the data feed, and how symbols are spelled on the provider side.

## Alpaca

**Account.** Create the API key and secret in the *paper trading* dashboard. Paper and live use the same API with different base URLs; which one the broker adapter targets is decided by `ALPACA_PAPER` (see [Configuration reference](configuration.md)). The data stream authenticates with the same keys, which is why signal-only runs need them too. The adapter logs `alpaca broker connected (paper=True)` so you can confirm in the first lines.

**Feeds.** `ALPACA_FEED=iex` (default) is the free feed: one exchange's quotes and trades, up to 30 symbols on one websocket connection. `ALPACA_FEED=sip` is the consolidated feed and needs a paid data plan. The adapter passes the feed to both the live stream and the warm-up history request (`tradesignals/providers/alpaca.py:97`, `tradesignals/providers/alpaca.py:150`).

> **Warning.** Subscribing to SIP without the plan makes alpaca-py's stream loop give up after an "insufficient subscription" error (verified against alpaca-py source). Nothing in this adapter notices; the engine stays up and simply receives nothing. If you see `engine running` and then silence during market hours with `ALPACA_FEED=sip`, that is why.

**Hours.** IEX quotes flow during regular hours (9:30 to 16:00 New York time); outside them expect few or no events. The engine does not know market hours.

**Symbols.** Passed through unchanged, upper-cased by the CLI. No `SymbolMapper` is involved. A symbol Alpaca does not know produces no events and no error.

**Limits worth knowing.** Market orders only; fractional quantities are forwarded as given (Alpaca accepts fractional market orders in DAY time-in-force for eligible symbols); `cancel_all()` is account-wide.

## XTB

**Account.** A demo account from xtb.com gives you a user id and password. `XTB_DEMO=true` (default) selects `wss://ws.xtb.com/demo`; `false` selects `/real`. The session logs `XTB logged in (wss://ws.xtb.com/demo, demo=True)`.

> **Warning.** Third-party client libraries report that XTB discontinued public xAPI access on 14 March 2025, and the official documentation host `developers.xstore.pro` no longer resolves. This project could not verify the current state. Confirm with your account before planning around XTB; the adapter is tested offline against a recorded protocol, not against a live endpoint.

**Symbol suffix.** XTB names instruments with a suffix: `AAPL.US` is a CFD on Apple, `AAPL.US_9` is usually the real stock (category `STC` in `getAllSymbols`). `XTB_SYMBOL_SUFFIX` is appended to every canonical symbol on the way out and stripped on the way in (`tradesignals/providers/xtb.py:152`, `tradesignals/providers/xtb.py:220`). The default is `.US`.

The startup check against `getAllSymbols` from the roadmap is not implemented: a wrong suffix produces no ticks, no bars and no error. To verify your suffix, log in to xStation, open the instrument details, or query `getAllSymbols` once with a small script and look for your ticker.

> **Warning.** With the `.US` (CFD) suffix, a SELL that does not close an existing position opens a leveraged short. The built-in strategies only emit LONG and FLAT, so they never send such a SELL, but a custom strategy emitting `SHORT` would. With `.US_9` XTB rejects it instead.

**Currency.** `getMarginLevel` reports equity and balance in the account currency; the adapter falls back to `PLN` when the field is missing. The risk limit `RISK_MAX_DAILY_LOSS` is compared against that currency, while `RISK_MAX_ORDER_NOTIONAL` is compared against the instrument's quote currency (USD for US tickers). Set both with that in mind.

**Rate limits.** The main socket allows one request per 200 ms; the session enforces 0.21 s between requests. Subscriptions on the stream socket are sent back to back, which has not caused problems with a handful of symbols.

## Bar alignment and market hours

Bars are aligned to UTC clock multiples regardless of provider (see [Data flow](../internals/data-flow.md)). The engine has no notion of sessions: the last bar of a day is emitted by the first event of the next session, and a strategy started before the open warms up on whatever history the window covers, which for a 1-minute strategy is yesterday's last 93 minutes only if the session is less than 93 minutes old.
