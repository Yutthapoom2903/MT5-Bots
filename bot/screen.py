"""
สีและการจัดคอลัมน์บนหน้าจอ — ที่เดียวของทั้งโปรเจกต์

ใช้ ANSI เขียนเอง ไม่เพิ่ม dependency: requirements.txt ปักหมุด MetaTrader5 ซึ่งลงบน
Linux ไม่ได้ การเพิ่ม colorama/rich เข้าไปจะกลายเป็นลงได้เครื่องเดียวจากสองเครื่อง
(เหตุผลเดียวกับที่เมนูไม่ใช่ TUI)

ห้าม import อะไรจาก bot/ หรือ analysis/ ที่นี่ — core.py กับ report.py ใช้ไฟล์นี้ทั้งคู่
และ report ต้องรันบน WSL ที่ไม่มี MetaTrader5 ได้
"""

import os
import re
import sys
import unicodedata

RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"

ANSI = re.compile(r"\033\[[0-9;]*m")

BAR_WIDTH = 10


def color_enabled(stream=None):
    """
    จอนี้รับสีได้ไหม

    ปิดเมื่อ NO_COLOR ถูกตั้ง (ธรรมเนียมของ no-color.org) หรือปลายทางไม่ใช่ terminal
    เช่นตอน redirect ลงไฟล์หรือส่งต่อผ่าน pipe ซึ่งรหัสสีจะกลายเป็นขยะ
    """
    if os.getenv("NO_COLOR") is not None:
        return False

    stream = stream or sys.stdout
    return bool(getattr(stream, "isatty", lambda: False)())


def paint(text, *styles):
    """ย้อมข้อความถ้าจอรับสีได้ ไม่ได้ก็คืนข้อความเดิม เรียกได้เสมอโดยไม่ต้องเช็คก่อน"""
    if not styles or not color_enabled():
        return text

    return f"{''.join(styles)}{text}{RESET}"


def width(text):
    """
    ความกว้างจริงบนจอ

    len() ใช้จัดคอลัมน์ภาษาไทยไม่ได้ — สระบน/ล่างและวรรณยุกต์ซ้อนอยู่บนตัวก่อนหน้า
    ไม่กินที่ แต่ len() นับเป็นตัวหนึ่ง ตารางเลยเบี้ยวทีละคอลัมน์ตามจำนวนสระในคำ
    รหัสสีก็ไม่กินที่เหมือนกัน จึงถอดออกก่อนนับ

    ดูจาก category ไม่ใช่ combining() — สระไทยอย่าง U+0E31 เป็น Mn (ไม่กินที่) แต่
    combining class ของมันเป็น 0 ซึ่งทำให้ combining() ตอบว่าไม่ใช่ตัวซ้อน
    """
    plain = ANSI.sub("", text)
    total = 0

    for char in plain:
        if unicodedata.category(char) in ("Mn", "Me", "Cf"):
            continue
        total += 2 if unicodedata.east_asian_width(char) in "WF" else 1

    return total


def pad(text, size, align="<"):
    """เติมช่องว่างให้กว้างตามที่สั่ง โดยนับความกว้างบนจอ ไม่ใช่จำนวนตัวอักษร"""
    space = max(0, size - width(text))

    if align == ">":
        return " " * space + text

    return text + " " * space


def bar(fraction, size=BAR_WIDTH):
    """แถบสัดส่วน — เห็น 21% เป็นภาพเร็วกว่าอ่านตัวเลข"""
    fraction = min(1.0, max(0.0, fraction))
    filled = int(round(fraction * size))
    return "█" * filled + "░" * (size - filled)


def rate_style(fraction, good=0.9, fair=0.5):
    """สีตามสัดส่วน — เขียวคือครบ เหลืองคือพอใช้ แดงคือน้อยจนต้องดู"""
    if fraction >= good:
        return GREEN
    if fraction >= fair:
        return YELLOW

    return RED
