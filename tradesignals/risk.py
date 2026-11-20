"""Pre-trade risk guards. These run in *our* code, before anything reaches a broker.

Orders that only reduce an existing position (without flipping it) are always allowed: they can
only lower risk, and a halt, a notional cap or a stale quote must never trap you in a position.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

from .models import Account, OrderRequest, Position, Side, Tick, utcnow


@dataclass
class RiskLimits:
    max_order_notional: float = 2000.0
    max_position_qty: float = 50.0
    max_open_positions: int = 5
    max_daily_loss: float = 200.0
    max_quote_age_sec: float = 15.0

    @classmethod
    def from_env(cls) -> "RiskLimits":
        g = os.environ.get
        return cls(
            max_order_notional=float(g("RISK_MAX_ORDER_NOTIONAL", 2000)),
            max_position_qty=float(g("RISK_MAX_POSITION_QTY", 50)),
            max_open_positions=int(g("RISK_MAX_OPEN_POSITIONS", 5)),
            max_daily_loss=float(g("RISK_MAX_DAILY_LOSS", 200)),
            max_quote_age_sec=float(g("RISK_MAX_QUOTE_AGE_SEC", 15)),
        )


class RiskManager:
    def __init__(self, limits: RiskLimits):
        self.limits = limits
        self.start_equity: float | None = None
        self.halted = False
        self.halt_reason = ""

    def check(
        self,
        req: OrderRequest,
        last_tick: Tick | None,
        positions: list[Position],
        account: Account | None,
        now: datetime | None = None,
        commit: bool = True,
    ) -> tuple[bool, str]:
        """``commit=False`` asks "would this pass?" without latching a halt or fixing the loss baseline."""
        now = now or utcnow()
        L = self.limits
        if reduces_position(req, positions):
            return True, "ok: reduces position"
        if self.halted:
            return False, f"halted: {self.halt_reason}"
        if last_tick is None:
            return False, "no quote"
        age = (now - last_tick.ts).total_seconds()
        if age > L.max_quote_age_sec:
            return False, f"stale quote ({age:.0f}s)"
        price = req.limit_price or last_tick.price
        if req.qty * price > L.max_order_notional:
            return False, f"notional {req.qty * price:.0f} > {L.max_order_notional}"
        pos = {p.symbol: p for p in positions}
        cur = pos.get(req.symbol)
        cur_qty = cur.qty if cur else 0.0
        new_qty = cur_qty + (req.qty if req.side == Side.BUY else -req.qty)
        if abs(new_qty) > L.max_position_qty:
            return False, f"position would be {new_qty} > {L.max_position_qty}"
        if cur is None and new_qty != 0 and len(positions) >= L.max_open_positions:
            return False, f"max open positions ({L.max_open_positions})"
        if account is not None:
            start = self.start_equity if self.start_equity is not None else account.equity
            if commit:
                self.start_equity = start
            dd = start - account.equity
            if dd > L.max_daily_loss:
                reason = f"daily loss {dd:.0f} > {L.max_daily_loss}"
                if commit:
                    self.halted, self.halt_reason = True, reason
                return False, reason
        return True, "ok"

    def halt(self, reason: str) -> None:
        self.halted, self.halt_reason = True, reason

    def resume(self) -> None:
        """Clear a halt. The loss baseline restarts from the next account seen, so a loss halt
        does not re-trigger at once; the operator has decided to carry on from here."""
        self.halted, self.halt_reason = False, ""
        self.start_equity = None


def reduces_position(req: OrderRequest, positions: list[Position]) -> bool:
    """True when the order shrinks an existing position towards zero without crossing it."""
    cur = next((p.qty for p in positions if p.symbol == req.symbol), 0.0)
    if abs(cur) < 1e-9:
        return False
    signed = req.qty if req.side == Side.BUY else -req.qty
    return (cur > 0) != (signed > 0) and abs(signed) <= abs(cur) + 1e-9
