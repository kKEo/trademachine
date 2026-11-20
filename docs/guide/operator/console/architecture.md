# Architecture of the console

This chapter shows how the console is attached to the engine without the engine knowing about it: an in-process event bus, a SQLite store fed from that bus, a service layer that is the only thing the web pages talk to, and an HTTP server running on the engine's own event loop.

```
Provider ──► SignalEngine ──► Broker             order path unchanged, risk first
                 │   ▲
        publishes│   │control methods (halt, flatten, submit_manual, ...)
                 ▼   │
             EventBus ──► StoreWriter ──► SQLite
                 │                           ▲ reads
                 └────► ConsoleService ◄─────┘
                             │
                     FastAPI app: pages, JSON API, server-sent events
                             ▲                    ▲
                          browser           terminal UI
```

## Modules

| Path | Owns |
|---|---|
| `tradesignals/events.py` | `EventBus`, `Subscription` and the event dataclasses (`SignalEvent`, `SignalOutcome`, `OrderEvent`, `BarEvent`, `QuoteEvent`, `SnapshotEvent`, `StatusEvent`). Standard library only. |
| `tradesignals/store.py` | `Store` (schema, queries, pruning; synchronous and thread-safe) and `StoreWriter` (drains the bus into the store from a worker thread). |
| `tradesignals/analytics.py` | Pure functions: indicator series, resampling, trend state, round trips, trade statistics, forward returns, signal statistics, execution quality. |
| `tradesignals/console/service.py` | `ConsoleService`: every query and every action the pages need; live notifications; the log buffer. |
| `tradesignals/console/app.py`, `api.py`, `views.py`, `sse.py` | FastAPI factory and number formatting, JSON routes and actions, HTML routes and partials, the event stream. |
| `tradesignals/console/security.py` | `ConsoleGuard`: host, origin and token checks, security headers. |
| `tradesignals/console/server.py` | `ConsoleServer`: uvicorn on the engine's loop, bound to `127.0.0.1`. |
| `tradesignals/console/templates/`, `static/` | Jinja pages and partials; one stylesheet, two scripts, and vendored copies of htmx and TradingView Lightweight Charts (see `static/vendor/NOTICE.md`). No build step. |
| `tradesignals/tui.py` | Textual application; an HTTP client of the JSON API. |

## Rules

These extend the architecture rules of the engine and are just as binding.

1. **The engine does not import the console, the store or FastAPI.** It imports `events.py` only and publishes to an optional `EventBus`. Without a bus it behaves as before (`tests/test_events_store.py::test_engine_without_bus_is_unchanged_and_history_is_bounded`).
2. **Publishing never blocks and never raises.** Each subscriber has a bounded queue; when it is full the oldest event is dropped and counted (`tests/test_events_store.py::test_bus_drops_oldest_for_a_slow_subscriber_and_never_blocks`). A slow browser tab or a full disk cannot delay an order.
3. **Disk writes happen off the event loop.** `StoreWriter` writes batches through `asyncio.to_thread`; a failing database is logged and counted, and trading continues (`tests/test_events_store.py::test_store_failure_never_stops_trading`).
4. **The console never touches a broker or a provider module.** Reads come from the store and from the engine's public state; anything that changes state calls a `SignalEngine` method. `engine.py` stays the only place that submits orders, and every order still passes `RiskManager.check()`.
5. **The console is a guest.** If its server cannot start (port in use) or a page raises, the failure is logged and the engine keeps running (`tests/test_console.py::test_server_start_failure_does_not_raise`, `tests/test_system.py::test_a_failing_page_returns_500_and_the_engine_keeps_its_state`).
6. **History comes from the database, liveness from the bus.** Pages render from SQLite, so they survive a restart; live updates are small notifications that make the browser re-fetch a fragment.

## What the engine publishes

| Event | When | Persisted |
|---|---|---|
| `SignalEvent(signal_id, signal, timeframe)` | every signal, before any execution | `signals` |
| `SignalOutcome(signal_id, kind, detail, client_order_id)` | in execute mode: `order`, `no_op`, `risk_reject` or `submit_failed`; also for flatten and manual orders (then `signal_id` is None) | `signal_outcomes` |
| `OrderEvent(order, origin, signal_id, update)` | when a submit returns (`origin` is `<strategy>:<kind>`, `flatten` or `manual`) and for every broker update (`update=True`) | `orders`, `order_updates` |
| `BarEvent(bar)` | each completed bar, once, whatever the number of strategies | `bars` |
| `QuoteEvent(tick)` | at most once per symbol per `quote_interval` (1 s) | no |
| `SnapshotEvent(account, positions)` | at start, every `snapshot_interval` (10 s), after terminal order updates, at stop | `equity_snapshots` |
| `StatusEvent(state, halted, halt_reason)` | with every snapshot and on halt, resume, strategy and limit changes | no |

A signal and the order it caused are linked twice: the outcome carries the order's `client_order_id`, and the order row carries the `signal_id`. A broker's push update can overtake the reply to the engine's own submit; the store keeps the terminal state and the origin whichever arrives first (`tests/test_events_store.py::test_store_push_update_before_submit_reply_keeps_terminal_state_and_origin`).

## Live updates

The browser opens one `EventSource` on `/events`. `ConsoleService` sends named notifications: `quote` and `bar` carry data; `signal`, `order` and `snapshot` are sent **after** the rows are in the database (the writer calls the service back), so the fragment the browser then fetches always contains the new row (`tests/test_console.py::test_sse_delivers_a_signal_after_it_is_stored`); `status` and `audit` follow state changes; `bye` ends the stream at shutdown. The page script turns each notification into a `live-<name>` DOM event, and HTMX attributes such as `hx-trigger="live-signal from:body"` re-fetch the affected partial. Charts update the last candle from quotes and reload on new bars.

## Bars for charts

`ConsoleService.bars()` serves a chart from three sources, in this order: bars the engine produced live (stored), history fetched on demand through `MarketDataProvider.get_bars()` (once per bar period per symbol and timeframe, and only once for timeframes the engine keeps current itself), and, for a timeframe coarser than the strategy's, a roll-up of the finer live bars after the last stored coarse bar (`tests/test_markets.py::test_coarser_chart_is_rolled_up_from_live_finer_bars`). History goes through the adapter, so the XTB command throttle applies. If the provider fails, the chart shows what the database has and a note (`tests/test_markets.py::test_history_failure_degrades_to_a_note`).

Indicator lines are computed with the same incremental `SMA`, `EMA` and `RSI` classes the strategies use (`tests/test_markets.py::test_indicator_series_equal_the_incremental_indicators`).

## Startup and shutdown with `--console`

`_run()` in `tradesignals/cli.py` builds the bus and the engine, opens the store (pruning old rows), starts a run row and the `StoreWriter`, then `_start_console()` creates the `ConsoleService`, the FastAPI app and the `ConsoleServer` and starts it as a task. The engine runs until stopped. On the way out the service says `bye` to browsers, the server stops (two seconds of grace), the writer flushes what is queued, the run row gets its end time and the database is closed.
