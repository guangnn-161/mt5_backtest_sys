"""Auditable report statistics. Amounts are in account currency; rates in percent."""
from __future__ import annotations

import math
import numpy as np
import pandas as pd


def finite(value):
    return float(value) if value is not None and np.isfinite(value) else None


def drawdowns(values, initial):
    values = np.r_[initial, np.asarray(values, dtype=float)]
    peaks = np.maximum.accumulate(values)
    amounts = peaks - values
    ratios = np.divide(amounts * 100, peaks, out=np.zeros_like(amounts), where=peaks > 0)
    cash_idx, pct_idx = int(np.argmax(amounts)), int(np.argmax(ratios))
    return {
        'absolute': max(0., initial - float(values.min())),
        'maximal': float(amounts[cash_idx]),
        'maximal_pct': float(ratios[cash_idx]),
        'relative_pct': float(ratios[pct_idx]),
        'relative_amount': float(amounts[pct_idx]),
    }


def local_times(values, source_timezone='UTC', report_timezone='UTC'):
    times = pd.to_datetime(values)
    if times.dt.tz is None:
        times = times.dt.tz_localize(source_timezone)
    return times.dt.tz_convert(report_timezone)


def daily_equity(curve, initial, source_timezone='UTC', report_timezone='UTC'):
    if curve is None or curve.empty:
        return pd.DataFrame(columns=['date', 'equity', 'pnl', 'return_pct'])
    times = local_times(curve['time'], source_timezone, report_timezone)
    daily = pd.DataFrame({'date': times.dt.date, 'equity': curve['equity'].to_numpy()})
    daily = daily.groupby('date', sort=True)['equity'].last().reset_index()
    previous = daily.equity.shift(1, fill_value=initial)
    daily['pnl'] = daily.equity - previous
    daily['return_pct'] = daily.pnl / previous.where(previous > 0) * 100
    return daily


def monthly_returns(daily, initial):
    if daily.empty:
        return pd.DataFrame(columns=['month', 'equity', 'pnl', 'return_pct'])
    frame = daily.copy()
    frame['month'] = pd.to_datetime(frame.date).dt.strftime('%Y-%m')
    frame = frame.groupby('month', sort=True).equity.last().reset_index()
    previous = frame.equity.shift(1, fill_value=initial)
    frame['pnl'] = frame.equity - previous
    frame['return_pct'] = frame.pnl / previous.where(previous > 0) * 100
    return frame


def streaks(pnl, positive=True):
    runs, current = [], []
    for value in pnl:
        if (value > 0 if positive else value < 0):
            current.append(float(value))
        elif current:
            runs.append((len(current), sum(current)))
            current = []
    if current:
        runs.append((len(current), sum(current)))
    if not runs:
        return {'longest_count': 0, 'longest_amount': 0., 'largest_amount': 0.,
                'largest_count': 0, 'average_count': 0.}
    longest = max(runs, key=lambda x: (x[0], abs(x[1])))
    largest = max(runs, key=lambda x: abs(x[1]))
    return dict(longest_count=longest[0], longest_amount=longest[1],
                largest_amount=largest[1], largest_count=largest[0],
                average_count=float(np.mean([x[0] for x in runs])))


def calculate_metrics(trade_history, initial_balance=None, equity_curve=None,
                      source_timezone='UTC', report_timezone='UTC'):
    trades = pd.DataFrame(trade_history).copy()
    if not trades.empty and 'exit_time' in trades:
        trades = trades.sort_values('exit_time', kind='stable').reset_index(drop=True)
    pnl = trades['pnl_usd'].astype(float).to_numpy() if not trades.empty else np.array([])
    n = len(pnl)
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    gross_profit, gross_loss = float(wins.sum()), float(losses.sum())
    ratio = gross_profit / abs(gross_loss) if gross_loss else None
    metrics = {
        'total_trades': n, 'net_profit': float(pnl.sum()),
        'gross_profit': gross_profit, 'gross_loss': gross_loss,
        'profit_factor': ratio,
        'profit_factor_status': 'no_losses' if n and gross_loss == 0 else 'defined' if ratio is not None else 'no_trades',
        'win_rate_pct': len(wins) / n * 100 if n else 0.,
        'profit_trades': len(wins), 'loss_trades': len(losses),
        'loss_rate_pct': len(losses) / n * 100 if n else 0.,
        'breakeven_trades': int((pnl == 0).sum()),
        'largest_profit_trade': float(wins.max()) if len(wins) else None,
        'largest_loss_trade': float(losses.min()) if len(losses) else None,
        'average_profit_trade': float(wins.mean()) if len(wins) else None,
        'average_loss_trade': float(losses.mean()) if len(losses) else None,
        'expected_payoff': float(pnl.mean()) if n else None,
        'payoff_ratio': float(wins.mean() / abs(losses.mean())) if len(wins) and len(losses) else None,
        'median_trade': float(np.median(pnl)) if n else None,
        'trade_pnl_std': float(pnl.std(ddof=1)) if n > 1 else None,
        'commission_usd': float(trades.commission_usd.sum()) if 'commission_usd' in trades else None,
        'total_volume_lots': float(trades['size'].sum()) if 'size' in trades else None,
        'post_failure_trades': int(trades.post_failure_entry.sum()) if 'post_failure_entry' in trades else 0,
    }
    for side, name in [('BUY', 'long'), ('SELL', 'short')]:
        subset = trades[trades['type'] == side] if 'type' in trades else pd.DataFrame()
        metrics[name + '_trades'] = len(subset)
        metrics[name + '_won_pct'] = float((subset.pnl_usd > 0).mean() * 100) if len(subset) else None
    for positive, name in [(True, 'wins'), (False, 'losses')]:
        metrics['consecutive_' + name] = streaks(pnl, positive)
    metrics['max_consecutive_losses_count'] = metrics['consecutive_losses']['longest_count']
    metrics['max_consecutive_wins_count'] = metrics['consecutive_wins']['longest_count']

    returns = trades.pnl_pct.to_numpy(dtype=float) / 100 if 'pnl_pct' in trades else np.array([])
    metrics['ahpr'] = finite(1 + returns.mean()) if len(returns) else None
    metrics['ghpr'] = finite(np.exp(np.log1p(returns).mean())) if len(returns) and np.all(returns > -1) else None
    signs = np.sign(pnl[pnl != 0])
    nw, nl = int((signs > 0).sum()), int((signs < 0).sum())
    z = None
    if nw and nl and len(signs) > 2:
        total = nw + nl
        runs = 1 + int((signs[1:] != signs[:-1]).sum())
        expectation = 1 + 2 * nw * nl / total
        variance = 2 * nw * nl * (2 * nw * nl - total) / (total**2 * (total - 1))
        if variance > 0:
            z = (runs - expectation) / math.sqrt(variance)
    metrics['runs_z_score'] = z
    metrics['runs_p_value'] = math.erfc(abs(z) / math.sqrt(2)) if z is not None else None

    duration = ((pd.to_datetime(trades.exit_time) - pd.to_datetime(trades.entry_time)).dt.total_seconds()
                if {'entry_time', 'exit_time'} <= set(trades.columns) else pd.Series(dtype=float))
    for name, value in [('min', duration.min()), ('mean', duration.mean()), ('max', duration.max())]:
        metrics['holding_seconds_' + name] = finite(value)

    for key in ['initial_balance', 'ending_balance', 'ending_equity', 'return_pct', 'recovery_factor',
                'sharpe_daily_annualized', 'sortino_daily_annualized', 'best_day', 'worst_day',
                'lr_correlation', 'lr_standard_error', 'balance_drawdown', 'equity_drawdown']:
        metrics[key] = None
    metrics['sampled_days'] = 0
    if initial_balance is not None:
        initial = float(initial_balance)
        if not math.isfinite(initial) or initial <= 0:
            raise ValueError('initial_balance must be finite and positive')
        closing_balances = initial + np.cumsum(pnl)
        balance_values = (equity_curve.balance.to_numpy() if equity_curve is not None
                          and 'balance' in equity_curve else closing_balances)
        bd = drawdowns(balance_values, initial)
        metrics.update(initial_balance=initial, ending_balance=float(initial + pnl.sum()),
                       return_pct=float(pnl.sum() / initial * 100), balance_drawdown=bd)
        eq_values = equity_curve.equity.to_numpy() if equity_curve is not None and 'equity' in equity_curve else None
        if eq_values is not None:
            ed = drawdowns(eq_values, initial)
            metrics.update(equity_drawdown=ed, ending_equity=float(eq_values[-1]) if len(eq_values) else initial,
                           recovery_factor=finite(pnl.sum() / ed['maximal']) if ed['maximal'] else None)
        y = np.r_[initial, closing_balances]
        if len(y) > 2 and np.ptp(y) > 0:
            x = np.arange(len(y))
            predicted = np.polyval(np.polyfit(x, y, 1), x)
            metrics['lr_correlation'] = finite(np.corrcoef(x, y)[0, 1])
            metrics['lr_standard_error'] = float(np.sqrt(np.sum((y - predicted)**2) / (len(y) - 2)))
        daily = daily_equity(equity_curve, initial, source_timezone, report_timezone)
        if len(daily):
            metrics.update(sampled_days=len(daily), best_day=float(daily.pnl.max()), worst_day=float(daily.pnl.min()))
            r = daily.return_pct.to_numpy() / 100
            if len(r) > 1 and np.isfinite(r).all() and r.std(ddof=1) > 1e-12:
                metrics['sharpe_daily_annualized'] = float(np.sqrt(252) * r.mean() / r.std(ddof=1))
            downside = np.sqrt(np.mean(np.minimum(r, 0)**2))
            if len(r) > 1 and np.isfinite(r).all() and downside > 1e-12:
                metrics['sortino_daily_annualized'] = float(np.sqrt(252) * r.mean() / downside)
    return metrics
