"""
บอทเทรดอัตโนมัติ XAUUSD M15 ด้วย MA20/MA50 crossover

*** สคริปต์นี้ส่งคำสั่งซื้อขายจริง ***
ค่าเริ่มต้นคือยอมให้รันบนบัญชี Demo เท่านั้น ถ้าจะใช้กับบัญชีจริงต้องตั้ง
ALLOW_LIVE_ACCOUNT = True ด้วยตัวเองก่อน

ขนาด SL/TP อิง ATR ไม่ใช่ค่าคงที่ เพราะความผันผวนของทองเปลี่ยนไปตามช่วงตลาด
ขนาดไม้คำนวณย้อนจากจำนวนเงินที่ยอมเสียได้ต่อไม้ ไม่ใช่ lot ตายตัว
"""

import os
import time
from datetime import datetime

import MetaTrader5 as mt5
import pandas as pd
import requests
from dotenv import load_dotenv

import mt5_core as core
import mt5_trade as trade

# ---------- ตั้งค่าหลัก ----------
SYMBOL = "XAUUSD"
TIMEFRAME = mt5.TIMEFRAME_M15
FAST_MA = 20
SLOW_MA = 50
ATR_PERIOD = 14
BARS = 300
CHECK_EVERY_SECONDS = 30

# ---------- การบริหารความเสี่ยง ----------
ALLOW_LIVE_ACCOUNT = False   # ต้องแก้เป็น True เองก่อนใช้กับบัญชีจริง
RISK_PERCENT = 0.5           # เปอร์เซ็นต์ของ balance ที่ยอมเสียต่อไม้
USE_FIXED_LOT = False        # True = ใช้ FIXED_LOT แทนการคำนวณจากความเสี่ยง
FIXED_LOT = 0.01
SL_ATR_MULT = 1.5            # ระยะ SL = ATR x ค่านี้
TP_ATR_MULT = 3.0            # ระยะ TP = ATR x ค่านี้ (RR 1:2)
MAX_SPREAD_POINTS = 50       # ไม่เข้าไม้ถ้า spread กว้างกว่านี้
CLOSE_ON_REVERSE = True      # ปิดไม้เดิมก่อนเมื่อสัญญาณกลับทาง
MAGIC = 123456
DEVIATION = 20

# ---------- ไฟล์ ----------
STATE_FILE = "bot_state.json"
TRADE_LOG = "trade_log.csv"
LOG_FILE = "bot.log"

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logger = core.setup_logging(LOG_FILE)


def send_telegram(message):
    """ส่งข้อความเข้า Telegram — ล้มเหลวได้โดยไม่ทำให้บอทหยุด แต่ต้องเห็นใน log"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.debug("ยังไม่ได้ตั้ง TELEGRAM_TOKEN / TELEGRAM_CHAT_ID ข้ามการแจ้งเตือน")
        return

    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=10,
        )
    except requests.RequestException as error:
        logger.warning("ส่ง Telegram ไม่สำเร็จ: %s", error)


def log_trade(row):
    """บันทึกไม้ที่ส่งไปแล้วลง CSV เพื่อไล่ย้อนดูภายหลัง"""
    frame = pd.DataFrame([row])
    frame.to_csv(TRADE_LOG, mode="a", header=not os.path.exists(TRADE_LOG), index=False)


def guard_account(account):
    """หยุดทันทีถ้าเป็นบัญชีจริงแต่ยังไม่ได้เปิดสวิตช์อนุญาตไว้"""
    kind = "Demo" if core.is_demo(account) else "บัญชีจริง"
    logger.info(
        "บัญชี %s (%s) server %s balance %.2f %s",
        account.login, kind, account.server, account.balance, account.currency,
    )

    if not core.is_demo(account) and not ALLOW_LIVE_ACCOUNT:
        raise core.MT5Error(
            "นี่คือบัญชีจริง แต่ ALLOW_LIVE_ACCOUNT ยังเป็น False — หยุดเพื่อความปลอดภัย"
        )


def build_levels(info, tick, action, atr):
    """คำนวณ SL/TP จากราคาปัจจุบัน โดยเคารพระยะขั้นต่ำที่ broker กำหนด"""
    minimum = trade.min_stop_distance(info, tick)

    sl_distance = max(atr * SL_ATR_MULT, minimum)
    tp_distance = max(atr * TP_ATR_MULT, minimum)

    if action == "BUY":
        entry = tick.ask
        return entry, entry - sl_distance, entry + tp_distance, sl_distance

    entry = tick.bid
    return entry, entry + sl_distance, entry - tp_distance, sl_distance


def handle_existing_positions(signal):
    """
    คืน True ถ้าเปิดไม้ใหม่ต่อได้

    ถืออยู่ทางเดียวกันแล้วให้ข้าม ไม่เปิดซ้อน
    ถือสวนทางอยู่ให้ปิดก่อนถ้าเปิด CLOSE_ON_REVERSE ไว้
    """
    positions = trade.open_positions(SYMBOL, MAGIC)

    if not positions:
        return True

    wanted = mt5.POSITION_TYPE_BUY if signal == "BUY" else mt5.POSITION_TYPE_SELL

    for position in positions:
        if position.type == wanted:
            logger.info("ถือไม้ทาง %s อยู่แล้ว (ticket %s) ข้ามสัญญาณนี้", signal, position.ticket)
            return False

    if not CLOSE_ON_REVERSE:
        logger.info("มีไม้สวนทางอยู่ แต่ CLOSE_ON_REVERSE = False จึงไม่เปิดไม้ใหม่")
        return False

    for position in positions:
        logger.info("สัญญาณกลับทาง — ปิด ticket %s ก่อน", position.ticket)
        result = trade.close_position(position, DEVIATION, logger)

        if result is None or result.retcode != trade.RETCODE_DONE:
            logger.error("ปิดไม้เดิมไม่สำเร็จ ยกเลิกการเปิดไม้ใหม่รอบนี้")
            return False

        send_telegram(f"ปิดไม้เดิม ticket {position.ticket} เพราะสัญญาณกลับเป็น {signal}")

    return True


def try_enter(signal, candle, atr):
    """ตรวจเงื่อนไขทั้งหมดแล้วส่งคำสั่งถ้าผ่านครบ"""
    info = mt5.symbol_info(SYMBOL)
    tick = mt5.symbol_info_tick(SYMBOL)

    if info is None or tick is None:
        logger.warning("ดึงข้อมูล Symbol/ราคาไม่ได้ ข้ามสัญญาณนี้")
        return

    if not trade.symbol_is_tradable(info):
        logger.warning("%s ปิดการเทรดอยู่ (trade_mode %s) ข้ามสัญญาณนี้", SYMBOL, info.trade_mode)
        return

    spread = core.spread_points(SYMBOL)
    if spread is not None and spread > MAX_SPREAD_POINTS:
        logger.warning("spread %.1f points กว้างเกิน %d ข้ามสัญญาณนี้", spread, MAX_SPREAD_POINTS)
        return

    if pd.isna(atr) or atr <= 0:
        logger.warning("ค่า ATR ใช้ไม่ได้ ข้ามสัญญาณนี้")
        return

    if not handle_existing_positions(signal):
        return

    entry, sl, tp, sl_distance = build_levels(info, tick, signal, atr)

    account = mt5.account_info()
    if USE_FIXED_LOT or account is None:
        lots = trade.normalize_volume(info, FIXED_LOT)
    else:
        lots = trade.calculate_lot(info, account, sl_distance, RISK_PERCENT)

    risk = trade.estimated_loss(info, lots, sl_distance)
    risk_text = f"{risk:.2f}" if risk is not None else "?"

    logger.info(
        "เตรียมเข้า %s %.2f lot ที่ %.2f | SL %.2f (ระยะ %.2f) | TP %.2f | เสี่ยงราว %s",
        signal, lots, entry, sl, sl_distance, tp, risk_text,
    )

    result = trade.send_market_order(SYMBOL, signal, lots, sl, tp, MAGIC, DEVIATION, logger)
    success = result is not None and result.retcode == trade.RETCODE_DONE

    if success:
        logger.info("เข้าไม้สำเร็จ ticket %s ที่ราคา %.2f", result.order, result.price)
        send_telegram(
            f"เข้า {signal} {lots} lot\n"
            f"ราคา: {result.price:.2f}\n"
            f"SL: {sl:.2f}  TP: {tp:.2f}\n"
            f"เสี่ยงราว {risk_text} {account.currency if account else ''}"
        )
    else:
        logger.error("เข้าไม้ไม่สำเร็จ: %s", trade.describe_result(result))
        send_telegram(f"เข้า {signal} ไม่สำเร็จ\n{trade.describe_result(result)}")

    log_trade({
        "logged_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "candle_time": candle["time"],
        "symbol": SYMBOL,
        "signal": signal,
        "lots": lots,
        "intended_entry": round(entry, 2),
        "filled_price": round(result.price, 2) if success else "",
        "sl": round(sl, 2),
        "tp": round(tp, 2),
        "atr": round(float(atr), 2),
        "spread_points": spread,
        "risk_amount": round(risk, 2) if risk is not None else "",
        "status": "OK" if success else trade.describe_result(result),
        "ticket": result.order if success else "",
    })


def main():
    account = core.connect()
    guard_account(account)
    core.prepare_symbol(SYMBOL)

    state = core.load_state(STATE_FILE)
    last_candle_time = state.get("last_candle_time")

    logger.info(
        "เริ่มบอท %s M15 | MA%d/MA%d | SL %.1fxATR TP %.1fxATR | ความเสี่ยง %s",
        SYMBOL, FAST_MA, SLOW_MA, SL_ATR_MULT, TP_ATR_MULT,
        f"{FIXED_LOT} lot คงที่" if USE_FIXED_LOT else f"{RISK_PERCENT}% ต่อไม้",
    )
    if last_candle_time:
        logger.info("แท่งล่าสุดที่ประมวลผลไปแล้วก่อน restart: %s", last_candle_time)

    send_telegram(f"บอท {SYMBOL} เริ่มทำงาน ({'Demo' if core.is_demo(account) else 'บัญชีจริง'})")

    min_bars = SLOW_MA + ATR_PERIOD + 5

    while True:
        df = core.get_rates(SYMBOL, TIMEFRAME, BARS, min_bars)

        if df is None:
            logger.warning("ดึงแท่งราคาไม่สำเร็จ: %s", core.last_error_text())
            time.sleep(CHECK_EVERY_SECONDS)
            continue

        core.add_moving_averages(df, FAST_MA, SLOW_MA)
        df["atr"] = core.calculate_atr(df, ATR_PERIOD)

        candle = core.closed_candle(df)
        candle_time = str(candle["time"])

        # แท่งนี้ประมวลผลไปแล้ว — state เก็บลงไฟล์ไว้จึงไม่ยิงซ้ำแม้ restart กลางแท่ง
        if candle_time == last_candle_time:
            time.sleep(CHECK_EVERY_SECONDS)
            continue

        last_candle_time = candle_time
        core.save_state(STATE_FILE, {"last_candle_time": candle_time})

        signal = core.crossover_signal(df)
        logger.info(
            "แท่งปิด %s | Close %.2f | MA%d %.2f | MA%d %.2f | ATR %.2f | Signal %s",
            candle_time, candle["close"], FAST_MA, candle["ma_fast"],
            SLOW_MA, candle["ma_slow"], candle["atr"], signal,
        )

        if signal != "HOLD":
            try_enter(signal, candle, candle["atr"])

        time.sleep(CHECK_EVERY_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except core.MT5Error as error:
        logger.error("%s", error)
        raise SystemExit(1)
    except KeyboardInterrupt:
        logger.info("หยุดบอทแล้ว")
    except Exception:
        logger.exception("บอทหยุดเพราะข้อผิดพลาดที่ไม่ได้คาดไว้")
        send_telegram("บอทหยุดทำงานเพราะข้อผิดพลาด ดูรายละเอียดใน bot.log")
        raise
    finally:
        mt5.shutdown()
