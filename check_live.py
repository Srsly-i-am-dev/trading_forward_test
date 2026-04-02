"""Quick check of live MT5 account status and open positions."""
import MetaTrader5 as mt5

mt5.initialize()
mt5.login(24017565, server="PUPrime-Live2")
info = mt5.account_info()
if info:
    print(f"Account: {info.login} | Balance: ${info.balance:.2f} | Equity: ${info.equity:.2f} | Profit: ${info.profit:.2f}")
    print(f"Server: {info.server} | Leverage: 1:{info.leverage}")
else:
    print("ERROR: Cannot connect to PUPrime-Live2")
    print("Last error:", mt5.last_error())
    mt5.shutdown()
    exit(1)

positions = mt5.positions_get()
if positions:
    print(f"\nOpen positions: {len(positions)}")
    for p in positions:
        side = "BUY" if p.type == 0 else "SELL"
        print(f"  {p.symbol:12s} {side:4s} | vol={p.volume:.2f} | entry={p.price_open:.5f} | current={p.price_current:.5f} | profit=${p.profit:.2f} | SL={p.sl:.5f} | TP={p.tp:.5f} | ticket={p.ticket}")
else:
    print("\nNo open positions")
mt5.shutdown()
