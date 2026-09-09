"""
ชั้นจัดการคำสั่งซื้อขาย — ทุกอย่างที่ต้องคุยกับ broker จริง

แยกออกจาก mt5_core.py เพราะสคริปต์ที่แค่บันทึกข้อมูลไม่ควรต้อง import ส่วนนี้เลย
งานหลักคือทำให้ order ผ่านการตรวจของ broker: ปัดราคาตามจำนวนหลัก, เคารพระยะ
stop ขั้นต่ำ, เลือก filling mode ที่ broker รองรับ และปัด lot ตาม volume step
"""

import math
from decimal import Decimal

import MetaTrader5 as mt5

# retcode ที่ต้องรับมือ — ระบุเป็นตัวเลขตรงๆ เพราะชื่อค่าคงที่ต่างกันตามเวอร์ชันแพ็กเกจ
RETCODE_DONE = 10009
RETCODE_REQUOTE = 10004
RETCODE_INVALID_STOPS = 10016
RETCODE_MARKET_CLOSED = 10018
RETCODE_NO_MONEY = 10019
RETCODE_PRICE_CHANGED = 10020
RETCODE_PRICE_OFF = 10021
RETCODE_INVALID_FILL = 10030

RETRYABLE = (RETCODE_REQUOTE, RETCODE_PRICE_CHANGED, RETCODE_PRICE_OFF)

RETCODE_MESSAGES = {
    RETCODE_DONE: "สำเร็จ",
    RETCODE_REQUOTE: "ราคาเปลี่ยน (requote)",
    RETCODE_INVALID_STOPS: "SL/TP ใกล้ราคาเกินกว่าที่ broker ยอม",
    RETCODE_MARKET_CLOSED: "ตลาดปิด",
    RETCODE_NO_MONEY: "เงินในบัญชีไม่พอ",
    RETCODE_PRICE_CHANGED: "ราคาเปลี่ยนระหว่างส่งคำสั่ง",
    RETCODE_PRICE_OFF: "ไม่มีราคาให้เทรดตอนนี้",
    RETCODE_INVALID_FILL: "broker ไม่รองรับ filling mode ที่ส่งไป",
}


def describe_result(result):
    """แปลงผลลัพธ์ order_send เป็นข้อความอ่านออก"""
    if result is None:
        return "order_send คืน None"

    meaning = RETCODE_MESSAGES.get(result.retcode, "")
    suffix = f" — {meaning}" if meaning else ""
    return f"retcode {result.retcode}{suffix}: {result.comment}"


# ---------- การปัดค่าให้ broker ยอมรับ ----------

def normalize_price(info, price):
    """ปัดราคาตามจำนวนหลักของ Symbol — ถ้าไม่ปัด broker จะตีกลับด้วย invalid price"""
    return round(float(price), info.digits)


def normalize_volume(info, volume):
    """ปัด lot ลงให้ลงตัวกับ volume_step แล้วบีบให้อยู่ในช่วง min/max ที่ broker ยอม"""
    step = info.volume_step or 0.01
    decimals = max(0, -Decimal(str(step)).as_tuple().exponent)

    # ปัดลงเสมอ เพื่อไม่ให้ความเสี่ยงเกินที่ตั้งไว้
    lots = math.floor(float(volume) / step) * step
    lots = round(lots, decimals)

    lots = max(lots, info.volume_min)
    lots = min(lots, info.volume_max)
    return round(lots, decimals)


def min_stop_distance(info, tick):
    """
    ระยะขั้นต่ำระหว่างราคาเข้ากับ SL/TP ที่ broker ยอมรับ

    broker บางรายคืน trade_stops_level เป็น 0 ซึ่งแปลว่าใช้ระยะแบบ dynamic
    อิง spread จึงเผื่อเป็น 3 เท่าของ spread ปัจจุบันไว้ด้วย
    """
    level_distance = (getattr(info, "trade_stops_level", 0) or 0) * info.point
    spread_distance = (tick.ask - tick.bid) * 3 if tick else 0
    return max(level_distance, spread_distance, info.point * 10)


def pick_filling_modes(info):
    """
    ไล่ filling mode ตามที่ Symbol ประกาศว่ารองรับ

    filling_mode เป็น bitmask: bit 0 = FOK, bit 1 = IOC
    คืนเป็น list เพื่อให้ผู้เรียกลองตัวถัดไปได้เมื่อโดน retcode 10030
    """
    modes = []
    allowed = getattr(info, "filling_mode", 0) or 0

    if allowed & 1:
        modes.append(mt5.ORDER_FILLING_FOK)
    if allowed & 2:
        modes.append(mt5.ORDER_FILLING_IOC)

    modes.append(mt5.ORDER_FILLING_RETURN)
    return modes


# ---------- ขนาดไม้ ----------

def calculate_lot(info, account, sl_distance, risk_percent):
    """
    คำนวณ lot จากจำนวนเงินที่ยอมเสียได้ต่อไม้

    ขาดทุนต่อ 1 lot = (ระยะ SL / tick_size) * tick_value
    lot = เงินที่ยอมเสีย / ขาดทุนต่อ 1 lot
    """
    tick_value = getattr(info, "trade_tick_value", 0) or 0
    tick_size = getattr(info, "trade_tick_size", 0) or info.point

    if sl_distance <= 0 or tick_value <= 0 or tick_size <= 0:
        return info.volume_min

    risk_money = account.balance * (risk_percent / 100)
    loss_per_lot = (sl_distance / tick_size) * tick_value

    if loss_per_lot <= 0:
        return info.volume_min

    return normalize_volume(info, risk_money / loss_per_lot)


def lot_exceeds_budget(info, account, sl_distance, risk_percent):
    """
    True เมื่อไม้เล็กที่สุดที่ broker ยอมให้เปิด ยังเสี่ยงเกินงบที่ตั้งไว้

    เกิดได้จริงกับพอร์ตเล็ก: XAUUSD lot ขั้นต่ำ 0.01 กับ SL ราว 16 USD
    เท่ากับเสี่ยง 16 USD ต่อไม้ ซึ่งเกิน 0.5% ของพอร์ต 1000 USD ไปหลายเท่า
    normalize_volume จะปัดขึ้นให้ถึง volume_min เสมอ ความเสี่ยงจึงเกินงบเงียบๆ
    ถ้าไม่ตรวจตรงนี้
    """
    minimum_loss = estimated_loss(info, info.volume_min, sl_distance)

    if minimum_loss is None:
        return False

    return minimum_loss > risk_budget(account, risk_percent)


def risk_budget(account, risk_percent):
    """จำนวนเงินที่ยอมเสียได้ต่อไม้ตามเปอร์เซ็นต์ที่ตั้งไว้"""
    return account.balance * (risk_percent / 100)


def estimated_loss(info, lots, sl_distance):
    """ประมาณเงินที่จะเสียถ้าโดน SL — ใช้แสดงใน log และ Telegram"""
    tick_value = getattr(info, "trade_tick_value", 0) or 0
    tick_size = getattr(info, "trade_tick_size", 0) or info.point

    if tick_value <= 0 or tick_size <= 0:
        return None

    return (sl_distance / tick_size) * tick_value * lots


# ---------- สถานะพอร์ต ----------

def symbol_is_tradable(info):
    """True เมื่อ Symbol เปิดให้ส่งคำสั่งได้เต็มรูปแบบ"""
    return info.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL


def open_positions(symbol, magic):
    """position ของ Symbol นี้ที่เปิดโดยบอทตัวนี้ (กรองด้วย magic)"""
    positions = mt5.positions_get(symbol=symbol)

    if positions is None:
        return []

    return [position for position in positions if position.magic == magic]


# ---------- ส่งคำสั่ง ----------

def send_market_order(symbol, action, lots, sl, tp, magic, deviation, logger, attempts=3):
    """
    ส่งคำสั่ง market พร้อม SL/TP

    ดึงราคาใหม่ทุกครั้งที่ลองใหม่ เพราะสาเหตุที่ต้องลองใหม่คือราคาขยับ
    และไล่ filling mode ตัวถัดไปเมื่อ broker ตอบว่าไม่รองรับตัวที่ส่งไป
    """
    info = mt5.symbol_info(symbol)
    if info is None:
        return None

    filling_modes = pick_filling_modes(info)
    filling_index = 0

    for attempt in range(1, attempts + 1):
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            logger.warning("ดึงราคาไม่ได้ ข้ามการส่งคำสั่งรอบนี้")
            return None

        if action == "BUY":
            price = tick.ask
            order_type = mt5.ORDER_TYPE_BUY
        else:
            price = tick.bid
            order_type = mt5.ORDER_TYPE_SELL

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lots,
            "type": order_type,
            "price": normalize_price(info, price),
            "sl": normalize_price(info, sl),
            "tp": normalize_price(info, tp),
            "magic": magic,
            "deviation": deviation,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_modes[filling_index],
            "comment": "ma-cross bot",
        }

        result = mt5.order_send(request)

        if result is not None and result.retcode == RETCODE_DONE:
            return result

        logger.warning("ส่งคำสั่งครั้งที่ %d ไม่ผ่าน: %s", attempt, describe_result(result))

        if result is None:
            return None

        if result.retcode == RETCODE_INVALID_FILL and filling_index + 1 < len(filling_modes):
            filling_index += 1
            logger.info("เปลี่ยน filling mode แล้วลองใหม่")
            continue

        if result.retcode not in RETRYABLE:
            return result

    return result


def close_position(position, deviation, logger):
    """ปิด position ที่ถืออยู่ด้วยคำสั่งสวนทาง"""
    info = mt5.symbol_info(position.symbol)
    tick = mt5.symbol_info_tick(position.symbol)

    if info is None or tick is None:
        logger.warning("ปิด position ไม่ได้: ดึงข้อมูล Symbol ไม่สำเร็จ")
        return None

    if position.type == mt5.POSITION_TYPE_BUY:
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
    else:
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask

    for filling_mode in pick_filling_modes(info):
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "volume": position.volume,
            "type": order_type,
            "position": position.ticket,
            "price": normalize_price(info, price),
            "deviation": deviation,
            "magic": position.magic,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_mode,
            "comment": "ma-cross close",
        }

        result = mt5.order_send(request)

        if result is not None and result.retcode == RETCODE_DONE:
            return result

        if result is None or result.retcode != RETCODE_INVALID_FILL:
            logger.warning("ปิด position ไม่สำเร็จ: %s", describe_result(result))
            return result

    return None


# ---------- การดูแลไม้ที่เปิดอยู่ ----------

def breakeven_level(position_type, entry, current_price, initial_risk, trigger_r, buffer_r):
    """
    ระดับ SL ใหม่เมื่อกำไรถึงจุดที่ควรย้ายมาเสมอทุน

    initial_risk คือระยะ SL ตอนเปิดไม้ (1R) ถ้ากำไรถึง trigger_r เท่าของ R
    ให้ย้าย SL มาที่ทุนบวก buffer เล็กน้อยเผื่อ spread กับค่าคอม
    คืน None ถ้ายังไม่ถึงเงื่อนไข
    """
    if initial_risk <= 0:
        return None

    if position_type == mt5.POSITION_TYPE_BUY:
        if current_price - entry < initial_risk * trigger_r:
            return None
        return entry + initial_risk * buffer_r

    if entry - current_price < initial_risk * trigger_r:
        return None
    return entry - initial_risk * buffer_r


def trailing_level(position_type, entry, current_price, initial_risk, trigger_r, atr, multiplier):
    """
    ระดับ SL แบบไล่ตามราคา — เริ่มทำงานหลังกำไรถึง trigger_r เท่าของ R

    ลากตามห่างจากราคาปัจจุบัน atr x multiplier คืน None ถ้ายังไม่ถึงเงื่อนไข
    """
    if initial_risk <= 0 or atr is None or atr <= 0:
        return None

    if position_type == mt5.POSITION_TYPE_BUY:
        if current_price - entry < initial_risk * trigger_r:
            return None
        return current_price - atr * multiplier

    if entry - current_price < initial_risk * trigger_r:
        return None
    return current_price + atr * multiplier


def better_stop(position_type, current_sl, candidate):
    """
    คืน candidate เฉพาะเมื่อมันดีกว่า SL เดิม (ขยับไปทางกำไรเท่านั้น)

    SL ต้องไม่ถอยหลังเด็ดขาด ไม่งั้นการไล่ stop จะกลายเป็นการขยายความเสี่ยง
    """
    if candidate is None:
        return None

    if not current_sl:
        return candidate

    if position_type == mt5.POSITION_TYPE_BUY:
        return candidate if candidate > current_sl else None

    return candidate if candidate < current_sl else None


def stop_is_far_enough(position_type, current_price, candidate, minimum_distance):
    """broker ปฏิเสธ SL ที่ใกล้ราคาปัจจุบันเกินไป ตรวจก่อนส่งจะได้ไม่โดนตีกลับ"""
    if position_type == mt5.POSITION_TYPE_BUY:
        return current_price - candidate >= minimum_distance

    return candidate - current_price >= minimum_distance


def modify_stops(position, new_sl, new_tp, logger):
    """ย้าย SL/TP ของไม้ที่เปิดอยู่"""
    info = mt5.symbol_info(position.symbol)

    if info is None:
        return None

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": position.symbol,
        "position": position.ticket,
        "sl": normalize_price(info, new_sl),
        "tp": normalize_price(info, new_tp),
        "magic": position.magic,
    }

    result = mt5.order_send(request)

    if result is None or result.retcode != RETCODE_DONE:
        logger.warning("ย้าย SL ticket %s ไม่สำเร็จ: %s", position.ticket, describe_result(result))

    return result


# ---------- สรุปผลรายวันสำหรับตัวตัดวงจร ----------

def summarize_deals(deals, symbol, magic):
    """
    สรุปดีลที่ปิดแล้วของบอทตัวนี้

    รับ list ของ deal object เพื่อให้เทสได้โดยไม่ต้องต่อ MT5
    นับเฉพาะดีลขาออก (entry == DEAL_ENTRY_OUT) เพราะนั่นคือตอนที่กำไร/ขาดทุนเกิดจริง
    """
    closed = [
        deal for deal in deals or []
        if deal.symbol == symbol
        and deal.magic == magic
        and deal.entry == mt5.DEAL_ENTRY_OUT
    ]

    profit = sum(deal.profit for deal in closed)

    consecutive_losses = 0
    for deal in reversed(closed):
        if deal.profit < 0:
            consecutive_losses += 1
        else:
            break

    return {
        "trades": len(closed),
        "profit": profit,
        "wins": sum(1 for deal in closed if deal.profit > 0),
        "losses": sum(1 for deal in closed if deal.profit < 0),
        "consecutive_losses": consecutive_losses,
    }


def deals_today(symbol, magic, now=None):
    """สรุปผลของวันนี้จากประวัติจริงใน MT5 — ปลอดภัยต่อการ restart เพราะอ่านจากต้นทาง"""
    from datetime import datetime, timedelta

    now = now or datetime.now()
    start = datetime(now.year, now.month, now.day)

    # เผื่อท้ายวันไว้กันปัญหาเวลาเซิร์ฟเวอร์ต่างจากเวลาเครื่อง
    deals = mt5.history_deals_get(start, now + timedelta(days=1))

    return summarize_deals(deals, symbol, magic)
