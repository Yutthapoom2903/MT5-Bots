import MetaTrader5 as mt5

if not mt5.initialize():
    print("เชื่อมต่อ MT5 ไม่สำเร็จ:", mt5.last_error())
    raise SystemExit(1)

print("เชื่อมต่อ MT5 สำเร็จ")
print("เวอร์ชัน MT5:", mt5.version())

account = mt5.account_info()

if account is None:
    print("ไม่พบข้อมูลบัญชี:", mt5.last_error())
else:
    print(f"เลขบัญชี: {account.login}")
    print(f"Server: {account.server}")
    print(f"Balance: {account.balance}")
    print(f"Equity: {account.equity}")
    print(f"Leverage: 1:{account.leverage}")
    print(f"โหมดบัญชี: {account.trade_mode}")  # 0 มักเป็น Demo

mt5.shutdown()