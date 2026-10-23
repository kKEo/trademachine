"""Provider registry. Adding a provider = implement the interfaces, register it here."""
from __future__ import annotations

from collections.abc import Callable

from ..interfaces import Broker, MarketDataProvider


def _alpaca_data() -> MarketDataProvider:
    from .alpaca import AlpacaData
    return AlpacaData()


def _alpaca_broker() -> Broker:
    from .alpaca import AlpacaBroker
    return AlpacaBroker()


def _xtb_data() -> MarketDataProvider:
    from .xtb import XtbData
    return XtbData()


def _xtb_broker() -> Broker:
    from .xtb import XtbBroker
    return XtbBroker()


def _paper_broker() -> Broker:
    from .fake import PaperBroker
    return PaperBroker()


DATA_PROVIDERS: dict[str, Callable[[], MarketDataProvider]] = {
    "alpaca": _alpaca_data,
    "xtb": _xtb_data,
}
BROKERS: dict[str, Callable[[], Broker]] = {
    "alpaca": _alpaca_broker,
    "xtb": _xtb_broker,
    "paper": _paper_broker,
}


def make_data(name: str) -> MarketDataProvider:
    return DATA_PROVIDERS[name]()


def make_broker(name: str) -> Broker:
    return BROKERS[name]()
