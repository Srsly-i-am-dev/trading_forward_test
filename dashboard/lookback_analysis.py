"""Analyze whether lookback 10 or 20 produces better trades."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import pandas as pd
import numpy as np
from trade_parser import load_and_parse, filter_trades, _get_pip_size

positions, open_pos, summary = load_and_parse(
    r'C:\Users\DEV\Downloads\ReportHistory-52801643 (1).html')
df = filter_trades(positions, '2026-03-30', '2026-04-02')

# Higher lookback = bigger pivots = larger SL/TP = fewer trades
# Lower lookback = smaller pivots = tighter SL/TP = more trades
# We analyze pattern size (SL distance) as proxy for lookback effect.

top7 = ['EURUSD', 'ETHUSD', 'ADAUSD', 'GBPAUD', 'EURJPY', 'USDCHF', 'GBPUSD']
t7 = df[df['symbol'].isin(top7)].copy()

t7['sl_pips'] = t7.apply(
    lambda r: abs(r['entry_price'] - r['sl']) / _get_pip_size(r['symbol']), axis=1)
t7['tp_pips'] = t7.apply(
    lambda r: abs(r['entry_price'] - r['tp']) / _get_pip_size(r['symbol']), axis=1)
t7['tp_sl_ratio'] = t7['tp_pips'] / t7['sl_pips'].replace(0, np.nan)
t7['duration_hrs'] = t7['duration_minutes'] / 60

print('=' * 120)
print('PATTERN SIZE ANALYSIS (SL distance = proxy for lookback effect)')
print('  Lookback 10 = smaller pivots = tighter SL/TP = more frequent signals')
print('  Lookback 20 = bigger pivots = wider SL/TP = fewer, larger patterns')
print('=' * 120)
print()

# Per-symbol small vs large
for sym in top7:
    s = t7[t7['symbol'] == sym]
    if len(s) < 4:
        continue
    median_sl = s['sl_pips'].median()
    small = s[s['sl_pips'] <= median_sl]
    large = s[s['sl_pips'] > median_sl]

    print(f'--- {sym} (median SL: {median_sl:.1f} pips, {len(s)} trades) ---')
    s_wr = small['is_win'].mean() * 100
    l_wr = large['is_win'].mean() * 100
    print(f'  Small patterns (SL <= {median_sl:.1f}p): '
          f'{len(small)} trades | WR {s_wr:.0f}% | '
          f'Avg P&L ${small["net_pnl"].mean():.2f} | Total ${small["net_pnl"].sum():.2f}')
    print(f'  Large patterns (SL >  {median_sl:.1f}p): '
          f'{len(large)} trades | WR {l_wr:.0f}% | '
          f'Avg P&L ${large["net_pnl"].mean():.2f} | Total ${large["net_pnl"].sum():.2f}')
    winner = 'SMALL (lookback 10)' if small['net_pnl'].sum() > large['net_pnl'].sum() else 'LARGE (lookback 20)'
    print(f'  --> Winner: {winner}')
    print()

# Overall
print('=' * 120)
print('OVERALL: Small vs Large Patterns (across all top 7)')
print('=' * 120)
overall_median = t7['sl_pips'].median()
small_all = t7[t7['sl_pips'] <= overall_median]
large_all = t7[t7['sl_pips'] > overall_median]
print(f'  Small (SL <= {overall_median:.1f}p): {len(small_all)} trades | '
      f'WR {small_all["is_win"].mean()*100:.1f}% | '
      f'Total P&L ${small_all["net_pnl"].sum():.2f} | '
      f'Avg ${small_all["net_pnl"].mean():.2f}')
print(f'  Large (SL >  {overall_median:.1f}p): {len(large_all)} trades | '
      f'WR {large_all["is_win"].mean()*100:.1f}% | '
      f'Total P&L ${large_all["net_pnl"].sum():.2f} | '
      f'Avg ${large_all["net_pnl"].mean():.2f}')

# MFE capture
print()
print('=' * 120)
print('MFE CAPTURE BY PATTERN SIZE')
print('=' * 120)
t7['mfe_capture'] = t7['pips'] / t7['tp_pips'].replace(0, np.nan) * 100
small_cap = t7.loc[t7['sl_pips'] <= overall_median, 'mfe_capture'].dropna()
large_cap = t7.loc[t7['sl_pips'] > overall_median, 'mfe_capture'].dropna()
print(f'  Small patterns: Avg capture {small_cap.mean():.1f}% of TP target')
print(f'  Large patterns: Avg capture {large_cap.mean():.1f}% of TP target')
print(f'  Small patterns: Avg duration {small_all["duration_hrs"].mean():.1f} hrs')
print(f'  Large patterns: Avg duration {large_all["duration_hrs"].mean():.1f} hrs')

# Quartile analysis
print()
print('=' * 120)
print('QUARTILE ANALYSIS (by SL distance in pips)')
print('=' * 120)
try:
    t7['quartile'] = pd.qcut(t7['sl_pips'], 4,
                              labels=['Q1 (Smallest)', 'Q2', 'Q3', 'Q4 (Largest)'],
                              duplicates='drop')
    for q in ['Q1 (Smallest)', 'Q2', 'Q3', 'Q4 (Largest)']:
        qd = t7[t7['quartile'] == q]
        if qd.empty:
            continue
        print(f'  {q}: {len(qd)} trades | '
              f'SL {qd["sl_pips"].min():.0f}-{qd["sl_pips"].max():.0f}p | '
              f'WR {qd["is_win"].mean()*100:.0f}% | '
              f'Total ${qd["net_pnl"].sum():.2f} | '
              f'Avg ${qd["net_pnl"].mean():.2f}')
except Exception as e:
    print(f'  Quartile error: {e}')

# RR ratio analysis
print()
print('=' * 120)
print('INTENDED RR RATIO ANALYSIS')
print('=' * 120)
t7['rr_bucket'] = pd.cut(t7['rrr_intended'], bins=[0, 0.5, 1.0, 1.5, 2.0, 3.0, 100],
                          labels=['<0.5', '0.5-1.0', '1.0-1.5', '1.5-2.0', '2.0-3.0', '>3.0'])
for rr in ['<0.5', '0.5-1.0', '1.0-1.5', '1.5-2.0', '2.0-3.0', '>3.0']:
    rd = t7[t7['rr_bucket'] == rr]
    if rd.empty:
        continue
    print(f'  RR {rr}: {len(rd)} trades | '
          f'WR {rd["is_win"].mean()*100:.0f}% | '
          f'Total ${rd["net_pnl"].sum():.2f} | '
          f'Avg ${rd["net_pnl"].mean():.2f} | '
          f'Avg SL {rd["sl_pips"].mean():.0f}p')

# Final verdict
print()
print('=' * 120)
print('VERDICT')
print('=' * 120)
small_total = small_all['net_pnl'].sum()
large_total = large_all['net_pnl'].sum()
small_avg = small_all['net_pnl'].mean()
large_avg = large_all['net_pnl'].mean()
print(f'  Small patterns total: ${small_total:,.2f} ({len(small_all)} trades, ${small_avg:.2f}/trade)')
print(f'  Large patterns total: ${large_total:,.2f} ({len(large_all)} trades, ${large_avg:.2f}/trade)')
if small_total > large_total and small_avg > large_avg:
    print('  --> KEEP LOOKBACK 10: Smaller patterns are more profitable overall AND per-trade')
elif large_total > small_total and large_avg > large_avg:
    print('  --> USE LOOKBACK 20: Larger patterns are more profitable overall AND per-trade')
elif small_avg > large_avg:
    print('  --> KEEP LOOKBACK 10: Better per-trade avg (more efficient), even if fewer total $')
elif large_avg > small_avg:
    print('  --> CONSIDER LOOKBACK 20: Better per-trade avg, but fewer trades may reduce total $')
else:
    print('  --> KEEP LOOKBACK 10: No clear advantage for larger patterns')
