# File: tools/analyzer.py
import json
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path


def generate_mt5_report(trade_history: list) -> dict:
    if not trade_history:
        return {"total_trades": 0, "net_profit": 0, "win_rate_pct": 0}

    df = pd.DataFrame(trade_history)
    total_trades = len(df)
    net_profit = df['pnl_usd'].sum()
    winning_trades = df[df['pnl_usd'] > 0]
    win_rate = (len(winning_trades) / total_trades) * \
        100 if total_trades > 0 else 0

    gross_profit = winning_trades['pnl_usd'].sum()
    losing_trades = df[df['pnl_usd'] < 0]
    gross_loss = abs(losing_trades['pnl_usd'].sum())

    profit_factor = (
        gross_profit / gross_loss) if gross_loss > 0 else float('inf')

    # Tính chuỗi thua liên tiếp dài nhất trong lịch sử backtest thật
    is_loss = (df['pnl_usd'] < 0).astype(int)
    streak_id = (is_loss != is_loss.shift()).cumsum()
    max_consec_losses = int(
        (is_loss.groupby(streak_id).cumsum() * is_loss).max())

    return {
        "total_trades": total_trades,
        "net_profit": round(net_profit, 2),
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "max_consecutive_losses_count": max_consec_losses,
    }


def save_report_and_trades(strategy_name: str, run_id: str, trade_history: list, metrics: dict, df_trades: pd.DataFrame = None):
    root_dir = Path(__file__).parent.parent
    report_dir = root_dir / "reports" / strategy_name
    report_dir.mkdir(parents=True, exist_ok=True)

    # 1. Lưu file JSON báo cáo
    report_data = {
        "run_id": run_id,
        "metrics": metrics
    }
    json_path = report_dir / f"{run_id}_report.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(report_data, f, indent=4, ensure_ascii=False)

    # 2. Lưu file CSV danh sách lệnh
    if trade_history:
        csv_path = report_dir / f"{run_id}_trades.csv"
        pd.DataFrame(trade_history).to_csv(csv_path, index=False)

    # 3. Vẽ Dashboard biểu đồ Equity Curve phân tách trạng thái Fail
    plot_path = report_dir / f"{run_id}_dashboard.jpg"
    plot_equity_curve_split(df_trades, metrics, plot_path,
                            strategy_name.upper(), run_id)

    print(f"[*] Đã lưu toàn bộ báo cáo tại: {report_dir}")


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
