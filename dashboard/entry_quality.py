"""Analyze how many trades entered far from C level."""
import sys, io, os
os.environ['PYTHONIOENCODING'] = 'utf-8'

import pandas as pd
import numpy as np
from trade_parser import load_and_parse, filter_trades, _get_pip_size

positions, open_pos, summary = load_and_parse(
    r'C:\Users\DEV\Downloads\ReportHistory-52801643 (1).html')
df = filter_trades(positions, '2026-03-30', '2026-04-02')

results = []
for _, t in df.iterrows():
    entry = t['entry_price']
    tp = t['tp']
    sl = t['sl']

    if tp == 0 or sl == 0:
        continue

    tp_dist = abs(tp - entry)
    sl_dist = abs(entry - sl)

    if tp_dist == 0:
        continue

    # Estimate C level: SL is 0.5% beyond C
    if t['trade_type'] == 'buy':
        est_c = sl / (1 - 0.005)
        dist_from_c = entry - est_c
    else:
        est_c = sl / (1 + 0.005)
        dist_from_c = est_c - entry

    pct_used = (dist_from_c / tp_dist * 100) if tp_dist > 0 else 0
    pip = _get_pip_size(t['symbol'])
    pips_from_c = abs(dist_from_c) / pip

    results.append({
        'symbol': t['symbol'],
        'type': t['trade_type'],
        'entry': entry,
        'est_c': round(est_c, 5),
        'pips_from_c': round(pips_from_c, 1),
        'pct_tp_used': round(pct_used, 1),
        'pnl': t['net_pnl'],
        'is_win': t['is_win'],
        'exit': t['exit_type'],
    })

rdf = pd.DataFrame(results)

rdf['entry_quality'] = rdf['pct_tp_used'].apply(
    lambda x: 'GOOD (<25%)' if x < 25 else ('OK (25-50%)' if x < 50 else 'BAD (>50%)'))

print('ENTRY QUALITY ANALYSIS')
print('=' * 60)
print(f'Total trades: {len(rdf)}')
print()

for q in ['GOOD (<25%)', 'OK (25-50%)', 'BAD (>50%)']:
    subset = rdf[rdf['entry_quality'] == q]
    if len(subset) == 0:
        continue
    wr = subset['is_win'].mean() * 100
    pnl = subset['pnl'].sum()
    avg_pips = subset['pips_from_c'].mean()
    print(f'{q:15s}: {len(subset):3d} trades | WR: {wr:5.1f}% | PnL: ${pnl:>8.2f} | Avg {avg_pips:.1f} pips from C')

print()
print('BAD ENTRY TRADES (>50% of TP used at entry):')
print('-' * 80)
bad = rdf[rdf['pct_tp_used'] > 50].sort_values('pnl')
for _, r in bad.iterrows():
    w = 'W' if r['is_win'] else 'L'
    print(f"  {r['symbol']:8s} {r['type']:4s} | {r['pips_from_c']:6.1f} pips from C | {r['pct_tp_used']:5.1f}% used | ${r['pnl']:>7.2f} {w} | {r['exit']}")

good_pnl = rdf[rdf['pct_tp_used'] <= 50]['pnl'].sum()
bad_pnl = bad['pnl'].sum()
total_pnl = rdf['pnl'].sum()
print()
print(f'Total PnL:                ${total_pnl:>8.2f}')
print(f'Without bad entries:      ${good_pnl:>8.2f}')
print(f'Bad entries cost you:     ${bad_pnl:>8.2f}')
