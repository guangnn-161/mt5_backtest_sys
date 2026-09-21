from strategy.simplersi import SimpleRSIStrategy
from strategy.momentum import MomentumStrategy
from strategy.triple_momentum import TripleMomentumStrategy
from tools.experiment_logger import log_experiment
from tools.analyzer import generate_mt5_report, save_report_and_trades
from backtest.monte_carlo import MonteCarloFTMO
from backtest.engine import BacktestEngine
from risk.risk_manager import RiskManager
from risk.compliance_guard import ComplianceGuard
import pandas as pd
import yaml
import sys
from pathlib import Path
from backtest.walk_forward import run_rolling_window_backtest, summarize

# =====================================================================
# [ĐIỂM ĐIỀU KHIỂN DUY NHẤT]
# Bạn chỉ cần thay đổi tên chiến lược ở đây để chuyển đổi toàn bộ hệ thống
# =====================================================================
STRATEGY_NAME = "simplersi"  # Tùy chọn: "momentum", "simplersi", v.v.
# =====================================================================

# Tự động xác định thư mục gốc của dự án
ROOT_DIR = Path(__file__).parent
sys.path.append(str(ROOT_DIR))


# --- ĐĂNG KÝ CÁC CHIẾN LƯỢC VÀO ĐÂY KHI BẠN TẠO MỚI ---

STRATEGY_REGISTRY = {
    "momentum": MomentumStrategy,
    "simplersi": SimpleRSIStrategy,
    'triplemomentum': TripleMomentumStrategy
}
# -------------------------------------------------------


def load_data(filepath: Path) -> pd.DataFrame:
    df = pd.read_csv(filepath)
    df['time'] = pd.to_datetime(df['time'])
    return df


def main():
    print(
        f"[*] Đang khởi chạy luồng kiểm định cho chiến lược: [{STRATEGY_NAME.upper()}]")

    # 1. Tự động tìm file cấu hình tương ứng (VD: simplersi_params.yaml, nếu không có sẽ lấy strategy_params.yaml)
    config_path = ROOT_DIR / "configs" / f"{STRATEGY_NAME}_params.yaml"
    if not config_path.exists():
        config_path = ROOT_DIR / "configs" / "strategy_params.yaml"

    with open(config_path, 'r') as f:
        strat_params = yaml.safe_load(f)

    raw_df = load_data(ROOT_DIR / "data" / "raw" / "xauusdm_m5.csv")
    print(f"[*] Đã nạp {len(raw_df)} nến dữ liệu.")

    # 2. Khởi tạo các module lõi
    guard = ComplianceGuard(ROOT_DIR / "configs" / "ftmo_rules.yaml",
                            ROOT_DIR / "configs" / "risk_params.yaml")
    risk = RiskManager(ROOT_DIR / "configs" / "risk_params.yaml")

    # Tự động lấy Class chiến lược từ Sổ đăng ký dựa trên biến STRATEGY_NAME ở đầu file
    if STRATEGY_NAME not in STRATEGY_REGISTRY:
        raise ValueError(
            f"[!] Lỗi: Chiến lược '{STRATEGY_NAME}' chưa được đăng ký trong STRATEGY_REGISTRY của main.py!")

    StrategyClass = STRATEGY_REGISTRY[STRATEGY_NAME]
    strategy = StrategyClass(strat_params)

    # 3. Tự động nạp chỉ báo (Indicator) của chiến lược
    df = strategy.prepare_data(raw_df.copy())

    # 4. Chạy Backtest Engine
    print("[*] Đang chạy Backtest Engine...")
    engine = BacktestEngine(df, strategy, risk, guard)
    df_trades = engine.run()
    trade_history = df_trades.to_dict('records') if not df_trades.empty else []

    if not trade_history:
        print("[!] Backtest không sinh ra lệnh nào. Hãy kiểm tra lại logic Strategy.")

    # 5. Phân tích kết quả chuẩn MT5
    metrics = generate_mt5_report(trade_history)
    print(
        f"[*] Kết quả thô: Lợi nhuận {metrics.get('net_profit', 0)}$ | Winrate: {metrics.get('win_rate_pct', 0)}% | Tổng lệnh: {metrics.get('total_trades', 0)}")

    # 7. Rolling-window: rủi ro chế độ thị trường, độc lập với Monte Carlo reshuffle.
    print("[*] Đang chạy Rolling-window Robustness Test (30 ngày, bước 5 ngày)...")
    rolling_results = run_rolling_window_backtest(
        df=raw_df,
        strategy_factory=lambda: StrategyClass(strat_params),
        ftmo_rules_path=ROOT_DIR / "configs" / "ftmo_rules.yaml",
        risk_params_path=ROOT_DIR / "configs" / "risk_params.yaml",
        window_days=30,
        step_days=5,
    )
    rolling_metrics = summarize(rolling_results)
    print(f"[*] Rolling Window: {rolling_metrics}")

    # Monte Carlo is optional when no trades exist; rolling always runs first.
    mc_metrics = {"status": "skipped", "reason": "no_trades"}
    if trade_history:
        print("[*] Đang chạy Monte Carlo (10,000 kịch bản)...")
        mc = MonteCarloFTMO(ROOT_DIR / "configs" / "ftmo_rules.yaml")
        mc_metrics = mc.run_simulation(
            df_trades[['exit_time', 'pnl_pct']], n_sims=10000)
        mc_metrics["source_scope"] = "full_simulation_including_post_failure"
        print(f"[*] Monte Carlo p_pass: {mc_metrics['p_pass']}%")

    # 8. Ghi Log và Lưu Báo cáo tự động theo tên chiến lược
    combined_metrics = {
        **metrics,
        "initial_balance": guard.initial_balance,
        "simulation_scope": "full_history_including_post_failure",
        "first_fail_time": (
            str(df_trades.attrs['first_fail_time'])
            if df_trades.attrs.get('first_fail_time') is not None else None
        ),
        "first_fail_reason": df_trades.attrs.get('first_fail_reason'),
        "post_failure_trades": sum(
            bool(trade.get('post_failure_entry', False)) for trade in trade_history
        ),
        "monte_carlo": mc_metrics,
        "rolling_window": rolling_metrics,
    }

    run_id = log_experiment(params=strat_params, metrics=combined_metrics,
                            notes=f"Tự động chạy chiến lược {STRATEGY_NAME}")

    # Tự động tạo thư mục và lưu report vào reports/<STRATEGY_NAME>/
    save_report_and_trades(STRATEGY_NAME, run_id, trade_history, combined_metrics, df_trades)
    rolling_results.to_csv(
        ROOT_DIR / "reports" / STRATEGY_NAME / f"{run_id}_rolling_windows.csv",
        index=False,
    )
    print(
        f"[*] Hoàn tất! Báo cáo, dữ liệu lệnh và Dashboard đã lưu tại: reports/{STRATEGY_NAME}/")
if __name__ == "__main__":
    main()
