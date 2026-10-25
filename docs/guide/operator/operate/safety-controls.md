# Safety controls and their limits

This chapter lists each control that stands between a signal and real money, what it actually does in code, and the point where it stops protecting you. Every row is verified in code and, where marked, covered by a test.

| Control | What it does | Where it stops | Evidence |
|---|---|---|---|
| Signal-only default | `execute` is on only with `--execute` **and** a broker (`tradesignals/engine.py:64`). | A broker named without `--execute` still connects and needs credentials. | `tests/test_engine_robustness.py::test_execute_without_broker_is_signal_only` |
| Paper/demo defaults | `ALPACA_PAPER` and `XTB_DEMO` default to `true` and are parsed strictly; unknown spellings raise instead of selecting live (`tradesignals/config.py:15`). | A deliberate `false` selects live. There is no check of the account type with the broker. | `tests/test_config.py`, `tests/test_alpaca_mapping.py::test_creds_paper_flag_is_strict` |
| Live guard | `--execute` is refused when `broker.paper` is false unless `--allow-live` is given; `Broker.paper` defaults to `False` so a forgotten attribute fails closed (`tradesignals/cli.py:83-95`, `tradesignals/interfaces.py:66`). | Only the CLI enforces it; a program embedding the engine can bypass it. | `tests/test_cli.py::test_live_guard_refuses_execute_on_live_account_without_allow_live`, `tests/test_cli.py::test_live_guard_allows_live_with_flag_and_warns`, `tests/test_cli.py::test_base_broker_defaults_to_not_paper` |
| Order notional cap | Rejects `qty × price > RISK_MAX_ORDER_NOTIONAL` (`tradesignals/risk.py:57`). | Also blocks a *sale* once the shares are worth more than the cap. Measured in the quote currency. | `tests/test_risk.py::test_rejects_oversized_notional_using_limit_price_when_given` |
| Position cap | Rejects a resulting absolute position above `RISK_MAX_POSITION_QTY` (`tradesignals/risk.py:64`). | Counts positions you opened by hand too. | `tests/test_risk.py::test_rejects_position_limit_on_both_sides` |
| Open-positions cap | Rejects a new symbol when the account already holds `RISK_MAX_OPEN_POSITIONS` positions (`tradesignals/risk.py:66`). | Counts every position on the account, not only the engine's. | `tests/test_risk.py::test_rejects_new_symbol_when_open_position_cap_reached` |
| Loss halt | Halts once equity is `RISK_MAX_DAILY_LOSS` below the equity at the first check (`tradesignals/risk.py:69-72`). | Baseline is "since this process started", never reset by date; skipped while the account is unknown; the halt blocks exits too. | `tests/test_risk.py::test_daily_loss_halts_and_latches_until_restart`, `::test_halted_also_blocks_position_reducing_orders` |
| Stale-quote check | Rejects when the last tick is older than `RISK_MAX_QUOTE_AGE_SEC` (`tradesignals/risk.py:55`). | Blocks exits as well; wall-clock based. | `tests/test_risk.py::test_rejects_stale_quote` |
| Kill switch | Halts risk, cancels pending orders, stops; `stop()` runs even if cancel fails (`tradesignals/engine.py:119-128`). | Not wired to any signal or flag in the CLI; cancels nothing on paper, everything on the Alpaca account, pending orders only on XTB; never flattens. | `tests/test_engine_robustness.py::test_kill_switch_stops_even_if_cancel_all_fails` |
| Clean stop on `Ctrl-C` | `SIGINT`/`SIGTERM` set the stop event; tasks are cancelled and connections closed (`tradesignals/cli.py:135`, `tradesignals/engine.py:82-114`). | Cancels and flattens nothing. | `tests/test_engine_robustness.py::test_run_closes_broker_even_if_data_close_fails` |
| Stream failure handling | A failing data stream stops the engine with an ERROR line instead of hanging (`tradesignals/engine.py:156-171`). | It stops; it does not reconnect. Positions stay as they are. | `tests/test_engine_robustness.py::test_stream_failure_stops_engine_and_logs` |
| Credential hygiene | Keys come from the environment, are never logged by the adapters, and `-v` cannot make the websockets library log frames (`tradesignals/cli.py:76-80`). | A `DEBUG` root logger configured by an embedding program would. | `tests/test_cli.py::test_verbose_only_raises_tradesignals_loggers` |
| Duplicate-bar protection | Providers that push bars are never double-fed from ticks; late events are dropped (`tradesignals/engine.py:183`, `tradesignals/bars.py:47`). | Buckets before the first provider bar can be tick-built for an adapter that forgot `pushes_bars`. | `tests/test_engine_robustness.py::test_provider_that_declares_bars_never_builds_them_from_ticks`, `tests/test_bars.py` |

## What no control covers

- **Orders in flight.** Sizing and every risk rule look at filled positions only (`tradesignals/engine.py:223-235`, `tradesignals/risk.py:60-66`). A slow fill plus a quick second signal can produce a duplicate order.
- **Positions you did not open.** The engine corrects any position in a symbol it trades towards its own target.
- **Order rate.** No throttle, cooldown or hysteresis.
- **Reconnect.** Neither adapter reconnects after a failure; the engine stops (data) or continues blind (order updates).
- **Account type.** The paper flags are trusted; nothing asks the broker whether the account is paper.
- **Currency.** Limits are plain numbers; the quote and account currencies may differ on XTB.

## Recommended settings for a paper run

```bash
RISK_MAX_ORDER_NOTIONAL=500
RISK_MAX_POSITION_QTY=5
RISK_MAX_OPEN_POSITIONS=2
RISK_MAX_DAILY_LOSS=50
RISK_MAX_QUOTE_AGE_SEC=15
```

Export `ALPACA_PAPER=true` and `XTB_DEMO=true` explicitly in the service environment rather than relying on the default, so that a stray variable from another project cannot change the target account.
