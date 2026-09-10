"""
สรุปว่าบอททำอะไรไปบ้าง จากไฟล์ที่มันเขียนไว้

ออกแบบมาให้รันหลังปล่อยบอทไว้ข้ามคืน แล้วได้ภาพรวมในหน้าจอเดียวว่า
เห็นสัญญาณกี่ครั้ง ตัวกรองไหนบล็อกมากที่สุด ส่งคำสั่งสำเร็จไหม และมี error อะไรบ้าง

อ่านอย่างเดียว ไม่ต้องต่อ MT5 จึงรันบน WSL ได้
"""

import os
import re

import pandas as pd

import strategy

SIGNAL_LOG = "signal_log.csv"
FEATURE_LOG = "market_training_data.csv"
TRADE_LOG = "trade_log.csv"
BOT_LOG = "bot.log"

CANDLE_MINUTES = 15    # บอทเขียนหนึ่งแถวต่อหนึ่งแท่ง M15 ที่ปิดแล้ว
LONG_GAP_HOURS = 24    # ยาวกว่านี้ถือว่าตลาดปิด ไม่ใช่บอทดับ


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


def _candle_times(frame):
    """candle_time ที่แปลงเป็นเวลาแล้ว เรียงเก่าไปใหม่ แถวที่แปลงไม่ได้ถูกทิ้ง"""
    times = pd.to_datetime(frame["candle_time"], errors="coerce").dropna()
    return times.sort_values().reset_index(drop=True)


def find_gaps(times, minutes=CANDLE_MINUTES):
    """ช่วงที่ไม่มีแท่ง คืน (ก่อนหน้า, ถัดไป, จำนวนแท่งที่หาย) เรียงจากใหญ่ไปเล็ก

    ระยะห่างเกินหนึ่งแท่งแปลว่าช่วงนั้นไม่ได้บันทึก — บอทดับ เน็ตหลุด หรือตลาดปิด
    ไฟล์นี้แยกสามอย่างนั้นจากกันไม่ได้ จึงรายงานตามที่เห็นแล้วให้คนตัดสิน
    """
    step = pd.Timedelta(minutes=minutes)
    gaps = []

    for index in range(1, len(times)):
        span = times[index] - times[index - 1]
        if span > step:
            gaps.append((times[index - 1], times[index], int(span / step) - 1))

    return sorted(gaps, key=lambda gap: -gap[2])


def summarise_coverage(lines, show=5):
    """บอทเก็บแท่งครบไหม — คำถามแรกหลังปล่อยรันข้ามคืน"""
    frame = _read(FEATURE_LOG)

    if frame is None or "candle_time" not in frame:
        return

    times = _candle_times(frame)

    if len(times) < 2:
        return

    long_gap = pd.Timedelta(hours=LONG_GAP_HOURS) / pd.Timedelta(minutes=CANDLE_MINUTES)
    gaps = find_gaps(times)
    closed = [gap for gap in gaps if gap[2] >= long_gap]
    missed = [gap for gap in gaps if gap[2] < long_gap]

    lines.append(_section("ความต่อเนื่อง"))

    dropped = sum(gap[2] for gap in missed)
    expected = len(times) + dropped
    lines.append(f"เก็บได้ {len(times)} จาก {expected} แท่งที่ควรมี ({len(times) / expected:.0%})")

    if not gaps:
        lines.append("ไม่มีช่วงที่ขาดเลย บอทรันต่อเนื่องตลอด")
        return

    if missed:
        hours = dropped * CANDLE_MINUTES / 60
        lines.append(f"ช่วงที่ขาด {len(missed)} ครั้ง รวม {dropped} แท่ง (~{hours:.1f} ชม.):")
        for start, end, count in missed[:show]:
            lines.append(f"  {start} -> {end}  ขาด {count} แท่ง")
        if len(missed) > show:
            lines.append(f"  (อีก {len(missed) - show} ช่วง)")

    for start, end, count in closed:
        lines.append(f"ช่วงยาว {start} -> {end} ({count} แท่ง) — น่าจะสุดสัปดาห์หรือวันหยุด ไม่นับเป็นบอทดับ")


def _number(value):
    """ตัวเลขหนึ่งตำแหน่ง หรือขีดถ้าไม่มีค่า (แถวเก่าไม่มี adx_14)"""
    return f"{value:.1f}" if pd.notna(value) else "-"


def summarise_by_day(lines):
    """แยกรายวัน — ค่าเฉลี่ยรวมหลายวันกลบวันที่ตลาดวิ่งจริง"""
    frame = _read(FEATURE_LOG)

    if frame is None or "candle_time" not in frame:
        return

    times = pd.to_datetime(frame["candle_time"], errors="coerce")
    frame = frame[times.notna()].copy()
    frame["_day"] = times.dropna().dt.date

    days = sorted(frame["_day"].unique())

    if len(days) < 2:
        return    # วันเดียวก็ดูจากสรุปรวมข้างบนพอ

    lines.append(_section("รายวัน"))
    lines.append(f"{'วัน':<12}{'แท่ง':>6}{'สัญญาณ':>9}{'เข้า':>7}{'ADX':>8}{'spread':>9}")

    for day in days:
        rows = frame[frame["_day"] == day]
        signals = rows["bot_signal"].isin(["BUY", "SELL"]).sum() if "bot_signal" in rows else 0
        entered = (rows["bot_decision"] == "ENTER").sum() if "bot_decision" in rows else 0
        adx = pd.to_numeric(rows.get("adx_14"), errors="coerce").mean()
        spread = pd.to_numeric(rows.get("spread_points"), errors="coerce").mean()

        lines.append(
            f"{str(day):<12}{len(rows):>6}{signals:>9}{entered:>7}"
            f"{_number(adx):>8}{_number(spread):>9}"
        )


def summarise_filter_margins(lines):
    """แต่ละแท่งห่างจากเกณฑ์ของตัวกรองแค่ไหน

    ของเดิมบอกแค่ต่ำสุด/เฉลี่ย/สูงสุด คนอ่านต้องจำเกณฑ์ใน strategy.py เองแล้วคิดในหัว
    ว่าผ่านกี่แท่ง ตรงนี้คิดให้ — เป็นการบรรยายข้อมูลเฉยๆ ไม่ใช่ข้อเสนอให้ไปลดเกณฑ์
    """
    frame = _read(FEATURE_LOG)

    if frame is None:
        return

    lines.append(_section("ระยะห่างจากเกณฑ์ตัวกรอง"))

    adx = pd.to_numeric(frame.get("adx_14"), errors="coerce").dropna()
    if len(adx):
        passed = (adx >= strategy.ADX_MIN).sum()
        lines.append(
            f"ADX >= {strategy.ADX_MIN:g} (ADX_MIN): ผ่าน {passed}/{len(adx)} "
            f"({passed / len(adx):.0%}) ค่ากลาง {adx.median():.1f}"
        )

    spread = pd.to_numeric(frame.get("spread_points"), errors="coerce").dropna()
    if len(spread):
        passed = (spread <= strategy.MAX_SPREAD_POINTS).sum()
        headroom = 1 - spread.max() / strategy.MAX_SPREAD_POINTS
        lines.append(
            f"spread <= {strategy.MAX_SPREAD_POINTS:g} (MAX_SPREAD_POINTS): ผ่าน {passed}/{len(spread)} "
            f"({passed / len(spread):.0%}) ค่ากลาง {spread.median():.1f} "
            f"กว้างสุด {spread.max():.1f} เหลือที่ว่าง {headroom:.0%}"
        )

    rsi = pd.to_numeric(frame.get("rsi_14"), errors="coerce").dropna()
    if len(rsi):
        inside = ((rsi < strategy.RSI_MAX_FOR_BUY) & (rsi > strategy.RSI_MIN_FOR_SELL)).sum()
        lines.append(
            f"RSI อยู่ระหว่าง {strategy.RSI_MIN_FOR_SELL:g}-{strategy.RSI_MAX_FOR_BUY:g}: "
            f"{inside}/{len(rsi)} แท่ง (นอกช่วงคือแท่งที่ราคายืดจนไม่ไล่ตาม)"
        )

    lines.append("ตัวเลขนี้บอกว่าตลาดเป็นยังไง ไม่ได้แปลว่าเกณฑ์ตั้งผิด — ดูหลายวันก่อนค่อยขยับ")


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

    if "candle_time" in features:
        times = _candle_times(features)
        if len(times) > 1:
            long_gap = pd.Timedelta(hours=LONG_GAP_HOURS) / pd.Timedelta(minutes=CANDLE_MINUTES)
            dropped = sum(gap[2] for gap in find_gaps(times) if gap[2] < long_gap)
            if dropped and len(times) / (len(times) + dropped) < 0.9:
                lines.append(
                    f"ขาดไป {dropped} แท่งระหว่างที่ตลาดเปิด — ดูว่าบอทดับ เน็ตหลุด "
                    "หรือ MT5 ปิดตัวเอง ก่อนจะเชื่อสถิติข้างบน"
                )

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
    summarise_coverage(lines)
    summarise_by_day(lines)
    summarise_filter_margins(lines)
    summarise_decisions(lines)
    summarise_trades(lines)
    summarise_log(lines)
    next_steps(lines)

    return "\n".join(lines)
