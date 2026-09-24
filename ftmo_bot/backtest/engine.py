"""Event-driven backtest engine with conservative execution assumptions."""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd

from execution import OhlcExecutionModel, OrderIntent, PendingOrder


class BacktestEngine:
    """Run one-position-at-a-time strategies on OHLC bars.

    Signals are generated after a bar closes and filled at the next bar open. The
    engine deliberately resolves an ambiguous bar (both SL and TP touched) in
    favour of the stop loss. This avoids silently granting the strategy
    information that is unavailable in OHLC data.
    """

    def __init__(
        self,
        df: pd.DataFrame,
        strategy,
        risk_manager,
        compliance_guard,
        trading_start_time=None,
        continue_after_failure: bool = True,
        enforce_limits: bool = True,
        enforce_internal_stop: bool = True,
    ):
        self.df = self._validate_data(df)
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.compliance_guard = compliance_guard
        self.continue_after_failure = continue_after_failure
        self.enforce_limits = enforce_limits
        self.enforce_internal_stop = enforce_internal_stop

        self.initial_balance = compliance_guard.initial_balance
        self.balance = self.initial_balance
        self.equity = self.initial_balance

        execution = risk_manager.risk_params.get("execution", {})
        self.contract_size = float(execution.get("contract_size", 100.0))
        self.point_size = float(execution.get("point_size", 0.01))
        self.spread_points = float(execution.get("spread_points", 0.0))
        self.slippage_points = float(execution.get("slippage_points", 0.0))
        self.commission_round_turn = float(
            execution.get("commission_per_lot_round_turn_usd", 0.0)
        )
        if min(
            self.contract_size,
            self.point_size,
        ) <= 0 or min(
            self.spread_points, self.slippage_points, self.commission_round_turn
        ) < 0:
            raise ValueError("Execution sizes must be positive and costs non-negative")
        self.intrabar_policy = execution.get("intrabar_policy", "stop_first")
        if self.intrabar_policy != "stop_first":
            raise ValueError("Only the conservative 'stop_first' policy is supported")
        self.max_pending_orders = int(execution.get("max_pending_orders", 1))
        if self.max_pending_orders <= 0:
            raise ValueError("max_pending_orders must be positive")
        self.execution_model = OhlcExecutionModel()

        self.data_timezone = ZoneInfo(
            compliance_guard.ftmo_rules.get("data_timezone", "UTC")
        )
        self.daily_reset_timezone = ZoneInfo(
            compliance_guard.ftmo_rules.get("daily_reset_timezone", "Europe/Prague")
        )
        self.trading_start_time = (
            self._normalise_timestamp(trading_start_time)
            if trading_start_time is not None
            else None
        )

    @staticmethod
    def _validate_data(df: pd.DataFrame) -> pd.DataFrame:
        required = {"time", "open", "high", "low", "close"}
        missing = required.difference(df.columns)
        if missing:
            raise ValueError("OHLC data is missing columns: " + ", ".join(sorted(missing)))

        clean = df.copy()
        clean["time"] = pd.to_datetime(clean["time"])
        price_columns = ["open", "high", "low", "close"]
        clean[price_columns] = clean[price_columns].apply(pd.to_numeric, errors="coerce")
        if clean[["time", *price_columns]].isna().any().any():
            raise ValueError("OHLC data contains missing or non-numeric values")
        if clean["time"].duplicated().any():
            raise ValueError("OHLC data contains duplicate timestamps")
        invalid_high = clean["high"] < clean[["open", "close", "low"]].max(axis=1)
        invalid_low = clean["low"] > clean[["open", "close", "high"]].min(axis=1)
        if invalid_high.any() or invalid_low.any():
            raise ValueError("OHLC data contains impossible high/low values")
        return clean.sort_values("time").reset_index(drop=True)

    @property
    def spread_price(self) -> float:
        return self.spread_points * self.point_size

    @property
    def slippage_price(self) -> float:
        return self.slippage_points * self.point_size

    def _normalise_timestamp(self, value) -> pd.Timestamp:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize(self.data_timezone)
        return timestamp.tz_convert(self.daily_reset_timezone)

    def _trading_day(self, value):
        return self._normalise_timestamp(value).date()

    def _mark_to_market(self, trade: dict, price: float) -> float:
        if trade["type"] == "BUY":
            price_diff = price - trade["entry"]
        else:
            price_diff = trade["entry"] - price
        return self.balance + price_diff * trade["size"] * self.contract_size

    def _open_trade(self, pending_order: PendingOrder, raw_fill_price: float,
                    fill_reason: str, timestamp) -> dict | None:
        """Create a position from an actual execution, not directly from a signal."""
        intent = pending_order.intent
        intent.validate_price_geometry()
        side = intent.side
        reference_entry = intent.entry
        signal_stop = intent.sl
        signal_target = intent.tp
        stop_distance = abs(reference_entry - signal_stop)
        target_distance = abs(signal_target - reference_entry)
        if stop_distance <= 0 or target_distance <= 0:
            return None

        adverse_cost = self.spread_price + self.slippage_price
        entry = raw_fill_price + adverse_cost if side == "BUY" else raw_fill_price - adverse_cost
        stop = entry - stop_distance if side == "BUY" else entry + stop_distance
        target = entry + target_distance if side == "BUY" else entry - target_distance

        size = self.risk_manager.calculate_position_size(
            current_equity=self.equity,
            entry_price=entry,
            stop_loss=stop,
            contract_size=self.contract_size,
            current_open_risk_usd=0.0,
            additional_risk_per_lot_usd=(
                self.slippage_price * self.contract_size
                + self.commission_round_turn
            ),
        )
        if size <= 0:
            return None

        balance_before_entry = self.balance
        entry_commission = self.commission_round_turn * size / 2.0
        self.balance -= entry_commission
        return {
            "order_id": pending_order.order_id,
            "order_type": intent.order_type.value,
            "order_tag": intent.tag,
            "signal_time": pending_order.signal_time,
            "order_created_time": pending_order.signal_time,
            "entry_time": timestamp,
            "type": side,
            "entry": entry,
            "sl": stop,
            "tp": target,
            "size": size,
            "balance_before_entry": balance_before_entry,
            "entry_commission_usd": entry_commission,
            "entry_fill_reason": fill_reason,
        }

    def _resolve_exit(self, trade: dict, row) -> tuple[float, str] | None:
        bar_open = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])

        if trade["type"] == "BUY":
            if bar_open <= trade["sl"]:
                return bar_open, "gap_stop"
            if bar_open >= trade["tp"]:
                return trade["tp"], "take_profit"
            hit_stop = low <= trade["sl"]
            hit_target = high >= trade["tp"]
        else:
            if bar_open >= trade["sl"]:
                return bar_open, "gap_stop"
            if bar_open <= trade["tp"]:
                return trade["tp"], "take_profit"
            hit_stop = high >= trade["sl"]
            hit_target = low <= trade["tp"]

        if hit_stop:  # Also wins when both levels were touched.
            return trade["sl"], "stop_loss"
        if hit_target:
            return trade["tp"], "take_profit"
        return None

    def _close_trade(
        self, trade: dict, raw_exit_price: float, timestamp, reason: str
    ) -> dict:
        if trade["type"] == "BUY":
            exit_price = raw_exit_price - self.slippage_price
            price_diff = exit_price - trade["entry"]
        else:
            exit_price = raw_exit_price + self.slippage_price
            price_diff = trade["entry"] - exit_price

        gross_pnl = price_diff * trade["size"] * self.contract_size
        exit_commission = self.commission_round_turn * trade["size"] / 2.0
        self.balance += gross_pnl - exit_commission
        total_pnl = self.balance - trade["balance_before_entry"]

        trade.update(
            {
                "exit_time": timestamp,
                "exit_price": exit_price,
                "gross_pnl_usd": gross_pnl,
                "commission_usd": trade["entry_commission_usd"]
                + exit_commission,
                "pnl_usd": total_pnl,
                "pnl_pct": total_pnl / trade["balance_before_entry"] * 100,
                "balance": self.balance,
                "closed_by": reason,
            }
        )
        return trade

    def _intrabar_extreme_equity(self, trade: dict, row) -> tuple[float, float]:
        """Return conservative (worst, best) equity observable inside a bar."""
        estimated_exit_commission = self.commission_round_turn * trade["size"] / 2.0
        if trade["type"] == "BUY":
            if float(row["open"]) <= trade["sl"]:
                adverse_price = float(row["open"])
            else:
                adverse_price = max(float(row["low"]), trade["sl"])
            favourable_price = min(float(row["high"]), trade["tp"])
        else:
            if float(row["open"]) >= trade["sl"]:
                adverse_price = float(row["open"])
            else:
                adverse_price = min(float(row["high"]), trade["sl"])
            favourable_price = max(float(row["low"]), trade["tp"])

        worst = self._mark_to_market(trade, adverse_price) - estimated_exit_commission
        best = self._mark_to_market(trade, favourable_price) - estimated_exit_commission
        return worst, best

    def run(self) -> pd.DataFrame:
        trades: list[dict] = []
        equity_curve: list[dict] = []
        open_trade = None
        pending_orders: list[PendingOrder] = []
        order_events: list[dict] = []
        next_order_number = 1

        peak_equity = self.initial_balance
        daily_start_balance = self.initial_balance
        current_day = None
        trading_halted = False

        first_fail_index = None
        first_fail_time = None
        first_fail_reason = None
        first_internal_stop_time = None
        first_internal_stop_reason = None

        last_index = len(self.df) - 1
        for position, (_, row) in enumerate(self.df.iterrows()):
            timestamp = row["time"]
            normalised_time = self._normalise_timestamp(timestamp)

            # Indicator warm-up rows build strategy state but cannot trade.
            if self.trading_start_time is not None and normalised_time < self.trading_start_time:
                self.strategy.generate_signal(row)
                continue

            day = normalised_time.date()
            if current_day != day:
                current_day = day
                # FTMO fixes the daily floor from balance at 00:00 CE(S)T,
                # while live compliance is evaluated against current equity.
                daily_start_balance = self.balance

            stress_mode = self.continue_after_failure and first_fail_time is not None
            # An intent becomes eligible only after the close that created it.
            # Pending stop/limit orders remain live until their explicit expiry.
            still_pending = []
            for order in pending_orders:
                if order.is_expired_before(position):
                    order_events.append({
                        "time": timestamp, "order_id": order.order_id,
                        "event": "expired", "order_type": order.intent.order_type.value,
                    })
                else:
                    still_pending.append(order)
            pending_orders = still_pending

            if open_trade is None and (not trading_halted or stress_mode):
                for order in list(pending_orders):
                    if position < order.eligible_index:
                        continue
                    decision = self.execution_model.entry_fill(order.intent, row)
                    if decision is None:
                        continue
                    # A fill is terminal for this intent even if risk sizing rejects it.
                    pending_orders.remove(order)
                    open_trade = self._open_trade(
                        order, decision.raw_price, decision.reason, timestamp
                    )
                    order_events.append({
                        "time": timestamp, "order_id": order.order_id,
                        "event": "filled" if open_trade is not None else "rejected_risk",
                        "order_type": order.intent.order_type.value,
                        "reason": decision.reason,
                    })
                    if open_trade is not None:
                        open_trade["post_failure_entry"] = stress_mode
                    break  # The current portfolio model permits one open position.

            worst_equity = self.balance
            if open_trade is not None:
                worst_equity, best_equity = self._intrabar_extreme_equity(open_trade, row)
                peak_equity = max(peak_equity, best_equity)

                hard_breach, hard_reason = (self.compliance_guard.check_hard_violation(
                    worst_equity, daily_start_balance, peak_equity
                ) if self.enforce_limits else (False, 'limits disabled'))
                if hard_breach and first_fail_time is None:
                    first_fail_index = len(trades)
                    first_fail_time = timestamp
                    first_fail_reason = hard_reason
                    trading_halted = True

                internal_stop, internal_reason = (self.compliance_guard.check_internal_stop(
                    worst_equity, daily_start_balance, peak_equity
                ) if self.enforce_limits and self.enforce_internal_stop else (False, 'internal stop disabled'))
                if internal_stop and first_internal_stop_time is None:
                    first_internal_stop_time = timestamp
                    first_internal_stop_reason = internal_reason
                    trading_halted = True

                resolved_exit = self._resolve_exit(open_trade, row)
                if resolved_exit is not None:
                    raw_exit, reason = resolved_exit
                    trades.append(
                        self._close_trade(open_trade, raw_exit, timestamp, reason)
                    )
                    open_trade = None

            self.equity = (
                self._mark_to_market(open_trade, float(row["close"]))
                if open_trade is not None
                else self.balance
            )
            peak_equity = max(peak_equity, self.equity)

            trading_state = (self.compliance_guard.get_trading_state(
                self.equity, daily_start_balance, peak_equity
            ) if self.enforce_limits else 'safe')
            stress_mode = self.continue_after_failure and first_fail_time is not None
            if trading_state == "critical" and open_trade is not None and not stress_mode:
                trades.append(
                    self._close_trade(
                        open_trade, float(row["close"]), timestamp, "critical_soft_stop"
                    )
                )
                open_trade = None
                self.equity = self.balance

            hard_breach, hard_reason = (self.compliance_guard.check_hard_violation(
                min(self.equity, worst_equity), daily_start_balance, peak_equity
            ) if self.enforce_limits else (False, 'limits disabled'))
            if hard_breach and first_fail_time is None:
                first_fail_index = len(trades)
                first_fail_time = timestamp
                first_fail_reason = hard_reason
                trading_halted = True

            internal_stop, internal_reason = (self.compliance_guard.check_internal_stop(
                min(self.equity, worst_equity), daily_start_balance, peak_equity
            ) if self.enforce_limits and self.enforce_internal_stop else (False, 'internal stop disabled'))
            if internal_stop and first_internal_stop_time is None:
                first_internal_stop_time = timestamp
                first_internal_stop_reason = internal_reason
                trading_halted = True

            # Force reconciliation between the trade ledger and final equity.
            if position == last_index and open_trade is not None:
                trades.append(
                    self._close_trade(open_trade, float(row["close"]), timestamp, "end_of_data")
                )
                open_trade = None
                self.equity = self.balance

            # A hard failure is permanent for scoring. After it, research mode
            # bypasses account-level entry/soft-stop gates so we can observe the
            # remaining strategy path. SL/TP, costs and position sizing still apply.
            stress_mode = self.continue_after_failure and first_fail_time is not None
            # Stateful strategies must observe every completed bar.
            signal = self.strategy.generate_signal(row) if position < last_index else None
            if (
                signal
                and open_trade is None
                and len(pending_orders) < self.max_pending_orders
                and (stress_mode or (trading_state == "safe" and not trading_halted))
            ):
                intent = OrderIntent.from_signal(signal)
                intent.validate_price_geometry()
                order_id = f"ORD-{next_order_number:06d}"
                next_order_number += 1
                expires_after = (
                    position + intent.valid_for_bars
                    if intent.valid_for_bars is not None
                    else None
                )
                pending_orders.append(PendingOrder(
                    order_id=order_id,
                    intent=intent,
                    signal_time=timestamp,
                    created_index=position,
                    eligible_index=position + 1,
                    expires_after_index=expires_after,
                ))
                order_events.append({
                    "time": timestamp, "order_id": order_id, "event": "submitted",
                    "order_type": intent.order_type.value,
                    "valid_for_bars": intent.valid_for_bars,
                })

            equity_curve.append(
                {
                    "time": timestamp,
                    "equity": self.equity,
                    "balance": self.balance,
                    "hard_breach": first_fail_time is not None,
                    "internal_stop": first_internal_stop_time is not None,
                    # Backward-compatible column used by the existing dashboard.
                    "is_failed": first_fail_time is not None,
                    "stress_mode": stress_mode,
                }
            )

        df_trades = pd.DataFrame(trades)
        df_trades.attrs.update(
            {
                "first_fail_index": first_fail_index,
                "first_fail_time": first_fail_time,
                "first_fail_reason": first_fail_reason,
                "first_internal_stop_time": first_internal_stop_time,
                "first_internal_stop_reason": first_internal_stop_reason,
                "equity_curve": pd.DataFrame(equity_curve),
                "initial_balance": self.initial_balance,
                "ending_balance": self.balance,
                "continue_after_failure": self.continue_after_failure,
                "enforce_limits": self.enforce_limits,
                "enforce_internal_stop": self.enforce_internal_stop,
                "order_events": pd.DataFrame(order_events),
                "pending_orders_at_end": len(pending_orders),
            }
        )
        return df_trades
