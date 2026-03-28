# Trading Forward Test System - Quick Start Guide

**Last Updated:** 2026-03-16
**System Status:** Ready to run (simulated mode, no real trades)
**Setup Time:** ~2 minutes | **Startup Time:** ~30 seconds

---

## 🚀 Quick Summary

This guide gets your forward testing system running in 4 terminals:
1. **Init & Validation** → `python run_system.py`
2. **Webhook Server** → `python -m server.webhook_server`
3. **Public Tunnel** → `ngrok http 5000`
4. **Analytics Dashboard** → `streamlit run dashboard/dashboard.py`

Then test with webhook payloads to verify the complete signal → execution → logging pipeline.

---

## ✅ Pre-Run Checklist

Before starting, verify these 4 things (should take 30 seconds):

```bash
# 1. Check Python version (need 3.8+)
python --version

# 2. Verify dependencies installed
python -c "import flask; import streamlit; import pandas; print('✓ All dependencies installed')"

# 3. Check .env file exists and is configured
test -f .env && echo "✓ .env file exists" || echo "✗ .env file missing"

# 4. Check project files exist
test -f run_system.py && test -d server && test -d dashboard && echo "✓ Project files OK"
```

If all checks pass ✓, you're ready to run. If any fail ✗, see **Troubleshooting** section below.

---

## 📋 Service Ports & URLs Reference

Keep this handy while running:

| Service | Start Command | Port | URL | Health Check |
|---------|--------------|------|-----|--------------|
| **Webhook Server** | `python -m server.webhook_server` | 5000 | http://localhost:5000 | `curl http://localhost:5000/health` |
| **Dashboard** | `streamlit run dashboard/dashboard.py` | 8501 | http://localhost:8501 | Open in browser |
| **Ngrok Tunnel** | `ngrok http 5000` | - | See ngrok output | Tunnel URL shown in terminal |
| **Database** | Auto-created | - | `logs/trades.db` | File exists check |

---

## 🎯 Step-by-Step Startup

### Terminal 1: Initialize System
```bash
cd C:\Users\DEV\OneDrive\Desktop\trading_forward_test

# Initialize and validate configuration
python run_system.py
```

**Expected Output:**
```
✓ Config loaded successfully
✓ Database initialized at logs/trades.db
✓ Services ready to start

Next steps:
  Terminal 2: python -m server.webhook_server
  Terminal 3: ngrok http 5000
  Terminal 4: streamlit run dashboard/dashboard.py
```

⏱️ **Wait for this to complete** before starting other services.

---

### Terminal 2: Start Webhook Server
```bash
cd C:\Users\DEV\OneDrive\Desktop\trading_forward_test

# Start Flask webhook server (port 5000)
python -m server.webhook_server
```

**Expected Output:**
```
 * Running on http://0.0.0.0:5000
 * Press CTRL+C to quit
```

✓ Server is ready to receive webhooks.

---

### Terminal 3: Start Ngrok Tunnel
```bash
# Expose webhook server to internet via ngrok
ngrok http 5000
```

**Expected Output:**
```
ngrok                                       (Ctrl+C to quit)

Session Status:  online
Account:         [your-email]
Version:         3.x.x
Region:          us-california
Forwarding:      https://abc123-def456.ngrok-free.app -> http://localhost:5000

Web Interface:   http://127.0.0.1:4040
```

📌 **Copy the HTTPS URL** (e.g., `https://abc123-def456.ngrok-free.app`) → This is your webhook endpoint for TradingView.

---

### Terminal 4: Start Dashboard
```bash
cd C:\Users\DEV\OneDrive\Desktop\trading_forward_test

# Start Streamlit analytics dashboard
streamlit run dashboard/dashboard.py
```

**Expected Output:**
```
You can now view your Streamlit app in your browser.

Local URL: http://localhost:8501
Network URL: http://192.168.x.x:8501
```

✓ Open http://localhost:8501 in your browser → Should show empty dashboard (waiting for signals).

---

## ✔️ Health Checks (Verify Everything Works)

Run these commands in **Terminal 5** (or any new terminal) to verify:

```bash
# 1. Check webhook server is healthy
curl -s http://localhost:5000/health
# Expected: {"status":"healthy","timestamp":"2026-03-16T..."}

# 2. Check database exists
test -f logs/trades.db && echo "✓ Database initialized" || echo "✗ Database missing"

# 3. Check ngrok is running
curl -s http://127.0.0.1:4040/api/tunnels | grep -q "active"
if [ $? -eq 0 ]; then echo "✓ Ngrok tunnel active"; else echo "✗ Ngrok tunnel down"; fi
```

---

## 🧪 Test End-to-End Signal Flow

### Option A: Send Test Webhook (Best for verification)

From **Terminal 5**, send a test webhook payload:

```bash
# Set variables
WEBHOOK_URL="http://localhost:5000/webhook"
TOKEN="pBNYsisvmd7kxs9nNGRk7_Gk--V3Ep23wNbmmx44Oog"

# Send test BUY signal
curl -X POST $WEBHOOK_URL \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Token: $TOKEN" \
  -d '{
    "indicator_id": "test_indicator_1",
    "strategy_id": "test_strategy_1",
    "symbol": "EURUSD",
    "action": "BUY",
    "risk": 1.0
  }'
```

**Expected Response:**
```json
{
  "signal_id": "sig_abc123xyz",
  "status": "executed",
  "action": "BUY",
  "symbol": "EURUSD",
  "latency_ms": 145,
  "execution_result": {
    "status": "simulated",
    "broker_order_id": "sim_12345",
    "message": "Simulated execution (EXECUTOR_MODE=simulated)"
  }
}
```

### Option B: Check Dashboard

Refresh http://localhost:8501 → Should show:
- ✓ Signal received in metrics
- ✓ Execution status in tables
- ✓ Charts updating in real-time

---

## 📊 Understanding the Dashboard

Once signals start flowing, you'll see:

| Section | What It Shows |
|---------|---------------|
| **Signal Ingestion** | Total signals received, success rate, last signal timestamp |
| **Execution Metrics** | Orders executed, failed, simulated; average latency |
| **Latency Distribution** | 50th/95th/99th percentile latency (network + processing) |
| **Strategy Coverage** | Matrix showing which indicators × symbol pairs are active |
| **Recent Signals** | Last 20 signals with status, symbol, action, execution result |
| **Execution Details** | Detailed log of each execution attempt |

---

## 🔧 Troubleshooting Quick Fixes

### Problem: "ModuleNotFoundError: No module named 'flask'"
**Solution:** Install dependencies
```bash
pip install -r requirements.txt
```

### Problem: "Address already in use" on port 5000
**Solution:** Find and kill process using port 5000
```bash
# Windows
netstat -ano | findstr :5000
taskkill /PID <PID> /F

# Mac/Linux
lsof -ti:5000 | xargs kill -9
```

### Problem: "ngrok connection failed"
**Solution:** Check ngrok is installed and authenticated
```bash
ngrok --version
ngrok config add-authtoken <your-authtoken>  # Get from https://dashboard.ngrok.com/auth
```

### Problem: Webhook returns 403 "Invalid token"
**Solution:** Verify token in header matches .env file
```bash
# Check token in .env
grep WEBHOOK_SHARED_TOKEN .env

# Use exact token in curl (copy-paste from above)
curl ... -H "X-Webhook-Token: pBNYsisvmd7kxs9nNGRk7_Gk--V3Ep23wNbmmx44Oog"
```

### Problem: Dashboard shows "Connection refused"
**Solution:** Ensure run_system.py was executed first and database initialized
```bash
# Verify database was created
ls -lah logs/trades.db

# Re-initialize if needed
python run_system.py
```

### Problem: All services running but no signals show up
**Solution:** Check ngrok tunnel is capturing traffic
```bash
# Open ngrok web interface (shown in Terminal 3 output)
http://127.0.0.1:4040

# Look at "Requests" tab to see webhook calls coming through
```

---

## 📝 Next Time You Run This System

Just copy-paste this workflow:

```bash
# Terminal 1
python run_system.py

# Terminal 2
python -m server.webhook_server

# Terminal 3
ngrok http 5000

# Terminal 4
streamlit run dashboard/dashboard.py

# Terminal 5
# Send test payload (curl command from "Test End-to-End" section above)
```

**Total time to full system running: ~30 seconds**

---

## 📚 Additional Resources

- **Full Documentation**: See `RUNBOOK.md` for detailed setup and operations guide
- **Project Specification**: See `plan.txt` for system design and architecture
- **Source Code**:
  - `server/webhook_server.py` → Flask webhook server
  - `dashboard/dashboard.py` → Streamlit analytics UI
  - `executor/ctrader_executor.py` → Trade execution logic
  - `database/db.py` → SQLite operations
  - `config.py` → Configuration loader

---

## 🎯 Key Points to Remember

- ✅ **Simulated Mode**: Currently set to `EXECUTOR_MODE=simulated` → No real trades, safe testing
- ✅ **Demo Account**: cTrader environment set to `demo` → Paper trading only
- ✅ **Ngrok Tunnel**: Provides public HTTPS URL for TradingView webhooks
- ✅ **Database**: SQLite at `logs/trades.db` → Auto-created on first run
- ✅ **Health Checks**: Each service has quick validation commands
- ✅ **Real Trades**: To enable real trading, change `EXECUTOR_MODE=real` in `.env` (not recommended until verified)

---

**System Status: Ready to run** 🚀

Questions? Check **Troubleshooting** section or see `RUNBOOK.md` for detailed explanations.
