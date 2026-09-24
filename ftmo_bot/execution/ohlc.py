"""Conservative OHLC order triggering for historical simulation.

OHLC data cannot reveal the exact route within a candle.  This model therefore
does not pretend to know queue position or tick order.  It only answers whether
an order was executable given the observed range and uses no favourable price
improvement on gap-through pending entries.
"""
from __future__ import annotations

from dataclasses import dataclass

from execution.models import OrderIntent, OrderType


@dataclass(frozen=True)
class FillDecision:
    raw_price: float
    reason: str


class OhlcExecutionModel:
    """Translate executable market/limit/stop intents into OHLC fill decisions."""

    def entry_fill(self, intent: OrderIntent, row) -> FillDecision | None:
        open_price = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        trigger = intent.entry

        if intent.order_type is OrderType.MARKET:
            return FillDecision(open_price, "market_next_open")

        if intent.order_type is OrderType.LIMIT:
            if intent.side == "BUY":
                if open_price <= trigger:
                    return FillDecision(trigger, "limit_gap_through")
                if low <= trigger <= high:
                    return FillDecision(trigger, "limit_touched")
            else:
                if open_price >= trigger:
                    return FillDecision(trigger, "limit_gap_through")
                if low <= trigger <= high:
                    return FillDecision(trigger, "limit_touched")
            return None

        # Stop entries must pay a worse price when a gap opens beyond the stop.
        if intent.side == "BUY":
            if open_price >= trigger:
                return FillDecision(open_price, "stop_gap_through")
            if high >= trigger:
                return FillDecision(trigger, "stop_triggered")
        else:
            if open_price <= trigger:
                return FillDecision(open_price, "stop_gap_through")
            if low <= trigger:
                return FillDecision(trigger, "stop_triggered")
        return None
