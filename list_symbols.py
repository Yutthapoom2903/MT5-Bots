import MetaTrader5 as mt5

if not mt5.initialize():
    print("เชื่อมต่อ MT5 ไม่สำเร็จ:", mt5.last_error())
    raise SystemExit(1)

symbols = mt5.symbols_get()

if symbols is None:
    print("ดึงรายชื่อ Symbol ไม่สำเร็จ:", mt5.last_error())
else:
    keywords = ("XAU", "GOLD", "EURUSD", "BTC")
    
    for symbol in symbols:
        name = symbol.name.upper()
        if any(keyword in name for keyword in keywords):
            print(symbol.name)

mt5.shutdown()