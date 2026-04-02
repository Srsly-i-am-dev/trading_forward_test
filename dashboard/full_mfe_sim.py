"""Full MFE simulation for ALL demo symbols."""
import sys, io, os

import pandas as pd
import numpy as np
from trade_parser import load_and_parse, filter_trades, _get_pip_size
from mfe_sim import simulate_mfe_tp, load_prices

positions, open_pos, summary = load_and_parse(
    r'C:\Users\DEV\Downloads\ReportHistory-52801643 (1).html')
df = filter_trades(positions, '2026-03-30', '2026-04-02')

FULL_CONFIG = {
    # Live 7
    'EURUSD':  {'strategy': 'fixed_mfe', 'tp_pips': 29.0},
    'ETHUSD':  {'strategy': 'fixed_mfe', 'tp_pips': 845.0},
    'ADAUSD':  {'strategy': 'fixed_mfe', 'tp_pips': 1280.0},
    'GBPAUD':  {'strategy': 'sl_ratio',  'ratio': 0.75},
    'EURJPY':  {'strategy': 'rr_match',  'ratio': 1.0},
    'USDCHF':  {'strategy': 'time_exit', 'max_bars': 24},
    'GBPUSD':  {'strategy': 'fixed_mfe', 'tp_pips': 20.5},
    # Demo-only
    'AUDJPY':  {'strategy': 'fixed_mfe', 'tp_pips': 26.0},
    'AUDUSD':  {'strategy': 'fixed_mfe', 'tp_pips': 14.3},
    'BTCUSD':  {'strategy': 'fixed_mfe', 'tp_pips': 425.3},
    'EURCHF':  {'strategy': 'fixed_mfe', 'tp_pips': 5.5},
    'EURAUD':  {'strategy': 'fixed_mfe', 'tp_pips': 14.5},
    'GBPJPY':  {'strategy': 'fixed_mfe', 'tp_pips': 36.1},
    'NZDUSD':  {'strategy': 'fixed_mfe', 'tp_pips': 10.8},
    'USDCAD':  {'strategy': 'fixed_mfe', 'tp_pips': 10.1},
    'USDJPY':  {'strategy': 'fixed_mfe', 'tp_pips': 18.4},
}

LIVE_SYMBOLS = {'EURUSD','EURJPY','GBPAUD','ADAUSD','USDCHF','GBPUSD','ETHUSD'}

sim_df = simulate_mfe_tp(df, FULL_CONFIG)
valid = sim_df[sim_df['sim_result'].isin(['tp_hit', 'sl_hit', 'time_exit', 'signal_close'])]

header = f"{'Symbol':<10} {'Trades':>6} {'Actual$':>9} {'ActWR%':>7} {'MFE$':>9} {'MFE_WR%':>8} {'Delta$':>9} {'Strategy':>30}"
print(header)
print('=' * 100)

rows = []
for sym in sorted(valid['symbol'].unique()):
    s = valid[valid['symbol'] == sym]
    cfg = FULL_CONFIG[sym]
    strat = cfg['strategy']
    if 'tp_pips' in cfg:
        strat += f" {cfg['tp_pips']}pip"
    if 'ratio' in cfg:
        strat += f" {cfg['ratio']}x"
    if 'max_bars' in cfg:
        strat += f" {cfg['max_bars']*5}min"

    actual = s['actual_pnl'].sum()
    sim = s['sim_pnl'].sum()
    act_wr = s['is_win_actual'].mean() * 100
    sim_wr = s['is_win_sim'].mean() * 100
    delta = sim - actual
    rows.append((sym, len(s), actual, act_wr, sim, sim_wr, delta, strat))

rows.sort(key=lambda x: x[4], reverse=True)
for r in rows:
    sym, n, actual, act_wr, sim, sim_wr, delta, strat = r
    marker = ' [LIVE]' if sym in LIVE_SYMBOLS else ''
    print(f"{sym:<10} {n:>6} {actual:>9.2f} {act_wr:>6.1f}% {sim:>9.2f} {sim_wr:>7.1f}% {delta:>+9.2f} {strat:>30}{marker}")

total_actual = sum(r[2] for r in rows)
total_sim = sum(r[4] for r in rows)
total_act_wr = valid['is_win_actual'].mean() * 100
total_sim_wr = valid['is_win_sim'].mean() * 100
print('=' * 100)
print(f"{'TOTAL':<10} {len(valid):>6} {total_actual:>9.2f} {total_act_wr:>6.1f}% {total_sim:>9.2f} {total_sim_wr:>7.1f}% {total_sim - total_actual:>+9.2f}")
