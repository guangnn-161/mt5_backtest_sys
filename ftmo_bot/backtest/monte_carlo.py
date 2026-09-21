"""Day-block bootstrap for FTMO outcome estimates."""

from __future__ import annotations

import numpy as np
import pandas as pd
import yaml
from zoneinfo import ZoneInfo


class MonteCarloFTMO:
    def __init__(self, ftmo_config_path: str, block_days: int = 5):
        with open(ftmo_config_path, "r", encoding="utf-8") as file:
            self.rules = yaml.safe_load(file)

        self.target_pct = float(self.rules["profit_target_pct"])
        self.max_dd_pct = float(self.rules["max_total_loss_pct"])
        self.max_daily_loss_pct = float(self.rules["max_daily_loss_pct"])
        self.dd_type = self.rules.get("drawdown_type", "static_from_initial")
        self.daily_reset_timezone = ZoneInfo(
            self.rules.get("daily_reset_timezone", "Europe/Prague")
        )
        self.block_days = int(block_days)
        if self.block_days <= 0:
            raise ValueError("block_days must be greater than zero")

    def _daily_sequences(self, trades) -> list[list[float]]:
        frame = trades.copy() if isinstance(trades, pd.DataFrame) else pd.DataFrame(trades)
        if frame.empty:
            raise ValueError("Trade history is empty; run the backtest first")
        required = {"exit_time", "pnl_pct"}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(
                "Monte Carlo requires actual trade dates; missing columns: "
                + ", ".join(sorted(missing))
            )

        frame = frame.loc[:, ["exit_time", "pnl_pct"]].copy()
        frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True)
        frame = frame.sort_values("exit_time")
        frame["trading_day"] = (
            frame["exit_time"].dt.tz_convert(self.daily_reset_timezone).dt.date
        )
        return [
            group["pnl_pct"].astype(float).tolist()
            for _, group in frame.groupby("trading_day", sort=True)
        ]

    def _sample_days(self, days, rng):
        """Sample contiguous blocks, retaining order and trade count within each day."""
        sampled = []
        while len(sampled) < len(days):
            start = int(rng.integers(0, len(days)))
            for offset in range(self.block_days):
                sampled.append(days[(start + offset) % len(days)])
                if len(sampled) == len(days):
                    break
        return sampled

    def run_simulation(
        self, trades, n_sims: int = 10000, seed: int = 42
    ) -> dict:
        """Bootstrap real trading-day blocks instead of inventing N trades/day."""
        if n_sims <= 0:
            raise ValueError("n_sims must be greater than zero")
        days = self._daily_sequences(trades)
        rng = np.random.default_rng(seed)

        outcomes = {
            "pass": 0,
            "fail_max_dd": 0,
            "fail_daily_loss": 0,
            "timeout": 0,
        }
        max_drawdowns = []
        days_to_pass = []

        for _ in range(n_sims):
            sampled_days = self._sample_days(days, rng)
            equity = 100.0
            peak_equity = 100.0
            sim_status = "timeout"
            max_dd_this_sim = 0.0

            for day_number, daily_trades in enumerate(sampled_days, start=1):
                daily_start_balance = equity
                for pnl in daily_trades:
                    equity *= 1 + pnl / 100
                    peak_equity = max(peak_equity, equity)

                    if self.dd_type == "trailing_from_peak":
                        current_dd = (peak_equity - equity) / peak_equity * 100
                    else:
                        current_dd = (100.0 - equity) / 100.0 * 100
                    max_dd_this_sim = max(max_dd_this_sim, current_dd)

                    # FTMO percentage limits are based on initial account size.
                    daily_loss = (daily_start_balance - equity) / 100.0 * 100
                    if daily_loss >= self.max_daily_loss_pct:
                        sim_status = "fail_daily_loss"
                        break
                    if current_dd >= self.max_dd_pct:
                        sim_status = "fail_max_dd"
                        break
                    if equity >= 100.0 + self.target_pct:
                        sim_status = "pass"
                        days_to_pass.append(day_number)
                        break
                if sim_status != "timeout":
                    break

            outcomes[sim_status] += 1
            max_drawdowns.append(max_dd_this_sim)

        return {
            "method": f"{self.block_days}-day block bootstrap",
            "source_trading_days": len(days),
            "p_pass": round(outcomes["pass"] / n_sims * 100, 2),
            "p_fail_max_dd": round(outcomes["fail_max_dd"] / n_sims * 100, 2),
            "p_fail_daily_loss": round(
                outcomes["fail_daily_loss"] / n_sims * 100, 2
            ),
            "p_timeout": round(outcomes["timeout"] / n_sims * 100, 2),
            "max_dd_mean": round(float(np.mean(max_drawdowns)), 2),
            "max_dd_p95": round(float(np.percentile(max_drawdowns, 95)), 2),
            "avg_days_to_pass": round(float(np.mean(days_to_pass)), 1)
            if days_to_pass
            else 0.0,
        }
