
import pandas as pd
from pathlib import Path
from datetime import timedelta

from backtest.engine import BacktestEngine
from risk.compliance_guard import ComplianceGuard
from risk.risk_manager import RiskManager


def run_rolling_window_backtest(
    df: pd.DataFrame,
    # callable() -> instance chiến lược MỚI (tránh state rò rỉ giữa windows)
    strategy_factory,
    ftmo_rules_path: Path,
    risk_params_path: Path,
    window_days: int = 30,
    step_days: int = 5,
    warmup_bars: int = 300,
) -> pd.DataFrame:
    """
    Chạy backtest độc lập tại nhiều điểm bắt đầu khác nhau trong lịch sử.
    Mỗi window mô phỏng: "nếu FTMO Challenge của tôi bắt đầu đúng ngày này thì sao?"

    Trả về DataFrame, mỗi dòng là kết quả của một window:
    start_date, end_date, outcome
    ('pass'/'fail_daily'/'fail_total'/'internal_stop'/'timeout'),
    days_to_result, max_dd_in_window_pct, num_trades
    """
    
    if window_days <= 0 or step_days <= 0 or warmup_bars < 0:
        raise ValueError("window_days/step_days must be positive and warmup_bars non-negative")

    df = df.sort_values('time').reset_index(drop=True).copy()
    df['date_only'] = pd.to_datetime(df['time']).dt.date

    unique_days = sorted(df['date_only'].unique())
    results = []

    start_idx = 0
    # FTMO duration là ngày lịch: window 30 ngày bắt đầu ngày D kết thúc D+29,
    # dù lịch sử M5 không chứa dữ liệu cuối tuần. step_days vẫn trượt qua các
    # ngày có dữ liệu để mỗi lần bắt đầu đều có một phiên thực tế.
    while start_idx < len(unique_days):
        window_start_day = unique_days[start_idx]
        window_end_day = window_start_day + timedelta(days=window_days - 1)
        if window_end_day > unique_days[-1]:
            break

        window_mask = (
            (df['date_only'] >= window_start_day)
            & (df['date_only'] <= window_end_day)
        )
        window_positions = df.index[window_mask]
        window_df = df.loc[window_mask].drop(columns=['date_only'])

        if len(window_df) < 50:  # quá ít dữ liệu (window cuối bị cắt cụt) -> bỏ qua
            break

        # Include history only to initialise causal indicators and state. The engine
        # explicitly forbids orders before the first bar of the test window.
        context_start = max(0, int(window_positions[0]) - warmup_bars)
        context_end = int(window_positions[-1])
        context_df = df.iloc[context_start:context_end + 1].drop(
            columns=['date_only']
        )

        guard = ComplianceGuard(ftmo_rules_path, risk_params_path)
        risk_mgr = RiskManager(risk_params_path)
        strategy = strategy_factory()
        context_df = strategy.prepare_data(context_df.copy())

        engine = BacktestEngine(
            df=context_df,
            strategy=strategy,
            risk_manager=risk_mgr,
            compliance_guard=guard,
            trading_start_time=window_df.iloc[0]['time'],
        )
        trades = engine.run()

        outcome, days_to_result, result_time = _classify_outcome(
            trades, guard, window_start_day, window_end_day
        )
        equity_curve = trades.attrs.get('equity_curve')
        # Challenge kết thúc ở event pass/fail đầu tiên. Không đưa giao dịch hay
        # drawdown phát sinh sau event đó vào thống kê của window.
        if result_time is not None:
            trades_to_result = trades[trades['exit_time'] <=
                                      result_time] if not trades.empty else trades
            equity_to_result = (
                equity_curve[equity_curve['time'] <= result_time]
                if equity_curve is not None else None
            )
        else:
            trades_to_result = trades
            equity_to_result = equity_curve
        max_dd_pct = _compute_max_dd_pct(
            equity_to_result, guard.initial_balance) if equity_to_result is not None else None

        results.append({
            "start_date": window_start_day,
            "end_date": window_end_day,
            "outcome": outcome,
            "days_to_result": days_to_result,
            "max_dd_in_window_pct": max_dd_pct,
            "num_trades": len(trades_to_result),
            "num_trades_full_simulation": len(trades),
            "post_failure_trades": (
                int(trades['post_failure_entry'].sum()) if not trades.empty else 0
            ),
            "ending_balance_full_simulation": trades.attrs['ending_balance'],
        })

        start_idx += step_days

    return pd.DataFrame(results)


def _classify_outcome(trades: pd.DataFrame, guard: ComplianceGuard, start_day, end_day):
    """Phân loại kết quả 1 window: pass / fail_daily / fail_dd / timeout."""
    profit_target_usd = guard.initial_balance * \
        (guard.ftmo_rules["profit_target_pct"] / 100)
    target_balance = guard.initial_balance + profit_target_usd

    # Một Challenge kết thúc ngay khi đạt target; không được dùng tổng PnL ở
    # cuối window vì phần dữ liệu sau đó có thể làm kết quả pass bị biến thành fail.
    pass_time = None
    if not trades.empty and 'balance' in trades:
        target_hits = trades[trades['balance'] >= target_balance]
        if not target_hits.empty:
            pass_time = target_hits.iloc[0]['exit_time']

    fail_time = trades.attrs.get('first_fail_time')
    internal_stop_time = trades.attrs.get('first_internal_stop_time')
    if (
        pass_time is not None
        and (fail_time is None or pass_time < fail_time)
        and (internal_stop_time is None or pass_time < internal_stop_time)
    ):
        return "pass", (pass_time.date() - start_day).days, pass_time

    if fail_time is not None:
        reason = trades.attrs.get('first_fail_reason', '')
        fail_type = "fail_daily" if "Daily" in reason else "fail_total"
        return fail_type, (fail_time.date() - start_day).days, fail_time

    if internal_stop_time is not None:
        return (
            "internal_stop",
            (internal_stop_time.date() - start_day).days,
            internal_stop_time,
        )

    return "timeout", (end_day - start_day).days, None


def _compute_max_dd_pct(equity_curve: pd.DataFrame, initial_balance: float):
    if equity_curve is None or equity_curve.empty:
        return None
    running_max = equity_curve['equity'].cummax().clip(lower=initial_balance)
    dd = (running_max - equity_curve['equity']) / running_max * 100
    return float(dd.max())


def summarize(results: pd.DataFrame) -> dict:
    """Tổng hợp p_pass qua toàn bộ các điểm bắt đầu lịch sử đã test."""
    if results.empty:
        return {"num_windows": 0}

    n = len(results)
    pass_mask = results['outcome'] == 'pass'
    median_days = results.loc[pass_mask, 'days_to_result'].median()
    worst_dd = results['max_dd_in_window_pct'].max()
    fail_mask = results['outcome'].str.startswith('fail')
    internal_mask = results['outcome'] == 'internal_stop'
    return {
        "num_windows": n,
        "p_pass_across_history": round(float(pass_mask.sum() / n * 100), 2),
        "p_fail_across_history": round(float(fail_mask.sum() / n * 100), 2),
        "p_internal_stop_across_history": round(float(internal_mask.sum() / n * 100), 2),
        "p_timeout_across_history": round(float((results['outcome'] == 'timeout').sum() / n * 100), 2),
        "worst_max_dd_pct": round(float(worst_dd), 2) if pd.notna(worst_dd) else None,
        "median_days_to_pass": float(median_days) if pd.notna(median_days) else None,
    }
