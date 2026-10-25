# XTB xAPI notes

The protocol facts the XTB adapter relies on, in one place. Unless marked otherwise they come from third-party copies of the xAPI specification (the official host `developers.xstore.pro` no longer resolves) and from the project's own `CLAUDE.md` reminders; the adapter is exercised offline by `tests/test_xtb_broker.py`.

> **Warning.** Third-party client libraries report the public xAPI switched off on 14 March 2025. Verify with your account before relying on anything below.

## Endpoints and sessions

| Item | Value | In code |
|---|---|---|
| Main socket (request/response) | `wss://ws.xtb.com/demo` or `wss://ws.xtb.com/real` | `tradesignals/providers/xtb.py:68` |
| Stream socket (push) | same URL plus `Stream` | `tradesignals/providers/xtb.py:68` |
| Login | `login {userId, password}` → `streamSessionId` | `XtbSession.connect()` |
| Keep-alive | `ping` on both sockets every 10 s | `tradesignals/providers/xtb.py:137-139` |
| Rate limit | at most one request per 200 ms on the main socket; the session waits 0.21 s between sends | `tradesignals/providers/xtb.py:120` |
| Message size cap | `max_size=2**24` | `tradesignals/providers/xtb.py:95`, `tradesignals/providers/xtb.py:98` |

Replies are read in order; the session does not use `customTag`.

## Market data

| Command | Use | Notes |
|---|---|---|
| `getTickPrices` (stream) | `{symbol, minArrivalTime: 200, maxLevel: 0}` per symbol | only `tickPrices` messages with `level == 0` are turned into ticks |
| `getChartLastRequest` | `{info: {period, start, symbol}}` | `period` in minutes: 1, 5, 15, 60, 1440; `start` in epoch ms |
| `getSymbol` | current `bid`/`ask` before an order | |

**Price decoding** (verified against the project's protocol notes and tested): `scale = 10 ** digits`; `open = r.open / scale`; `high = open + r.high / scale`, likewise `low` and `close` (they are deltas from open); `volume = r.vol`; `start = r.ctm` (epoch ms, UTC).

## Trading

`tradeTransaction {tradeTransInfo: {cmd, type, order, symbol, volume, price, sl, tp, offset, expiration, customComment}}` returns `{order}`; `tradeTransactionStatus {order}` returns `{ask, bid, customComment, message, order, requestStatus}`.

| `cmd` | Meaning | | `type` | Meaning |
|---|---|---|---|---|
| 0 | BUY (market) | | 0 | OPEN |
| 1 | SELL (market) | | 1 | PENDING (streaming only) |
| 2 | BUY_LIMIT | | 2 | CLOSE |
| 3 | SELL_LIMIT | | 3 | MODIFY |
| 4 | BUY_STOP | | 4 | DELETE |
| 5 | SELL_STOP | | | |

| `requestStatus` | Mapped to |
|---|---|
| 0 ERROR | `REJECTED` |
| 1 PENDING | `NEW` |
| 3 ACCEPTED | `FILLED` for market cmd 0/1 (the request executed), `ACCEPTED` for pending cmds |
| 4 REJECTED | `REJECTED` |

How the adapter uses them (`XtbBroker`, verified in code):

- **Open**: `type=0`, `cmd` 0/1 with `price` = ask/bid, or `cmd` 2/3 with the limit price. `sl`, `tp`, `offset`, `expiration` are 0. `customComment` is the first 30 characters of the engine's `client_order_id`.
- **Close** (real stocks cannot be shorted): when an opposite-side open trade exists, `type=2`, `cmd` = the trade's cmd, `order` = the trade's `position`, `volume = min(requested, trade volume)`, `price` = bid for a long, ask for a short.
- **Delete** a pending order: `type=4`, `order` = the pending order's `order` number, with the order's own `cmd`, `symbol`, `volume` and `open_price` copied from its `getTrades` record. The adapter then checks `tradeTransactionStatus` and raises on `REJECTED`.
- The status reply has no fill price; market fills are reported at the price sent.

## Positions and account

| Command | Use |
|---|---|
| `getTrades {openedOnly: true}` | open trades; cmd 0/1 are positions, aggregated per symbol (signed `volume`, weighted `open_price`, summed `profit`, `position` kept as `provider_ref`) |
| `getTrades {openedOnly: false}` | includes pending orders (cmd 2 to 5) and closed records; used by `cancel_order()`/`cancel_all()` |
| `getMarginLevel` | `equity`, `balance` (→ cash), `margin_free` (→ buying power), `currency` (fallback `PLN`) |

## Symbols

| Suffix | Instrument |
|---|---|
| `.US` | CFD on a US stock (default; can be shorted, leveraged) |
| `.US_9` | the real stock on most accounts (category `STC` in `getAllSymbols`) |

`getAllSymbols` is not called by the adapter; verify the suffix for your account by hand.
