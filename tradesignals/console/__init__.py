"""Admin console: a local web UI (and JSON API) served from inside the engine process.

Optional: needs the ``console`` extra (``pip install -e ".[console]"``). The engine never
imports this package; ``cli.py`` wires it up when ``--console`` is given.
"""

from __future__ import annotations

from .app import create_app
from .server import ConsoleServer
from .service import ConsoleService

__all__ = ["ConsoleServer", "ConsoleService", "create_app"]
