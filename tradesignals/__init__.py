"""tradesignals: provider-agnostic trade-signal engine (Alpaca, XTB)."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

try:
    __version__ = _version("tradesignals")
except PackageNotFoundError:  # running from a checkout that was never installed
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
