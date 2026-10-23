from .base import Strategy
from .rsi_reversion import RsiReversion
from .sma_cross import SmaCross

REGISTRY: dict[str, type[Strategy]] = {
    SmaCross.name: SmaCross,
    RsiReversion.name: RsiReversion,
}

__all__ = ["Strategy", "SmaCross", "RsiReversion", "REGISTRY"]
