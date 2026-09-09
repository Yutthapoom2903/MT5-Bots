"""
ฟังก์ชันกลางที่ทุกสคริปต์ในโปรเจกต์นี้ใช้ร่วมกัน

รวมส่วนที่เคยถูกคัดลอกซ้ำอยู่ในทุกบอท: การเชื่อมต่อ MT5, การดึงแท่งราคา,
การคำนวณ indicator และกฎสัญญาณ MA crossover

กฎสำคัญที่ห้ามแก้:
สัญญาณคำนวณจากแท่งที่ปิดแล้วเท่านั้น (iloc[-2]) เทียบกับแท่งก่อนหน้า (iloc[-3])
แท่ง iloc[-1] คือแท่งที่กำลังก่อตัว ห้ามนำมาตัดสินใจ เพราะสัญญาณจะเปลี่ยนกลางแท่ง
"""

import json
import logging
import logging.handlers
import os
import sys
import time as _time
from datetime import datetime, timezone

import MetaTrader5 as mt5
import pandas as pd

# ตำแหน่งแท่งใน DataFrame
FORMING = -1     # แท่งที่กำลังก่อตัว — ห้ามใช้ตัดสินใจ
CLOSED = -2      # แท่งที่ปิดล่าสุด
PREVIOUS = -3    # แท่งก่อนหน้าแท่งที่ปิดล่าสุด


class MT5Error(RuntimeError):
    """ข้อผิดพลาดจากฝั่ง MT5 ที่ทำให้ทำงานต่อไม่ได้"""


# ---------- logging ----------

LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 5


def setup_logging(log_file=None, level=logging.INFO, console_level=logging.INFO):
    """
    ตั้ง logger ให้พิมพ์ออกจอและเขียนไฟล์พร้อมกัน รองรับภาษาไทยบน Windows console

    ไฟล์เก็บละเอียดกว่าจอเสมอ (DEBUG ลงไฟล์ แต่จอเห็นแค่ INFO) เพราะบอทรันทิ้งไว้
    เป็นวันๆ เวลามีปัญหาต้องย้อนดูได้ว่าเกิดอะไรขึ้นทีละรอบ
    ไฟล์หมุนเมื่อโตถึง 5MB เก็บย้อนหลัง 5 ไฟล์ จะได้ไม่กินดิสก์ไม่จำกัด
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(console_level)
    handlers = [console]

    if log_file:
        rotating = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8",
        )
        rotating.setLevel(level)
        handlers.append(rotating)

    logging.basicConfig(
        level=min(level, console_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )
    return logging.getLogger()


def last_error_text():
    """ข้อความ error ล่าสุดจาก MT5 ในรูปแบบที่อ่านออก"""
    code, description = mt5.last_error()
    return f"[{code}] {description}"


# ---------- การเชื่อมต่อ ----------

def connect(**kwargs):
    """เชื่อมต่อ MT5 แล้วคืน account_info — โยน MT5Error ถ้าไม่สำเร็จ"""
    if not mt5.initialize(**kwargs):
        raise MT5Error(f"เชื่อมต่อ MT5 ไม่สำเร็จ: {last_error_text()}")

    account = mt5.account_info()
    if account is None:
        mt5.shutdown()
        raise MT5Error(f"ไม่พบข้อมูลบัญชี: {last_error_text()}")

    return account


def prepare_symbol(symbol):
    """เลือก Symbol เข้า Market Watch แล้วคืน symbol_info — โยน MT5Error ถ้าไม่สำเร็จ"""
    if not mt5.symbol_select(symbol, True):
        raise MT5Error(f"เลือก Symbol {symbol} ไม่สำเร็จ: {last_error_text()}")

    info = mt5.symbol_info(symbol)
    if info is None:
        raise MT5Error(f"ไม่พบข้อมูล Symbol {symbol}: {last_error_text()}")

    return info


GOLD_KEYWORDS = ("XAUUSD", "XAUUS", "GOLD", "XAU")


def resolve_symbol(preferred, keywords=GOLD_KEYWORDS):
    """
    หาชื่อ Symbol ที่ broker นี้ใช้จริง

    broker แต่ละเจ้าตั้งชื่อทองไม่เหมือนกัน (XAUUSD, XAUUSD.m, XAUUSDm, GOLD, GOLD.spot)
    ถ้าชื่อที่ตั้งไว้ใช้ไม่ได้ ให้ลองหาชื่อใกล้เคียงแทนที่จะล้มไปเลย
    คืน (ชื่อที่ใช้ได้, รายชื่อที่เข้าข่ายทั้งหมด) หรือโยน MT5Error ถ้าไม่เจอเลย
    รายการที่สองว่างเปล่าแปลว่าใช้ชื่อเดิมได้ ไม่ได้เดา
    """
    if mt5.symbol_select(preferred, True) and mt5.symbol_info(preferred) is not None:
        return preferred, []

    symbols = mt5.symbols_get()
    if symbols is None:
        raise MT5Error(f"ไม่พบ Symbol {preferred} และดึงรายชื่อไม่ได้: {last_error_text()}")

    upper = preferred.upper()
    candidates = [
        symbol.name for symbol in symbols
        if any(keyword in symbol.name.upper() for keyword in keywords)
    ]

    if not candidates:
        raise MT5Error(
            f"ไม่พบ Symbol {preferred} และไม่เจอชื่อที่ใกล้เคียงเลย "
            f"— ลอง python run.py symbols เพื่อดูรายชื่อทั้งหมด"
        )

    def rank(name):
        """
        ชื่อที่ขึ้นต้นด้วยชื่อเต็มที่ขอมาก่อนเสมอ

        สำคัญมาก: XAUUSD.m กับ XAUEUR.m ยาวเท่ากันและขึ้นต้น XAU เหมือนกัน
        ถ้าเรียงตามตัวอักษรจะได้ XAUEUR.m ซึ่งเป็นทองเทียบยูโร ไม่ใช่ที่ต้องการ
        """
        upper_name = name.upper()
        return (
            not upper_name.startswith(upper),   # XAUUSD.m มาก่อน XAUEUR.m
            upper not in upper_name,            # แล้วค่อยชื่อที่มีคำนั้นอยู่ข้างใน
            len(name),                          # แล้วค่อยชื่อสั้นสุด มักเป็นตัวหลัก
            name,
        )

    ordered = sorted(candidates, key=rank)

    for name in ordered:
        if mt5.symbol_select(name, True) and mt5.symbol_info(name) is not None:
            return name, ordered

    raise MT5Error(f"เจอชื่อใกล้เคียง {ordered[:5]} แต่เลือกเข้า Market Watch ไม่ได้สักตัว")


def is_demo(account):
    """True เมื่อเป็นบัญชี Demo (trade_mode 0)"""
    return account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO


# ---------- ข้อมูลราคา ----------

def get_rates(symbol, timeframe, bars, min_bars=3):
    """ดึงแท่งราคาเป็น DataFrame พร้อมแปลง time — คืน None ถ้าข้อมูลไม่พอ"""
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)

    if rates is None or len(rates) < min_bars:
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def add_moving_averages(df, fast_period, slow_period):
    """เพิ่มคอลัมน์ ma_fast / ma_slow ลงใน DataFrame (แก้ไข df ในที่)"""
    df["ma_fast"] = df["close"].rolling(fast_period).mean()
    df["ma_slow"] = df["close"].rolling(slow_period).mean()
    return df


def crossover_signal(df, fast_col="ma_fast", slow_col="ma_slow"):
    """
    กฎสัญญาณกลางของทั้งโปรเจกต์

    เทียบแท่งก่อนหน้า (iloc[-3]) กับแท่งที่ปิดล่าสุด (iloc[-2])
    ma_fast ตัดขึ้นเหนือ ma_slow = BUY, ตัดลง = SELL, นอกนั้น = HOLD
    """
    if len(df) < abs(PREVIOUS):
        return "HOLD"

    previous = df.iloc[PREVIOUS]
    current = df.iloc[CLOSED]

    values = (previous[fast_col], previous[slow_col], current[fast_col], current[slow_col])
    if any(pd.isna(value) for value in values):
        return "HOLD"

    if previous[fast_col] <= previous[slow_col] and current[fast_col] > current[slow_col]:
        return "BUY"

    if previous[fast_col] >= previous[slow_col] and current[fast_col] < current[slow_col]:
        return "SELL"

    return "HOLD"


def closed_candle(df):
    """แท่งที่ปิดล่าสุด — แท่งเดียวที่อนุญาตให้ใช้ตัดสินใจ"""
    return df.iloc[CLOSED]


# ---------- indicator ----------

def calculate_rsi(series, period=14):
    """RSI แบบ Wilder (ewm) ให้ตรงกับค่าที่ MT5 และ TradingView แสดง"""
    delta = series.diff()

    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    avg_gain = gains.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1 / period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return (100 - (100 / (1 + rs))).fillna(100)


def calculate_atr(df, period=14):
    """ATR แบบ Wilder เพื่อวัดความผันผวน"""
    previous_close = df["close"].shift(1)

    true_range = pd.concat([
        df["high"] - df["low"],
        (df["high"] - previous_close).abs(),
        (df["low"] - previous_close).abs(),
    ], axis=1).max(axis=1)

    return true_range.ewm(alpha=1 / period, adjust=False).mean()


def broker_gmt_offset(symbol):
    """
    ส่วนต่างเวลาเซิร์ฟเวอร์ broker กับ UTC เป็นชั่วโมง

    จำเป็นสำหรับตั้ง SESSION_HOURS ให้ถูก เพราะเวลาในแท่งราคาเป็นเวลาเซิร์ฟเวอร์
    ไม่ใช่เวลาไทยและไม่ใช่ UTC  broker ส่วนใหญ่อยู่ GMT+2/+3 และขยับตาม DST ด้วย
    คืน None ถ้าดึงราคาไม่ได้
    """
    tick = mt5.symbol_info_tick(symbol)

    if tick is None or not tick.time:
        return None

    server = datetime.fromtimestamp(tick.time, tz=timezone.utc)
    now = datetime.now(timezone.utc)

    return round((server - now).total_seconds() / 3600)


def spread_points(symbol):
    """ค่า spread ปัจจุบันเป็น point — คืน None ถ้าดึงราคาไม่ได้"""
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)

    if tick is None or info is None or not info.point:
        return None

    return round((tick.ask - tick.bid) / info.point, 1)


# ---------- state ที่อยู่รอดข้ามการ restart ----------

def load_state(path):
    """อ่าน state ที่บันทึกไว้ — คืน dict ว่างถ้าไฟล์ไม่มีหรือเสีย"""
    if not os.path.exists(path):
        return {}

    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(path, state):
    """บันทึก state ลงไฟล์ — เขียนไฟล์ชั่วคราวก่อนแล้วค่อย replace กันไฟล์พังกลางคัน"""
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def calculate_adx(df, period=14):
    """
    ADX แบบ Wilder — วัดว่า "มีเทรนด์แค่ไหน" ไม่ได้บอกทิศ

    ต่ำกว่า 20 ถือว่าตลาดไม่มีเทรนด์ (sideway) ซึ่งเป็นสภาพที่ MA crossover
    ให้สัญญาณหลอกถี่ที่สุด จึงใช้เป็นตัวกรองหลักของบอท
    """
    high, low, close = df["high"], df["low"], df["close"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = ((up_move > down_move) & (up_move > 0)) * up_move.clip(lower=0)
    minus_dm = ((down_move > up_move) & (down_move > 0)) * down_move.clip(lower=0)

    previous_close = close.shift(1)
    true_range = pd.concat([
        high - low,
        (high - previous_close).abs(),
        (low - previous_close).abs(),
    ], axis=1).max(axis=1)

    alpha = 1 / period
    atr = true_range.ewm(alpha=alpha, adjust=False).mean()

    plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr.replace(0, float("nan"))
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr.replace(0, float("nan"))

    di_sum = (plus_di + minus_di).replace(0, float("nan"))
    dx = 100 * (plus_di - minus_di).abs() / di_sum

    return dx.ewm(alpha=alpha, adjust=False).mean()


def ma_trend(df):
    """ทิศของ MA บนแท่งที่ปิดแล้ว — UPTREND / DOWNTREND / SIDEWAY / UNKNOWN"""
    if df is None or len(df) < abs(PREVIOUS):
        return "UNKNOWN"

    candle = df.iloc[CLOSED]

    if pd.isna(candle.get("ma_fast")) or pd.isna(candle.get("ma_slow")):
        return "UNKNOWN"

    if candle["ma_fast"] > candle["ma_slow"]:
        return "UPTREND"

    if candle["ma_fast"] < candle["ma_slow"]:
        return "DOWNTREND"

    return "SIDEWAY"
