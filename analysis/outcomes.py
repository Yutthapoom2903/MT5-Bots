"""
หลังจากแท่งนั้นราคาไปทางไหนต่อ — ติดป้ายผลลัพธ์ให้แท่งที่บอทบันทึกไว้เอง

market_training_data.csv เก็บ OHLC ของทุกแท่ง M15 ที่ปิดแล้ว แถวถัดๆ ไปในไฟล์
จึงเป็น "อนาคต" ของแถวก่อนหน้าอยู่ในตัว ตอบได้เลยว่าแท่งนั้นตามด้วยอะไร
โดยไม่ต้องต่อ MT5 และไม่ต้องรอคนกรอกผลด้วยมือ

ประเด็นไม่ใช่การไล่หาไม้ที่พลาดไป แต่คือการวัดตัวกรอง ทุกแท่งได้ป้ายหนึ่งใบ
ไม่ใช่เฉพาะแท่งที่มีสัญญาณตัดกัน (ซึ่งมีวันละ 2-3 แท่ง) ป้ายจึงสะสมเร็วกว่ากันสามสิบเท่า
และตอบได้ว่า ADX >= 20 แยกแท่งที่เทรนด์ไปต่อ ออกจากแท่งที่ไม่ไปต่อ ได้จริงหรือเปล่า

ไฟล์นี้บริสุทธิ์ทั้งไฟล์ — DataFrame เข้า DataFrame ออก ไม่แตะ MT5 ไม่แตะดิสก์
สิ่งที่มันวัดคือ "ตลาดหลังแท่งนั้น" ไม่ใช่ "ไม้ที่บอทจะได้" — ไม่มี spread ไม่มี
การเลื่อน stop ไม่มีการจัดการไม้ ของพวกนั้นอยู่ใน backtest.simulate() ซึ่งจำลองครบ
ตัวเลขสองที่นี้จึงไม่ควรเอามาเทียบกันตรงๆ
"""

import numpy as np
import pandas as pd

from bot import strategy

HORIZON = 8              # แท่ง M15 ที่มองไปข้างหน้า = 2 ชั่วโมง
SL_ATR_MULT = 1.5        # ต้องตรงกับ runner.SL_ATR_MULT — มีเทสตรึงไว้
MIN_SAMPLE = 30          # น้อยกว่านี้ยังไม่เรียกว่าหลักฐาน
CANDLE_MINUTES = 15

FORWARD_COLUMNS = ("fwd_up_r", "fwd_down_r", "fwd_net_r", "fwd_trend_r")

DIRECTIONS = {"UPTREND": 1.0, "DOWNTREND": -1.0}


def _usable_rows(times, horizon, minutes=CANDLE_MINUTES):
    """แถวที่มีแท่งถัดไปครบ horizon แท่งติดกันจริง ไม่มีช่องขาดคั่น

    ถ้าบอทดับไปหนึ่งชั่วโมง แถวถัดไปในไฟล์ไม่ใช่แท่งถัดไปในตลาด การนับข้ามช่อง
    แบบนั้นทำให้ป้ายเป็นผลของเวลาที่ผิด — ทิ้งแถวนั้นดีกว่าติดป้ายมั่ว
    """
    step = pd.Timedelta(minutes=minutes)
    gaps = times.diff() != step          # แถวแรกเป็น NaT จึงนับเป็นช่องขาดโดยปริยาย
    usable = []

    for index in range(len(times) - horizon):
        if not gaps.iloc[index + 1:index + horizon + 1].any():
            usable.append(index)

    return usable


def add_forward_outcomes(frame, horizon=HORIZON, sl_mult=SL_ATR_MULT):
    """เติมคอลัมน์ fwd_* ให้ทุกแถวที่มองไปข้างหน้าได้ครบ แถวที่มองไม่ได้เป็น NaN

    วัดเป็น R (ระยะ stop = ATR x sl_mult) ไม่ใช่ดอลลาร์ วันที่ ATR 13 กับวันที่ ATR 6
    จึงเทียบกันได้ ซึ่งเป็นเหตุผลเดียวกับที่ backtest รายงานเป็น R

        fwd_up_r     ราคาขึ้นไปสูงสุดกี่ R นับจาก close ของแท่งนี้
        fwd_down_r   ราคาลงไปต่ำสุดกี่ R (ติดลบ)
        fwd_net_r    ปลาย horizon อยู่ห่างจาก close ของแท่งนี้กี่ R
        fwd_trend_r  fwd_net_r ที่เซ็นตามเทรนด์ H1 — บวกแปลว่าเทรนด์ไปต่อ
    """
    out = frame.copy()
    times = pd.to_datetime(out.get("candle_time"), errors="coerce")

    out = out[times.notna()].copy()
    out["_time"] = times.dropna().values
    out = out.sort_values("_time").reset_index(drop=True)

    for column in FORWARD_COLUMNS:
        out[column] = np.nan

    if len(out) <= horizon:
        return out.drop(columns="_time")

    def numbers(name):
        """คอลัมน์ตัวเลข — เต็มไปด้วย NaN ถ้าไฟล์ยังไม่มีคอลัมน์นั้น (ชุดคอลัมน์เปลี่ยนมาหลายรอบ)"""
        if name not in out:
            return np.full(len(out), np.nan)

        return pd.to_numeric(out[name], errors="coerce").to_numpy(dtype=float)

    close, high, low = numbers("close"), numbers("high"), numbers("low")
    risk = numbers("atr_14") * sl_mult
    trend = out.get("h1_trend", pd.Series(index=out.index, dtype=object))

    for index in _usable_rows(out["_time"], horizon):
        step = risk[index]
        if not step > 0:
            continue    # แถวเก่าก่อน 2026-09-09 ไม่มี atr_14 หรือ ATR เป็นศูนย์

        ahead = slice(index + 1, index + horizon + 1)
        here = close[index]

        out.loc[index, "fwd_up_r"] = (np.nanmax(high[ahead]) - here) / step
        out.loc[index, "fwd_down_r"] = (np.nanmin(low[ahead]) - here) / step
        out.loc[index, "fwd_net_r"] = net = (close[index + horizon] - here) / step

        direction = DIRECTIONS.get(str(trend.iloc[index]).strip().upper())
        if direction:
            out.loc[index, "fwd_trend_r"] = net * direction

    return out.drop(columns="_time")


def _stats(values):
    """สรุปฝั่งหนึ่งของการแบ่ง — None ถ้าฝั่งนั้นว่าง"""
    values = pd.Series(values).dropna()

    if not len(values):
        return None

    return {
        "count": len(values),
        "mean": values.mean(),
        "median": values.median(),
        "positive": (values > 0).mean(),
    }


def split_by(frame, column, threshold, measure="fwd_trend_r", above=True):
    """แบ่งแท่งด้วยเกณฑ์หนึ่งข้อ แล้วเทียบว่าหลังจากนั้นเกิดอะไรขึ้นสองฝั่ง

    above=True  หมายถึงฝั่งที่ "ผ่าน" คือค่ามากกว่าหรือเท่ากับเกณฑ์ (เช่น ADX)
    above=False หมายถึงฝั่งที่ "ผ่าน" คือค่าน้อยกว่าหรือเท่ากับเกณฑ์ (เช่น spread)
    """
    if column not in frame or measure not in frame:
        return None

    values = pd.to_numeric(frame[column], errors="coerce")
    outcome = pd.to_numeric(frame[measure], errors="coerce")
    known = values.notna() & outcome.notna()

    passes = (values >= threshold) if above else (values <= threshold)

    return {
        "passed": _stats(outcome[known & passes]),
        "blocked": _stats(outcome[known & ~passes]),
    }


def split_by_agreement(frame, measure="fwd_trend_r"):
    """M5 เห็นตรงกับ H1 ไหม — ตัวกรองยืนยันที่ไม่ใช่ตัวเลข จึงแบ่งด้วยการเทียบข้อความ"""
    if "h1_trend" not in frame or "m5_trend" not in frame or measure not in frame:
        return None

    outcome = pd.to_numeric(frame[measure], errors="coerce")
    known = outcome.notna() & frame["h1_trend"].notna() & frame["m5_trend"].notna()
    agrees = frame["h1_trend"] == frame["m5_trend"]

    return {
        "passed": _stats(outcome[known & agrees]),
        "blocked": _stats(outcome[known & ~agrees]),
    }


def _side_line(label, stats):
    if stats is None:
        return f"  {label:<8} ไม่มีแท่งฝั่งนี้เลย"

    return (
        f"  {label:<8} n={stats['count']:<4} เฉลี่ย {stats['mean']:+.2f}R  "
        f"ค่ากลาง {stats['median']:+.2f}R  เป็นบวก {stats['positive']:.0%}"
    )


def format_split(title, split, min_sample=MIN_SAMPLE):
    """สองบรรทัดของฝั่งที่ผ่าน/ไม่ผ่าน แล้วปิดท้ายด้วยส่วนต่างและคำเตือนขนาดตัวอย่าง"""
    lines = [title]

    if split is None:
        lines.append("  ยังไม่มีคอลัมน์ที่ต้องใช้")
        return lines

    passed, blocked = split["passed"], split["blocked"]
    lines.append(_side_line("ผ่าน", passed))
    lines.append(_side_line("ไม่ผ่าน", blocked))

    if passed is None or blocked is None:
        lines.append("  แบ่งไม่ได้ ทุกแท่งอยู่ฝั่งเดียวกันหมด")
        return lines

    smallest = min(passed["count"], blocked["count"])
    gap = passed["mean"] - blocked["mean"]
    verdict = f"  ส่วนต่าง {gap:+.2f}R"

    if smallest < min_sample:
        verdict += f" — ฝั่งเล็กสุดมี {smallest} แท่ง ({min_sample} ขึ้นไปถึงจะพูดได้) ตอนนี้ยังเป็นเสียงรบกวน"

    lines.append(verdict)
    return lines


def analyse(frame, horizon=HORIZON, sl_mult=SL_ATR_MULT, min_sample=MIN_SAMPLE):
    """รายงานทั้งชุด — คืนเป็น list ของบรรทัด เพื่อให้เทสอ่านได้โดยไม่ต้องจับ stdout"""
    labelled = add_forward_outcomes(frame, horizon, sl_mult)
    known = labelled["fwd_net_r"].notna().sum()

    lines = [
        f"มองไปข้างหน้า {horizon} แท่ง ({horizon * CANDLE_MINUTES / 60:.1f} ชม.) "
        f"วัดเป็น R ที่ SL {sl_mult:g}xATR",
        f"ติดป้ายได้ {known} จาก {len(labelled)} แท่ง "
        "(ที่เหลือติดช่วงที่บอทขาด หรือยังไม่มีอนาคตครบ)",
    ]

    if not known:
        lines.append("")
        lines.append("ยังไม่มีแท่งไหนติดป้ายได้ — เก็บข้อมูลต่อเนื่องอีกสักสองชั่วโมงแล้วรันใหม่")
        return lines

    hours = pd.to_datetime(labelled["candle_time"], errors="coerce").dt.hour.dropna()
    if len(hours):
        # เรียงเป็นรายชั่วโมง ไม่ใช่ min-max เพราะรอบที่คร่อมเที่ยงคืนจะกลายเป็น 01-23
        listed = ", ".join(f"{hour:02d}" for hour in sorted(hours.unique()))
        lines.append(
            f"ครอบคลุม {hours.nunique()} จาก 24 ชม. (เวลา broker): {listed}"
            " — ข้อสรุปเป็นของช่วงนี้ ไม่ใช่ของทั้งวัน"
        )

    lines.append("")
    lines.append("ตัววัดทุกข้อข้างล่างคือ 'เทรนด์ H1 ไปต่อกี่ R' บวกคือไปต่อ ลบคือสวนกลับ")
    lines.append("")

    lines += format_split(
        f"ADX >= {strategy.ADX_MIN:g} (ADX_MIN)",
        split_by(labelled, "adx_14", strategy.ADX_MIN),
        min_sample,
    )
    lines.append("")
    lines += format_split(
        f"spread <= {strategy.MAX_SPREAD_POINTS:g} (MAX_SPREAD_POINTS)",
        split_by(labelled, "spread_points", strategy.MAX_SPREAD_POINTS, above=False),
        min_sample,
    )
    lines.append("")
    lines += format_split(
        "M5 เห็นตรงกับ H1 (USE_M5_CONFIRM)",
        split_by_agreement(labelled),
        min_sample,
    )

    lines.append("")
    lines.append("ตัวเลขนี้คือการเคลื่อนไหวของตลาดดิบๆ ไม่ใช่กำไรของไม้ — ไม่มี spread")
    lines.append("ไม่มีการเลื่อน stop ไม่มี TP ถ้าจะตัดสินว่ากลยุทธ์ทำเงินได้ไหม ใช้ run.py backtest")

    return lines
