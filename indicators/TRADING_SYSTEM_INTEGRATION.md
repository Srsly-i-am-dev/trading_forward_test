# ABC Pattern Indicator - Trading System Integration Guide

**Purpose:** How to connect the ABC Pattern indicator to your automated trading system

---

## 🔗 Integration Overview

```
TradingView Chart
    ↓
Indicator generates BUY/SELL signals at C point
    ↓
Alert message sent to webhook
    ↓
Your Trading System (Flask server on port 5000)
    ↓
Webhook receives signal with entry, TP, SL
    ↓
System executes trade with TP/SL orders
```

---

## 📋 Step 1: Configure TradingView Alert

### In TradingView:

1. **Open your chart** with the ABC Pattern indicator loaded
2. **Create new alert:**
   - Click: **Alerts** (bell icon)
   - Click: **Create Alert**

3. **Configure alert:**
   ```
   Condition:    ABC Pattern - Trade Signals with TP/SL
   Condition:    📈 BUY at ABC C Level  (for buy signals)
   Frequency:    Once Per Bar Close
   ```

4. **Enable webhook:**
   - Check: "Webhook URL"
   - URL: http://localhost:5000/webhook

5. **Add headers:**
   - Header: X-Webhook-Token
   - Value: (your webhook token from .env file)

6. **Create alert for both:**
   - One for: 📈 BUY at ABC C Level
   - One for: 📉 SELL at ABC C Level

---

## 📊 Signal Format Received by System

### Alert Message Example (Bullish):

```
📈 BUY SIGNAL
Entry (C): 211.52
Target (TP): 211.62
Stop Loss (SL): 211.14
Risk/Reward: 1.5:1
```

### Alert Message Example (Bearish):

```
📉 SELL SIGNAL
Entry (C): 211.52
Target (TP): 211.42
Stop Loss (SL): 211.90
Risk/Reward: 2.0:1
```

---

## 🔄 How Your Trading System Processes It

Your current webhook endpoint (`server/webhook_server.py`) will receive:

```json
{
  "indicator_id": "ABC_Pattern_v2",
  "strategy_id": "abc_geometric_target",
  "symbol": "GBPJPY",
  "action": "BUY",
  "payload_json": "📈 BUY SIGNAL\nEntry (C): 211.52\nTarget (TP): 211.62\nStop Loss (SL): 211.14\nRisk/Reward: 1.5:1"
}
```

---

## 📝 Parsing the Alert Message

Your system should extract:

```python
# From alert message, extract these values:
entry_price = 211.52      # Line: "Entry (C): 211.52"
tp_price = 211.62         # Line: "Target (TP): 211.62"
sl_price = 211.14         # Line: "Stop Loss (SL): 211.14"
risk_reward = 1.5         # Line: "Risk/Reward: 1.5:1"

# Action from message:
if "BUY" in message:
    action = "BUY"
elif "SELL" in message:
    action = "SELL"
```

---

## 🎯 Enhanced Signal Schema

**Suggested enhancement to your signal validation:**

Add ABC pattern support to `server/signal_schema.py`:

```python
# Add to VALID_ACTIONS
VALID_ACTIONS = {"BUY", "SELL", "CLOSE"}

# Add ABC pattern normalization
def normalize_abc_signal(payload):
    """
    Extract ABC pattern signal data

    Expected payload format:
    {
        "indicator_id": "ABC_Pattern_v2",
        "strategy_id": "abc_geometric_target",
        "symbol": "GBPJPY",
        "action": "BUY",
        "payload_json": "📈 BUY SIGNAL\n..."
    }
    """
    import re

    payload_text = payload.get("payload_json", "")

    # Extract values using regex
    entry = float(re.search(r'Entry \(C\): (\d+\.\d+)', payload_text).group(1))
    tp = float(re.search(r'Target \(TP\): (\d+\.\d+)', payload_text).group(1))
    sl = float(re.search(r'Stop Loss \(SL\): (\d+\.\d+)', payload_text).group(1))

    return {
        "signal_id": generate_signal_id(),
        "indicator": "ABC_Pattern",
        "symbol": payload.get("symbol"),
        "action": payload.get("action"),
        "entry_price": entry,
        "tp_price": tp,
        "sl_price": sl,
        "risk_reward": tp / sl if action == "BUY" else sl / tp,
        "payload": payload_text
    }
```

---

## 💼 Integration with Executor

Your `executor/ctrader_executor.py` can now use TP/SL:

```python
class CTraderExecutor(BaseExecutor):
    def execute_trade(self, signal):
        """
        Execute trade with ABC pattern TP/SL

        signal format:
        {
            "entry_price": 211.52,
            "tp_price": 211.62,
            "sl_price": 211.14,
            "action": "BUY",
            "symbol": "GBPJPY"
        }
        """

        # Execute order
        if signal["action"] == "BUY":
            order = self.place_buy_order(
                symbol=signal["symbol"],
                entry=signal["entry_price"],
                tp=signal["tp_price"],
                sl=signal["sl_price"],
                volume=self.default_volume
            )
        else:  # SELL
            order = self.place_sell_order(
                symbol=signal["symbol"],
                entry=signal["entry_price"],
                tp=signal["tp_price"],
                sl=signal["sl_price"],
                volume=self.default_volume
            )

        return {
            "status": "executed",
            "order_id": order["id"],
            "entry": signal["entry_price"],
            "tp": signal["tp_price"],
            "sl": signal["sl_price"]
        }
```

---

## 🧪 Testing Integration

### Step 1: Start Your Trading System

```bash
# Terminal 1: Init
python run_system.py

# Terminal 2: Webhook server
python -m server.webhook_server

# Terminal 3: ngrok (for TradingView)
ngrok http 5000

# Terminal 4: Dashboard
streamlit run dashboard/dashboard.py
```

### Step 2: Get Your Public URL

From Terminal 3 (ngrok), copy the HTTPS URL:
```
Forwarding: https://abc123-def456.ngrok-free.app -> http://localhost:5000
```

### Step 3: Configure TradingView Alert with ngrok URL

In TradingView alert settings:
```
Webhook URL: https://abc123-def456.ngrok-free.app/webhook
Header: X-Webhook-Token
Value: pBNYsisvmd7kxs9nNGRk7_Gk--V3Ep23wNbmmx44Oog
```

### Step 4: Test with Manual Alert

Send a test webhook from your terminal:

```bash
# Test BUY signal
curl -X POST https://abc123-def456.ngrok-free.app/webhook \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Token: pBNYsisvmd7kxs9nNGRk7_Gk--V3Ep23wNbmmx44Oog" \
  -d '{
    "indicator_id": "ABC_Pattern_v2",
    "strategy_id": "abc_geometric",
    "symbol": "GBPJPY",
    "action": "BUY",
    "payload_json": "📈 BUY SIGNAL\nEntry (C): 211.52\nTarget (TP): 211.62\nStop Loss (SL): 211.14\nRisk/Reward: 1.5:1"
  }'
```

### Step 5: Verify in Dashboard

Check your dashboard at `http://localhost:8501`:
- Signal should appear in recent signals table
- Execution status should show as "executed" (simulated mode)
- TP/SL values should be logged

---

## 📈 Expected Behavior in Dashboard

### Before Signal:
- No entries in "Recent Signals" table
- "Execution Metrics" shows 0 executed

### After BUY Signal Received:
```
Recent Signals Table:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Signal ID    │ Symbol │ Action │ Status
────────────────────────────────────────
sig_abc123   │ GBPJPY │ BUY    │ executed
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Execution Details:
Entry Price:  211.52
TP Price:     211.62
SL Price:     211.14
Status:       Simulated
```

---

## 🔐 Security Notes

**Never expose your ngrok URL publicly:**
- Token is sent in headers but URL is in browser history
- ngrok URLs are temporary and reset on restart
- Use Strong webhook token from .env

**Token-based auth protects against:**
- Random people sending fake signals
- Unwanted alert messages
- Your system will reject any webhook without correct token

---

## 🚀 Going Live (Real Trading)

**When you're ready to enable real trading:**

1. **In .env file, change:**
   ```
   EXECUTOR_MODE=simulated
   ```
   **to:**
   ```
   EXECUTOR_MODE=real
   ```

2. **Verify cTrader credentials are correct:**
   ```
   CTRADER_CLIENT_ID=<your-id>
   CTRADER_CLIENT_SECRET=<your-secret>
   CTRADER_ACCOUNT_ID=<your-account>
   CTRADER_ENVIRONMENT=demo  # Keep on demo first!
   ```

3. **Test with DEMO account first** (safer):
   ```
   CTRADER_ENVIRONMENT=demo
   ```

4. **Only after successful demo trading:**
   ```
   CTRADER_ENVIRONMENT=live
   ```

5. **Monitor your actual trades** on cTrader platform

---

## 📊 Example Full Flow

### Scenario: GBPJPY Bullish ABC Pattern Completes

1. **12:00 UTC** - Chart forms A point (pivot low)
2. **15:30 UTC** - Chart forms B point (pivot high)
3. **18:45 UTC** - Chart forms C point (pivot low)
   - ✅ C point identified → Signal generated
   - Entry Price = C = 211.52
   - TP = Target = 211.62
   - SL = C - 0.5% = 211.14
   - Alert message created

4. **18:46 UTC** - Alert sent to webhook
   - TradingView → Webhook → Your server
   - System receives signal with TP/SL

5. **18:47 UTC** - Trading system processes:
   - Validates signal authenticity (token check)
   - Parses entry, TP, SL prices
   - In simulated mode: Logs trade
   - In real mode: Sends order to cTrader

6. **18:48 UTC** - Dashboard updates:
   - Shows new BUY signal
   - Displays entry/TP/SL values
   - Shows execution status

7. **Trade active** - Monitor in cTrader:
   - Order placed at 211.52 BUY
   - TP pending at 211.62
   - SL pending at 211.14

8. **19:30 UTC** - Price hits TP at 211.62
   - ✅ TP order closes trade
   - Profit: +0.10 pips per unit
   - Status: Completed

---

## 🎯 Integration Checklist

- [ ] ABC Pattern indicator loaded on chart
- [ ] TP/SL inputs configured as desired
- [ ] TradingView alerts created for BUY and SELL
- [ ] Webhook URL configured (localhost or ngrok)
- [ ] Auth token added to alert headers
- [ ] Trading system running (webhook server active)
- [ ] Test signal sent and verified in dashboard
- [ ] TP/SL prices parsed correctly
- [ ] Simulated execution logs entry, TP, SL
- [ ] Ready for live testing with real signals

---

## 📞 Troubleshooting

### Signal Not Reaching Webhook
- Verify TradingView alert is enabled
- Check ngrok is running and URL is correct
- Confirm webhook token matches .env file
- Check firewall allows webhook traffic

### Prices Parsing Incorrectly
- Verify alert message format matches examples
- Check regex patterns in parsing code
- Ensure decimal format is consistent (X.XX)

### Dashboard Doesn't Update
- Confirm signal reached webhook (check Flask logs)
- Verify database is initialized (`logs/trades.db`)
- Check dashboard refresh interval (5 seconds default)

### Trades Not Executing
- In simulated mode: Should log without error
- In real mode: Verify cTrader credentials are valid
- Check order size doesn't exceed account limits
- Verify symbol format matches cTrader (e.g., "EURUSD" not "EUR/USD")

---

**Last Updated:** 2026-03-16
**Compatibility:** ABC Pattern v2.0 + Forward Testing System
