import numpy as np
import yaml
from pathlib import Path


class MonteCarloFTMO:
    def __init__(self, ftmo_config_path: str, avg_trades_per_day: int = 3):
        """
        avg_trades_per_day: Tham số giả định số lệnh trung bình mỗi ngày để mô phỏng mốc reset Daily Loss.
        """
        with open(ftmo_config_path, 'r') as f:
            self.rules = yaml.safe_load(f)

        self.target_pct = self.rules["profit_target_pct"]
        self.max_dd_pct = self.rules["max_total_loss_pct"]
        self.max_daily_loss_pct = self.rules["max_daily_loss_pct"]

        # Kiểm tra loại Drawdown (FTMO tiêu chuẩn thường là static_from_initial)
        self.dd_type = self.rules.get("drawdown_type", "static_from_initial")
        self.avg_trades_per_day = avg_trades_per_day

    def run_simulation(self, trade_pnl_pct: list, n_sims: int = 10000, seed: int = 42) -> dict:
        """
        Thực thi Monte Carlo Simulation.
        trade_pnl_pct: list hoặc array chứa % PnL của từng lệnh (ví dụ: [1.2, -0.5, 0.8, -0.5...]).
        """
        if not trade_pnl_pct:
            raise ValueError(
                "Danh sách lệnh trống. Cần chạy Backtest Engine trước.")

        rng = np.random.default_rng(seed)

        outcomes = {
            "pass": 0,
            "fail_max_dd": 0,
            "fail_daily_loss": 0,
            "timeout": 0
        }

        max_drawdowns = []
        days_to_pass = []

        for _ in range(n_sims):
            # Bootstrap: Lấy mẫu ngẫu nhiên CÓ HOÀN LẠI (Tạo kịch bản chuỗi thua khắc nghiệt hơn lịch sử)
            shuffled_trades = rng.choice(
                trade_pnl_pct, size=len(trade_pnl_pct), replace=True)

            equity = 100.0  # Bắt đầu với 100% balance
            peak_equity = 100.0
            daily_start_equity = 100.0

            sim_status = "timeout"
            min_dd_this_sim = 0.0

            for trade_idx, pnl in enumerate(shuffled_trades):
                # 1. Mô phỏng chuyển ngày (Reset mốc Daily Loss)
                if trade_idx > 0 and trade_idx % self.avg_trades_per_day == 0:
                    daily_start_equity = equity

                # 2. Cập nhật Equity sau lệnh
                equity *= (1 + pnl / 100)

                # Cập nhật đỉnh Equity nếu là dạng Trailing Drawdown
                if equity > peak_equity:
                    peak_equity = equity

                # 3. Tính toán Drawdown hiện tại
                if self.dd_type == "trailing_from_peak":
                    current_dd = ((peak_equity - equity) / peak_equity) * 100
                else:  # static_from_initial
                    current_dd = ((100.0 - equity) / 100.0) * 100

                if current_dd > min_dd_this_sim:
                    min_dd_this_sim = current_dd

                # 4. KIỂM TRA GUARD (Luật tử hình)
                # Vi phạm Max Total Drawdown
                if current_dd >= self.max_dd_pct:
                    sim_status = "fail_max_dd"
                    break

                # Vi phạm Daily Loss
                daily_loss = ((daily_start_equity - equity) /
                              daily_start_equity) * 100
                if daily_loss >= self.max_daily_loss_pct:
                    sim_status = "fail_daily_loss"
                    break

                # 5. Kiểm tra mục tiêu Pass quỹ
                if equity >= 100.0 + self.target_pct:
                    sim_status = "pass"
                    days_to_pass.append(
                        (trade_idx + 1) / self.avg_trades_per_day)
                    break

            # Ghi nhận kết quả cuối cùng của 1 kịch bản (1 vũ trụ)
            outcomes[sim_status] += 1
            max_drawdowns.append(min_dd_this_sim)

        # Trích xuất và định dạng kết quả thống kê
        avg_days = np.mean(days_to_pass) if days_to_pass else 0.0

        return {
            "p_pass": round((outcomes["pass"] / n_sims) * 100, 2),
            "p_fail_max_dd": round((outcomes["fail_max_dd"] / n_sims) * 100, 2),
            "p_fail_daily_loss": round((outcomes["fail_daily_loss"] / n_sims) * 100, 2),
            "p_timeout": round((outcomes["timeout"] / n_sims) * 100, 2),
            "max_dd_mean": round(float(np.mean(max_drawdowns)), 2),
            # Rủi ro đuôi (Kịch bản tệ nhất)
            "max_dd_p95": round(float(np.percentile(max_drawdowns, 95)), 2),
            "avg_days_to_pass": round(float(avg_days), 1)
        }
