"""
ที่อยู่ของไฟล์ที่บอทเขียน — รวมไว้ที่เดียว

เดิมชื่อไฟล์พวกนี้ถูกพิมพ์ซ้ำใน runner, report, menu และ engine ทำให้ย้ายที่เก็บ
ทีเดียวไม่ได้ ต้องไล่แก้ทุกไฟล์แล้วหวังว่าไม่ลืมสักที่

path เป็นแบบ relative กับ working directory ตั้งใจให้เป็นแบบนั้น — เทสหลายตัว chdir
เข้าโฟลเดอร์ชั่วคราวแล้วเรียกฟังก์ชันตรงๆ ถ้าผูกกับตำแหน่งไฟล์ .py แทน เทสพวกนั้นจะไป
อ่านข้อมูลจริงของผู้ใช้ ส่วนคนใช้ก็ต้องรันจากรากโปรเจกต์อยู่แล้วเหมือนเดิม
"""

import os

DATA_DIR = "data"

# ปฏิทินข่าวเป็นของทั้งโลก ไม่ใช่ของ symbol ใด จึงอยู่ที่ data/ เสมอ ไม่ตามโฟลเดอร์ของ symbol
NEWS_CACHE = os.path.join(DATA_DIR, "news_calendar.json")


def configure(data_dir):
    """ย้ายไฟล์ของ symbol ไปอยู่ใต้ data_dir — run.py เรียกก่อน import อย่างอื่นเท่านั้น

    ไฟล์ที่ผูกกับ symbol (log, state, CSV) ต้องแยกกัน ไม่งั้นข้อมูลสองตลาดปนในไฟล์เดียว
    """
    global SIGNAL_LOG, FEATURE_LOG, TRADE_LOG, STATE_FILE, LOG_FILE

    SIGNAL_LOG = os.path.join(data_dir, "signal_log.csv")
    FEATURE_LOG = os.path.join(data_dir, "market_training_data.csv")
    TRADE_LOG = os.path.join(data_dir, "trade_log.csv")
    STATE_FILE = os.path.join(data_dir, "bot_state.json")
    LOG_FILE = os.path.join(data_dir, "bot.log")


configure(DATA_DIR)


def ensure_parent(path):
    """สร้างโฟลเดอร์ปลายทางถ้ายังไม่มี — เรียกก่อนเขียนทุกครั้ง

    data/ อยู่ใน git (CSV ถูก commit) แต่ clone ใหม่ที่ยังไม่มีไฟล์เลย หรือเทสที่ chdir
    เข้าโฟลเดอร์ว่าง จะไม่มีโฟลเดอร์นี้ ถ้าไม่สร้างให้ก่อน การเขียนแท่งแรกจะล้ม
    """
    directory = os.path.dirname(path)

    if directory:
        os.makedirs(directory, exist_ok=True)
