# Pages, API and database

This chapter is the look-up page for the console: every page, every endpoint, the actions and their answers, the database tables, retention, and the terminal UI.

## Pages

| Path | Shows | Live updates on |
|---|---|---|
| `/` | account figures, equity curve, positions, latest signals and orders, halt banner | snapshot, signal, order |
| `/portfolio` | figures, positions with share of portfolio and a close button, order ticket, orders waiting to fill, trade history (round trips, realised P&L by symbol and strategy) | snapshot, order |
| `/markets` | watchlist: sparkline, last price, change, trend (10-bar against 30-bar average), 14-bar RSI zone, holding, watching strategies | bar, snapshot |
| `/markets/{symbol}?tf=` | candlesticks on `1m`, `5m`, `15m`, `1h` or `1d`; averages, EMA, RSI pane; signal and fill markers; the symbol's state and latest signals | quote (last candle), bar, signal, order |
| `/signals` | filters (strategy, symbol, signal, result, dates, session), summary, per-strategy comparison, the signals with the move after 1, 5 and 15 bars | signal |
| `/signals/{id}` | one signal: reason, outcome, the order and its execution, the trade it opened or closed, the chart around it | none |
| `/signals.csv` | the filtered signals as CSV (same query string as `/signals`) | none |
| `/orders` | order history | order |
| `/strategies` | running strategies with settings and state; forms to start one | status |
| `/risk` | limits with usage, halt banner, controls, limit form, audit trail | status, snapshot, audit |
| `/system` | health, log tail (all or warnings and errors), settings in effect, sessions, audit trail | timed refresh |

Times are UTC. "Correct after N bars" is the share of signals followed by a move in their favour N bars later: up after a long, down after a flat or a short. The reference price is the close of the bar the signal fired on.

## JSON API (read)

All answer `application/json`; none changes state.

| Endpoint | Returns |
|---|---|
| `GET /api/status` | mode, engine state, feed, halt, symbols, strategies with id and paused flag |
| `GET /api/overview` | status, account figures, positions, quotes |
| `GET /api/quotes`, `GET /api/positions` | the tape; enriched positions |
| `GET /api/signals?limit&offset&symbol&strategy&kind&since&until` | signals, newest first, with their latest outcome |
| `GET /api/signals/report?...` | the Signals page as data: signals with forward returns, per-strategy statistics |
| `GET /api/signals/{id}` | one signal with outcomes, order, order updates, execution quality and trade |
| `GET /api/orders?limit&offset&symbol&status&since` | orders, newest first |
| `GET /api/trades` | round trips and statistics |
| `GET /api/equity?since` | equity points for the chart (thinned to at most 2000) |
| `GET /api/markets` | the watchlist |
| `GET /api/bars/{symbol}?tf&limit&before&sma&ema&rsi` | bars, indicator series, markers; `before` pages backwards |
| `GET /api/strategies` | running strategies and the catalogue of registered ones |
| `GET /api/risk`, `GET /api/audit` | limit usage, capabilities, open orders; audit entries |
| `GET /api/system`, `GET /api/logs?level` | health, settings, sessions; log tail |
| `GET /events` | the server-sent event stream |

## Actions (write)

All are `POST` with a JSON body, need the token, and answer `{"ok": true, "message": ...}` or `{"ok": false, "error": ...}`.

| Endpoint | Body | Notes |
|---|---|---|
| `/unlock`, `/lock` | `{"token"}`; none | set or clear the browser cookie |
| `/api/actions/halt` | `{"reason"}` (optional) | allowed on a live account |
| `/api/actions/resume` | none | 409 when not halted |
| `/api/actions/flatten` | `{"symbol"}` or `{"confirm": "FLATTEN"}` | one position, or all |
| `/api/actions/cancel` | `{"order_id"}` | only orders the engine still tracks as open |
| `/api/actions/kill-switch` | `{"confirm": "STOP"}` | allowed on a live account; the engine and the console stop |
| `/api/actions/order/preview` | `{"symbol","side","qty","type","limit_price"}` | risk's verdict; changes nothing and is not audited |
| `/api/actions/order` | same | 409 with the reason when risk or the broker refuses |
| `/api/actions/limits` | any of `max_daily_loss`, `max_open_positions`, `max_position_qty`, `max_order_notional`, `max_quote_age_sec` | bounds below; unchanged fields are ignored |
| `/api/actions/strategies/add` | `{"strategy","symbols","timeframe", ...parameters}` | symbols must be among the session's |
| `/api/actions/strategies/pause`, `/resume`, `/remove` | `{"id"}` | ids look like `sma_cross-1` |

Status codes: 400 invalid input or missing confirmation, 401 token missing or wrong, 403 read-only console, foreign origin or live account, 409 refused in the current state, 502 the action raised.

Limit bounds accepted by the console: loss 1 to 1,000,000; open positions 1 to 100 (whole number); position size 0.0001 to 1,000,000 shares; order value 1 to 1,000,000; quote age 1 to 3600 seconds. Changes last until the engine restarts; `.env` sets the starting values.

## Database

One SQLite file in WAL mode. Timestamps are UTC epoch seconds.

| Table | One row per | Key columns |
|---|---|---|
| `runs` | engine session | `id`, `started_at`, `stopped_at` (empty after a crash), feed, broker, paper, execute, symbols, strategies |
| `signals` | signal | `id`, `run_id`, `ts` (bar start), symbol, kind, strategy, strength, reason, timeframe |
| `signal_outcomes` | thing done with a signal (or with a manual order: `signal_id` empty) | `signal_id`, kind, detail, `client_order_id` |
| `orders` | order (key: source and id) | `client_order_id`, `signal_id`, origin, symbol, side, qty, status, `filled_qty`, `avg_fill_price`, `ts`, `updated_at` |
| `order_updates` | state change of an order | status, `filled_qty`, `avg_fill_price`, `ts` |
| `bars` | completed bar (key: symbol, timeframe, start) | OHLC, volume, source |
| `equity_snapshots` | account snapshot | equity, cash, buying power, currency, positions (JSON) |
| `audit_log` | console action | action, detail, result, `run_id` |

Retention: at each start `Store.prune()` deletes equity snapshots older than 90 days and intraday bars older than 365 days; daily bars, signals, orders and the audit log are kept (`tests/test_system.py::test_prune_drops_old_equity_points_and_intraday_bars_but_keeps_daily_bars`). A file written by an older version gains missing columns on open. To start afresh, stop the engine and delete the file together with its `-wal` and `-shm` companions.

Useful queries:

```sql
-- what happened to today's signals
SELECT datetime(s.ts,'unixepoch') AS t, s.symbol, s.kind, o.kind AS result, o.detail
FROM signals s LEFT JOIN signal_outcomes o ON o.signal_id = s.id
WHERE s.ts >= strftime('%s','now','start of day') ORDER BY s.ts;

-- fills with the strategy that caused them
SELECT datetime(o.ts,'unixepoch') AS t, o.symbol, o.side, o.filled_qty, o.avg_fill_price, o.origin
FROM orders o WHERE o.filled_qty > 0 ORDER BY o.ts;
```

## Terminal UI

`tradesignals-tui [--url http://127.0.0.1:8788] [--interval 2.0]` shows status, account figures, quotes, positions, latest signals and latest orders, refreshed every `--interval` seconds by polling the JSON API.

| Key | Does |
|---|---|
| `r` | refresh now |
| `h` | halt trading, or resume when halted; asks first |
| `k` | kill switch; asks for the word `STOP` |
| `q` | quit the view (the engine keeps running) |

Actions need `CONSOLE_TOKEN` in the environment or `.env` of the shell that starts the view; without it the view is read-only and says so. The token is never taken from the command line. When the console cannot be reached, the status line says so and the view keeps trying.
