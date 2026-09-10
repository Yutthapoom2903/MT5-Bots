"""
เมนูเลือกด้วยตัวเลข — จำคำสั่งเดียวพอ แล้วให้มันบอกเองว่าทำอะไรได้บ้าง

ทำไมไม่ใช่ TUI เต็มรูปแบบ: ต้องรันได้ทั้ง PowerShell บน Windows และ WSL โดยไม่เพิ่ม
dependency (requirements.txt ปักหมุด MetaTrader5 ซึ่งลงบน Linux ไม่ได้อยู่แล้ว การเพิ่ม
curses/textual เข้าไปทำให้เครื่องหนึ่งลงได้อีกเครื่องลงไม่ได้) เมนูตัวเลขกับ input()
ทำงานเหมือนกันทั้งสองฝั่งและไม่พังเวลา terminal แปลกๆ

ส่วนที่คิดทั้งหมดในไฟล์นี้บริสุทธิ์ — items() render() choose() รับค่าเข้า คืนค่าออก
ไม่แตะดิสก์ ไม่แตะ MT5 ไม่เรียก input() เทสจึงกดเมนูได้โดยไม่ต้องมี terminal
run() เป็นที่เดียวที่คุยกับผู้ใช้จริง
"""

import os
from collections import namedtuple

Item = namedtuple("Item", "key label command needs_mt5 overrides group")

FEATURE_LOG = "market_training_data.csv"
TRADE_LOG = "trade_log.csv"

QUIT_KEYS = ("q", "quit", "exit", "0")


def items():
    """รายการเมนูทั้งหมด เรียงตามลำดับที่ใช้จริงในหนึ่งวัน ไม่ใช่ตามตัวอักษร"""
    return [
        Item("1", "เทรดบน Demo (เก็บข้อมูล + ส่งคำสั่งจริง)", "all", True,
             {"trade": True}, "ตอนถึงบ้าน"),
        Item("2", "เฝ้าดูอย่างเดียว ไม่ส่งคำสั่ง", "all", True,
             {"trade": False}, "ตอนถึงบ้าน"),
        Item("3", "ตรวจความพร้อม บัญชี และความเสี่ยง", "check", True,
             {}, "ตอนถึงบ้าน"),

        Item("4", "สรุปว่าบอททำอะไรไปบ้าง", "report", False, {}, "อ่านผล"),
        Item("5", "ตัวกรองแยกอะไรได้จริงไหม", "outcomes", False, {}, "อ่านผล"),
        Item("6", "ให้คะแนนป้ายที่กรอกเอง", "review", False, {}, "อ่านผล"),

        Item("7", "จำลองย้อนหลังบนข้อมูลจริง", "backtest", True, {}, "วิเคราะห์"),
        Item("8", "กวาดค่า ดูว่าผลทนหรือฟลุค", "sweep", True, {}, "วิเคราะห์"),

        Item("9", "เช็คว่าทำไม Telegram ไม่เข้า", "notify", False,
             {"check": True, "dry": False}, "เครื่องมือ"),
        Item("10", "รันเทส logic", "test", False, {}, "เครื่องมือ"),
    ]


def choose(raw, available=None):
    """แปลงสิ่งที่พิมพ์มาเป็นรายการเมนู — None ถ้าไม่ตรงอะไรเลย, "quit" ถ้าสั่งออก"""
    text = (raw or "").strip().lower()

    if text in QUIT_KEYS:
        return "quit"

    for item in (available if available is not None else items()):
        if item.key == text:
            return item

    return None


def status_lines(mt5_available, candles=None, last_candle=None, has_trades=False):
    """สองสามบรรทัดบนสุด — บอกว่าตอนนี้อยู่ตรงไหนของงาน ไม่ใช่แค่ลิสต์คำสั่ง"""
    lines = []

    if candles:
        collected = f"เก็บมาแล้ว {candles} แท่ง"
        if last_candle:
            collected += f" · ล่าสุด {last_candle}"
        lines.append(collected)
    else:
        lines.append("ยังไม่มีข้อมูลที่เก็บไว้เลย")

    lines.append(
        "ส่งคำสั่งจริงไปแล้ว (มี trade_log.csv)" if has_trades
        else "ยังไม่เคยส่งคำสั่งจริง — retcode ของ broker ยังไม่เคยถูกพิสูจน์"
    )

    if not mt5_available:
        lines.append("เครื่องนี้ไม่มี MetaTrader5 — ข้อที่ต้องใช้ MT5 เลือกไม่ได้")

    return lines


def render(mt5_available, status=None, entries=None):
    """หน้าจอเมนูทั้งหน้าเป็นสตริงเดียว เทสจึงตรวจได้โดยไม่ต้องจับ stdout"""
    entries = items() if entries is None else entries

    lines = ["", "=" * 52, "  MT5-Bots", "=" * 52]
    lines += [f"  {line}" for line in (status or [])]

    group = None
    for item in entries:
        if item.group != group:
            group = item.group
            lines.append("")
            lines.append(f"  {group}")

        locked = "  (ต้องมี MT5)" if item.needs_mt5 and not mt5_available else ""
        lines.append(f"   {item.key:>2}) {item.label}{locked}")

    lines.append("")
    lines.append("    q) ออก")
    lines.append("")

    return "\n".join(lines)


# ---------- ส่วนที่คุยกับผู้ใช้จริง ----------

def _mt5_available():
    try:
        import MetaTrader5  # noqa: F401
    except ImportError:
        return False

    return True


def _collected():
    """จำนวนแท่งและแท่งล่าสุด อ่านจากไฟล์ตรงๆ — คืนศูนย์ถ้ายังไม่มีไฟล์"""
    if not os.path.exists(FEATURE_LOG):
        return 0, None

    try:
        import pandas as pd
        frame = pd.read_csv(FEATURE_LOG)
    except Exception:
        return 0, None

    if not len(frame) or "candle_time" not in frame:
        return len(frame), None

    return len(frame), str(frame["candle_time"].iloc[-1])


def run(commands, base_args, prompt=input, out=print):
    """วนแสดงเมนูจนกว่าจะสั่งออก — commands คือ dict ชื่อคำสั่ง -> ฟังก์ชันของ run.py"""
    import copy

    available = _mt5_available()
    entries = items()

    while True:
        candles, last = _collected()
        out(render(available, status_lines(
            available, candles, last, os.path.exists(TRADE_LOG),
        ), entries))

        try:
            picked = choose(prompt("เลือก: "), entries)
        except (EOFError, KeyboardInterrupt):
            out("\nออกแล้ว")
            return 0

        if picked == "quit":
            out("ออกแล้ว")
            return 0

        if picked is None:
            out("\nไม่มีข้อนั้น ลองใหม่")
            continue

        if picked.needs_mt5 and not available:
            out(f"\n'{picked.label}' ต้องรันบนเครื่องที่มี MetaTrader5 (Windows + terminal เปิดอยู่)")
            continue

        args = copy.copy(base_args)
        for name, value in picked.overrides.items():
            setattr(args, name, value)

        out("")
        _invoke(commands[picked.command], args, picked.needs_mt5, out)

        if picked.needs_mt5:
            return 0    # เมนูที่ต่อ MT5 จบแล้วจบเลย ไม่วนกลับไปต่อ session ที่ปิดไปแล้ว

        out("")
        try:
            prompt("กด Enter เพื่อกลับไปที่เมนู ")
        except (EOFError, KeyboardInterrupt):
            return 0


def _invoke(function, args, needs_mt5, out):
    """เรียกคำสั่งหนึ่งข้อ — ความล้มเหลวของคำสั่งต้องไม่พาเมนูตายไปด้วย"""
    if not needs_mt5:
        try:
            function(args)
        except Exception as error:
            out(f"คำสั่งล้มเหลว: {type(error).__name__}: {error}")
        return

    import MetaTrader5 as mt5
    import mt5_core as core

    try:
        function(args)
    except core.MT5Error as error:
        out(str(error))
    except KeyboardInterrupt:
        out("\nหยุดแล้ว")
    finally:
        mt5.shutdown()
