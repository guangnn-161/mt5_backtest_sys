# File: risk/compliance_guard.py
import yaml
from pathlib import Path


class ComplianceGuard:
    def __init__(self, ftmo_rules_path: Path, risk_params_path: Path):
        with open(ftmo_rules_path, 'r', encoding='utf-8') as f:
            self.ftmo_rules = yaml.safe_load(f)
        with open(risk_params_path, 'r', encoding='utf-8') as f:
            self.risk_params = yaml.safe_load(f)

        # Đọc initial_balance THẬT từ config, không hard-code
        self.initial_balance = float(self.ftmo_rules["account_size"])

        # Ngưỡng an toàn = luật FTMO trừ buffer, đọc ĐÚNG key có trong risk_params.yaml
        raw_daily_limit = self.ftmo_rules["max_daily_loss_pct"]
        raw_total_limit = self.ftmo_rules["max_total_loss_pct"]
        daily_buffer = self.risk_params.get("daily_loss_buffer_pct", 0.0)
        total_buffer = self.risk_params.get("total_loss_buffer_pct", 0.0)

        self.max_daily_loss_pct = raw_daily_limit - daily_buffer
        self.max_total_dd_pct = raw_total_limit - total_buffer

        # Đọc đúng loại drawdown từ config thay vì hard-code kiểu trailing
        self.drawdown_type = self.ftmo_rules.get(
            "drawdown_type", "trailing_from_peak")

    def check_violation(self, current_equity: float, daily_start_equity: float, peak_equity: float):
        """
        LƯU Ý: tham số đổi từ *_balance sang *_equity — xem Bug #3 để hiểu vì sao.
        Kiểm tra vi phạm luật FTMO. Trả về (violated: bool, reason: str).
        """
        # 1. Max Daily Loss
        daily_loss_usd = daily_start_equity - current_equity
        daily_loss_pct = (daily_loss_usd / self.initial_balance) * 100

        if daily_loss_pct >= self.max_daily_loss_pct:
            return True, (f"Vi phạm Max Daily Loss: {daily_loss_pct:.2f}% "
                          f"(Ngưỡng an toàn: {self.max_daily_loss_pct}%)")

        # 2. Max Total Drawdown — nhánh theo đúng drawdown_type trong config
        if self.drawdown_type == "trailing_from_peak":
            dd_usd = peak_equity - current_equity
        else:  # static_from_initial
            dd_usd = self.initial_balance - current_equity

        total_dd_pct = (dd_usd / self.initial_balance) * 100

        if total_dd_pct >= self.max_total_dd_pct:
            return True, (f"Vi phạm Max Total Drawdown ({self.drawdown_type}): "
                          f"{total_dd_pct:.2f}% (Ngưỡng an toàn: {self.max_total_dd_pct}%)")

        return False, "OK"

    def evaluate_entry(self, current_equity: float, daily_start_equity: float, peak_equity: float):
        """
        Method này TRƯỚC ĐÂY bị RiskManager.evaluate() gọi mà không tồn tại (xem Bug #2).
        Cùng logic với check_violation — đặt tên riêng để ngữ cảnh gọi rõ ràng hơn
        (kiểm tra TRƯỚC khi cho phép vào lệnh mới, không phải phát hiện vi phạm sau khi đã xảy ra).
        """
        return self.check_violation(current_equity, daily_start_equity, peak_equity)


    def get_trading_state(self, current_equity: float, daily_start_equity: float,
                        peak_equity: float) -> str:
        """
        Trả về 'safe' | 'caution' | 'critical' | 'violated' dựa trên % ngưỡng an toàn đã dùng.
        Dùng % của NGƯỠNG AN TOÀN (đã trừ buffer), không phải % của luật FTMO gốc —
        để nhất quán với các ngưỡng đã cấu hình.
        """
        daily_loss_pct = max(
            0.0, (daily_start_equity - current_equity) / self.initial_balance * 100)

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
