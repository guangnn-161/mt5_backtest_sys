"""Self-contained HTML report + machine-readable artifacts, one directory per run."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from html import escape
import json
import math
from pathlib import Path
import re

import numpy as np
import pandas as pd

from tools.backtest_metrics import calculate_metrics, daily_equity, monthly_returns
from tools.report_charts import render_charts

TRADE_COLUMNS = ['order_id', 'order_type', 'order_tag', 'signal_time', 'order_created_time', 'entry_time', 'exit_time', 'type', 'entry', 'sl', 'tp',
                 'exit_price', 'size', 'gross_pnl_usd', 'commission_usd', 'pnl_usd',
                 'pnl_pct', 'balance', 'closed_by', 'entry_fill_reason', 'post_failure_entry']
ROLLING_COLUMNS = ['start_date', 'end_date', 'outcome', 'days_to_result',
                   'max_dd_in_window_pct', 'num_trades', 'num_trades_full_simulation',
                   'post_failure_trades', 'ending_balance_full_simulation']
WALK_FORWARD_COLUMNS = ['train_start', 'train_end', 'test_start', 'test_end',
                        'candidate_count', 'selected_params_json', 'train_net_profit',
                        'train_hard_breach', 'test_net_profit', 'test_total_trades',
                        'test_hard_breach', 'test_first_fail_time']


def clean_json(value):
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(v) for v in value]
    if isinstance(value, (datetime, pd.Timestamp, Path)):
        return str(value)
    if isinstance(value, np.generic):
        return clean_json(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def fmt(value, kind='number'):
    if value is None or (isinstance(value, (float, np.floating)) and not math.isfinite(value)):
        return 'N/A'
    if kind == 'text':
        return escape(str(value))
    if kind == 'int':
        return f'{int(value):,}'
    if kind == 'duration':
        seconds = max(0, int(round(value)))
        days, seconds = divmod(seconds, 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        return (f'{days}d ' if days else '') + f'{hours:02}:{minutes:02}:{seconds:02}'
    return f'{value:,.2f}' + ('%' if kind == 'pct' else '')


def table(title, rows):
    cells = ''.join(f'<tr><td>{escape(label)}</td><td>{value}</td></tr>' for label, value in rows)
    return f'<div class="tablebox"><h3>{escape(title)}</h3><table class="metrics">{cells}</table></div>'


def section(number, title, subtitle, body, anchor):
    return (f'<section class="section" id="{anchor}"><div class="section-head"><h2>'
            f'<b class="section-num">{number:02}</b>{escape(title)}</h2><span>{escape(subtitle)}</span>'
            f'</div>{body}</section>')


def image_tag(path):
    content = base64.b64encode(path.read_bytes()).decode('ascii')
    return f'<div class="panel"><img class="chart" src="data:image/png;base64,{content}" alt="{escape(path.stem)}"></div>'


def metric_tables(m):
    def items(spec):
        return [(label, fmt(m.get(key), kind)) for label, key, kind in spec]
    performance = items([
        ('Net profit', 'net_profit', 'number'), ('Gross profit', 'gross_profit', 'number'),
        ('Gross loss', 'gross_loss', 'number'), ('Return on deposit', 'return_pct', 'pct'),
        ('Profit factor', 'profit_factor', 'number'), ('Expected payoff / trade', 'expected_payoff', 'number'),
        ('Recovery / equity drawdown', 'recovery_factor', 'number'),
        ('Average win / average loss', 'payoff_ratio', 'number'),
        ('Commission paid', 'commission_usd', 'number'), ('Volume traded (lots)', 'total_volume_lots', 'number'),
    ])
    distribution = items([
        ('Closed trades', 'total_trades', 'int'), ('Winners', 'profit_trades', 'int'),
        ('Win rate', 'win_rate_pct', 'pct'), ('Losers', 'loss_trades', 'int'),
        ('Loss rate', 'loss_rate_pct', 'pct'), ('Breakeven trades', 'breakeven_trades', 'int'),
        ('Largest winning trade', 'largest_profit_trade', 'number'),
        ('Largest losing trade', 'largest_loss_trade', 'number'),
        ('Average winning trade', 'average_profit_trade', 'number'),
        ('Average losing trade', 'average_loss_trade', 'number'),
    ])
    statistics = items([
        ('Sharpe · daily annualized', 'sharpe_daily_annualized', 'number'),
        ('Sortino · daily annualized', 'sortino_daily_annualized', 'number'),
        ('LR correlation · signed', 'lr_correlation', 'number'),
        ('LR standard error', 'lr_standard_error', 'number'),
        ('Runs Z-score', 'runs_z_score', 'number'), ('Runs test p-value', 'runs_p_value', 'number'),
        ('Median trade', 'median_trade', 'number'), ('Trade P/L standard deviation', 'trade_pnl_std', 'number'),
    ])
    for key in ['ahpr', 'ghpr']:
        value = m.get(key)
        statistics.append((key.upper(), f'{value:.5f} ({(value - 1) * 100:+.3f}%)' if value is not None else 'N/A'))
    risk_tables = []
    for key, label in [('balance_drawdown', 'Balance drawdown'), ('equity_drawdown', 'Equity drawdown')]:
        d = m.get(key) or {}
        risk_tables.append(table(label, [
            ('Below initial deposit', fmt(d.get('absolute'))),
            ('Largest cash drawdown', fmt(d.get('maximal'))),
            ('Percent at largest cash DD', fmt(d.get('maximal_pct'), 'pct')),
            ('Largest percentage DD', fmt(d.get('relative_pct'), 'pct')),
            ('Cash at largest % DD', fmt(d.get('relative_amount'))),
        ]))
    exposure = []
    for direction in ['long', 'short']:
        exposure.append((direction.title() + ' trades / win rate',
                         fmt(m.get(direction + '_trades'), 'int') + ' / ' + fmt(m.get(direction + '_won_pct'), 'pct')))
    exposure += items([
        ('Minimum holding time', 'holding_seconds_min', 'duration'),
        ('Average holding time', 'holding_seconds_mean', 'duration'),
        ('Maximum holding time', 'holding_seconds_max', 'duration'),
        ('Best sampled day', 'best_day', 'number'), ('Worst sampled day', 'worst_day', 'number'),
        ('Sampled days', 'sampled_days', 'int'),
    ])
    sequences = []
    for key, label in [('consecutive_wins', 'Winning streaks'), ('consecutive_losses', 'Losing streaks')]:
        s = m[key]
        sequences.append(table(label, [
            ('Longest streak / P&L', f'{s["longest_count"]} / {fmt(s["longest_amount"])}'),
            ('Largest streak P&L / count', f'{fmt(s["largest_amount"])} / {s["largest_count"]}'),
            ('Average streak length', fmt(s['average_count'])),
        ]))
    return ('<div class="gridtables">' + table('Profit & efficiency', performance)
            + table('Trade distribution', distribution) + table('Statistical profile', statistics) + '</div>',
            '<div class="gridtables">' + ''.join(risk_tables) + table('Direction & holding time', exposure) + '</div>',
            '<div class="two">' + ''.join(sequences) + '</div>')


def ledger_html(trades):
    if trades.empty:
        return '<div class="panel pad">No closed trades were generated in this run.</div>'
    headers = ['Entry', 'Exit', 'Side', 'Lots', 'Entry price', 'Exit price', 'Net P/L', 'Balance', 'Exit reason', 'Phase']
    rows = []
    for _, row in trades.tail(200).iterrows():
        stress = bool(row.get('post_failure_entry', False))
        values = [fmt(row.get('entry_time'), 'text'), fmt(row.get('exit_time'), 'text'),
                  fmt(row.get('type'), 'text'), fmt(row.get('size')), fmt(row.get('entry')),
                  fmt(row.get('exit_price')), fmt(row.get('pnl_usd')), fmt(row.get('balance')),
                  fmt(row.get('closed_by'), 'text'), 'Post-failure' if stress else 'Before failure']
        cells = ''.join(f'<td>{value}</td>' for value in values)
        rows.append(f'<tr class="{"stress" if stress else ""}">{cells}</tr>')
    return ('<div class="panel table-scroll"><table class="ledger"><thead><tr>'
            + ''.join(f'<th>{label}</th>' for label in headers)
            + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>')


def build_html(strategy, run_id, m, meta, trades, images, config):
    esc = lambda value: escape(str(value))
    pf = '∞' if m.get('profit_factor_status') == 'no_losses' and m['gross_profit'] > 0 else fmt(m.get('profit_factor'))
    dd = (m.get('equity_drawdown') or {}).get('relative_pct')
    constrained = m.get('ftmo_constrained_path') or {}
    kpis = [
        ('No-loss-constraint profit', fmt(m['net_profit']), meta.get('currency', 'USD') + ' · full-history primary result', 'positive' if m['net_profit'] >= 0 else 'negative'),
        ('FTMO-constrained profit', fmt(constrained.get('net_profit')), meta.get('currency', 'USD') + ' · shown for comparison and Monte Carlo', 'positive' if (constrained.get('net_profit') or 0) >= 0 else 'negative'),
        ('Profit factor', pf, 'Net winning / net losing trades', ''),
        ('Equity drawdown', fmt(dd, 'pct'), 'Largest peak-to-trough decline', 'negative'),
        ('Win rate', fmt(m['win_rate_pct'], 'pct'), f'{m["total_trades"]:,} constrained-path closed trades', ''),
    ]
    cards = ''.join(f'<div class="kpi"><label>{esc(k)}</label><strong class="{c}">{v}</strong><small>{esc(note)}</small></div>' for k,v,note,c in kpis)
    perf, risk, streak = metric_tables(m)
    failed = meta.get('first_fail_time') is not None
    status = 'FTMO HARD BREACH · CONSTRAINED PATH HALTED' if failed else 'NO FTMO HARD BREACH RECORDED'
    notice = ''
    if failed:
        notice = (f'<div class="notice"><strong>FTMO hard breach: {esc(meta["first_fail_time"])}</strong> · '
                  f'{esc(meta.get("first_fail_reason", ""))}<br>The teal constrained path opens no new position after this point. '
                  'The orange path is a separate fresh simulation that ignores daily/total loss limits.</div>')
    profile = table('Run specification', [
        ('Strategy', esc(strategy)), ('Symbol / timeframe', esc(meta.get('symbol', 'N/A')) + ' / ' + esc(meta.get('timeframe', 'N/A'))),
        ('Period from', esc(meta.get('period_start', 'N/A'))), ('Period to', esc(meta.get('period_end', 'N/A'))),
        ('OHLC bars', fmt(meta.get('bars'), 'int')), ('Initial deposit', fmt(m['initial_balance'])),
        ('No-loss-constraint ending balance', fmt(m['ending_balance'])), ('No-loss-constraint ending equity', fmt(m['ending_equity'])),
        ('FTMO-constrained ending balance', fmt(constrained.get('ending_balance'))),
        ('FTMO-constrained closed trades', fmt(constrained.get('total_trades'), 'int')),
        ('Report timezone', esc(meta.get('report_timezone', 'UTC'))), ('Source commit', esc(meta.get('git_commit', 'N/A'))),
    ])
    unavailable = table('Data & execution coverage', [
        ('Model', 'OHLC · next-bar open · stop first'),
        ('Equity sampling', 'Bar close; not tick-by-tick'), ('History quality (MT5)', 'N/A · no native quality audit'),
        ('Modeled ticks', 'N/A · OHLC engine'), ('Native deals / orders', 'N/A · closed-position ledger'),
        ('Minimum margin level', 'N/A · margin not modeled'), ('Swap', 'N/A · not modeled'),
        ('MFE / MAE correlations', 'N/A · excursions not recorded'),
        ('OnTester value', 'N/A · no EA callback'), ('Session statistics', 'Exit-time grouping'),
    ])
    mc = m.get('monte_carlo', {})
    rolling = m.get('rolling_window', {})
    walk_forward = m.get('walk_forward', {})
    mc_status = mc.get('status', 'completed')
    rolling_status = 'no valid complete windows' if rolling.get('num_windows', 0) == 0 else 'completed'
    robustness = '<div class="two">' + table('Monte Carlo · empirical outcomes', [
        ('Status', esc(mc_status)), ('Pass', fmt(mc.get('p_pass'), 'pct')), ('Fail daily loss', fmt(mc.get('p_fail_daily_loss'), 'pct')),
        ('Fail total loss', fmt(mc.get('p_fail_max_dd'), 'pct')), ('Timeout', fmt(mc.get('p_timeout'), 'pct')),
        ('95th percentile DD', fmt(mc.get('max_dd_p95'), 'pct')), ('Average days to pass', fmt(mc.get('avg_days_to_pass'))),
        ('Method', esc(mc.get('method', 'N/A'))),
    ]) + table('Rolling-window · no loss constraint', [
        ('Status', rolling_status),
        ('Windows', fmt(rolling.get('num_windows', 0), 'int')),
        ('Pass across windows', fmt(rolling.get('p_pass_across_history'), 'pct')),
        ('Fail across windows', fmt(rolling.get('p_fail_across_history'), 'pct')),
        ('Internal stops', fmt(rolling.get('p_internal_stop_across_history'), 'pct')),
        ('Timeouts', fmt(rolling.get('p_timeout_across_history'), 'pct')),
        ('Worst window DD', fmt(rolling.get('worst_max_dd_pct'), 'pct')),
        ('Median days to pass', fmt(rolling.get('median_days_to_pass'))),
    ]) + table('Walk-forward · out of sample', [
        ('Windows', fmt(walk_forward.get('num_windows', 0), 'int')),
        ('OOS net profit', fmt(walk_forward.get('oos_net_profit'))),
        ('Profitable OOS windows', fmt(walk_forward.get('oos_profitable_windows_pct'), 'pct')),
        ('OOS hard-breach windows', fmt(walk_forward.get('oos_hard_breach_windows_pct'), 'pct')),
        ('Candidates / window', fmt(walk_forward.get('candidate_count_per_window'), 'int')),
        ('Status', esc(walk_forward.get('status', 'completed'))),
    ]) + '</div>'
    methodology = '''<div class="panel pad method">
<p><strong>Two-path scope.</strong> Performance tables, trade ledger, trade charts, daily/monthly returns, rolling windows and walk-forward results use the no-loss-constraint path. It is a fresh full-history simulation that ignores only FTMO daily- and total-loss gates while retaining compounding, position sizing and execution costs. The teal equity line is the separate FTMO-constrained comparison path, which stops new entries after a hard breach. Monte Carlo is deliberately calculated from that constrained path.</p>
<p><strong>Drawdowns.</strong> Positive peak-to-trough values include the initial deposit as a starting peak. Cash-maximal and percentage-relative drawdowns can occur at different times. Equity and balance are sampled at bar close; intrabar compliance checks can detect a breach that is not visible in these sampled drawdowns.</p>
<p><strong>Ratios.</strong> Sharpe uses daily equity returns, zero risk-free rate, sample standard deviation and √252 annualization. Sortino uses the root mean square of negative daily returns (target zero). Only dates present in the equity series enter these statistics; missing dates are not fabricated. These are documented research estimates, not a claim of numerical identity with the native MT5 tester.</p>
<p><strong>Trade statistics.</strong> AHPR is the mean trade growth factor; GHPR is its geometric mean. Net P/L includes recorded costs. Gross profit/loss split net trades by sign. Recovery uses net profit / maximal sampled equity drawdown. Streaks break on a zero-P/L trade; runs testing excludes zero trades. LR uses initial plus closed-trade balances versus trade index, with n−2 residual degrees of freedom. Holding time is timestamp-based and has bar-level precision.</p>
<p><strong>Availability.</strong> N/A means insufficient observations, an undefined denominator, or a field the engine does not record. The profit-factor card uses ∞ for positive profit with no losses; JSON stores null with an explicit status. No MT5 history-quality percentage, tick count, margin figure, swap or MFE/MAE value is invented.</p>
<p><strong>Robustness.</strong> Monte Carlo resamples observed day blocks from the FTMO-constrained ledger; it does not reconstruct intratrade floating losses. Rolling and walk-forward use no-loss-constraint fresh-account simulations. Overlapping rolling windows are not independent samples. Monthly returns use sampled month-end equity; first/last months may be partial. Costs and source timezones are preserved in the configuration snapshot.</p>
<p>Reference: <a href="https://www.metatrader5.com/en/terminal/help/algotrading/testing_report">MetaTrader 5 tester report field reference</a>. This report is produced by the Python research engine.</p>
'''
    methodology += '<details><summary>Configuration snapshot</summary><pre>' + esc(json.dumps(clean_json(config), ensure_ascii=False, indent=2)) + '</pre></details></div>'
    sections = [
        section(1, 'Run overview', 'Inputs, account and measurement coverage', '<div class="two">'+profile+unavailable+'</div>', 'overview'),
        section(2, 'Performance statistics', 'No-loss-constraint path · amounts in '+str(meta.get('currency', 'USD')), perf, 'performance'),
        section(3, 'Equity & risk', 'Teal = FTMO-constrained comparison · orange and risk statistics = no-loss constraint', image_tag(images[0])+notice+'<div style="height:18px"></div>'+risk+'<div style="height:18px"></div>'+image_tag(images[1]), 'risk'),
        section(4, 'Trade anatomy', 'No-loss-constraint path', image_tag(images[2])+'<div style="height:18px"></div>'+streak+'<div style="height:18px"></div>'+image_tag(images[3]), 'trades'),
        section(5, 'Monthly performance', 'No-loss-constraint equity returns by calendar month', image_tag(images[4]), 'calendar'),
        section(6, 'Robustness laboratory', 'Rolling windows & empirical Monte Carlo', robustness+'<div style="height:18px"></div>'+image_tag(images[5])+'<div style="height:18px"></div>'+image_tag(images[6]), 'robustness'),
        section(7, 'Trade ledger', 'No-loss-constraint history in trades.csv · times retain source timezone', '<details class="panel pad"><summary>Expand latest 200 closed trades</summary>' + ledger_html(trades) + '</details>', 'ledger'),
        section(8, 'Definitions & reproducibility', 'Documented assumptions', methodology, 'methods'),
    ]
    css = (Path(__file__).parent / 'templates/report.css').read_text(encoding='utf-8')
    demo = '<div class="demo">SYNTHETIC DEMO — layout preview only; not actual strategy results.</div>' if meta.get('is_demo') else ''
    nav = ''.join(f'<a href="#{anchor}">{title}</a>' for anchor,title in [('overview','Overview'),('performance','Statistics'),('risk','Equity & risk'),('trades','Trades'),('calendar','Monthly'),('robustness','Robustness'),('ledger','Ledger'),('methods','Methodology')])
    downloads = ''.join(f'<a href="{name}" download>{label}</a>' for name,label in [('report.json','Report JSON'),('trades.csv','No-loss-constraint trades'),('order_events.csv','No-loss order lifecycle'),('equity_curve.csv','FTMO-constrained equity'),('trades_constrained.csv','FTMO-constrained trades'),('equity_curve_unconstrained.csv','No-loss-constraint equity'),('rolling_windows.csv','No-loss rolling windows'),('walk_forward.csv','No-loss walk-forward windows'),('daily_returns.csv','No-loss daily returns'),('monthly_returns.csv','No-loss monthly returns')])
    return f'''<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(strategy)} · Backtest research report</title><style>{css}</style></head><body><main class="shell">
<div class="masthead"><div class="brand">QUANT / RESEARCH</div><div class="edition">STRATEGY TESTER REPORT · {esc(meta['created_at'])}</div></div>{demo}
<header class="hero"><div class="eyebrow">Systematic trading · performance dossier</div><h1>{esc(strategy.upper())}</h1><p>{esc(meta.get('symbol','N/A'))} · {esc(meta.get('timeframe','N/A'))} &nbsp; / &nbsp; {esc(meta.get('period_start','N/A'))} → {esc(meta.get('period_end','N/A'))}</p>
<div class="tags"><span class="tag status {'ok' if not failed else ''}">{status}</span><span class="tag">{esc(run_id)}</span><span class="tag">Full simulation · costs included as configured</span></div></header>
<nav class="nav">{nav}</nav><div class="kpis">{cards}</div>{''.join(sections)}
<section class="section"><div class="downloads">{downloads}</div><p class="note">This HTML embeds all figures and opens offline. CSV/JSON downloads require the companion files in the same folder.</p></section>
<footer><span>QUANT / RESEARCH · Python backtest engine</span><span>{esc(run_id)} · {esc(meta.get('git_commit','N/A'))}</span></footer></main></body></html>'''


def save_run_report(strategy_name, run_id, trade_history, metrics, df_trades=None, *,
                    unconstrained_df_trades=None,
                    rolling_results=None, walk_forward_results=None, metadata=None, config=None, reports_root=None, created_at=None):
    meta, config = dict(metadata or {}), dict(config or {})
    timestamp = created_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise ValueError('created_at must include a timezone')
    timestamp = timestamp.astimezone(timezone.utc)
    meta['created_at'] = timestamp.isoformat()
    safe_name = re.sub(r'[^A-Za-z0-9_.-]+', '_', strategy_name).strip('.') or 'strategy'
    root = Path(reports_root) if reports_root is not None else Path(__file__).resolve().parents[1] / 'reports'
    parent = root / safe_name
    parent.mkdir(parents=True, exist_ok=True)
    stem = timestamp.strftime('%Y-%m-%d_%H-%M-%S_%f_UTC')
    # Exclusive creation protects even frozen timestamps/concurrent callers.
    for suffix in range(10000):
        folder = parent / (stem if suffix == 0 else f'{stem}_{suffix:03d}')
        try:
            folder.mkdir()
            break
        except FileExistsError:
            continue
    else:
        raise FileExistsError('Cannot allocate a unique run report directory')
    attrs = df_trades.attrs if df_trades is not None else {}
    unconstrained_attrs = unconstrained_df_trades.attrs if unconstrained_df_trades is not None else {}
    constrained_trades = pd.DataFrame(trade_history)
    if constrained_trades.empty:
        constrained_trades = pd.DataFrame(columns=TRADE_COLUMNS)
    constrained_curve = attrs.get('equity_curve', pd.DataFrame(columns=['time','balance','equity','is_failed'])).copy()
    unconstrained_trades = (pd.DataFrame(unconstrained_df_trades)
                            if unconstrained_df_trades is not None else pd.DataFrame(columns=TRADE_COLUMNS))
    unconstrained_curve = unconstrained_attrs.get(
        'equity_curve', pd.DataFrame(columns=['time', 'balance', 'equity', 'is_failed'])
    ).copy()
    order_events = unconstrained_attrs.get('order_events', attrs.get('order_events', pd.DataFrame())).copy()
    # Legacy/demo callers may not provide a second path. In that case their sole
    # path remains the report's primary result.
    trades = unconstrained_trades if unconstrained_df_trades is not None else constrained_trades
    result_curve = unconstrained_curve if not unconstrained_curve.empty else constrained_curve
    if rolling_results is None or rolling_results.empty:
        rolling_results = pd.DataFrame(columns=ROLLING_COLUMNS)
    if walk_forward_results is None or walk_forward_results.empty:
        walk_forward_results = pd.DataFrame(columns=WALK_FORWARD_COLUMNS)
    initial = metrics.get('initial_balance') or attrs.get('initial_balance') or unconstrained_attrs.get('initial_balance')
    if initial is None:
        raise ValueError('Reports require initial_balance; it is never inferred as $10,000')
    source_tz, report_tz = meta.get('source_timezone', 'UTC'), meta.get('report_timezone', 'UTC')
    full_metrics = {**metrics, **calculate_metrics(trades.to_dict('records'), initial, result_curve, source_tz, report_tz)}
    for key in ['first_fail_time','first_fail_reason','first_internal_stop_time','first_internal_stop_reason']:
        meta[key] = attrs.get(key, meta.get(key))
    meta.setdefault('bars', len(result_curve))
    meta.setdefault('period_start', str(result_curve.time.iloc[0]) if len(result_curve) else 'N/A')
    meta.setdefault('period_end', str(result_curve.time.iloc[-1]) if len(result_curve) else 'N/A')
    daily = daily_equity(result_curve, initial, source_tz, report_tz)
    monthly = monthly_returns(daily, initial)
    images = render_charts(folder / 'images', trades, constrained_curve, daily, monthly, rolling_results,
                           full_metrics, meta, unconstrained_curve=unconstrained_curve)
    payload = {'schema_version': 1, 'run_id': run_id, 'strategy': strategy_name,
               'metadata': meta, 'metrics': full_metrics, 'configuration': config,
               'artifacts': ['report.html', 'report.json', 'trades.csv', 'equity_curve.csv',
                             'trades_constrained.csv', 'equity_curve_unconstrained.csv', 'order_events.csv',
                             'rolling_windows.csv', 'walk_forward.csv', 'daily_returns.csv', 'monthly_returns.csv']
                            + ['images/' + p.name for p in images]}
    (folder / 'report.json').write_text(json.dumps(clean_json(payload), ensure_ascii=False,
                                                indent=2, allow_nan=False), encoding='utf-8')
    for name, frame in [('trades',trades),('equity_curve',constrained_curve),
                        ('trades_constrained', constrained_trades),
                        ('equity_curve_unconstrained', unconstrained_curve),
                        ('order_events', order_events),
                        ('rolling_windows',rolling_results),
                        ('walk_forward',walk_forward_results),
                        ('daily_returns',daily),('monthly_returns',monthly)]:
        frame.to_csv(folder / (name + '.csv'), index=False)
    (folder / 'report.html').write_text(build_html(strategy_name, run_id, full_metrics, meta,
                                                  trades, images, config), encoding='utf-8')
    print(f'[*] Report: {folder / "report.html"}')
    return folder
