# Safety controls and their limits

This chapter lists each control that stands between a signal and real money, what it actually does in code, and the point where it stops protecting you. Every row is verified in code and, where marked, covered by a test.

| Control | What it does | Where it stops | Evidence |
|---|---|---|---|
| Signal-only default | `execute` is on only with `--execute` **and** a broker (`tradesignals/engine.py:102`). | A broker named without `--execute` still connects and needs credentials. | `tests/test_engine_robustness.py::test_execute_without_broker_is_signal_only` |
| Paper/demo defaults | `ALPACA_PAPER` and `XTB_DEMO` default to `true` and are parsed strictly; unknown spellings raise instead of selecting live (`tradesignals/config.py:15`). | A deliberate `false` selects live. There is no check of the account type with the broker. | `tests/test_config.py`, `tests/test_alpaca_mapping.py::test_creds_paper_flag_is_strict` |
| Live guard | `--execute` is refused when `broker.paper` is false unless `--allow-live` is given; `Broker.paper` defaults to `False` so a forgotten attribute fails closed (`tradesignals/cli.py:108-120`, `tradesignals/interfaces.py:66`). | Only the CLI enforces it; a program embedding the engine can bypass it. | `tests/test_cli.py::test_live_guard_refuses_execute_on_live_account_without_allow_live`, `tests/test_cli.py::test_live_guard_allows_live_with_flag_and_warns`, `tests/test_cli.py::test_base_broker_defaults_to_not_paper` |
| Order notional cap | Rejects `qty × price > RISK_MAX_ORDER_NOTIONAL` (`tradesignals/risk.py:65`). | Never applied to an order that only reduces a position. Measured in the quote currency. | `tests/test_risk.py::test_rejects_oversized_notional_using_limit_price_when_given` |
| Position cap | Rejects a resulting absolute position above `RISK_MAX_POSITION_QTY` (`tradesignals/risk.py:72`). | Counts positions you opened by hand too. | `tests/test_risk.py::test_rejects_position_limit_on_both_sides` |
| Open-positions cap | Rejects a new symbol when the account already holds `RISK_MAX_OPEN_POSITIONS` positions (`tradesignals/risk.py:74`). | Counts every position on the account, not only the engine's. | `tests/test_risk.py::test_rejects_new_symbol_when_open_position_cap_reached` |
| Loss halt | Halts once equity is `RISK_MAX_DAILY_LOSS` below the equity at the first check (`tradesignals/risk.py:76-84`). | Baseline is "since this process started or was resumed", never reset by date; skipped while the account is unknown; the halt blocks new and larger positions only, exits still pass. | `tests/test_risk.py::test_daily_loss_halts_and_latches_until_restart`, `tests/test_risk.py::test_halt_blocks_new_risk_but_never_an_exit` |
| Stale-quote check | Rejects when the last tick is older than `RISK_MAX_QUOTE_AGE_SEC` (`tradesignals/risk.py:63`). | Wall-clock based; exits are exempt. | `tests/test_risk.py::test_rejects_stale_quote` |
| Kill switch | Halts risk, cancels pending orders, stops; `stop()` runs even if cancel fails (`tradesignals/engine.py:174-184`). | Reached from the admin console and the terminal UI, not from a CLI flag or signal; cancels nothing on paper, everything on the Alpaca account, pending orders only on XTB; never flattens. | `tests/test_engine_robustness.py::test_kill_switch_stops_even_if_cancel_all_fails`, `tests/test_controls.py::test_kill_switch_halts_cancels_and_stops_a_running_engine` |
| Exits always pass | An order that only reduces a position skips every risk rule (`tradesignals/risk.py:97`). | An order that crosses zero is new risk and is judged normally. | `tests/test_risk.py::test_halt_blocks_new_risk_but_never_an_exit` |
| Orders in flight | Sizing and risk count the unfilled part of open orders, so a second signal before a fill does not duplicate the order (`tradesignals/engine.py:536`). | An open order the broker stops reporting is forgotten after ten minutes. | `tests/test_controls.py::test_second_signal_before_the_fill_does_not_duplicate_the_order` |
| Console access | The console binds to `127.0.0.1`, refuses foreign `Host` and `Origin` headers, and needs `CONSOLE_TOKEN` for anything that changes state; without a token it is view only. Trading actions are refused on a live account. | Anyone with a shell on the machine and the token can act; there are no user accounts. | `tests/test_console.py::test_requests_with_a_foreign_host_header_are_refused`, `tests/test_controls.py::test_token_matrix_for_every_action`, `tests/test_controls.py::test_live_account_allows_halt_and_kill_switch_only` |
| Clean stop on `Ctrl-C` | `SIGINT`/`SIGTERM` set the stop event; tasks are cancelled and connections closed (`tradesignals/cli.py:189`, `tradesignals/engine.py:128-169`). | Cancels and flattens nothing. | `tests/test_engine_robustness.py::test_run_closes_broker_even_if_data_close_fails` |
| Stream failure handling | A failing data stream stops the engine with an ERROR line instead of hanging (`tradesignals/engine.py:429-444`). | It stops; it does not reconnect. Positions stay as they are. | `tests/test_engine_robustness.py::test_stream_failure_stops_engine_and_logs` |
| Credential hygiene | Keys come from the environment, are never logged by the adapters, and `-v` cannot make the websockets library log frames (`tradesignals/cli.py:101-105`). | A `DEBUG` root logger configured by an embedding program would. | `tests/test_cli.py::test_verbose_only_raises_tradesignals_loggers` |
| Duplicate-bar protection | Providers that push bars are never double-fed from ticks; late events are dropped (`tradesignals/engine.py:466`, `tradesignals/bars.py:47`). | Buckets before the first provider bar can be tick-built for an adapter that forgot `pushes_bars`. | `tests/test_engine_robustness.py::test_provider_that_declares_bars_never_builds_them_from_ticks`, `tests/test_bars.py` |

## What no control covers

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
