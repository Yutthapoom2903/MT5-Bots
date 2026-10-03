"""
ค่าที่ต่างกันตามตลาด — เลือกได้ทีละตัวต่อหนึ่งการรัน (python run.py --symbol BTCUSD)

โปรเจกต์ยังเป็น single-symbol ตามเดิม: ไม่ได้เฝ้าสองตลาดพร้อมกัน แค่เปลี่ยนว่าจะเฝ้าตัวไหน
ค่าที่ผูกกับตลาดมีสี่อย่าง และทั้งหมดอยู่ที่นี่ที่เดียว:

  - คำค้นหาชื่อ symbol ของ broker (ทองกับ BTC ตั้งชื่อคนละแบบ)
  - เพดาน spread  — หน่วยเป็น point และ point ของ BTC ไม่เท่าทอง เพดานของทอง (50) จะบล็อก
                     BTC ทุกแท่ง
  - spread สมมติของ backtest
  - โฟลเดอร์ข้อมูล — แยกต่อ symbol เด็ดขาด ไม่งั้นแท่ง BTC จะไปต่อท้าย CSV ของทอง แล้ว
                     outcomes ติดป้ายกำไรข้ามสองตลาด และ bot_state.json จำ position_risk ปนกัน

ทองใช้ data/ เหมือนเดิมทุกอย่าง ข้อมูลที่สะสมมาจึงไม่ต้องย้าย

ไม่ import MT5 และไม่ import ตัวอื่นในโปรเจกต์ ให้ paths.py กับ strategy.py ดึงไปใช้ได้
"""

from collections import namedtuple

Profile = namedtuple("Profile", "symbol keywords max_spread backtest_spread data_dir")

PROFILES = {
    "XAUUSD": Profile(
        symbol="XAUUSD",
        keywords=("XAUUSD", "XAUUS", "GOLD", "XAU"),
        max_spread=50.0,
        backtest_spread=30.0,
        data_dir="data",
    ),
    # ตัวเลข spread ของ BTC เป็นการประมาณ ไม่ได้วัดจาก broker จริง — broker ส่วนใหญ่ให้
    # BTCUSD กว้างหลักสิบดอลลาร์ ซึ่งที่ point 0.01 คือหลักพัน แต่ถ้า broker ตั้ง digits
    # ต่างออกไปตัวเลขนี้จะเพี้ยนทั้งชุด ให้ดู "spread ตอนนี้" จาก python run.py check
    # แล้วปรับที่นี่ ก่อนเชื่อว่าการที่บอทไม่เข้าไม้เป็นเรื่องของตัวกรองอื่น
    "BTCUSD": Profile(
        symbol="BTCUSD",
        keywords=("BTCUSD", "BTC"),
        max_spread=3000.0,
        backtest_spread=1500.0,
        data_dir="data/btcusd",
    ),
}

DEFAULT = "XAUUSD"

# ตลาดที่ปิดแล้วให้สลับไปเฝ้าตัวไหนแทน — ทองปิดเสาร์-อาทิตย์ แต่ BTC เปิดตลอด
# ตัวที่ไม่อยู่ในนี้ (BTC) ไม่มีที่ให้สลับไป ปิดแล้วก็รอเฉยๆ
FALLBACKS = {"XAUUSD": "BTCUSD"}

_current = DEFAULT


def normalize(name):
    """ชื่อที่ผู้ใช้พิมพ์ -> key ใน PROFILES — โยน ValueError พร้อมรายชื่อที่มี"""
    key = (name or DEFAULT).strip().upper()

    if key not in PROFILES:
        raise ValueError(f"ไม่รู้จัก symbol {name!r} — ที่รองรับ: {', '.join(PROFILES)}")

    return key


def select(name):
    """เลือก profile ของการรันนี้ ต้องเรียกก่อน import runner/strategy/report"""
    global _current
    _current = normalize(name)
    return PROFILES[_current]


def fallback_for(key):
    """key ของตลาดสำรองตอน key ปิด — None ถ้าไม่มี"""
    return FALLBACKS.get(key)


def current():
    return PROFILES[_current]
