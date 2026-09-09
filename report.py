"""
สรุปว่าบอททำอะไรไปบ้าง จากไฟล์ที่มันเขียนไว้

ออกแบบมาให้รันหลังปล่อยบอทไว้ข้ามคืน แล้วได้ภาพรวมในหน้าจอเดียวว่า
เห็นสัญญาณกี่ครั้ง ตัวกรองไหนบล็อกมากที่สุด ส่งคำสั่งสำเร็จไหม และมี error อะไรบ้าง

อ่านอย่างเดียว ไม่ต้องต่อ MT5 จึงรันบน WSL ได้
"""

import os
import re

import pandas as pd

SIGNAL_LOG = "signal_log.csv"
FEATURE_LOG = "market_training_data.csv"
TRADE_LOG = "trade_log.csv"
BOT_LOG = "bot.log"


def _read(path):
    if not os.path.exists(path):
        return None

    try:
        frame = pd.read_csv(path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        return None

    return frame if len(frame) else None


def _section(title):
    return f"\n--- {title} ---"


def _counts(series):
    return ", ".join(f"{name} {count}" for name, count in series.value_counts().items())


def summarise_candles(lines):
    """แท่งที่บอทประมวลผลไปแล้วและสัญญาณที่เห็น"""
    frame = _read(FEATURE_LOG)

    if frame is None:
        lines.append("ยังไม่มีข้อมูลใน market_training_data.csv — บอทอาจยังไม่เจอแท่งใหม่เลย")
        return

    lines.append(_section("แท่งที่บันทึกไว้"))
    lines.append(f"จำนวน: {len(frame)} แท่ง")
    lines.append(f"ช่วง: {frame['candle_time'].iloc[0]} ถึง {frame['candle_time'].iloc[-1]}")

    if "bot_signal" in frame:
        lines.append(f"สัญญาณ: {_counts(frame['bot_signal'])}")

    if "h1_trend" in frame:
        lines.append(f"เทรนด์ H1: {_counts(frame['h1_trend'])}")

    for column, label in (("adx_14", "ADX"), ("atr_14", "ATR"), ("spread_points", "spread")):
        if column in frame:
            values = pd.to_numeric(frame[column], errors="coerce").dropna()
            if len(values):
                lines.append(
                    f"{label}: ต่ำสุด {values.min():.1f} เฉลี่ย {values.mean():.1f} "
                    f"สูงสุด {values.max():.1f}"
                )


def summarise_decisions(lines):
    """คำตัดสินของบอทและตัวกรองที่บล็อก"""
    frame = _read(FEATURE_LOG)

    if frame is None or "bot_decision" not in frame:
        return

    lines.append(_section("คำตัดสิน"))
    lines.append(_counts(frame["bot_decision"]))

    signals = frame[frame["bot_signal"].isin(["BUY", "SELL"])] if "bot_signal" in frame else frame

    if not len(signals):
        lines.append("ยังไม่เจอสัญญาณตัดกันเลยในช่วงนี้ — ปกติสำหรับข้อมูลไม่กี่ชั่วโมง")
        return

    lines.append(f"แท่งที่มีสัญญาณตัดกัน: {len(signals)}")

    if "bot_blockers" not in frame:
        return

    blockers = {}
    for value in signals["bot_blockers"].dropna():
        for name in str(value).split(";"):
            name = name.strip()
            if name:
                blockers[name] = blockers.get(name, 0) + 1

    if blockers:
        lines.append("ตัวกรองที่บล็อก:")
        for name, count in sorted(blockers.items(), key=lambda item: -item[1]):
            lines.append(f"  {name}: {count} ครั้ง")


def summarise_trades(lines):
    """คำสั่งที่ส่งไปจริง"""
    frame = _read(TRADE_LOG)

    lines.append(_section("คำสั่งซื้อขาย"))

    if frame is None:
        lines.append("ยังไม่มีการส่งคำสั่ง (โหมดเฝ้าดู หรือยังไม่มีสัญญาณผ่านตัวกรอง)")
        return

    lines.append(f"ส่งไปทั้งหมด: {len(frame)} ครั้ง")

    if "status" in frame:
        ok = (frame["status"] == "OK").sum()
        lines.append(f"สำเร็จ {ok} / ไม่สำเร็จ {len(frame) - ok}")

        failures = frame[frame["status"] != "OK"]
        if len(failures):
            lines.append("สาเหตุที่ไม่สำเร็จ:")
            for reason, count in failures["status"].value_counts().items():
                lines.append(f"  {reason} ({count} ครั้ง)")

    for column in ("risk_amount", "lots"):
        if column in frame:
            values = pd.to_numeric(frame[column], errors="coerce").dropna()
            if len(values):
                lines.append(f"{column}: เฉลี่ย {values.mean():.2f} สูงสุด {values.max():.2f}")


def summarise_log(lines, tail=2000):
    """error กับ warning จาก bot.log"""
    if not os.path.exists(BOT_LOG):
        lines.append(_section("bot.log"))
        lines.append("ยังไม่มีไฟล์ bot.log")
        return

    with open(BOT_LOG, encoding="utf-8", errors="replace") as handle:
        rows = handle.readlines()[-tail:]

    problems = [row.rstrip() for row in rows if "[ERROR]" in row or "[WARNING]" in row]

    lines.append(_section("ปัญหาใน bot.log"))
    lines.append(f"อ่าน {len(rows)} บรรทัดล่าสุด พบ {len(problems)} รายการ")

    if not problems:
        lines.append("ไม่มี error หรือ warning เลย")
        return

    # จัดกลุ่มข้อความซ้ำ ตัดตัวเลขออกเพื่อให้ข้อความแบบเดียวกันนับรวมกันได้
    grouped = {}
    for problem in problems:
        message = problem.split("] ", 1)[-1]
        key = re.sub(r"[-+]?\d[\d,.:]*", "N", message)[:110]
        grouped.setdefault(key, [0, problem])
        grouped[key][0] += 1

    for count, sample in sorted(grouped.values(), key=lambda item: -item[0])[:12]:
        lines.append(f"  x{count}  {sample[:160]}")


def next_steps(lines):
    """บอกว่าควรทำอะไรต่อจากสิ่งที่เห็น"""
    features = _read(FEATURE_LOG)
    trades = _read(TRADE_LOG)

    lines.append(_section("ทำอะไรต่อ"))

    if features is None:
        lines.append("บอทยังไม่บันทึกแท่งไหนเลย — ตรวจว่ารันค้างไว้จริงและตลาดเปิดอยู่")
        return

    if len(features) < 96:
        lines.append(f"มีข้อมูลแค่ {len(features)} แท่ง (ไม่ถึงหนึ่งวัน) ปล่อยเก็บต่ออีกหน่อย")

    if "bot_decision" in features and (features["bot_decision"] == "ENTER").sum() == 0:
        lines.append("ยังไม่มีสัญญาณผ่านตัวกรองเลย ดูรายการตัวกรองข้างบนว่าตัวไหนบล็อกบ่อยสุด")
        lines.append("ถ้าเป็น ADX หรือเทรนด์ H1 แปลว่าตลาดช่วงนี้ไม่มีเทรนด์ ถือว่าบอททำงานถูก")

    if trades is not None and "status" in trades and (trades["status"] != "OK").any():
        lines.append("มีคำสั่งที่ broker ปฏิเสธ — เอาข้อความ retcode ข้างบนไปหาสาเหตุ")

    lines.append("รัน `python run.py backtest` เพื่อดูว่ากลยุทธ์นี้เคยทำเงินได้ไหมในอดีต")


def build_report():
    lines = ["=== สรุปการทำงานของบอท ==="]

    summarise_candles(lines)
    summarise_decisions(lines)
    summarise_trades(lines)
    summarise_log(lines)
    next_steps(lines)

    return "\n".join(lines)
