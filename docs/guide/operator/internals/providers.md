# Provider adapters

This chapter describes the three adapters side by side: the in-memory reference pair, Alpaca and XTB. Read the comparison table first; the rest is per-adapter detail and the places where behaviour differs from what the engine would ideally like.

## Comparison

| Aspect | `PaperBroker` / `ReplayProvider` | Alpaca | XTB |
|---|---|---|---|
| Data events | whatever the replay list holds | quote ticks **and** 1-minute bars (`pushes_bars = True`) | quote ticks only |
| `stream()` ends | when the list is exhausted (engine stops) | never | when the socket closes |
| `get_bars()` | filters the replay history by timeframe, `start` and inclusive `end` | REST, with `feed` matching the stream | `getChartLastRequest`, `end` filtered client-side |
| `paper` attribute | `True` | `ALPACA_PAPER` | `XTB_DEMO` |
| `submit_order()` returns | `FILLED` at once (or `REJECTED` for a non-marketable limit) | usually `NEW`/`ACCEPTED`, fills asynchronously | `FILLED`, `ACCEPTED` (pending limit) or `REJECTED` after one status poll |
| Fill price | last mark (mid), no spread, slippage or fees | broker's `filled_avg_price` | the price sent with the request (xAPI reports no fill price) |
| Limit orders | fill at the mark if marketable, else `REJECTED` | rest on the book | placed; reported `ACCEPTED` and never progressed by polling |
| Partial fills | never | `PARTIAL` possible | never |
| SELL with no position | opens a short | broker rules (shortable, margin) | fresh SELL: a CFD short with `.US`, rejected for real stocks |
| Positions | one signed position per symbol | broker-netted | one record per trade, aggregated per symbol on read; a close acts on the first trade only |
| `cancel_all()` | no-op | every open order on the account | every pending order on the account |
| `order_updates()` | each submitted order once | every trade-update event | orders still pending at submit, once, when they turn terminal |
| Account | equity = cash + marks, buying power = cash, USD | broker values | `balance` as cash, `margin_free` as buying power, currency fallback `PLN` |
| Reconnect | n/a | alpaca-py's own loop (backoff, resubscribe) | none |

## PaperBroker and ReplayProvider (`tradesignals/providers/fake.py`)

These are the reference semantics: if a real adapter behaves differently from the engine's point of view, the adapter is probably wrong.

- `ReplayProvider(events, history=None, delay=0.0, pushes_bars=False)` yields events in the order given, sleeping `delay` seconds before each one. `subscribe()` only records the symbols; it does not filter. `get_bars()` honours timeframe, `start` and an inclusive `end`.
- `PaperBroker(cash=100_000.0)` (`tradesignals/providers/fake.py:83`) fills market orders instantly at the last price fed through `mark(symbol, price)`. The CLI feeds marks from every tick through the engine's `on_tick` callback (`tradesignals/cli.py:98-108`). Bars never update marks, so a bar-only replay cannot fill orders.
- Limit orders fill at the mark only if marketable (buy: mark ≤ limit; sell: mark ≥ limit, `tradesignals/providers/fake.py:104`); otherwise the order is returned `REJECTED` with nothing changed.
- Positions are netted per symbol (`tradesignals/providers/fake.py:111-128`): adding averages the cost basis, reducing or closing keeps it, flipping through zero restarts it at the fill price. A SELL with no position opens a short and credits the proceeds.
- `cancel_order()` and `cancel_all()` are no-ops. `get_order()` raises `KeyError` for an unknown id. Order ids are 8 hex characters.
- The paper broker is optimistic by design: no spread, slippage, fees, lot sizes or buying-power check (`buying_power` can go negative).

## Alpaca (`tradesignals/providers/alpaca.py`)

**Verified against alpaca-py source** where noted; the installed version in this repository's virtual environment is 0.44.

- Credentials: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` (required, `tradesignals/providers/alpaca.py:66-68`), `ALPACA_PAPER` parsed with `env_bool` (`tradesignals/providers/alpaca.py:69`). `ALPACA_FEED` selects `sip` only when the value is exactly `sip` (case-insensitive); anything else is IEX (`tradesignals/providers/alpaca.py:97`). The feed is used for both the stream and `get_bars()`.
- `AlpacaData.subscribe()` registers quote and bar handlers for all symbols and starts alpaca-py's private `_run_forever()` as a task (`tradesignals/providers/alpaca.py:133-135`). Quotes become `Tick(bid, ask)` with no `last`; bars become `Bar` at `Timeframe.M1`. Both land in one unbounded queue that `stream()` drains. Calling `subscribe()` twice starts a second stream task; the engine calls it once.
- `get_bars()` builds `TimeFrame(n, TimeFrameUnit[unit])` by attribute name (`tradesignals/providers/alpaca.py:150`). Looking the unit up by *value* fails because `TimeFrameUnit.Minute == "Min"` (verified against alpaca-py source); `tests/test_alpaca_mapping.py` guards this.
- `_to_order()` (`tradesignals/providers/alpaca.py:72`) maps side and status through `_enum_value()` (`tradesignals/providers/alpaca.py:56`), which reads the enum's `.value`. `str()` of an alpaca-py enum is `'OrderSide.BUY'`, not `'buy'` (verified in Python 3.11+), which is why the mapping must not use `str()`. Statuses not in `_STATUS` (done_for_day, replaced, pending_cancel, held, ...) map to `UNKNOWN`, which is not terminal.
- `submit_order()` sends a market or limit request with DAY or GTC time-in-force and the engine's `client_order_id`; fractional quantities are passed through. No `extended_hours`, no stop or bracket orders.
- `cancel_all()` calls `cancel_orders()`, which is account-wide (`tradesignals/providers/alpaca.py:223`).
- `get_positions()` tolerates `None` market value and unrealised P&L.
- Reconnect: alpaca-py's `_run_forever()` reconnects with backoff and resubscribes, but returns for good on an "insufficient subscription" error (verified against alpaca-py source). Nothing in this adapter watches the task: if it ends, `stream()` simply never yields again and the engine sits silently. `ALPACA_FEED=sip` on a free plan is the common way to hit this.
- `AlpacaBroker.connect()` logs `alpaca broker connected (paper=True|False)`.

## XTB (`tradesignals/providers/xtb.py`)

**From third-party copies of the xAPI spec** unless noted; see the [XTB xAPI notes](../appendix/xtb-protocol.md).

- Credentials: `XTB_USER_ID`, `XTB_PASSWORD` (required, `tradesignals/providers/xtb.py:80-82`), `XTB_DEMO` via `env_bool` (`tradesignals/providers/xtb.py:83`), `XTB_SYMBOL_SUFFIX=.US` (`tradesignals/providers/xtb.py:152`, `tradesignals/providers/xtb.py:220`).
- `XtbSession` is one per `(user, demo)` per process, cached in a class dict (`tradesignals/providers/xtb.py:61`), reference-counted across the data and broker adapters, and stores `demo`, which the broker exposes as `paper`. Hosts are hard-coded: `wss://ws.xtb.com/demo` or `/real`, plus `Stream` for the push socket (`tradesignals/providers/xtb.py:68`); message size cap `2**24` bytes.
- `command()` serialises every request through a lock and enforces at least 0.21 s between sends (`tradesignals/providers/xtb.py:120`), the protocol's 1-request-per-200-ms rule. It assumes the next message on the socket is the reply (no `customTag`), and has no receive timeout. A keep-alive task pings both sockets every 10 s (`tradesignals/providers/xtb.py:137-139`).
- `XtbData.subscribe()` sends one `getTickPrices` per symbol with `minArrivalTime=200` (`tradesignals/providers/xtb.py:162`). `stream()` yields only `tickPrices` messages at level 0 as `Tick(bid, ask)`; everything else is dropped. A wrong suffix produces no ticks and no error.
- `get_bars()` decodes `getChartLastRequest`: prices divided by `10**digits`, high/low/close added to open as deltas (verified against the project's own protocol notes and `tests/test_xtb_broker.py`).
- `XtbBroker.submit_order()` (`tradesignals/providers/xtb.py:234-298`): fetches the quote, lists open trades, and if an opposite-side trade exists sends a **CLOSE** (`type=2`) of the first one for `min(qty, its volume)`; otherwise an **OPEN** (`type=0`) with cmd 0/1 for market or 2/3 for limit. It then polls `tradeTransactionStatus` once. `ACCEPTED` on a market order means executed and is reported `FILLED` at the price sent; a limit order stays `ACCEPTED` and is polled every `poll_sec=2.0` seconds, but the status call never reflects its later fill (the roadmap's `getTrades` stream would).
- `cancel_order(id)` looks the pending order up in `getTrades(openedOnly=False)`, sends a **DELETE** (`type=4`) carrying the order's own cmd, symbol, volume and price (`tradesignals/providers/xtb.py:319-346`), and raises if the status comes back rejected or the id is not a pending order. `cancel_all()` does the same for every pending order (cmd 2 to 5) and logs failures instead of raising.
- `get_positions()` aggregates open trades per canonical symbol: signed quantity, volume-weighted `open_price`, summed `profit`; `market_value` is not provided. `get_account()` uses `getMarginLevel` with `balance` as cash and `margin_free` as buying power (`tradesignals/providers/xtb.py:386-387`).
- Robustness: no reconnect (ping failures are logged every 10 s forever), no receive timeout, replies matched by order, stream-socket sends not throttled, session cache shared across event loops, an order whose status maps to `UNKNOWN` is polled forever, one consumer per stream.

> **Warning.** XTB's public xAPI is reported by third-party client libraries to have been switched off on 14 March 2025 (unverified by this project; the official documentation host no longer resolves). Confirm with your account before relying on this adapter.
