
import pandas as pd
from pathlib import Path
from datetime import timedelta
from itertools import product
import json

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
            # Rolling windows answer the FTMO-constrained challenge question.
            # The published hard limits, rather than an internal buffer, end entries.
            continue_after_failure=False,
            enforce_internal_stop=False,
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


def _set_param(params: dict, dotted_key: str, value) -> None:
    target = params
    pieces = dotted_key.split('.')
    for piece in pieces[:-1]:
        target = target.setdefault(piece, {})
        if not isinstance(target, dict):
            raise ValueError(f'Parameter path is not a mapping: {dotted_key}')
    target[pieces[-1]] = value


def expand_parameter_grid(base_params: dict, parameter_grid: dict | None) -> list[dict]:
    """Expand a declarative dotted-key grid without introducing an optimizer dependency."""
    grid = parameter_grid or {}
    if not isinstance(grid, dict):
        raise ValueError('walk-forward parameter_grid must be a mapping of dotted key to values')
    if not grid:
        return [dict(base_params)]
    keys = sorted(grid)
    values = []
    for key in keys:
        candidates = grid[key]
        if not isinstance(candidates, list) or not candidates:
            raise ValueError(f'Parameter grid {key} must be a non-empty list')
        values.append(candidates)
    result = []
    for combination in product(*values):
        params = json.loads(json.dumps(base_params))
        for key, value in zip(keys, combination):
            _set_param(params, key, value)
        result.append(params)
    return result


def _context_for_window(df, start_position, end_position, warmup_bars):
    context_start = max(0, start_position - warmup_bars)
    return df.iloc[context_start:end_position + 1].copy(), df.iloc[start_position]['time']


def _run_window(df, start_position, end_position, strategy_class, params,
                ftmo_rules, risk_params, warmup_bars):
    context, trading_start = _context_for_window(df, start_position, end_position, warmup_bars)
    strategy = strategy_class(dict(params))
    prepared = strategy.prepare_data(context)
    engine = BacktestEngine(
        prepared, strategy, RiskManager(risk_params), ComplianceGuard(ftmo_rules, risk_params),
        trading_start_time=trading_start,
        continue_after_failure=False,
        enforce_internal_stop=False,
    )
    return engine.run()


def run_walk_forward_backtest(
    df: pd.DataFrame,
    strategy_class,
    base_params: dict,
    ftmo_rules: dict | Path,
    risk_params: dict | Path,
    *, train_days: int = 180, test_days: int = 30, step_days: int = 30,
    warmup_bars: int = 300, parameter_grid: dict | None = None,
) -> pd.DataFrame:
    """Chronological train-select-test evaluation with no test-period parameter use.

    The chosen candidate is the best *non-hard-breaching* configuration by
    train net P/L; any hard breach ranks below a non-breaching candidate. Test
    windows are fresh account simulations and never feed back into selection.
    """
    if min(train_days, test_days, step_days) <= 0 or warmup_bars < 0:
        raise ValueError('train_days/test_days/step_days must be positive and warmup_bars non-negative')
    frame = df.sort_values('time').reset_index(drop=True).copy()
    frame['time'] = pd.to_datetime(frame['time'])
    days = sorted(frame['time'].dt.date.unique())
    candidates = expand_parameter_grid(base_params, parameter_grid)
    results = []
    for start_idx in range(0, len(days), step_days):
        train_start = days[start_idx]
        train_end = train_start + timedelta(days=train_days - 1)
        test_start = train_end + timedelta(days=1)
        test_end = test_start + timedelta(days=test_days - 1)
        if test_end > days[-1]:
            break
        train_positions = frame.index[(frame.time.dt.date >= train_start) & (frame.time.dt.date <= train_end)]
        test_positions = frame.index[(frame.time.dt.date >= test_start) & (frame.time.dt.date <= test_end)]
        # Do not impose an M5-specific bar count: D1/W1 research naturally has
        # fewer rows. Individual strategies may still decline to trade until
        # their own indicators have enough warm-up history.
        if len(train_positions) < 2 or len(test_positions) < 2:
            continue
        scored = []
        for params in candidates:
            trades = _run_window(frame, int(train_positions[0]), int(train_positions[-1]),
                                 strategy_class, params, ftmo_rules, risk_params, warmup_bars)
            pnl = float(trades.pnl_usd.sum()) if not trades.empty else 0.0
            breached = trades.attrs.get('first_fail_time') is not None
            scored.append((not breached, pnl, params, trades))
        _, train_pnl, selected_params, train_trades = max(scored, key=lambda item: (item[0], item[1]))
        test_trades = _run_window(frame, int(test_positions[0]), int(test_positions[-1]),
                                  strategy_class, selected_params, ftmo_rules, risk_params, warmup_bars)
        test_pnl = float(test_trades.pnl_usd.sum()) if not test_trades.empty else 0.0
        results.append({
            'train_start': train_start, 'train_end': train_end,
            'test_start': test_start, 'test_end': test_end,
            'candidate_count': len(candidates), 'selected_params_json': json.dumps(selected_params, sort_keys=True),
            'train_net_profit': train_pnl, 'train_hard_breach': train_trades.attrs.get('first_fail_time') is not None,
            'test_net_profit': test_pnl, 'test_total_trades': len(test_trades),
            'test_hard_breach': test_trades.attrs.get('first_fail_time') is not None,
            'test_first_fail_time': test_trades.attrs.get('first_fail_time'),
        })
    return pd.DataFrame(results)


def summarize_walk_forward(results: pd.DataFrame) -> dict:
    if results.empty:
        return {'num_windows': 0}
    return {
        'num_windows': int(len(results)),
        'oos_net_profit': float(results.test_net_profit.sum()),
        'oos_profitable_windows_pct': round(float((results.test_net_profit > 0).mean() * 100), 2),
        'oos_hard_breach_windows_pct': round(float(results.test_hard_breach.mean() * 100), 2),
        'candidate_count_per_window': int(results.candidate_count.max()),
    }
