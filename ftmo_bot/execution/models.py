"""Broker-independent order intent types.

Strategies express *what* they want to trade.  The execution runtime decides
whether and when that intent becomes a fill.  Keeping this contract independent
from pandas and MetaTrader is what lets one strategy run in historical, demo,
and live modes.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


@dataclass(frozen=True)
class OrderIntent:
    side: str
    order_type: OrderType
    entry: float
    sl: float
    tp: float
    valid_for_bars: int | None = None
    tag: str | None = None

    @classmethod
    def from_signal(cls, signal: dict[str, Any]) -> "OrderIntent":
        """Normalize the legacy strategy dictionary into the new contract."""
        side = str(signal.get("type", "")).upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError(f"Unsupported signal type: {side!r}")
        try:
            order_type = OrderType(str(signal.get("order_type", "MARKET")).upper())
        except ValueError as error:
            raise ValueError("order_type must be MARKET, LIMIT, or STOP") from error
        valid_for_bars = signal.get("valid_for_bars")
        if valid_for_bars is not None:
            valid_for_bars = int(valid_for_bars)
            if valid_for_bars <= 0:
                raise ValueError("valid_for_bars must be a positive integer or null")
        return cls(
            side=side,
            order_type=order_type,
            entry=float(signal["entry"]),
            sl=float(signal["sl"]),
            tp=float(signal["tp"]),
            valid_for_bars=valid_for_bars,
            tag=signal.get("tag"),
        )

    def validate_price_geometry(self) -> None:
        if self.side == "BUY" and not self.sl < self.entry < self.tp:
            raise ValueError("BUY order must satisfy sl < entry < tp")
        if self.side == "SELL" and not self.tp < self.entry < self.sl:
            raise ValueError("SELL order must satisfy tp < entry < sl")


@dataclass
class PendingOrder:
    order_id: str
    intent: OrderIntent
    signal_time: Any
    created_index: int
    eligible_index: int
    expires_after_index: int | None

    def is_expired_before(self, bar_index: int) -> bool:
        return (
            self.expires_after_index is not None
            and bar_index > self.expires_after_index
        )
