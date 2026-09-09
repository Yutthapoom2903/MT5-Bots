import time
import os
from datetime import datetime

import MetaTrader5 as mt5
import pandas as pd

# ---------- ตั้งค่าหลัก ----------
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


def calculate_rsi(series, period=14):
    """คำนวณ RSI จากราคาปิด"""
    delta = series.diff()

    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    avg_gain = gains.rolling(period).mean()
    avg_loss = losses.rolling(period).mean()

    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def calculate_atr(df, period=14):
    """คำนวณ ATR เพื่อวัดความผันผวน"""
    previous_close = df["close"].shift(1)

    true_range = pd.concat([
        df["high"] - df["low"],
        (df["high"] - previous_close).abs(),
        (df["low"] - previous_close).abs(),
    ], axis=1).max(axis=1)

    return true_range.rolling(period).mean()


def get_h1_trend():
    """ดูแนวโน้มใหญ่จาก H1 ด้วย MA20 / MA50"""
    rates = mt5.copy_rates_from_pos(SYMBOL, TREND_TIMEFRAME, 0, BARS_H1)

    if rates is None or len(rates) < SLOW_MA + 3:
        return "UNKNOWN"

    df = pd.DataFrame(rates)
    df["ma_fast"] = df["close"].rolling(FAST_MA).mean()
    df["ma_slow"] = df["close"].rolling(SLOW_MA).mean()

    # ใช้แท่งที่ปิดแล้ว
    candle = df.iloc[-2]

    if candle["ma_fast"] > candle["ma_slow"]:
        return "UPTREND"
    elif candle["ma_fast"] < candle["ma_slow"]:
        return "DOWNTREND"

    return "SIDEWAY"


def get_market_features():
    """ดึงข้อมูลกราฟ M15 และคำนวณ feature สำหรับนำไปเรียนรู้"""
    rates = mt5.copy_rates_from_pos(SYMBOL, ENTRY_TIMEFRAME, 0, BARS_M15)

    if rates is None or len(rates) < SLOW_MA + RSI_PERIOD + 5:
        print("ดึงข้อมูลแท่งราคาไม่สำเร็จ:", mt5.last_error())
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")

    df["ma_fast"] = df["close"].rolling(FAST_MA).mean()
    df["ma_slow"] = df["close"].rolling(SLOW_MA).mean()
    df["rsi"] = calculate_rsi(df["close"], RSI_PERIOD)
    df["atr"] = calculate_atr(df, ATR_PERIOD)

    # -1 = แท่งที่กำลังก่อตัว, -2 = แท่งที่ปิดล่าสุด
    previous = df.iloc[-3]
    current = df.iloc[-2]

    signal = "HOLD"

    if previous["ma_fast"] <= previous["ma_slow"] and current["ma_fast"] > current["ma_slow"]:
        signal = "BUY"
    elif previous["ma_fast"] >= previous["ma_slow"] and current["ma_fast"] < current["ma_slow"]:
        signal = "SELL"

    tick = mt5.symbol_info_tick(SYMBOL)
    point = mt5.symbol_info(SYMBOL).point

    if tick is None or point is None:
        spread_points = None
    else:
        spread_points = round((tick.ask - tick.bid) / point, 1)

    candle_range = current["high"] - current["low"]
    candle_body = abs(current["close"] - current["open"])

    return {
        "candle_time": current["time"],
        "open": current["open"],
        "high": current["high"],
        "low": current["low"],
        "close": current["close"],
        "ma_fast": current["ma_fast"],
        "ma_slow": current["ma_slow"],
        "rsi": current["rsi"],
        "atr": current["atr"],
        "h1_trend": get_h1_trend(),
        "spread_points": spread_points,
        "candle_range": candle_range,
        "candle_body": candle_body,
        "signal": signal,
    }


def save_training_row(data):
    """บันทึกสถานะตลาด พร้อมช่องว่างให้คุณติดป้ายกำกับภายหลัง"""
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

        # คุณกรอกภายหลังใน Excel/CSV
        "your_decision": "",
        "your_reason": "",
        "entry_price": "",
        "stop_loss": "",
        "take_profit": "",
        "trade_result": "",
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

print(f"เริ่มเก็บข้อมูลฝึกสอน: {SYMBOL} / M15")
print("ยังไม่มีการเปิดออเดอร์")
print(f"ข้อมูลจะถูกบันทึกใน: {CSV_FILE}")
print("กด Ctrl+C เพื่อหยุด\n")

last_candle_time = None

try:
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

except KeyboardInterrupt:
    print("\nหยุดบอทแล้ว")

finally:
    mt5.shutdown()