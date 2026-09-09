import time
import os
from datetime import datetime

import MetaTrader5 as mt5
import pandas as pd

SYMBOL = "XAUUSD"
TIMEFRAME = mt5.TIMEFRAME_M15
FAST_MA = 20
SLOW_MA = 50
BARS = 250
CHECK_EVERY_SECONDS = 30
CSV_FILE = "signal_log.csv"


def get_closed_candle_signal():
    rates = mt5.copy_rates_from_pos(SYMBOL, TIMEFRAME, 0, BARS)

    if rates is None or len(rates) < SLOW_MA + 3:
        print("ดึงข้อมูลราคาไม่สำเร็จ:", mt5.last_error())
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df["ma_fast"] = df["close"].rolling(FAST_MA).mean()
    df["ma_slow"] = df["close"].rolling(SLOW_MA).mean()

    # -1 คือแท่งที่กำลังก่อตัว, -2 คือแท่งที่ปิดล่าสุด
    previous = df.iloc[-3]
    current = df.iloc[-2]

    signal = "HOLD"

    if previous["ma_fast"] <= previous["ma_slow"] and current["ma_fast"] > current["ma_slow"]:
        signal = "BUY"
    elif previous["ma_fast"] >= previous["ma_slow"] and current["ma_fast"] < current["ma_slow"]:
        signal = "SELL"

    return {
        "candle_time": current["time"],
        "close": current["close"],
        "ma_fast": current["ma_fast"],
        "ma_slow": current["ma_slow"],
        "signal": signal,
    }


def save_log(data):
    row = pd.DataFrame([{
        "logged_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "candle_time": data["candle_time"],
        "symbol": SYMBOL,
        "close": round(data["close"], 2),
        f"ma_{FAST_MA}": round(data["ma_fast"], 2),
        f"ma_{SLOW_MA}": round(data["ma_slow"], 2),
        "signal": data["signal"],
    }])

    file_exists = os.path.exists(CSV_FILE)
    row.to_csv(CSV_FILE, mode="a", header=not file_exists, index=False)


if not mt5.initialize():
    print("เชื่อมต่อ MT5 ไม่สำเร็จ:", mt5.last_error())
    raise SystemExit(1)

if not mt5.symbol_select(SYMBOL, True):
    print(f"เลือก Symbol ไม่สำเร็จ: {SYMBOL}")
    mt5.shutdown()
    raise SystemExit(1)

print(f"เริ่ม Monitor: {SYMBOL} / M15")
print("บอทจะบันทึกเฉพาะเมื่อมีแท่งใหม่ปิดแล้ว")
print("กด Ctrl+C เพื่อหยุด\n")

last_candle_time = None

try:
    while True:
        data = get_closed_candle_signal()

        if data is not None:
            candle_time = data["candle_time"]

            if candle_time != last_candle_time:
                last_candle_time = candle_time
                save_log(data)

                print(
                    f"[{datetime.now():%Y-%m-%d %H:%M:%S}] "
                    f"แท่งปิด: {candle_time} | "
                    f"Close: {data['close']:.2f} | "
                    f"MA{FAST_MA}: {data['ma_fast']:.2f} | "
                    f"MA{SLOW_MA}: {data['ma_slow']:.2f} | "
                    f"Signal: {data['signal']}"
                )

        time.sleep(CHECK_EVERY_SECONDS)

except KeyboardInterrupt:
    print("\nหยุดบอทแล้ว")

finally:
    mt5.shutdown()