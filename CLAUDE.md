# CLAUDE.md - Trading Forward Test Project

## MT5 Python API Gotchas
- Always call `mt5.symbol_select(symbol, True)` BEFORE `mt5.symbol_info()` or `mt5.symbol_info_tick()` — tick_value/bid/ask return 0 if symbol isn't selected
- After `symbol_select()`, MT5 may need 0.5s delay before tick data is available — always retry once on tick_value=0 or bid=0
- PUPrime broker uses `.sc` suffix for forex (EURUSD.sc) but NOT for crypto (ADAUSD, ETHUSD) — always verify symbol names with `mt5.symbols_get()`
- Symbols must be visible in Market Watch for tick data to work — `visible=False` means no price data even if symbol exists
- `mt5.symbol_info().trade_tick_value` returns 0 for symbols not in Market Watch even after symbol_select

## PineScript Patterns
- `ta.pivothigh/pivotlow` with lookback=N confirms N bars AFTER the pivot — on 5min chart with lookback=10, that's 50min delay
- Signal detection must happen AFTER TP/SL calculation, not before — signals need proximity filtering against computed TP/SL
- Always add proximity filter: only fire signal if price is within 25% of TP distance from C level — prevents stale entries
- When adding per-symbol lookups (f_mfe_tp_pips, f_exit_strategy), always include fallback to geometric for unknown symbols

## Environment / Architecture
- Demo server: port 5000, .env, ICMarkets (all 19 symbols)
- Live server: port 5001, .env.live, PUPrime-Live2 (7 MFE symbols)
- ngrok required for TradingView webhooks — free tier has latency, URL changes on restart
- Position monitor (executor/position_monitor.py) runs alongside live webhook server — separate terminal
- indicator_id "abc_pattern_mfe" separates live signals from demo

## Python/Bash on Windows
- Use `python -X utf8` for scripts with unicode — Windows charmap codec fails on arrows/special chars
- `pip` not in PATH on this machine — use `python -m pip install`
- F-strings with quotes inside bash -c cause parsing errors — write to .py file and execute instead
- `sys.stdout = io.TextIOWrapper(...)` can cause "I/O operation on closed file" — prefer `python -X utf8`

## Testing Signals
- Always verify all MT5 symbols exist and are visible before going live: `mt5.symbol_info(name)` check visible=True, bid>0
- Test signals can be rejected as duplicates — use unique timestamps to generate new signal_id
- MT5 retcode 10027 = AutoTrading disabled (Ctrl+E), 10017 = trading disabled by broker
