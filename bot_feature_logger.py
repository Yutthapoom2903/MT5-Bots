"""
เก็บสถานะตลาดของทุกแท่งที่ปิดลง CSV เพื่อเอาไปติดป้ายกำกับและเรียนรู้ภายหลัง

ไม่ส่งคำสั่งซื้อขายใดๆ คอลัมน์ท้ายตารางเว้นว่างไว้ให้กรอกเองใน Excel
"""

import os
import time
from datetime import datetime

import MetaTrader5 as mt5
import pandas as pd

import mt5_core as core

SYMBOL = "XAUUSD"
ENTRY_TIMEFRAME = mt5.TIMEFRAME_M15
TREND_TIMEFRAME = mt5.TIMEFRAME_H1

FAST_MA = 20
SLOW_MA = 50
RSI_PERIOD = 14
ATR_PERIOD = 14

BARS_M15 = 300
BARS_H1 = 150
CHECK_EVERY_SECONDS = 30

CSV_FILE = "market_training_data.csv"


def get_h1_trend():
    """ดูแนวโน้มใหญ่จาก H1 ด้วย MA20 / MA50 บนแท่งที่ปิดแล้ว"""
    df = core.get_rates(SYMBOL, TREND_TIMEFRAME, BARS_H1, min_bars=SLOW_MA + 3)

    if df is None:
        return "UNKNOWN"

    core.add_moving_averages(df, FAST_MA, SLOW_MA)
    candle = core.closed_candle(df)

    if pd.isna(candle["ma_fast"]) or pd.isna(candle["ma_slow"]):
        return "UNKNOWN"

    if candle["ma_fast"] > candle["ma_slow"]:
        return "UPTREND"

    if candle["ma_fast"] < candle["ma_slow"]:
        return "DOWNTREND"

    return "SIDEWAY"


def get_market_features():
    """ดึงกราฟ M15 แล้วคำนวณ feature ทั้งหมดของแท่งที่ปิดล่าสุด"""
    min_bars = SLOW_MA + RSI_PERIOD + 5
    df = core.get_rates(SYMBOL, ENTRY_TIMEFRAME, BARS_M15, min_bars)

    if df is None:
        print("ดึงข้อมูลแท่งราคาไม่สำเร็จ:", core.last_error_text())
        return None

    core.add_moving_averages(df, FAST_MA, SLOW_MA)
    df["rsi"] = core.calculate_rsi(df["close"], RSI_PERIOD)
    df["atr"] = core.calculate_atr(df, ATR_PERIOD)

    candle = core.closed_candle(df)

    return {
        "candle_time": candle["time"],
        "open": candle["open"],
        "high": candle["high"],
        "low": candle["low"],
        "close": candle["close"],
        "ma_fast": candle["ma_fast"],
        "ma_slow": candle["ma_slow"],
        "rsi": candle["rsi"],
        "atr": candle["atr"],
        "h1_trend": get_h1_trend(),
        "spread_points": core.spread_points(SYMBOL),
        "candle_range": candle["high"] - candle["low"],
        "candle_body": abs(candle["close"] - candle["open"]),
        "signal": core.crossover_signal(df),
    }


def save_training_row(data):
    """บันทึกสถานะตลาด พร้อมช่องว่างให้ติดป้ายกำกับภายหลัง"""
    row = pd.DataFrame([{
        "logged_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "candle_time": data["candle_time"],
        "symbol": SYMBOL,

        "open": round(data["open"], 2),
        "high": round(data["high"], 2),
        "low": round(data["low"], 2),
        "close": round(data["close"], 2),

        "ma_20": round(data["ma_fast"], 2),
        "ma_50": round(data["ma_slow"], 2),
        "rsi_14": round(data["rsi"], 2),
        "atr_14": round(data["atr"], 2),

        "h1_trend": data["h1_trend"],
        "spread_points": data["spread_points"],
        "candle_range": round(data["candle_range"], 2),
        "candle_body": round(data["candle_body"], 2),
        "bot_signal": data["signal"],

        # กรอกภายหลังใน Excel/CSV
        "your_decision": "",
        "your_reason": "",
        "entry_price": "",
        "stop_loss": "",
        "take_profit": "",
        "trade_result": "",
    }])

    row.to_csv(CSV_FILE, mode="a", header=not os.path.exists(CSV_FILE), index=False)


def main():
    core.connect()
    core.prepare_symbol(SYMBOL)

    print(f"เริ่มเก็บข้อมูลฝึกสอน: {SYMBOL} / M15")
    print("ไม่มีการเปิดออเดอร์")
    print(f"ข้อมูลจะถูกบันทึกใน: {CSV_FILE}")
    print("กด Ctrl+C เพื่อหยุด\n")

    last_candle_time = None

    while True:
        data = get_market_features()

        if data is not None and data["candle_time"] != last_candle_time:
            last_candle_time = data["candle_time"]
            save_training_row(data)

            print(
                f"[{datetime.now():%Y-%m-%d %H:%M:%S}] "
                f"M15: {data['candle_time']} | "
                f"Close: {data['close']:.2f} | "
                f"H1: {data['h1_trend']} | "
                f"RSI: {data['rsi']:.1f} | "
                f"ATR: {data['atr']:.2f} | "
                f"Spread: {data['spread_points']} | "
                f"Signal: {data['signal']}"
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
