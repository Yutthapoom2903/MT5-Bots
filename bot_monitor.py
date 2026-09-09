"""เฝ้ากราฟและบันทึกสัญญาณของทุกแท่งที่ปิดลง CSV — ไม่ส่งคำสั่งซื้อขายใดๆ"""

import os
import time
from datetime import datetime

import MetaTrader5 as mt5
import pandas as pd

import mt5_core as core

SYMBOL = "XAUUSD"
TIMEFRAME = mt5.TIMEFRAME_M15
FAST_MA = 20
SLOW_MA = 50
BARS = 250
CHECK_EVERY_SECONDS = 30
CSV_FILE = "signal_log.csv"


def save_log(candle, signal):
    row = pd.DataFrame([{
        "logged_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "candle_time": candle["time"],
        "symbol": SYMBOL,
        "close": round(candle["close"], 2),
        f"ma_{FAST_MA}": round(candle["ma_fast"], 2),
        f"ma_{SLOW_MA}": round(candle["ma_slow"], 2),
        "signal": signal,
    }])

    row.to_csv(CSV_FILE, mode="a", header=not os.path.exists(CSV_FILE), index=False)


def main():
    core.connect()
    core.prepare_symbol(SYMBOL)

    print(f"เริ่ม Monitor: {SYMBOL} / M15")
    print("บันทึกเฉพาะเมื่อมีแท่งใหม่ปิดแล้ว ไม่มีการเปิดออเดอร์")
    print("กด Ctrl+C เพื่อหยุด\n")

    last_candle_time = None

    while True:
        df = core.get_rates(SYMBOL, TIMEFRAME, BARS, min_bars=SLOW_MA + 3)

        if df is None:
            print("ดึงข้อมูลราคาไม่สำเร็จ:", core.last_error_text())
            time.sleep(CHECK_EVERY_SECONDS)
            continue

        core.add_moving_averages(df, FAST_MA, SLOW_MA)

        candle = core.closed_candle(df)

        if candle["time"] != last_candle_time:
            last_candle_time = candle["time"]
            signal = core.crossover_signal(df)
            save_log(candle, signal)

            print(
                f"[{datetime.now():%Y-%m-%d %H:%M:%S}] "
                f"แท่งปิด: {candle['time']} | "
                f"Close: {candle['close']:.2f} | "
                f"MA{FAST_MA}: {candle['ma_fast']:.2f} | "
                f"MA{SLOW_MA}: {candle['ma_slow']:.2f} | "
                f"Signal: {signal}"
            )

        time.sleep(CHECK_EVERY_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except core.MT5Error as error:
        print(error)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\nหยุดบอทแล้ว")
    finally:
        mt5.shutdown()
