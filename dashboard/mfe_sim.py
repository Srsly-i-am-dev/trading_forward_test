"""Simulate exact dollar P&L with MFE-based TP per symbol."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import pandas as pd
import numpy as np
from datetime import timedelta
from pathlib import Path
from trade_parser import load_and_parse, filter_trades, _get_pip_size
from exit_optimizer import SYMBOL_TO_FILE_PREFIX

DATA_DIR = r'C:\Users\DEV\OneDrive\Documents\GitHub\trading_forward_test\5 min data'

# MFE-based TP config per symbol
MFE_CONFIG = {
    # Top 5
    'EURUSD':  {'strategy': 'fixed_mfe', 'tp_pips': 29.0},
    'ETHUSD':  {'strategy': 'fixed_mfe', 'tp_pips': 845.0},
    'ADAUSD':  {'strategy': 'fixed_mfe', 'tp_pips': 1280.0},
    'GBPAUD':  {'strategy': 'sl_ratio',  'ratio': 0.75},
    'EURJPY':  {'strategy': 'rr_match',  'ratio': 1.0},
    # Candidates #6, #7, and others for comparison
    'USDCHF':  {'strategy': 'time_exit', 'max_bars': 24},
    'GBPUSD':  {'strategy': 'fixed_mfe', 'tp_pips': 20.5},
    'GBPJPY':  {'strategy': 'time_exit', 'max_bars': 12},
    'AUDJPY':  {'strategy': 'time_exit', 'max_bars': 24},
    'BTCUSD':  {'strategy': 'fixed_mfe', 'tp_pips': 425.3},
    'USDCAD':  {'strategy': 'rr_match',  'ratio': 1.5},
    'USDJPY':  {'strategy': 'rr_match',  'ratio': 1.5},
}


def load_prices(sym):
    prefix = SYMBOL_TO_FILE_PREFIX.get(sym)
    if not prefix:
        return pd.DataFrame()
    files = sorted(Path(DATA_DIR).glob(f'{prefix}_Minute_*_UTC.csv'))
    files = [f for f in files if '(1)' not in f.name]
    frames = []
    for f in files:
        try:
            d = pd.read_csv(f)
            d['UTC'] = pd.to_datetime(d['UTC'], format='%d.%m.%Y %H:%M:%S.%f UTC')
            frames.append(d)
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames).sort_values('UTC').reset_index(drop=True)


def simulate_mfe_tp(trades_df, config):
    results = []
    price_cache = {}

    for _, trade in trades_df.iterrows():
        sym = trade['symbol']
        if sym not in config:
            continue

        pip = _get_pip_size(sym)
        entry = trade['entry_price']
        sl = trade['sl']
        tp_orig = trade['tp']
        ttype = trade['trade_type']
        cfg = config[sym]

        # Calculate new TP
        if cfg['strategy'] == 'fixed_mfe':
            tp_dist = cfg['tp_pips'] * pip
            new_tp = entry + tp_dist if ttype == 'buy' else entry - tp_dist
        elif cfg['strategy'] in ('sl_ratio', 'rr_match'):
            sl_dist = abs(entry - sl)
            tp_dist = sl_dist * cfg['ratio']
            new_tp = entry + tp_dist if ttype == 'buy' else entry - tp_dist
        elif cfg['strategy'] == 'time_exit':
            new_tp = tp_orig
        else:
            new_tp = tp_orig

        # Load price data
        if sym not in price_cache:
            price_cache[sym] = load_prices(sym)

        prices = price_cache[sym]
        if prices.empty:
            continue

        start = trade['open_time'] - timedelta(minutes=5)
        end = trade['close_time'] + timedelta(minutes=5)
        path = prices[(prices['UTC'] >= start) & (prices['UTC'] <= end)].copy()

        if path.empty:
            continue

        # Simulate bar by bar
        sim_exit = None
        sim_result = None
        bar_count = 0
        max_bars = cfg.get('max_bars', 99999)

        for _, bar in path.iterrows():
            bar_count += 1
            if ttype == 'buy':
                if bar['High'] >= new_tp:
                    sim_exit = new_tp
                    sim_result = 'tp_hit'
                    break
                if sl > 0 and bar['Low'] <= sl:
                    sim_exit = sl
                    sim_result = 'sl_hit'
                    break
            else:
                if bar['Low'] <= new_tp:
                    sim_exit = new_tp
                    sim_result = 'tp_hit'
                    break
                if sl > 0 and bar['High'] >= sl:
                    sim_exit = sl
                    sim_result = 'sl_hit'
                    break

            if cfg['strategy'] == 'time_exit' and bar_count >= max_bars:
                sim_exit = bar['Close']
                sim_result = 'time_exit'
                break

        if sim_exit is None:
            sim_exit = trade['close_price']
            sim_result = 'signal_close'

        # Calculate sim pips and dollar P&L
        if ttype == 'buy':
            sim_pips = (sim_exit - entry) / pip
        else:
            sim_pips = (entry - sim_exit) / pip

        actual_pips = trade['pips']
        if abs(actual_pips) > 0.001:
            dollar_per_pip = trade['profit'] / actual_pips
        else:
            dollar_per_pip = 0

        sim_pnl = sim_pips * dollar_per_pip if dollar_per_pip != 0 else trade['net_pnl']

        results.append({
            'position_id': trade['position_id'],
            'symbol': sym,
            'trade_type': ttype,
            'actual_pnl': trade['net_pnl'],
            'actual_pips': actual_pips,
            'sim_pips': round(sim_pips, 1),
            'sim_pnl': round(sim_pnl, 2),
            'sim_result': sim_result,
            'is_win_actual': trade['is_win'],
            'is_win_sim': sim_pips > 0,
        })

    return pd.DataFrame(results)


if __name__ == '__main__':
    positions, open_pos, summary = load_and_parse(
        r'C:\Users\DEV\Downloads\ReportHistory-52801643 (1).html')
    df = filter_trades(positions, '2026-03-30', '2026-04-02')

    sim_df = simulate_mfe_tp(df, MFE_CONFIG)
    valid = sim_df[sim_df['sim_result'].isin(['tp_hit', 'sl_hit', 'time_exit', 'signal_close'])]

    print('=' * 160)
    print('EXACT P&L PROJECTION: MFE-BASED TP vs ACTUAL (per symbol)')
    print('=' * 160)

    sym_summary = []
    for sym in sorted(valid['symbol'].unique()):
        s = valid[valid['symbol'] == sym]
        cfg = MFE_CONFIG[sym]
        strat_str = cfg['strategy']
        if 'tp_pips' in cfg:
            strat_str += f" {cfg['tp_pips']}pip"
        if 'ratio' in cfg:
            strat_str += f" {cfg['ratio']}x"
        if 'max_bars' in cfg:
            strat_str += f" {cfg['max_bars']*5}min"

        sym_summary.append({
            'symbol': sym,
            'trades': len(s),
            'strategy': strat_str,
            'actual_$': round(s['actual_pnl'].sum(), 2),
            'actual_wr%': round(s['is_win_actual'].mean() * 100, 1),
            'sim_$': round(s['sim_pnl'].sum(), 2),
            'sim_wr%': round(s['is_win_sim'].mean() * 100, 1),
            'improvement_$': round(s['sim_pnl'].sum() - s['actual_pnl'].sum(), 2),
            'tp_hits': (s['sim_result'] == 'tp_hit').sum(),
            'sl_hits': (s['sim_result'] == 'sl_hit').sum(),
            'time/signal': ((s['sim_result'] == 'time_exit') | (s['sim_result'] == 'signal_close')).sum(),
        })

    result = pd.DataFrame(sym_summary).sort_values('sim_$', ascending=False)
    pd.set_option('display.max_columns', 50)
    pd.set_option('display.width', 220)
    pd.set_option('display.max_rows', 50)
    print(result.to_string(index=False))

    # Totals
    print()
    print('=' * 160)
    print('PORTFOLIO COMPARISON')
    print('=' * 160)

    top5 = ['EURUSD', 'ETHUSD', 'ADAUSD', 'GBPAUD', 'EURJPY']
    t5 = result[result['symbol'].isin(top5)]
    t5_actual = t5['actual_$'].sum()
    t5_sim = t5['sim_$'].sum()
    v5 = valid[valid['symbol'].isin(top5)]
    print(f"  Top 5:           Actual ${t5_actual:>10,.2f}  ->  MFE Sim ${t5_sim:>10,.2f}  |  Delta: ${t5_sim - t5_actual:>+10,.2f}  |  WR: {v5['is_win_actual'].mean()*100:.1f}% -> {v5['is_win_sim'].mean()*100:.1f}%")

    # +USDCHF
    top6 = top5 + ['USDCHF']
    t6 = result[result['symbol'].isin(top6)]
    t6_actual = t6['actual_$'].sum()
    t6_sim = t6['sim_$'].sum()
    v6 = valid[valid['symbol'].isin(top6)]
    print(f"  Top 5 + USDCHF:  Actual ${t6_actual:>10,.2f}  ->  MFE Sim ${t6_sim:>10,.2f}  |  Delta: ${t6_sim - t6_actual:>+10,.2f}  |  WR: {v6['is_win_actual'].mean()*100:.1f}% -> {v6['is_win_sim'].mean()*100:.1f}%")

    # +GBPUSD
    top7 = top6 + ['GBPUSD']
    t7 = result[result['symbol'].isin(top7)]
    t7_actual = t7['actual_$'].sum()
    t7_sim = t7['sim_$'].sum()
    v7 = valid[valid['symbol'].isin(top7)]
    print(f"  Top 5 + USDCHF + GBPUSD:  Actual ${t7_actual:>10,.2f}  ->  MFE Sim ${t7_sim:>10,.2f}  |  Delta: ${t7_sim - t7_actual:>+10,.2f}  |  WR: {v7['is_win_actual'].mean()*100:.1f}% -> {v7['is_win_sim'].mean()*100:.1f}%")

    # All
    all_actual = result['actual_$'].sum()
    all_sim = result['sim_$'].sum()
    print(f"  All {len(result)} symbols:   Actual ${all_actual:>10,.2f}  ->  MFE Sim ${all_sim:>10,.2f}  |  Delta: ${all_sim - all_actual:>+10,.2f}  |  WR: {valid['is_win_actual'].mean()*100:.1f}% -> {valid['is_win_sim'].mean()*100:.1f}%")

    # Per-trade breakdown for top 7
    print()
    print('=' * 160)
    print('RECOMMENDATION')
    print('=' * 160)
    for sym in top7:
        r = result[result['symbol'] == sym].iloc[0]
        verdict = 'YES - KEEP' if r['sim_$'] > 0 else 'REMOVE - Net negative even with MFE TP'
        print(f"  {sym:8s}  |  ${r['sim_$']:>10,.2f} sim P&L  |  {r['sim_wr%']:5.1f}% WR  |  {r['strategy']:25s}  |  {verdict}")
