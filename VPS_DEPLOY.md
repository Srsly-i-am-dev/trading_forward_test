# VPS Deployment Guide — Live Trading Server

**System**: ABC Pattern MFE Forward Test
**Account**: PUPrime-Live2 (24017565)
**Last Updated**: 2026-04-07

---

## Prerequisites

- **Windows VPS** (MT5 only runs on Windows)
- **MetaTrader 5** installed and logged into PUPrime-Live2 account 24017565
- **Python 3.11+** installed
- **Git** installed
- **ngrok** installed and authenticated (or a static IP/domain)
- **AutoTrading enabled** in MT5 (Ctrl+E — green icon in toolbar)

---

## Step 1: Clone the Repo

```powershell
cd C:\Trading
git clone https://github.com/YOUR_USERNAME/trading_forward_test.git
cd trading_forward_test
```

If already cloned, pull latest changes:

```powershell
cd C:\Trading\trading_forward_test
git pull origin main
```

---

## Step 2: Install Python Dependencies

```powershell
python -m pip install flask python-dotenv MetaTrader5 pandas requests
```

Minimum required packages for live trading:
- `flask` — webhook server
- `python-dotenv` — .env config loading
- `MetaTrader5` — trade execution
- `pandas` — data handling (used by MT5 library)
- `requests` — HTTP utilities

---

## Step 3: Verify `.env.live` Configuration

The `.env.live` file should already be correct from the repo. Verify these critical values:

```ini
# Server
SERVER_HOST=0.0.0.0
SERVER_PORT=5001

# MT5 Account
MT5_LOGIN=24017565
MT5_PASSWORD=U40pX5K$
MT5_SERVER=PUPrime-Live2
MT5_MAGIC=234001

# Trading
RISK_PER_TRADE=10
EXECUTOR_MODE=real

# Symbols (9 MFE forex pairs)
ALLOWED_SYMBOLS=EURUSD,EURJPY,GBPAUD,USDCHF,GBPUSD,AUDJPY,EURAUD,NZDUSD,USDJPY

# Symbol mapping (PUPrime uses .sc suffix for forex)
MT5_SYMBOL_MAP={"EURUSD":"EURUSD.sc","EURJPY":"EURJPY.sc","GBPAUD":"GBPAUD.sc","USDCHF":"USDCHF.sc","GBPUSD":"GBPUSD.sc","AUDJPY":"AUDJPY.sc","EURAUD":"EURAUD.sc","NZDUSD":"NZDUSD.sc","USDJPY":"USDJPY.sc"}

# Signal filter
MIN_RR_RATIO=0.75
```

---

## Step 4: Verify MT5 is Ready

Open a Python terminal and run:

```powershell
python -X utf8 -c "
import MetaTrader5 as mt5
mt5.initialize()
info = mt5.account_info()
print(f'Account: {info.login}, Balance: {info.balance}, Server: {info.server}')

# Check all 9 symbols are visible
symbols = ['EURUSD.sc','EURJPY.sc','GBPAUD.sc','USDCHF.sc','GBPUSD.sc','AUDJPY.sc','EURAUD.sc','NZDUSD.sc','USDJPY.sc']
for s in symbols:
    mt5.symbol_select(s, True)
    info = mt5.symbol_info(s)
    tick = mt5.symbol_info_tick(s)
    bid = tick.bid if tick else 0
    vis = info.visible if info else False
    tv = info.trade_tick_value if info else 0
    print(f'  {s:14s} visible={vis}  bid={bid:.5f}  tick_value={tv}')

mt5.shutdown()
"
```

**Expected**: All 9 symbols should show `visible=True`, `bid > 0`, `tick_value > 0`.

**If tick_value=0**: The symbol needs to be added to Market Watch in MT5 manually. Open MT5 > View > Market Watch > right-click > Show All.

**If AutoTrading is disabled**: Press Ctrl+E in MT5 until the green icon appears.

---

## Step 5: Start the Live Server

Open **Terminal 1** (Webhook Server):

```powershell
cd C:\Trading\trading_forward_test
python -X utf8 -m server.webhook_server_live
```

**Expected output**:
```
LIVE server -- allowed symbols: AUDJPY, EURAUD, EURUSD, ...
LIVE server -- minimum RR ratio: 0.75:1
MT5 logged in: account=24017565, server=PUPrime-Live2, balance=XXXX
 * Running on http://0.0.0.0:5001
```

---

## Step 6: Start the Position Monitor

Open **Terminal 2** (Position Monitor):

```powershell
cd C:\Trading\trading_forward_test
python -X utf8 executor/position_monitor.py
```

**Expected output**:
```
Position monitor started: account=24017565, server=PUPrime-Live2
Monitoring symbols: AUDJPY.sc, EURAUD.sc, EURUSD.sc, ...
Check interval: 30 seconds
```

---

## Step 7: Start ngrok Tunnel

Open **Terminal 3** (ngrok):

```powershell
ngrok http 5001
```

**Copy the HTTPS forwarding URL** (e.g., `https://xxxx-xxxx.ngrok-free.app`).

Set this as the TradingView webhook URL:
```
https://xxxx-xxxx.ngrok-free.app/webhook?token=pBNYsisvmd7kxs9nNGRk7_Gk--V3Ep23wNbmmx44Oog
```

---

## Step 8: Verify End-to-End

Send a test webhook from Terminal 4:

```powershell
curl -X POST http://localhost:5001/webhook -H "Content-Type: application/json" -H "X-Webhook-Token: pBNYsisvmd7kxs9nNGRk7_Gk--V3Ep23wNbmmx44Oog" -d "{\"indicator_id\":\"abc_pattern_mfe\",\"symbol\":\"EURUSD\",\"action\":\"buy\",\"risk\":1.0,\"meta\":{\"tp\":1.20,\"sl\":1.15,\"c_level\":1.16,\"rr\":\"2:1\",\"tp_mode\":\"MFE Optimal\"}}"
```

**Expected**: Server should log the signal. If market is open and AutoTrading is on, two positions should appear in MT5:
- One with comment `TV_XXXX_GEO` (geometric TP + trailing stop)
- One with comment `TV_XXXX_MFE` (MFE TP + time exit)

---

## How the Dual-Position System Works

When a TradingView signal arrives, the server opens **TWO positions** for every trade:

| Position | Comment | TP | SL | Exit Strategy |
|----------|---------|-----|------|---------------|
| **GEO** | `TV_XXXX_GEO` | 50% of geometric ABC projection | 0.1% beyond C level | Trailing stop (activates at 50% of TP, trails at 25%) |
| **MFE** | `TV_XXXX_MFE` | MFE-optimized TP from indicator | 0.1% beyond C level (same) | Time exit (360-480 min) |

Both positions have the **same SL** (0.1% beyond C level) and the **same lot size** (risk-based from SL distance).

**Safety checks**:
- If SL is too close to current price (< broker minimum), the trade is rejected
- If RR < 0.75:1, the signal is rejected
- If C level is missing, falls back to original signal SL

---

## Files That Changed (What's New)

| File | What Changed |
|------|-------------|
| `executor/mt5_executor.py` | Dual order execution (GEO+MFE), global SL from C level, min SL guard, GEO_MULTIPLIER per pair |
| `executor/position_monitor.py` | Comment-based routing (_GEO/_MFE), GEO trailing stop, extended time exits, updated PIP_SIZES |
| `server/webhook_server_live.py` | Min RR filter (0.75:1), rejects bad signals before execution |
| `.env.live` | Removed crypto (ADAUSD/ETHUSD), added 4 forex pairs, added MIN_RR_RATIO |

---

## Pulling Updates on VPS

When you make changes locally and push to GitHub:

```powershell
# On VPS
cd C:\Trading\trading_forward_test
git pull origin main

# Then restart both services:
# 1. Stop webhook server (Ctrl+C in Terminal 1), restart it
# 2. Stop position monitor (Ctrl+C in Terminal 2), restart it
```

---

## Troubleshooting

### "MT5 initialize failed"
- Make sure MetaTrader 5 is running on the VPS
- Make sure the MT5 terminal is logged into the correct account

### "No tick data for XXXXX.sc"
- Open MT5 > View > Market Watch
- Right-click > Show All (or search and add the specific symbol)
- Wait 5 seconds, then retry

### "AutoTrading disabled" (retcode 10027)
- Press Ctrl+E in MT5 terminal
- Green icon should appear in the toolbar
- Also check: Tools > Options > Expert Advisors > "Allow automated trading" must be checked

### "SL_TOO_CLOSE" rejections
- This means C level is very close to the current price
- Normal behavior — the signal is skipped safely
- Check server logs: `grep SL_TOO_CLOSE` in terminal output

### Server crashes / restarts
- The position monitor will reconnect automatically (built-in MT5 reconnect)
- The webhook server needs manual restart
- Consider using a process manager like `pm2` or a Windows Task Scheduler to auto-restart

### ngrok URL changed after restart
- Free ngrok gives a new URL each restart
- Update TradingView webhook URL with the new ngrok URL
- Consider ngrok paid plan for a static domain

---

## Checklist Before Going Live

- [ ] MT5 is running and logged into account 24017565
- [ ] AutoTrading is enabled (green icon, Ctrl+E)
- [ ] All 9 symbols visible in Market Watch with bid > 0
- [ ] `python -X utf8 -m server.webhook_server_live` starts without errors
- [ ] `python -X utf8 executor/position_monitor.py` starts without errors
- [ ] ngrok tunnel is active on port 5001
- [ ] TradingView webhook URL updated to new ngrok URL
- [ ] TradingView alerts are active for all 9 symbols
- [ ] Test webhook returns 200 OK
- [ ] Market is open (check trading hours)
