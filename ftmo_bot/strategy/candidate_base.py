"""Shared plumbing for the numbered research strategy candidates."""
from __future__ import annotations

import math

import pandas as pd

from strategy.base import BaseStrategy


ALL_TIMEFRAMES = ('M1', 'M2', 'M3', 'M4', 'M5', 'M6', 'M10', 'M12', 'M15', 'M20', 'M30',
                  'H1', 'H2', 'H3', 'H4', 'H6', 'H8', 'H12', 'D1', 'W1', 'MN1')


class CandidateBase(BaseStrategy):
    supported_asset_classes = ('metal',)
    supported_timeframes = ALL_TIMEFRAMES
    key = ''
    defaults = {}

    def __init__(self, params: dict | None = None):
        raw = dict(params or {})
        values = dict(self.defaults)
        values.update(raw.get(self.key, {}))
        raw[self.key] = values
        super().__init__(raw)
        self.p = values

    def valid(self, *values) -> bool:
        return all(value is not None and not pd.isna(value) and math.isfinite(float(value)) for value in values)

    def order(self, side: str, row, stop_atr: float, target_atr: float):
        atr = getattr(row, 'atr', None)
        if not self.valid(atr, row.close) or atr <= 0:
            return None
        entry, stop, target = float(row.close), float(atr) * stop_atr, float(atr) * target_atr
        if side == 'BUY':
            return self.order_intent(side, entry, entry - stop, entry + target, tag=self.key)
        return self.order_intent(side, entry, entry + stop, entry - target, tag=self.key)
