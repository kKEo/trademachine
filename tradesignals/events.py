"""In-process event bus: how the engine tells the outside world what it is doing.

The engine publishes typed events; subscribers (the SQLite writer, the admin console) consume
them from their own bounded queue. Publishing is synchronous, never blocks and never raises,
so a slow or broken subscriber cannot delay or stop trading: when a subscriber's queue is full
its oldest event is dropped and counted.

Standard library only. The engine imports this module; this module imports nothing but models.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .models import Account, Bar, Order, Position, Signal, Tick, utcnow

log = logging.getLogger("tradesignals.events")


class OutcomeKind(str, Enum):
    """What the engine did with a signal in execute mode."""

    ORDER = "order"  # an order was submitted (see client_order_id)
    NO_OP = "no_op"  # the position already equals the target
    RISK_REJECT = "risk_reject"  # RiskManager.check() said no
    SUBMIT_FAILED = "submit_failed"  # the broker raised on submit


@dataclass(frozen=True, slots=True)
class SignalEvent:
    signal_id: str
    signal: Signal
    timeframe: str = ""  # bar size of the strategy that emitted it, e.g. "1m"; empty when unknown


@dataclass(frozen=True, slots=True)
class SignalOutcome:
    signal_id: str | None  # None for orders that no signal caused (manual, flatten)
    kind: OutcomeKind
    detail: str = ""
    client_order_id: str | None = None
    ts: datetime = field(default_factory=utcnow)


@dataclass(frozen=True, slots=True)
class OrderEvent:
    order: Order
    origin: str = ""  # "<strategy>:<kind>", "manual", "flatten"; empty for broker push updates
    signal_id: str | None = None
    update: bool = False  # False: our submit returned; True: the broker pushed a state change
    ts: datetime = field(default_factory=utcnow)


@dataclass(frozen=True, slots=True)
class BarEvent:
    bar: Bar


@dataclass(frozen=True, slots=True)
class QuoteEvent:
    tick: Tick


@dataclass(frozen=True, slots=True)
class SnapshotEvent:
    account: Account | None
    positions: tuple[Position, ...]
    ts: datetime = field(default_factory=utcnow)


@dataclass(frozen=True, slots=True)
class StatusEvent:
    state: str  # "running" | "stopped"
    halted: bool = False
    halt_reason: str = ""
    ts: datetime = field(default_factory=utcnow)


Event = SignalEvent | SignalOutcome | OrderEvent | BarEvent | QuoteEvent | SnapshotEvent | StatusEvent


class Subscription:
    """One consumer's bounded queue. Iterate it with ``async for`` or call ``get()``."""

    def __init__(self, bus: EventBus, maxsize: int, types: tuple[type, ...] | None, name: str):
        self._bus = bus
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self._types = types
        self.name = name
        self.dropped = 0  # events discarded because this consumer was too slow

    def _offer(self, ev: Event) -> None:
        if self._types is not None and not isinstance(ev, self._types):
            return
        if self._queue.full():
            self._queue.get_nowait()  # drop the oldest: fresh state matters more than old state
            self.dropped += 1
            if self.dropped == 1 or self.dropped % 1000 == 0:
                log.warning("subscriber %s is slow: %d events dropped so far", self.name, self.dropped)
        self._queue.put_nowait(ev)

    async def get(self) -> Event:
        return await self._queue.get()

    def get_nowait(self) -> Event | None:
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def close(self) -> None:
        self._bus._subs.discard(self)

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> Event:
        return await self._queue.get()


class EventBus:
    def __init__(self) -> None:
        self._subs: set[Subscription] = set()

    def subscribe(self, maxsize: int = 1000, types: tuple[type, ...] | None = None, name: str = "?") -> Subscription:
        sub = Subscription(self, maxsize, types, name)
        self._subs.add(sub)
        return sub

    def subscribers(self) -> list[tuple[str, int]]:
        """``(name, events dropped)`` per subscriber, for health displays."""
        return sorted((sub.name, sub.dropped) for sub in self._subs)

    def publish(self, ev: Event) -> None:
        for sub in tuple(self._subs):
            try:
                sub._offer(ev)
            except Exception:  # noqa: BLE001 - a subscriber must never break the publisher
                log.exception("publishing %s to subscriber %s failed", type(ev).__name__, sub.name)
