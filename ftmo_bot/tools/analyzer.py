# File: tools/analyzer.py
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path
from tools.backtest_metrics import calculate_metrics
from tools.report_builder import save_run_report


def generate_mt5_report(trade_history: list, initial_balance=None, equity_curve=None,
                        source_timezone='UTC', report_timezone='UTC') -> dict:
    return calculate_metrics(trade_history, initial_balance, equity_curve,
                             source_timezone, report_timezone)


def save_report_and_trades(strategy_name: str, run_id: str, trade_history: list,
                          metrics: dict, df_trades: pd.DataFrame = None, **kwargs):
    return save_run_report(strategy_name, run_id, trade_history, metrics, df_trades, **kwargs)


def plot_equity_curve_split(df_trades: pd.DataFrame, metrics: dict, save_path: Path, strategy_name: str, run_id: str):
    plt.figure(figsize=(12, 8))

    # Lấy dữ liệu Equity Curve được truyền từ engine qua attrs
    if df_trades is not None and hasattr(df_trades, 'attrs') and 'equity_curve' in df_trades.attrs:
        df_eq = df_trades.attrs['equity_curve']
    else:
        # Fallback an toàn nếu không có dữ liệu
        plt.savefig(save_path)
        plt.close()
        return

    if df_eq.empty:
        plt.savefig(save_path)
        plt.close()
        return

    times = df_eq['time']
    equities = df_eq['equity']

    # Xác định điểm cắt khi tài khoản vi phạm quỹ lần đầu
    # Lưu ý: fail_idx là số lượng lệnh, ta cần quy đổi tương đối sang mốc thời gian nến nếu muốn chính xác tuyệt đối,
    # hoặc dùng trực tiếp cờ 'is_failed' có sẵn trong df_eq.

    if 'is_failed' in df_eq.columns and df_eq['is_failed'].any():
        # Chia làm 2 đoạn: Trước khi fail (bình thường) và Sau khi fail (vùng rủi ro)
        normal_mask = df_eq['is_failed'] == False
        failed_mask = df_eq['is_failed'] == True

        # Vẽ đoạn bình thường (Màu xanh dương)
        plt.plot(times[normal_mask], equities[normal_mask],
                 color='tab:blue', linewidth=2, label='Before hard breach')

        # Vẽ đoạn sau khi đã fail quỹ (Màu cam/xám cảnh báo)
        # Nối điểm cuối của đoạn normal với điểm đầu của đoạn failed để đường đi liền mạch không bị đứt đoạn
        fail_start_idx = df_eq[failed_mask].index[0]
        if fail_start_idx > 0:
            bridge_indices = [fail_start_idx - 1, fail_start_idx]
            plt.plot(times.iloc[bridge_indices], equities.iloc[bridge_indices],
                     color='tab:orange', linestyle='--', linewidth=1.5)

        plt.plot(times[failed_mask], equities[failed_mask], color='tab:orange',
                 linestyle='--', linewidth=1.5, label='Post-failure simulation')
        plt.axvline(times.iloc[fail_start_idx], color='tab:red', linestyle=':',
                    label='First hard breach')
    else:
        # Trường hợp không vi phạm lần nào suốt quãng đường test
        plt.plot(times, equities, color='tab:green',
                 linewidth=2, label='No FTMO hard breach')

    if 'internal_stop' in df_eq.columns and df_eq['internal_stop'].any():
        internal_idx = df_eq[df_eq['internal_stop']].index[0]
        plt.scatter(
            times.iloc[internal_idx], equities.iloc[internal_idx],
            color='tab:red', marker='x', s=80, zorder=5,
            label='Internal safety stop',
        )

    initial_balance = float(
        metrics.get('initial_balance', df_trades.attrs.get('initial_balance', 10000))
    )
    plt.axhline(y=initial_balance, color='black', linestyle=':',
                label=f'Initial Balance (${initial_balance:,.0f})')
    plt.title(
        f"Equity Curve - {strategy_name} (Run ID: {run_id})", fontsize=14, fontweight='bold')
    plt.xlabel("Thời gian", fontsize=11)
    plt.ylabel("Số dư tài khoản (USD)", fontsize=11)
    plt.legend(loc='upper left')
    plt.grid(True, alpha=0.3)

    # Thêm bảng thông số tóm tắt dưới biểu đồ
    mc = metrics.get('monte_carlo', {})
    def mc_pct(key):
        return f"{mc[key]}%" if key in mc else "N/A"

    table_data = [
        ["Total Trades", metrics.get(
            'total_trades', 0), "Monte Carlo p_pass", mc_pct('p_pass')],
        ["Win Rate", f"{metrics.get('win_rate_pct', 0)}%",
         "MC Worst Drawdown (p95)", mc_pct('max_dd_p95')],
        ["Net Profit", f"${metrics.get('net_profit', 0)}",
         "MC Fail by Daily Loss", mc_pct('p_fail_daily_loss')],
        ["Profit Factor", metrics.get('profit_factor', 0), "Max Consec Losses",
         metrics.get('max_consecutive_losses_count', 'N/A')]
    ]
    table = plt.table(cellText=table_data, loc='bottom',
                      cellLoc='center', bbox=[0.0, -0.32, 1.0, 0.22])
    table.auto_set_font_size(False)
    table.set_fontsize(10)

    plt.subplots_adjust(bottom=0.25)
    plt.savefig(save_path, dpi=300, bbox_inches='tight')
    plt.close()
