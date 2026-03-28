# TradingView -> cTrader Forward Testing Runbook

## 1) Install dependencies
```bash
pip install -r requirements.txt
```

## 2) Configure environment
- Copy `.env.example` values into your shell environment.
- Required for real execution:
  - `WEBHOOK_SHARED_TOKEN`
  - `CTRADER_CLIENT_ID`
  - `CTRADER_CLIENT_SECRET`
  - `CTRADER_ACCOUNT_ID`
  - `CTRADER_ORDER_URL`

## 3) Initialize system
```bash
python run_system.py
```

## 4) Start services
Terminal 1:
```bash
python -m server.webhook_server
```

Terminal 2:
```bash
ngrok http 5000
```

Terminal 3:
```bash
streamlit run dashboard/dashboard.py
```

## 5) TradingView webhook setup
- Webhook URL: `https://<ngrok-url>/webhook`
- Add header (if TradingView proxy supports custom headers): `X-Webhook-Token: <WEBHOOK_SHARED_TOKEN>`
- If header injection is unavailable, route through a relay that adds the header.

### Payload schema (v1)
```json
{
  "indicator_id": "EMA_RSI_V1",
  "symbol": "EURUSD",
  "action": "buy",
  "strategy_id": "EMA_RSI_V1_EURUSD",
  "signal_id": "optional-idempotency-id",
  "timestamp": "2026-03-11T15:00:00Z",
  "risk": 1.0,
  "meta": {
    "timeframe": "15m"
  }
}
```

## 6) Troubleshooting
- `401 unauthorized` from `/webhook`: token mismatch.
- `400 rejected`: payload missing required fields or invalid `action`.
- Execution `status=error`: inspect `error_message` in `executions` table and verify cTrader endpoint/token settings.
- Duplicate signals return `status=duplicate` and increment `dedupe_count`.
