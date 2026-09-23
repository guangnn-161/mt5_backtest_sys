"""Static, exportable figures for the offline research report."""
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.ticker import FuncFormatter, MaxNLocator
from matplotlib.colors import TwoSlopeNorm

from tools.backtest_metrics import local_times

TEAL, ORANGE, RED, NAVY = '#087f8c', '#e89938', '#cf5360', '#182b43'
MUTED, GRID = '#6b7c8e', '#e4eaf0'
OUTCOMES = {'pass': TEAL, 'fail_daily': RED, 'fail_total': '#933f52',
            'internal_stop': ORANGE, 'timeout': '#a9b5c3'}


def figure(title, subtitle, rows=1, cols=1, height=4.5):
    fig = Figure(figsize=(12, height), facecolor='white')
    FigureCanvasAgg(fig)
    axes = np.asarray(fig.subplots(rows, cols, squeeze=False)).ravel()
    fig.suptitle(title, x=.065, y=.985, ha='left', fontsize=17, fontweight='bold', color=NAVY)
    fig.text(.065, 1 - .5 / height, subtitle, fontsize=9, color=MUTED)
    for ax in axes:
        ax.set_facecolor('white')
        ax.spines[['top', 'right']].set_visible(False)
        ax.spines[['left', 'bottom']].set_color(GRID)
        ax.tick_params(colors=MUTED, labelsize=9)
        ax.grid(axis='y', color=GRID, linewidth=.7)
        ax.set_axisbelow(True)
    fig.subplots_adjust(left=.08, right=.97, bottom=.16, top=1 - .9 / height, hspace=.6, wspace=.27)
    return fig, axes


def empty(ax, message):
    ax.text(.5, .5, message, transform=ax.transAxes, ha='center', va='center', color=MUTED)
    ax.set_xticks([])
    ax.set_yticks([])


def money_axis(ax):
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{v:,.0f}'))


def save(fig, path):
    fig.savefig(path, dpi=160, facecolor='white')
    fig.clear()
    return Path(path)


def render_charts(folder, trades, curve, daily, monthly, rolling, metrics, metadata,
                  unconstrained_curve=None):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    initial = metrics['initial_balance']
    tz = metadata.get('report_timezone', 'UTC')
    source_tz = metadata.get('source_timezone', 'UTC')
    currency = metadata.get('currency', 'USD')
    fig, (ax,) = figure(
        'Capital trajectory',
        'Two independent paths from the same signals · teal = FTMO hard-loss constrained · orange = no loss constraint',
    )
    if curve.empty:
        empty(ax, 'No equity observations')
    else:
        times = pd.to_datetime(curve.time)
        values = curve.equity.to_numpy()
        ax.plot(times, values, color=TEAL, lw=1.65, label='FTMO-constrained equity')
        if unconstrained_curve is not None and not unconstrained_curve.empty:
            free_times = pd.to_datetime(unconstrained_curve.time)
            ax.plot(free_times, unconstrained_curve.equity, color=ORANGE, lw=1.4,
                    label='No-loss-constraint equity')
        hard_breach = curve.get('hard_breach', pd.Series(False, index=curve.index)).astype(bool).to_numpy()
        if hard_breach.any():
            first = int(np.flatnonzero(hard_breach)[0])
            ax.axvline(times.iloc[first], color=RED, ls=':', lw=1.3, label='FTMO hard breach')
        if 'balance' in curve:
            ax.plot(times, curve.balance, color=NAVY, lw=.85, alpha=.65, label='Balance')
        ax.axhline(initial, color=MUTED, ls='--', lw=.8, label='Initial deposit')
        ax.legend(loc='lower left', bbox_to_anchor=(0, 1.01), ncol=4,
                  frameon=False, fontsize=8)
        money_axis(ax)
        ax.set_ylabel(currency, color=MUTED, fontsize=9)
        fig.autofmt_xdate(rotation=15)
    paths.append(save(fig, folder / '01_equity_balance.png'))

    result_curve = unconstrained_curve if unconstrained_curve is not None and not unconstrained_curve.empty else curve
    fig, (ax,) = figure('Drawdown profile', 'No-loss-constraint path · peak-to-trough decline · initial deposit included in the running peak')
    if result_curve.empty:
        empty(ax, 'No equity observations')
    else:
        peak = result_curve.equity.cummax().clip(lower=initial)
        dd = (peak - result_curve.equity) / peak * 100
        ax.fill_between(pd.to_datetime(result_curve.time), -dd, 0, color=RED, alpha=.17)
        ax.plot(pd.to_datetime(result_curve.time), -dd, color=RED, lw=1.25, label='No-loss equity drawdown')
        if 'balance' in result_curve:
            bp = result_curve.balance.cummax().clip(lower=initial)
            ax.plot(pd.to_datetime(result_curve.time), -(bp - result_curve.balance) / bp * 100,
                    color=NAVY, lw=.9, label='Balance drawdown')
        ax.set_ylabel('Drawdown (%)', color=MUTED, fontsize=9)
        ax.legend(frameon=False, fontsize=8)
        fig.autofmt_xdate(rotation=15)
    paths.append(save(fig, folder / '02_drawdown.png'))

    fig, axes = figure('Trade economics', 'Net P/L includes recorded commissions · full simulation history', cols=2)
    if trades.empty:
        for ax in axes:
            empty(ax, 'No closed trades')
    else:
        pnl = trades.pnl_usd.astype(float)
        bins = min(40, max(5, int(np.sqrt(len(pnl)))))
        axes[0].hist(pnl, bins=bins, color=TEAL, alpha=.85, edgecolor='white')
        axes[0].axvline(0, color=NAVY, lw=1)
        axes[0].set_xlabel(f'Net P/L ({currency})', color=MUTED)
        axes[0].set_ylabel('Trades', color=MUTED)
        axes[1].bar(np.arange(1, len(pnl) + 1), pnl, color=np.where(pnl >= 0, TEAL, RED), width=1)
        axes[1].set_xlabel('Trade number', color=MUTED)
        axes[1].set_ylabel(f'Net P/L ({currency})', color=MUTED)
        money_axis(axes[1])
    paths.append(save(fig, folder / '03_trade_distribution.png'))

    fig, axes = figure('Calendar & timing', f'Closed-trade P/L grouped by exit time · {tz}', cols=2)
    if trades.empty or 'exit_time' not in trades:
        for ax in axes:
            empty(ax, 'No closed trades')
    else:
        times = local_times(trades.exit_time, source_tz, tz)
        for ax, group, index, label in [(axes[0], times.dt.dayofweek, range(7), 'Day of week'),
                                       (axes[1], times.dt.hour, range(24), 'Hour of exit')]:
            data = trades.pnl_usd.groupby(group).sum().reindex(index, fill_value=0)
            ax.bar(index, data, color=np.where(data >= 0, TEAL, RED))
            ax.set_xlabel(label, color=MUTED)
            money_axis(ax)
        axes[0].set_xticks(range(7), ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'])
        axes[0].set_ylabel(f'Net P/L ({currency})', color=MUTED)
        axes[1].set_xticks(range(0, 24, 3))
    paths.append(save(fig, folder / '04_trade_timing.png'))

    years = sorted(set(int(str(m)[:4]) for m in monthly.month)) if len(monthly) else []
    fig, (ax,) = figure('Monthly return map', 'Month-end equity change · partial months included · missing months are blank',
                        height=max(3.5, 2.5 + .45 * len(years)))
    if not years:
        empty(ax, 'No monthly observations')
    else:
        matrix = np.full((len(years), 12), np.nan)
        for row in monthly.itertuples():
            year, month = map(int, row.month.split('-'))
            matrix[years.index(year), month - 1] = row.return_pct
        limit = max(1., float(np.nanmax(np.abs(matrix)))) if np.isfinite(matrix).any() else 1.
        ax.imshow(matrix, cmap='RdYlGn', norm=TwoSlopeNorm(vmin=-limit, vcenter=0, vmax=limit), aspect='auto')
        ax.set_xticks(range(12), ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'])
        ax.set_yticks(range(len(years)), years)
        ax.grid(False)
        for i in range(len(years)):
            for j in range(12):
                if np.isfinite(matrix[i, j]):
                    ax.text(j, i, f'{matrix[i, j]:+.1f}%', ha='center', va='center', fontsize=9, color=NAVY)
    paths.append(save(fig, folder / '05_monthly_returns.png'))

    fig, axes = figure('Rolling-window robustness', 'Each window starts with a fresh no-loss-constraint account', cols=2)
    if rolling is None or rolling.empty:
        for ax in axes:
            empty(ax, 'No complete rolling windows in this dataset')
    else:
        axes[0].bar(np.arange(len(rolling)), rolling.max_dd_in_window_pct,
                    color=[OUTCOMES.get(s, MUTED) for s in rolling.outcome])
        axes[0].set_xlabel('Window index (chronological)', color=MUTED)
        axes[0].set_ylabel('Max equity drawdown to result (%)', color=MUTED)
        counts = rolling.outcome.value_counts().reindex(OUTCOMES, fill_value=0)
        axes[1].barh(list(counts.index), counts, color=list(OUTCOMES.values()))
        axes[1].set_xlabel('Windows', color=MUTED)
        axes[1].xaxis.set_major_locator(MaxNLocator(integer=True))
    paths.append(save(fig, folder / '06_rolling_windows.png'))

    mc = metrics.get('monte_carlo', {})
    fig, (ax,) = figure('Monte Carlo outcomes', mc.get('method', 'Day-block bootstrap') + ' · empirical resampling, not a live success forecast')
    if mc.get('p_pass') is None:
        empty(ax, 'Monte Carlo unavailable: no closed trades')
    else:
        labels = ['Pass', 'Daily loss', 'Total loss', 'Timeout']
        vals = [mc.get(k, 0) for k in ['p_pass', 'p_fail_daily_loss', 'p_fail_max_dd', 'p_timeout']]
        ax.barh(labels, vals, color=[TEAL, RED, '#933f52', '#a9b5c3'])
        ax.set_xlim(0, 112)
        ax.set_xticks([0, 25, 50, 75, 100])
        ax.set_xlabel('Simulations (%)', color=MUTED)
        for i, value in enumerate(vals):
            ax.text(value + 1, i, f'{value:.2f}%', va='center', color=NAVY, fontsize=10)
    paths.append(save(fig, folder / '07_monte_carlo.png'))
    return paths
