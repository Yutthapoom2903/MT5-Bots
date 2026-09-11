"""
สรุปว่าบอททำอะไรไปบ้าง จากไฟล์ที่มันเขียนไว้

ออกแบบมาให้รันหลังปล่อยบอทไว้ข้ามคืน แล้วได้ภาพรวมในหน้าจอเดียวว่า
เห็นสัญญาณกี่ครั้ง ตัวกรองไหนบล็อกมากที่สุด ส่งคำสั่งสำเร็จไหม และมี error อะไรบ้าง

อ่านอย่างเดียว ไม่ต้องต่อ MT5 จึงรันบน WSL ได้
"""

import os
import re

import pandas as pd

from bot import paths
from bot import screen
from bot import strategy

# การแสดงผลทั้งหมดมาจาก bot/screen.py ที่เดียว — สีเดียวกับที่ logger ใช้ และ
# ปิดตัวเองเหมือนกันเมื่อ NO_COLOR ถูกตั้งหรือปลายทางไม่ใช่ terminal
from bot.screen import (
    BOLD, CYAN, DIM, GREEN, RED, RESET, YELLOW, bar, pad, paint, width,   # noqa: F401
)

SIGNAL_LOG = paths.SIGNAL_LOG
FEATURE_LOG = paths.FEATURE_LOG
TRADE_LOG = paths.TRADE_LOG
BOT_LOG = paths.LOG_FILE

CANDLE_MINUTES = 15    # บอทเขียนหนึ่งแถวต่อหนึ่งแท่ง M15 ที่ปิดแล้ว
SESSION_BREAK_HOURS = 3.0   # ห่างเกินนี้ถือว่าคนละรอบที่รัน ไม่ใช่บอทดับกลางรอบ

WIDTH = 74          # ความกว้างของเส้นคั่น พอดีกับ terminal 80 คอลัมน์
LABEL_WIDTH = 14    # คอลัมน์ป้ายชื่อของบรรทัดแบบ "ป้าย: ค่า"


def _read(path):
    if not os.path.exists(path):
        return None

    try:
        frame = pd.read_csv(path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        return None

    return frame if len(frame) else None


def _section(title):
    return "\n" + paint(f"▌ {title}", BOLD, CYAN)


def _field(lines, label, value):
    """บรรทัดแบบ "ป้าย  ค่า" — ป้ายจางลง ค่าทุกบรรทัดเริ่มที่คอลัมน์เดียวกัน"""
    lines.append(f"  {pad(paint(label, DIM), LABEL_WIDTH)}{value}")


def _note(lines, text):
    """คำอธิบายประกอบ — จางไว้เพื่อให้ตัวเลขข้างบนเด่นกว่า"""
    lines.append(paint(f"  {text}", DIM))


def _numeric(frame, column):
    """คอลัมน์ตัวเลข — Series ว่างถ้าไฟล์ยังไม่มีคอลัมน์นั้น

    ชุดคอลัมน์เปลี่ยนมาหลายรอบ (adx_14, broker_gmt_offset เพิ่มทีหลัง) การ get()
    เฉยๆ คืน None แล้ว pd.to_numeric(None) ให้ float ตัวเดียวซึ่งไม่มี .dropna()
    """
    if column not in frame:
        return pd.Series(dtype=float)

    return pd.to_numeric(frame[column], errors="coerce")


def _counts(series):
    return " · ".join(f"{name} {count}" for name, count in series.value_counts().items())


def summarise_candles(lines):
    """แท่งที่บอทประมวลผลไปแล้วและสัญญาณที่เห็น"""
    frame = _read(FEATURE_LOG)

    if frame is None:
        lines.append("ยังไม่มีข้อมูลใน market_training_data.csv — บอทอาจยังไม่เจอแท่งใหม่เลย")
        return

    lines.append(_section("แท่งที่บันทึกไว้"))
    _field(lines, "จำนวน", f"{paint(str(len(frame)), BOLD)} แท่ง")
    _field(lines, "ช่วง", f"{frame['candle_time'].iloc[0]} → {frame['candle_time'].iloc[-1]}")

    if "bot_signal" in frame:
        _field(lines, "สัญญาณ", _counts(frame["bot_signal"]))

    if "h1_trend" in frame:
        _field(lines, "เทรนด์ H1", _counts(frame["h1_trend"]))

    rows = []
    for column, label in (("adx_14", "ADX"), ("atr_14", "ATR"), ("spread_points", "spread")):
        values = _numeric(frame, column).dropna()
        if len(values):
            rows.append((label, values.min(), values.mean(), values.max()))

    if not rows:
        return

    lines.append("")
    lines.append(paint(f"  {pad('', LABEL_WIDTH)}{pad('ต่ำสุด', 9, '>')}"
                       f"{pad('เฉลี่ย', 9, '>')}{pad('สูงสุด', 9, '>')}", DIM))

    for label, low, mean, high in rows:
        lines.append(f"  {pad(label, LABEL_WIDTH)}{pad(f'{low:.1f}', 9, '>')}"
                     f"{pad(f'{mean:.1f}', 9, '>')}{pad(f'{high:.1f}', 9, '>')}")


def summarise_decisions(lines):
    """คำตัดสินของบอทและตัวกรองที่บล็อก"""
    frame = _read(FEATURE_LOG)

    if frame is None or "bot_decision" not in frame:
        return

    lines.append(_section("คำตัดสิน"))
    _field(lines, "ผลรวม", _counts(frame["bot_decision"]))

    signals = frame[frame["bot_signal"].isin(["BUY", "SELL"])] if "bot_signal" in frame else frame

    if not len(signals):
        _note(lines, "ยังไม่เจอสัญญาณตัดกันเลยในช่วงนี้ — ปกติสำหรับข้อมูลไม่กี่ชั่วโมง")
        return

    _field(lines, "สัญญาณตัดกัน", f"{len(signals)} แท่ง")

    if "bot_blockers" not in frame:
        return

    blockers = {}
    for value in signals["bot_blockers"].dropna():
        for name in str(value).split(";"):
            name = name.strip()
            if name:
                blockers[name] = blockers.get(name, 0) + 1

    if not blockers:
        return

    lines.append("")
    lines.append(paint("  ตัวกรองที่บล็อก", DIM))

    most = max(blockers.values())
    for name, count in sorted(blockers.items(), key=lambda item: -item[1]):
        drawn = paint(bar(count / most), YELLOW)
        lines.append(f"    {pad(name, 24)}{drawn}  {count} ครั้ง")


def _candle_times(frame):
    """candle_time ที่แปลงเป็นเวลาแล้ว เรียงเก่าไปใหม่ แถวที่แปลงไม่ได้ถูกทิ้ง"""
    times = pd.to_datetime(frame["candle_time"], errors="coerce").dropna()
    return times.sort_values().reset_index(drop=True)


def split_sessions(times, break_hours=SESSION_BREAK_HOURS, minutes=CANDLE_MINUTES):
    """แบ่งแท่งเป็น "รอบที่รัน" — บอทไม่ได้รันตลอด 24 ชม. แต่รันเป็นรอบ เย็นถึงเช้า

    ห่างเกิน break_hours = คนละรอบ (ปิดเครื่อง ไม่อยู่บ้าน สุดสัปดาห์) ไม่ใช่ความผิดของบอท
    ห่างน้อยกว่านั้นแต่เกินหนึ่งแท่ง = ขาดกลางรอบ อันนี้แหละที่ควรตามหาสาเหตุ

    คืน list ของ (เริ่ม, จบ, จำนวนแท่งที่มี, จำนวนแท่งที่ขาดกลางรอบ)
    """
    if not len(times):
        return []

    limit = pd.Timedelta(hours=break_hours)
    step = pd.Timedelta(minutes=minutes)

    sessions = []
    start = times.iloc[0]
    held = 1
    dropped = 0

    for index in range(1, len(times)):
        span = times.iloc[index] - times.iloc[index - 1]

        if span > limit:
            sessions.append((start, times.iloc[index - 1], held, dropped))
            start, held, dropped = times.iloc[index], 1, 0
            continue

        held += 1
        if span > step:
            dropped += int(span / step) - 1

    sessions.append((start, times.iloc[-1], held, dropped))
    return sessions


def summarise_coverage(lines, show=8):
    """รันไปกี่รอบ แต่ละรอบขาดกลางคันไหม — คำถามแรกหลังปล่อยรันข้ามคืน

    ไม่คิดเลขเป็น "uptime จาก 24 ชม." เพราะบอทไม่ได้ตั้งใจรันตลอดเวลาอยู่แล้ว
    ตัวเลขที่ใช้ได้จริงคือ "ในรอบที่รัน เก็บครบไหม"
    """
    frame = _read(FEATURE_LOG)

    if frame is None or "candle_time" not in frame:
        return

    times = _candle_times(frame)

    if len(times) < 2:
        return

    sessions = split_sessions(times)
    dropped = sum(session[3] for session in sessions)
    held = sum(session[2] for session in sessions)

    lines.append(_section("รอบที่รัน"))
    lines.append(f"  รันไป {paint(str(len(sessions)), BOLD)} รอบ · เก็บได้ {paint(str(held), BOLD)} แท่ง")
    lines.append("")

    hidden = max(0, len(sessions) - show)
    for number, (start, end, count, missing) in enumerate(sessions[-show:], hidden + 1):
        hours = (end - start) / pd.Timedelta(hours=1)
        expected = count + missing
        drawn = paint(bar(count / expected), screen.rate_style(count / expected))
        note = paint(f"ขาดกลางรอบ {missing} แท่ง", RED) if missing else paint("ครบ", GREEN)

        lines.append(
            f"  {paint(f'#{number}', DIM)} {start:%m-%d %H:%M} → {end:%m-%d %H:%M}"
            f"{pad(f'{hours:.1f} ชม.', 12, '>')}{pad(f'{count} แท่ง', 11, '>')}"
            f"  {drawn}  {note}"
        )

    if hidden:
        _note(lines, f"(ก่อนหน้านั้นอีก {hidden} รอบ)")

    lines.append("")

    if dropped:
        kept = held / (held + dropped)
        lines.append(
            f"  {paint(f'{kept:.0%}', BOLD, screen.rate_style(kept))} ของแท่งที่ควรมีในรอบที่รัน — "
            f"ขาดกลางรอบรวม {dropped} แท่ง จาก {held + dropped}"
        )
        _note(lines, "เฉพาะพวกนี้ที่ควรตามหาสาเหตุ")
    else:
        lines.append(f"  {paint('ไม่มีแท่งขาดกลางรอบเลย ทุกรอบที่รันเก็บครบ', GREEN)}")

    _note(lines, "ช่วงระหว่างรอบไม่นับ — ปิดเครื่องหรือไม่อยู่บ้านไม่ใช่ความผิดของบอท")


def hour_strip(covered):
    """แถบ 24 ชั่วโมง — ชั่วโมงที่มีข้อมูลทึบ ที่ไม่มีจาง แบ่งกลุ่มละ 6 ให้กวาดตาได้"""
    marks = ["█" if hour in set(covered) else "·" for hour in range(24)]
    return " ".join("".join(marks[start:start + 6]) for start in range(0, 24, 6))


def summarise_hours(lines):
    """ข้อมูลครอบคลุมชั่วโมงไหนบ้าง — รันเฉพาะกลางคืนแปลว่าสถิติเป็นของกลางคืน"""
    frame = _read(FEATURE_LOG)

    if frame is None or "candle_time" not in frame:
        return

    times = _candle_times(frame)

    if len(times) < 2:
        return

    covered = sorted(times.dt.hour.unique())

    lines.append(_section("ชั่วโมงที่ครอบคลุม"))
    _field(lines, "เก็บได้", f"{paint(str(len(covered)), BOLD)} จาก 24 ชั่วโมง "
                             f"{paint('(เวลาเซิร์ฟเวอร์ broker)', DIM)}")
    _field(lines, "00 → 23", hour_strip(covered))
    _field(lines, "ชั่วโมง", ", ".join(f"{hour:02d}" for hour in covered))

    offsets = _numeric(frame, "broker_gmt_offset").dropna().unique()

    if len(offsets) == 1:
        _field(lines, "เวลาเซิร์ฟเวอร์", f"GMT{int(offsets[0]):+d} ตลอดทั้งไฟล์")
    elif len(offsets) > 1:
        # DST ของ broker ขยับปีละสองครั้ง ชั่วโมงเดียวกันในไฟล์จึงไม่ใช่เวลาเดียวกัน
        listed = ", ".join(f"GMT{int(value):+d}" for value in sorted(offsets))
        lines.append("")
        lines.append(paint(f"  ▲ เวลาเซิร์ฟเวอร์เปลี่ยนระหว่างเก็บ: {listed}", YELLOW))
        _note(lines, "ชั่วโมงในไฟล์นี้ไม่ใช่เวลาเดียวกันทุกแถว "
                     "แยกวิเคราะห์ทีละ offset ก่อนสรุปอะไรที่อิงชั่วโมง")
    else:
        _note(lines, "แถวเหล่านี้ยังไม่มี broker_gmt_offset — แถวที่เก็บหลังจากนี้จะมี")

    lines.append("")
    _note(lines, "สถิติทุกอย่างในรายงานนี้เป็นของชั่วโมงพวกนี้เท่านั้น ไม่ใช่ของตลาดทั้งวัน")


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

    columns = (("แท่ง", 8), ("สัญญาณ", 9), ("เข้า", 8), ("ADX", 9), ("spread", 9))

    lines.append(_section("รายวัน"))
    lines.append(paint("  " + pad("วัน", 12) + "".join(pad(name, size, ">")
                                                       for name, size in columns), DIM))

    for day in days:
        rows = frame[frame["_day"] == day]
        signals = rows["bot_signal"].isin(["BUY", "SELL"]).sum() if "bot_signal" in rows else 0
        entered = (rows["bot_decision"] == "ENTER").sum() if "bot_decision" in rows else 0
        adx = _numeric(rows, "adx_14").mean()
        spread = _numeric(rows, "spread_points").mean()

        values = (str(len(rows)), str(signals), str(entered), _number(adx), _number(spread))
        lines.append("  " + pad(str(day), 12)
                     + "".join(pad(value, size, ">")
                               for value, (_, size) in zip(values, columns)))


def summarise_filter_margins(lines):
    """แต่ละแท่งห่างจากเกณฑ์ของตัวกรองแค่ไหน

    ของเดิมบอกแค่ต่ำสุด/เฉลี่ย/สูงสุด คนอ่านต้องจำเกณฑ์ใน strategy.py เองแล้วคิดในหัว
    ว่าผ่านกี่แท่ง ตรงนี้คิดให้ — เป็นการบรรยายข้อมูลเฉยๆ ไม่ใช่ข้อเสนอให้ไปลดเกณฑ์
    """
    frame = _read(FEATURE_LOG)

    if frame is None:
        return

    lines.append(_section("ระยะห่างจากเกณฑ์ตัวกรอง"))

    def rule(label, passed, total, detail):
        share = passed / total
        drawn = paint(bar(share), screen.rate_style(share))
        lines.append(f"  {pad(label, 20)}{drawn}  ผ่าน {passed}/{total} ({share:.0%})")
        lines.append(paint(f"    {detail}", DIM))

    adx = _numeric(frame, "adx_14").dropna()
    if len(adx):
        rule(f"ADX >= {strategy.ADX_MIN:g}",
             int((adx >= strategy.ADX_MIN).sum()), len(adx),
             f"ค่ากลาง {adx.median():.1f} · เกณฑ์ ADX_MIN")

    spread = _numeric(frame, "spread_points").dropna()
    if len(spread):
        headroom = 1 - spread.max() / strategy.MAX_SPREAD_POINTS
        rule(f"spread <= {strategy.MAX_SPREAD_POINTS:g}",
             int((spread <= strategy.MAX_SPREAD_POINTS).sum()), len(spread),
             f"ค่ากลาง {spread.median():.1f} · กว้างสุด {spread.max():.1f} "
             f"· เหลือที่ว่าง {headroom:.0%} · เกณฑ์ MAX_SPREAD_POINTS")

    rsi = _numeric(frame, "rsi_14").dropna()
    if len(rsi):
        inside = int(((rsi < strategy.RSI_MAX_FOR_BUY) & (rsi > strategy.RSI_MIN_FOR_SELL)).sum())
        rule(f"RSI {strategy.RSI_MIN_FOR_SELL:g}-{strategy.RSI_MAX_FOR_BUY:g}",
             inside, len(rsi),
             "อยู่ในช่วง — นอกช่วงคือแท่งที่ราคายืดจนไม่ไล่ตาม")

    lines.append("")
    _note(lines, "ตัวเลขนี้บอกว่าตลาดเป็นยังไง ไม่ได้แปลว่าเกณฑ์ตั้งผิด — ดูหลายวันก่อนค่อยขยับ")


def summarise_trades(lines):
    """คำสั่งที่ส่งไปจริง"""
    frame = _read(TRADE_LOG)

    lines.append(_section("คำสั่งซื้อขาย"))

    if frame is None:
        _note(lines, "ยังไม่มีการส่งคำสั่ง (โหมดเฝ้าดู หรือยังไม่มีสัญญาณผ่านตัวกรอง)")
        return

    _field(lines, "ส่งไปทั้งหมด", f"{paint(str(len(frame)), BOLD)} ครั้ง")

    if "status" in frame:
        ok = int((frame["status"] == "OK").sum())
        failed = len(frame) - ok
        _field(lines, "ผล", f"{paint(f'สำเร็จ {ok}', GREEN)} · "
                            f"{paint(f'ไม่สำเร็จ {failed}', RED if failed else DIM)}")

        failures = frame[frame["status"] != "OK"]
        if len(failures):
            lines.append("")
            lines.append(paint("  สาเหตุที่ไม่สำเร็จ", DIM))
            for reason, count in failures["status"].value_counts().items():
                lines.append(f"    {pad(str(reason), 34)}{count} ครั้ง")

    for column, label in (("risk_amount", "risk_amount"), ("lots", "lots")):
        values = _numeric(frame, column).dropna()
        if len(values):
            _field(lines, label, f"เฉลี่ย {values.mean():.2f} · สูงสุด {values.max():.2f}")


def summarise_log(lines, tail=2000):
    """error กับ warning จาก bot.log"""
    if not os.path.exists(BOT_LOG):
        lines.append(_section("bot.log"))
        _note(lines, "ยังไม่มีไฟล์ bot.log")
        return

    with open(BOT_LOG, encoding="utf-8", errors="replace") as handle:
        rows = handle.readlines()[-tail:]

    problems = [row.rstrip() for row in rows if "[ERROR]" in row or "[WARNING]" in row]

    lines.append(_section("ปัญหาใน bot.log"))
    count = paint(str(len(problems)), BOLD, RED if problems else GREEN)
    _field(lines, "อ่าน", f"{len(rows)} บรรทัดล่าสุด พบ {count} รายการ")

    if not problems:
        _note(lines, "ไม่มี error หรือ warning เลย")
        return

    # จัดกลุ่มข้อความซ้ำ ตัดตัวเลขออกเพื่อให้ข้อความแบบเดียวกันนับรวมกันได้
    grouped = {}
    for problem in problems:
        message = problem.split("] ", 1)[-1]
        key = re.sub(r"[-+]?\d[\d,.:]*", "N", message)[:110]
        grouped.setdefault(key, [0, problem])
        grouped[key][0] += 1

    lines.append("")
    for count, sample in sorted(grouped.values(), key=lambda item: -item[0])[:12]:
        lines.append(f"  {paint(pad(f'x{count}', 6), YELLOW)}{sample[:150]}")


def next_steps(lines):
    """บอกว่าควรทำอะไรต่อจากสิ่งที่เห็น"""
    features = _read(FEATURE_LOG)
    trades = _read(TRADE_LOG)

    lines.append(_section("ทำอะไรต่อ"))

    if features is None:
        lines.append("  → บอทยังไม่บันทึกแท่งไหนเลย — ตรวจว่ารันค้างไว้จริงและตลาดเปิดอยู่")
        return

    steps = []

    if "candle_time" in features:
        times = _candle_times(features)
        if len(times) > 1:
            sessions = split_sessions(times)
            dropped = sum(session[3] for session in sessions)
            if dropped and len(times) / (len(times) + dropped) < 0.9:
                steps.append([
                    f"ขาดกลางรอบไป {dropped} แท่ง — ดูว่าเน็ตหลุด เครื่อง sleep",
                    "หรือ MT5 ปิดตัวเอง ระหว่างที่ยังตั้งใจรันอยู่",
                ])

    if len(features) < 96:
        steps.append([f"มีข้อมูลแค่ {len(features)} แท่ง (ไม่ถึงหนึ่งวัน) ปล่อยเก็บต่ออีกหน่อย"])

    if "bot_decision" in features and (features["bot_decision"] == "ENTER").sum() == 0:
        steps.append([
            "ยังไม่มีสัญญาณผ่านตัวกรองเลย ดูรายการตัวกรองข้างบนว่าตัวไหนบล็อกบ่อยสุด",
            "ถ้าเป็น ADX หรือเทรนด์ H1 แปลว่าตลาดช่วงนี้ไม่มีเทรนด์ ถือว่าบอททำงานถูก",
        ])

    if trades is None:
        steps.append([
            "ยังไม่เคยส่งคำสั่งจริงสักครั้ง — retcode, filling mode และระยะ stop ขั้นต่ำ",
            "ของ broker ยังไม่เคยถูกพิสูจน์ รัน `python run.py --trade` บนบัญชี Demo",
            "(ALLOW_LIVE_ACCOUNT = False กันบัญชีจริงไว้อยู่แล้ว)",
        ])

    if trades is not None and "status" in trades and (trades["status"] != "OK").any():
        steps.append(["มีคำสั่งที่ broker ปฏิเสธ — เอาข้อความ retcode ข้างบนไปหาสาเหตุ"])

    steps.append(["รัน `python run.py backtest` เพื่อดูว่ากลยุทธ์นี้เคยทำเงินได้ไหมในอดีต"])

    for step in steps:
        lines.append(f"  {paint('→', CYAN)} {step[0]}")
        for extra in step[1:]:
            lines.append(f"    {extra}")


def build_report():
    lines = [
        paint("━" * WIDTH, DIM),
        paint("  สรุปการทำงานของบอท", BOLD),
        paint(f"  อ่านจาก {FEATURE_LOG} · {TRADE_LOG} · {BOT_LOG}", DIM),
        paint("━" * WIDTH, DIM),
    ]

    summarise_candles(lines)
    summarise_coverage(lines)
    summarise_hours(lines)
    summarise_by_day(lines)
    summarise_filter_margins(lines)
    summarise_decisions(lines)
    summarise_trades(lines)
    summarise_log(lines)
    next_steps(lines)

    lines.append("")
    lines.append(paint("━" * WIDTH, DIM))

    return "\n".join(lines)
