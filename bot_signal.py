import MetaTrader5 as mt5
import pandas as pd

SYMBOL = "XAUUSD"
TIMEFRAME = mt5.TIMEFRAME_M15
FAST_MA = 20
SLOW_MA = 50
BARS = 250

if not mt5.initialize():
    print("เชื่อมต่อ MT5 ไม่สำเร็จ:", mt5.last_error())
    raise SystemExit(1)

if not mt5.symbol_select(SYMBOL, True):
    print(f"ไม่สามารถเลือก Symbol {SYMBOL} ได้:", mt5.last_error())
    mt5.shutdown()
    raise SystemExit(1)

rates = mt5.copy_rates_from_pos(SYMBOL, TIMEFRAME, 0, BARS)

if rates is None or len(rates) < SLOW_MA + 3:
    print("ข้อมูลแท่งราคาไม่เพียงพอ:", mt5.last_error())
    mt5.shutdown()
    raise SystemExit(1)

df = pd.DataFrame(rates)
df["time"] = pd.to_datetime(df["time"], unit="s")
df["ma_fast"] = df["close"].rolling(FAST_MA).mean()
df["ma_slow"] = df["close"].rolling(SLOW_MA).mean()

# ใช้แท่งปิดแล้วเท่านั้น เพื่อไม่ให้สัญญาณเปลี่ยนระหว่างแท่งกำลังก่อตัว
previous = df.iloc[-3]
current = df.iloc[-2]

signal = "HOLD"

if previous["ma_fast"] <= previous["ma_slow"] and current["ma_fast"] > current["ma_slow"]:
    signal = "BUY"
elif previous["ma_fast"] >= previous["ma_slow"] and current["ma_fast"] < current["ma_slow"]:
    signal = "SELL"

print(f"Symbol: {SYMBOL}")
print(f"Timeframe: M15")
print(f"แท่งล่าสุดที่ปิด: {current['time']}")
print(f"Close: {current['close']:.2f}")
print(f"MA {FAST_MA}: {current['ma_fast']:.2f}")
print(f"MA {SLOW_MA}: {current['ma_slow']:.2f}")
print(f"Signal: {signal}")

mt5.shutdown()