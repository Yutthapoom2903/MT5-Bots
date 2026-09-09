"""
Stub ของแพ็กเกจ MetaTrader5 สำหรับรันเทสบนเครื่องที่ไม่มี MT5 (เช่น Linux/WSL)

มีแค่ค่าคงที่และฟังก์ชันเปล่าที่ mt5_core / mt5_trade อ้างถึงตอน import
เทสในโปรเจกต์นี้ทดสอบเฉพาะ logic ล้วน จึงไม่ต้องมีฟังก์ชันที่ต่อ terminal จริง
ถ้ารันบน Windows ที่ติดตั้ง MetaTrader5 ไว้แล้ว ไฟล์นี้จะไม่ถูกใช้
"""

# ค่าคงที่ตรงตามที่แพ็กเกจจริงประกาศไว้
TIMEFRAME_M15 = 15
TIMEFRAME_H1 = 16385

ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1

ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2

TRADE_ACTION_DEAL = 1
ORDER_TIME_GTC = 0

POSITION_TYPE_BUY = 0
POSITION_TYPE_SELL = 1

ACCOUNT_TRADE_MODE_DEMO = 0
ACCOUNT_TRADE_MODE_CONTEST = 1
ACCOUNT_TRADE_MODE_REAL = 2

SYMBOL_TRADE_MODE_DISABLED = 0
SYMBOL_TRADE_MODE_FULL = 4


def _unavailable(*args, **kwargs):
    raise RuntimeError("stub MetaTrader5: ฟังก์ชันนี้ต่อ terminal จริง ห้ามเรียกในเทส")


def initialize(*args, **kwargs):
    return False


def shutdown():
    return None


def last_error():
    return (-10000, "stub MetaTrader5")


account_info = _unavailable
symbol_info = _unavailable
symbol_info_tick = _unavailable
symbol_select = _unavailable
copy_rates_from_pos = _unavailable
positions_get = _unavailable
order_send = _unavailable
