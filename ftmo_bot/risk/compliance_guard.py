# File: risk/compliance_guard.py
import yaml
from pathlib import Path


class ComplianceGuard:
    def __init__(self, ftmo_rules_path: Path | dict, risk_params_path: Path | dict):
        # Research jobs resolve an instrument profile in memory; the original
        # YAML-path API stays supported for main.py and existing callers.
        if isinstance(ftmo_rules_path, dict):
            self.ftmo_rules = dict(ftmo_rules_path)
        else:
            with open(ftmo_rules_path, 'r', encoding='utf-8') as f:
                self.ftmo_rules = yaml.safe_load(f)
        if isinstance(risk_params_path, dict):
            self.risk_params = dict(risk_params_path)
        else:
            with open(risk_params_path, 'r', encoding='utf-8') as f:
                self.risk_params = yaml.safe_load(f)

        # Đọc initial_balance THẬT từ config, không hard-code
        self.initial_balance = float(self.ftmo_rules["account_size"])

        # Keep FTMO hard limits separate from the operator's earlier safety stop.
        self.hard_daily_loss_pct = float(
            self.ftmo_rules["max_daily_loss_pct"])
        self.hard_total_dd_pct = float(
            self.ftmo_rules["max_total_loss_pct"])
        daily_buffer = self.risk_params.get("daily_loss_buffer_pct", 0.0)
        total_buffer = self.risk_params.get("total_loss_buffer_pct", 0.0)

        self.max_daily_loss_pct = self.hard_daily_loss_pct - daily_buffer
        self.max_total_dd_pct = self.hard_total_dd_pct - total_buffer
        if self.max_daily_loss_pct <= 0 or self.max_total_dd_pct <= 0:
            raise ValueError("Loss buffers must be smaller than the FTMO hard limits")

        # Đọc đúng loại drawdown từ config thay vì hard-code kiểu trailing
        self.drawdown_type = self.ftmo_rules.get(
            "drawdown_type", "trailing_from_peak")

    def _loss_metrics(self, current_equity, daily_start_balance, peak_equity):
        daily_loss_usd = daily_start_balance - current_equity
        daily_loss_pct = (daily_loss_usd / self.initial_balance) * 100
        if self.drawdown_type == "trailing_from_peak":
            dd_usd = peak_equity - current_equity
        else:
            dd_usd = self.initial_balance - current_equity
        total_dd_pct = (dd_usd / self.initial_balance) * 100
        return max(0.0, daily_loss_pct), max(0.0, total_dd_pct)

    def _check_limits(
        self,
        current_equity,
        daily_start_balance,
        peak_equity,
        daily_limit,
        total_limit,
        label,
    ):
        daily_loss_pct, total_dd_pct = self._loss_metrics(
            current_equity, daily_start_balance, peak_equity
        )
        if daily_loss_pct >= daily_limit:
            return True, (
                f"{label} - Max Daily Loss: {daily_loss_pct:.2f}% "
                f"(limit: {daily_limit:.2f}%)"
            )
        if total_dd_pct >= total_limit:
            return True, (
                f"{label} - Max Total Drawdown ({self.drawdown_type}): "
                f"{total_dd_pct:.2f}% (limit: {total_limit:.2f}%)"
            )
        return False, "OK"

    def check_hard_violation(
        self, current_equity: float, daily_start_balance: float, peak_equity: float
    ):
        return self._check_limits(
            current_equity,
            daily_start_balance,
            peak_equity,
            self.hard_daily_loss_pct,
            self.hard_total_dd_pct,
            "HARD BREACH",
        )

    def check_internal_stop(
        self, current_equity: float, daily_start_balance: float, peak_equity: float
    ):
        return self._check_limits(
            current_equity,
            daily_start_balance,
            peak_equity,
            self.max_daily_loss_pct,
            self.max_total_dd_pct,
            "INTERNAL STOP",
        )

    def check_violation(
        self, current_equity: float, daily_start_balance: float, peak_equity: float
    ):
        """Backward-compatible alias for the actual FTMO hard-limit check."""
        return self.check_hard_violation(
            current_equity, daily_start_balance, peak_equity
        )

    def evaluate_entry(self, current_equity: float, daily_start_balance: float, peak_equity: float):
        """
        Method này TRƯỚC ĐÂY bị RiskManager.evaluate() gọi mà không tồn tại (xem Bug #2).
        Cùng logic với check_violation — đặt tên riêng để ngữ cảnh gọi rõ ràng hơn
        (kiểm tra TRƯỚC khi cho phép vào lệnh mới, không phải phát hiện vi phạm sau khi đã xảy ra).
        """
        stopped, reason = self.check_internal_stop(
            current_equity, daily_start_balance, peak_equity
        )
        return not stopped, reason


    def get_trading_state(self, current_equity: float, daily_start_balance: float,
                        peak_equity: float) -> str:
        """
        Trả về 'safe' | 'caution' | 'critical' | 'violated' dựa trên % ngưỡng an toàn đã dùng.
        Dùng % của NGƯỠNG AN TOÀN (đã trừ buffer), không phải % của luật FTMO gốc —
        để nhất quán với các ngưỡng đã cấu hình.
        """
        daily_loss_pct = max(
            0.0, (daily_start_balance - current_equity) / self.initial_balance * 100)

        if self.drawdown_type == "trailing_from_peak":
            dd_pct = max(0.0, (peak_equity - current_equity) /
                        self.initial_balance * 100)
        else:
            dd_pct = max(0.0, (self.initial_balance - current_equity) /
                        self.initial_balance * 100)

        daily_ratio = daily_loss_pct / \
            self.max_daily_loss_pct if self.max_daily_loss_pct > 0 else 0
        dd_ratio = dd_pct / self.max_total_dd_pct if self.max_total_dd_pct > 0 else 0
        worst_ratio = max(daily_ratio, dd_ratio)

        caution_th = self.risk_params.get("caution_threshold_ratio", 0.6)
        critical_th = self.risk_params.get("critical_threshold_ratio", 0.9)

        if worst_ratio >= 1.0:
            return "violated"
        elif worst_ratio >= critical_th:
            return "critical"
        elif worst_ratio >= caution_th:
            return "caution"
        return "safe"
