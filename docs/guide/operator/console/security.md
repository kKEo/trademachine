# Security model

The console can send orders, so this chapter states precisely who can reach it, what each layer refuses, and where the protection ends. Every rule is enforced in `tradesignals/console/security.py` or `tradesignals/console/service.py` and covered by a test.

## Layers

| Layer | Rule | Refusal | Evidence |
|---|---|---|---|
| Bind address | The server listens on `127.0.0.1` only. `HOST` is a constant in `tradesignals/console/server.py`; no flag or variable changes it. | not reachable from the network | `tests/test_console.py::test_server_binds_to_loopback_only` |
| Host header | Every request must name a loopback host (`127.0.0.1`, `localhost`, `[::1]`). This defeats DNS rebinding, where a hostile page reaches your local server under its own name. | 403 for every path, static files included | `tests/test_console.py::test_requests_with_a_foreign_host_header_are_refused` |
| Origin | A state-changing request that carries an `Origin` header must come from the console's own origin. | 403 | `tests/test_console.py::test_state_changing_requests_need_same_origin_and_token` |
| Token | A state-changing request must present `CONSOLE_TOKEN`, in the `x-console-token` header (terminal UI) or the `console_token` cookie (browser, set by `/unlock`). Comparison is constant-time. | 401 | `tests/test_controls.py::test_token_matrix_for_every_action` |
| No token configured | With `CONSOLE_TOKEN` empty the console is view only: every state-changing request is refused, whatever it presents. | 403, "read-only" | `tests/test_console.py::test_console_without_token_is_read_only` |
| Account type | Trading actions (flatten, cancel, orders, limits, strategies, resume) are refused when the broker is not paper or demo, and when the engine runs without `--execute`. Halt and the kill switch stay available: they only reduce risk. | 403 | `tests/test_controls.py::test_live_account_allows_halt_and_kill_switch_only`, `tests/test_controls.py::test_engine_without_execute_refuses_console_trading` |
| Typed confirmation | "Close every position" needs the word `FLATTEN`, the kill switch the word `STOP`, checked on the server. | 400 | `tests/test_controls.py::test_halt_flatten_resume_from_the_console_with_audit_trail`, `tests/test_controls.py::test_console_kill_switch_needs_the_typed_word_and_stops_the_engine` |
| Risk | Every order the console sends passes `RiskManager.check()`. | 409 with the risk reason | `tests/test_management.py::test_console_order_blocked_by_risk_and_validated` |

"State-changing" means any method other than GET, HEAD and OPTIONS. GET requests never change anything.

## The cookie

`/unlock` takes the token in a JSON body and, if it matches, sets `console_token` as an `HttpOnly`, `SameSite=Strict` cookie (`tests/test_controls.py::test_unlock_sets_a_strict_http_only_cookie_and_lock_clears_it`). `HttpOnly` keeps page scripts from reading it; `SameSite=Strict` keeps other sites from sending it. It is not marked `Secure` because the console is served over plain HTTP on loopback. A wrong token is answered after half a second. `/lock` deletes the cookie.

## Response headers

Every response, refusals included, carries a content security policy (`default-src 'self'`, inline styles allowed, no inline or evaluated script, `frame-ancestors 'none'`), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`; everything but static files is `Cache-Control: no-store` (`tests/test_system.py::test_every_response_carries_security_headers`). The pages contain no inline script and no inline event handlers (`tests/test_system.py::test_pages_contain_no_inline_script_or_event_handlers`), and all scripts are served from the console itself: it works without a network connection and loads nothing from a CDN.

## Secrets

The console never shows a key or a password. The System page lists a fixed set of non-secret settings with their values and reports credentials and the console token only as "set" or "not set" (`tests/test_system.py::test_settings_show_secrets_only_as_set_or_not_set`). The log tail captures `tradesignals.*` loggers only; third-party loggers, which could log a login frame at DEBUG, never reach it (`tests/test_system.py::test_log_tail_captures_tradesignals_loggers_only_and_filters_by_level`).

## The audit trail

Every action attempted through the console is written to the `audit_log` table with its target and its result: the success message, `refused: <why>` or `failed: <error>`. Refusals by the guard (wrong token, wrong origin) happen before the action and are not audited. The Risk and System pages show the latest entries.

## Where the protection ends

- There are no user accounts. Whoever has the token and a shell (or a tunnel) on the machine can act.
- Other local users of the same machine can reach the port; the token is the only thing between them and the actions, so choose a long random one and keep `.env` private.
- Putting the console behind a reverse proxy changes the `Host` header and removes the loopback guarantee. It is unsupported.
- The token in `.env` is as sensitive as the broker keys; rotate it by changing the value and restarting.
