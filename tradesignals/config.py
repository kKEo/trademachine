"""Environment parsing helpers shared by the CLI and the adapters.

Safety-relevant flags (ALPACA_PAPER, XTB_DEMO) must never fail open: an unrecognised value
raises instead of silently selecting a live account.
"""

from __future__ import annotations

import os

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def env_bool(name: str, default: bool) -> bool:
    """Parse a boolean environment variable strictly.

    Unset or empty -> ``default``; ``true/1/yes/on`` -> True; ``false/0/no/off`` -> False
    (case-insensitive, surrounding whitespace ignored). Anything else raises ``ValueError``.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"{name}={raw!r} is not a boolean; use true or false")
