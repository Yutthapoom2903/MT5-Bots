"""
ลูปหลักของโปรเจกต์ — ดึงข้อมูลรอบเดียวต่อหนึ่งแท่ง แล้วป้อนให้ทุกงานพร้อมกัน

เดิมมีสามสคริปต์แยกกันที่ต่าง connect MT5 และ poll ราคาของตัวเอง ทั้งที่ใช้ข้อมูล
ชุดเดียวกัน ตอนนี้รวมเป็นลูปเดียว: ดึงครั้งเดียว -> บันทึก signal log -> บันทึก
feature log -> ให้ strategy ตัดสินใจ -> ส่งคำสั่งถ้าเปิดโหมดเทรดไว้

โหมดเทรดปิดเป็นค่าเริ่มต้น ต้องสั่ง --trade เองเท่านั้นถึงจะส่งคำสั่งจริง
"""

import logging
import os
import time
from datetime import datetime, timedelta

import MetaTrader5 as mt5
from dotenv import load_dotenv

import mt5_core as core
import mt5_trade as trade
import notify
import strategy

# ---------- ตลาดที่เฝ้า ----------
SYMBOL = "XAUUSD"
ENTRY_TIMEFRAME = mt5.TIMEFRAME_M15
TREND_TIMEFRAME = mt5.TIMEFRAME_H1
CONFIRM_TIMEFRAME = mt5.TIMEFRAME_M5

FAST_MA = 20
SLOW_MA = 50
RSI_PERIOD = 14
ATR_PERIOD = 14
ADX_PERIOD = 14

BARS_M15 = 300
BARS_H1 = 150
BARS_M5 = 200
CHECK_EVERY_SECONDS = 30

# ---------- การบริหารความเสี่ยง ----------
ALLOW_LIVE_ACCOUNT = False    # ต้องแก้เป็น True เองก่อนใช้กับบัญชีจริง
RISK_PERCENT = 1.0            # เปอร์เซ็นต์ของ balance ที่ยอมเสียต่อไม้
ALLOW_RISK_OVER_BUDGET = False  # True = ยอมเทรดแม้ไม้ขั้นต่ำจะเสี่ยงเกินงบ
USE_FIXED_LOT = False
FIXED_LOT = 0.01
SL_ATR_MULT = 1.5
TP_ATR_MULT = 3.0
CLOSE_ON_REVERSE = True
MAGIC = 123456
DEVIATION = 20

# ---------- การดูแลไม้หลังเปิดแล้ว ----------
USE_BREAKEVEN = True          # ย้าย SL มาเสมอทุนเมื่อกำไรถึงจุดที่กำหนด
BREAKEVEN_AT_R = 1.0          # กำไรกี่เท่าของความเสี่ยงถึงจะย้าย
BREAKEVEN_BUFFER_R = 0.1      # เผื่อเหนือทุนเล็กน้อยกัน spread กับค่าคอม
USE_TRAILING = True           # ไล่ SL ตามราคาเมื่อกำไรวิ่งต่อ
TRAIL_START_R = 1.5
TRAIL_ATR_MULT = 2.0
USE_PARTIAL_TP = True         # เก็บกำไรบางส่วนแล้วปล่อยที่เหลือวิ่ง
PARTIAL_TP_AT_R = 1.0
PARTIAL_TP_FRACTION = 0.5

# ---------- ตัวตัดวงจร หยุดเองเมื่อวันนี้ไม่เข้าทาง ----------
MAX_DAILY_LOSS_PERCENT = 3.0  # ขาดทุนถึงกี่ % ของทุนต้นวันแล้วหยุดเทรดทั้งวัน
MAX_TRADES_PER_DAY = 5
MAX_CONSECUTIVE_LOSSES = 3

# ---------- ความทนทานของลูป ----------
MARKET_CLOSED_SLEEP = 300     # ตลาดปิดแล้วไม่ต้อง poll ถี่
RECONNECT_DELAY = 30
CLOSE_LOOKUP_ATTEMPTS = 10    # รอประวัติดีลของไม้ที่เพิ่งปิดกี่รอบก่อนเลิกรอ
HEARTBEAT_EVERY_HOURS = 12    # แจ้ง Telegram เป็นระยะว่ายังทำงานอยู่ 0 = ปิด

# ---------- ไฟล์ ----------
SIGNAL_LOG = "signal_log.csv"
FEATURE_LOG = "market_training_data.csv"
TRADE_LOG = "trade_log.csv"
STATE_FILE = "bot_state.json"
LOG_FILE = "bot.log"

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ---------- แจ้งเตือน ----------

# ตัวเดียวทั้งโปรเจกต์ เพราะมันต้องจำว่าเพิ่งส่งอะไรไปถึงจะกันข้อความซ้ำได้
# ผูกกับ root logger ตัวเดียวกับที่ core.setup_logging() จะตั้งค่าให้ตอน run()
NOTIFIER = notify.Notifier(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, logging.getLogger())

# ใช้บอกอายุการทำงานใน heartbeat — เพิ่ง restart กับรันมาสามวันคนละเรื่องกัน
STARTED_AT = time.time()


# ---------- รวบรวมข้อมูลตลาด ----------

def build_context():
    """
    ดึงทุก timeframe แล้วประกอบเป็น context ก้อนเดียวให้ strategy ตัดสินใจ

    คืน (context, candle) หรือ (None, None) ถ้าข้อมูลไม่พอ
    candle คือแท่ง M15 ที่ปิดล่าสุด — แท่งเดียวที่ใช้ตัดสินใจได้
    """
    min_bars = SLOW_MA + max(RSI_PERIOD, ATR_PERIOD, ADX_PERIOD) + 5
    m15 = core.get_rates(SYMBOL, ENTRY_TIMEFRAME, BARS_M15, min_bars)

    if m15 is None:
        return None, None

    core.add_moving_averages(m15, FAST_MA, SLOW_MA)
    m15["rsi"] = core.calculate_rsi(m15["close"], RSI_PERIOD)
    m15["atr"] = core.calculate_atr(m15, ATR_PERIOD)
    m15["adx"] = core.calculate_adx(m15, ADX_PERIOD)

    h1 = core.get_rates(SYMBOL, TREND_TIMEFRAME, BARS_H1, SLOW_MA + 3)
    if h1 is not None:
        core.add_moving_averages(h1, FAST_MA, SLOW_MA)

    m5 = core.get_rates(SYMBOL, CONFIRM_TIMEFRAME, BARS_M5, SLOW_MA + 3)
    if m5 is not None:
        core.add_moving_averages(m5, FAST_MA, SLOW_MA)

    candle = core.closed_candle(m15)

    context = {
        "symbol": SYMBOL,
        "candle_time": candle["time"],
        "server_hour": candle["time"].hour,
        "fast_ma": FAST_MA,
        "slow_ma": SLOW_MA,

        "m15_signal": core.crossover_signal(m15),
        "close": float(candle["close"]),
        "ma_fast": float(candle["ma_fast"]),
        "ma_slow": float(candle["ma_slow"]),
        "rsi": float(candle["rsi"]),
        "atr": float(candle["atr"]),
        "adx": float(candle["adx"]),

        "h1_trend": core.ma_trend(h1),
        "m5_trend": core.ma_trend(m5),
        "spread_points": core.spread_points(SYMBOL),

        # เวลาในแท่งเป็นเวลาเซิร์ฟเวอร์ broker ซึ่งขยับตาม DST ปีละสองครั้ง
        # ไม่บันทึกไว้ตอนเก็บ แถวเก่ากับแถวใหม่จะอยู่คนละกรอบเวลาโดยไม่มีใครรู้
        "gmt_offset": core.broker_gmt_offset(SYMBOL),
    }

    return context, candle


# ---------- บันทึกข้อมูล ----------

def log_signal(context):
    core.append_csv(SIGNAL_LOG, {
        "logged_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "candle_time": context["candle_time"],
        "symbol": SYMBOL,
        "close": round(context["close"], 2),
        f"ma_{FAST_MA}": round(context["ma_fast"], 2),
        f"ma_{SLOW_MA}": round(context["ma_slow"], 2),
        "signal": context["m15_signal"],
    })


def log_features(context, candle, decision):
    """บันทึกสถานะตลาดพร้อมคำตัดสินของบอท และเว้นช่องให้ติดป้ายกำกับเองภายหลัง"""
    core.append_csv(FEATURE_LOG, {
        "logged_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "candle_time": context["candle_time"],
        "symbol": SYMBOL,
        "broker_gmt_offset": context["gmt_offset"],

        "open": round(float(candle["open"]), 2),
        "high": round(float(candle["high"]), 2),
        "low": round(float(candle["low"]), 2),
        "close": round(context["close"], 2),

        "ma_20": round(context["ma_fast"], 2),
        "ma_50": round(context["ma_slow"], 2),
        "rsi_14": round(context["rsi"], 2),
        "atr_14": round(context["atr"], 2),
        "adx_14": round(context["adx"], 2),

        "h1_trend": context["h1_trend"],
        "m5_trend": context["m5_trend"],
        "spread_points": context["spread_points"],
        "candle_range": round(float(candle["high"] - candle["low"]), 2),
        "candle_body": round(abs(float(candle["close"] - candle["open"])), 2),

        "bot_signal": context["m15_signal"],
        "bot_decision": "ENTER" if decision.enter else "SKIP",
        "bot_blockers": "; ".join(check.name for check in decision.blockers),

        # กรอกเองใน Excel ภายหลัง
        "your_decision": "",
        "your_reason": "",
        "entry_price": "",
        "stop_loss": "",
        "take_profit": "",
        "trade_result": "",
    })


# ---------- ส่งคำสั่ง ----------

def build_levels(info, tick, action, atr):
    """SL/TP อิง ATR โดยเคารพระยะขั้นต่ำที่ broker กำหนด"""
    minimum = trade.min_stop_distance(info, tick)
    sl_distance = max(atr * SL_ATR_MULT, minimum)
    tp_distance = max(atr * TP_ATR_MULT, minimum)

    if action == "BUY":
        entry = tick.ask
        return entry, entry - sl_distance, entry + tp_distance, sl_distance

    entry = tick.bid
    return entry, entry + sl_distance, entry - tp_distance, sl_distance


def handle_existing_positions(signal, logger):
    """คืน True ถ้าเปิดไม้ใหม่ต่อได้ — ทางเดียวกันข้าม สวนทางปิดก่อน"""
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

        NOTIFIER.closed_on_reverse(SYMBOL, position.ticket, signal)

    return True


# สีของคำตัดสินบนจอ: เขียวเข้าซื้อ แดงเข้าขาย เหลืองคือมีสัญญาณแต่ตัวกรองบล็อก
# จางคือไม่มีอะไรเกิดขึ้น ซึ่งเป็นกรณีส่วนใหญ่ของคืนหนึ่ง
VERDICT_STYLE = {"BUY": core.GREEN, "SELL": core.RED}

# สถิติของรอบที่กำลังรัน ใช้เติม heartbeat ให้ตอบได้ว่า "คืนนี้เห็นอะไรมาบ้าง"
# อยู่ในหน่วยความจำเหมือนสถานะของ notify — restart แล้วเริ่มนับใหม่ ซึ่งตรงกับ
# คำว่า "รอบนี้" อยู่แล้ว ส่วนตัวเลขที่ต้องอยู่ข้ามรอบมี report.py อ่านจาก CSV ให้
SESSION_STATS_CANDLES = 48   # เก็บราคาปิดล่าสุดกี่แท่งไว้วาด sparkline
SESSION_STATS = {"candles": 0, "crossovers": 0, "blockers": {}, "closes": []}


def record_candle(decision, context):
    """นับแท่งที่ผ่านตาไปแล้วหนึ่งแท่ง พร้อมตัวกรองที่บล็อกมันไว้"""
    SESSION_STATS["candles"] += 1

    close = context.get("close")
    if close is not None:
        SESSION_STATS["closes"].append(close)
        del SESSION_STATS["closes"][:-SESSION_STATS_CANDLES]

    if decision.signal == "HOLD":
        return

    SESSION_STATS["crossovers"] += 1

    for check in decision.blockers:
        SESSION_STATS["blockers"][check.name] = SESSION_STATS["blockers"].get(check.name, 0) + 1


def verdict_line(decision):
    """คำตัดสินหนึ่งบรรทัดที่ย้อมสีตามผล — ข้อความเหมือนเดิมทุกตัวอักษร เพิ่มแค่สี"""
    summary = decision.summary()

    if decision.enter:
        return core.paint(summary, core.BOLD, VERDICT_STYLE.get(decision.signal, ""))

    if decision.signal == "HOLD":
        return core.paint(summary, core.DIM)

    return core.paint(summary, core.YELLOW)


def over_budget_is_allowed(account):
    """
    ไม้ขั้นต่ำเสี่ยงเกินงบแล้วยังเดินต่อได้ไหม

    บน Demo ปล่อยผ่านโดยเตือน เพราะจุดประสงค์ของ Demo คือได้เห็นบอททำงานจริง
    บัญชีจริงบล็อกไว้ก่อนเสมอ เว้นแต่สั่งอนุญาตเอง
    เงื่อนไขอยู่ที่เดียวเพราะ run.py check บอกผู้ใช้ล่วงหน้าว่าจะเกิดอะไรขึ้น
    ถ้าสองที่ตอบไม่ตรงกัน check จะโกหก
    """
    return core.is_demo(account) or ALLOW_RISK_OVER_BUDGET


def execute(decision, state, logger):
    """ส่งคำสั่งตามคำตัดสิน — ตรวจเรื่องเงินและ broker ครบก่อนยิง"""
    signal = decision.signal
    context = decision.context

    info = mt5.symbol_info(SYMBOL)
    tick = mt5.symbol_info_tick(SYMBOL)
    account = mt5.account_info()

    if info is None or tick is None or account is None:
        logger.warning("ดึงข้อมูล Symbol/ราคา/บัญชีไม่ได้ ข้ามสัญญาณนี้")
        return

    if not trade.symbol_is_tradable(info):
        logger.warning("%s ปิดการเทรดอยู่ (trade_mode %s)", SYMBOL, info.trade_mode)
        return

    if not handle_existing_positions(signal, logger):
        return

    entry, sl, tp, sl_distance = build_levels(info, tick, signal, context["atr"])

    if USE_FIXED_LOT:
        lots = trade.normalize_volume(info, FIXED_LOT)
    else:
        if trade.lot_exceeds_budget(info, account, sl_distance, RISK_PERCENT):
            minimum_loss = trade.estimated_loss(info, info.volume_min, sl_distance)
            budget = trade.risk_budget(account, RISK_PERCENT)
            actual_percent = minimum_loss / account.balance * 100

            if over_budget_is_allowed(account):
                logger.warning(
                    "ไม้ขั้นต่ำ %.2f lot เสี่ยง %.2f %s = %.2f%% ของพอร์ต เกินงบที่ตั้งไว้ %.2f "
                    "(%.2f%%) — เดินต่อเพราะ%s",
                    info.volume_min, minimum_loss, account.currency, actual_percent,
                    budget, RISK_PERCENT,
                    "เป็นบัญชี Demo" if core.is_demo(account) else "เปิด ALLOW_RISK_OVER_BUDGET ไว้",
                )
            else:
                needed = minimum_loss / (RISK_PERCENT / 100)
                logger.error(
                    "ไม่เข้าไม้: ไม้ขั้นต่ำ %.2f lot เสี่ยง %.2f %s (%.2f%% ของพอร์ต) "
                    "แต่งบต่อไม้มีแค่ %.2f — ต้องมีทุนราว %.0f ลด SL_ATR_MULT "
                    "หรือเปิด ALLOW_RISK_OVER_BUDGET",
                    info.volume_min, minimum_loss, account.currency, actual_percent,
                    budget, needed,
                )
                NOTIFIER.entry_over_budget(
                    SYMBOL, signal, minimum_loss, budget, account.currency, needed,
                )
                return

        lots = trade.calculate_lot(info, account, sl_distance, RISK_PERCENT)

    risk = trade.estimated_loss(info, lots, sl_distance)
    risk_text = f"{risk:.2f}" if risk is not None else "?"

    logger.info(
        "เตรียมเข้า %s %.2f lot ที่ %.2f | SL %.2f (ระยะ %.2f) | TP %.2f | เสี่ยงราว %s %s",
        signal, lots, entry, sl, sl_distance, tp, risk_text, account.currency,
    )

    result = trade.send_market_order(SYMBOL, signal, lots, sl, tp, MAGIC, DEVIATION, logger)
    success = result is not None and result.retcode == trade.RETCODE_DONE

    if success:
        logger.info("เข้าไม้สำเร็จ ticket %s ที่ราคา %.2f", result.order, result.price)
        # จำ 1R ไว้ให้ตัวดูแลไม้ใช้ เพราะ SL จริงจะถูกขยับภายหลัง
        state.setdefault("position_risk", {})[str(result.order)] = sl_distance

        # รายละเอียดไม้เก็บแยกอีกก้อน เพราะตอนไม้ปิดเอง position object หายไปแล้ว
        # แต่ยังต้องรายงานให้ได้ว่าเข้าทางไหน ที่ราคาเท่าไร และ 1R คิดเป็นเงินเท่าไร
        state.setdefault("position_meta", {})[str(result.order)] = {
            "signal": signal,
            "entry": round(result.price, 2),
            "volume": lots,
            "risk_money": round(risk, 2) if risk is not None else None,
            "currency": account.currency,
            "opened_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        NOTIFIER.entry_filled(
            SYMBOL, signal, lots, result.price, sl, tp,
            risk_text, account.currency, result.order,
        )
    else:
        logger.error("เข้าไม้ไม่สำเร็จ: %s", trade.describe_result(result))
        NOTIFIER.entry_failed(SYMBOL, signal, trade.describe_result(result))

    core.append_csv(TRADE_LOG, {
        "logged_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "candle_time": context["candle_time"],
        "symbol": SYMBOL,
        "signal": signal,
        "lots": lots,
        "intended_entry": round(entry, 2),
        "filled_price": round(result.price, 2) if success else "",
        "sl": round(sl, 2),
        "tp": round(tp, 2),
        "atr": round(context["atr"], 2),
        "adx": round(context["adx"], 2),
        "h1_trend": context["h1_trend"],
        "spread_points": context["spread_points"],
        "risk_amount": round(risk, 2) if risk is not None else "",
        "status": "OK" if success else trade.describe_result(result),
        "ticket": result.order if success else "",
    })


# ---------- ความทนทานของการเชื่อมต่อ ----------

def connection_is_alive():
    """terminal_info() คืน None เมื่อ terminal ปิดหรือหลุดการเชื่อมต่อ"""
    return mt5.terminal_info() is not None


def reconnect(logger):
    """พยายามเชื่อมต่อใหม่ — บอทที่รันทิ้งไว้ต้องรอดจากการปิด/เปิด terminal"""
    logger.warning("ขาดการเชื่อมต่อ MT5 กำลังเชื่อมใหม่")
    mt5.shutdown()

    try:
        core.connect()
        core.prepare_symbol(SYMBOL)
    except core.MT5Error as error:
        logger.error("เชื่อมต่อใหม่ไม่สำเร็จ: %s", error)
        return False

    logger.info("เชื่อมต่อใหม่สำเร็จ")
    return True


# ---------- ดูแลไม้ที่เปิดอยู่ ----------

def _remembered_risk(state, position):
    """
    ระยะ SL ตอนเปิดไม้ (1R) — เก็บไว้ใน state เพราะ SL จริงจะถูกขยับภายหลัง

    ถ้าหาไม่เจอ (เช่นเปิดไม้ก่อน restart) ให้ประมาณจาก SL ปัจจุบันแทน
    """
    risks = state.setdefault("position_risk", {})
    key = str(position.ticket)

    if key not in risks and position.sl:
        risks[key] = abs(position.price_open - position.sl)

    return risks.get(key)


def _best_candidate(position, entry, price, initial_risk, atr):
    """เลือก SL ใหม่ที่ดีที่สุดระหว่าง breakeven กับ trailing"""
    candidates = []

    if USE_BREAKEVEN:
        candidates.append(trade.breakeven_level(
            position.type, entry, price, initial_risk, BREAKEVEN_AT_R, BREAKEVEN_BUFFER_R,
        ))

    if USE_TRAILING:
        candidates.append(trade.trailing_level(
            position.type, entry, price, initial_risk, TRAIL_START_R, atr, TRAIL_ATR_MULT,
        ))

    best = None
    for candidate in candidates:
        improved = trade.better_stop(position.type, best, candidate)
        if improved is not None:
            best = improved

    return best


def take_partial_profit(position, price, initial_risk, info, state, logger):
    """
    ปิดครึ่งไม้เมื่อกำไรถึงเป้าแรก แล้วปล่อยที่เหลือวิ่งต่อ

    ทำครั้งเดียวต่อไม้ จึงต้องจำไว้ใน state ไม่งั้นจะทยอยปิดจนหมดไม้
    """
    if not USE_PARTIAL_TP:
        return

    taken = state.setdefault("partial_taken", {})
    key = str(position.ticket)

    if taken.get(key):
        return

    profit = (price - position.price_open) if position.type == mt5.POSITION_TYPE_BUY \
        else (position.price_open - price)

    if profit < initial_risk * PARTIAL_TP_AT_R:
        return

    volume = trade.partial_close_volume(info, position.volume, PARTIAL_TP_FRACTION)

    if volume is None:
        # ปกติของพอร์ตเล็กที่เปิดแค่ 0.01 lot — ปิดครึ่งไม่ได้ บอกครั้งเดียวพอ
        taken[key] = "เล็กเกินจะแบ่งปิด"
        logger.info(
            "ticket %s ถึง %.1fR แล้วแต่ %.2f lot เล็กเกินจะแบ่งปิด ปล่อยเต็มไม้ต่อ",
            position.ticket, PARTIAL_TP_AT_R, position.volume,
        )
        NOTIFIER.partial_too_small(SYMBOL, position.ticket, position.volume, PARTIAL_TP_AT_R)
        return

    result = trade.close_partial(position, volume, DEVIATION, logger)

    if result is not None and result.retcode == trade.RETCODE_DONE:
        taken[key] = True
        logger.info(
            "เก็บกำไรบางส่วน ticket %s: ปิด %.2f จาก %.2f lot ที่ %.1fR",
            position.ticket, volume, position.volume, PARTIAL_TP_AT_R,
        )
        NOTIFIER.partial_taken(
            SYMBOL, position.ticket, volume, position.volume, PARTIAL_TP_AT_R,
        )


def _stop_reason(position, new_sl):
    """บอกว่า SL ใหม่หมายถึงอะไร — คนอ่านอยากรู้ว่าเสมอทุนแล้วหรือแค่ไล่ตามราคา"""
    if position.type == mt5.POSITION_TYPE_BUY:
        beyond_entry = new_sl >= position.price_open
    else:
        beyond_entry = new_sl <= position.price_open

    return "เสมอทุนแล้ว" if beyond_entry else "ไล่ตามราคา"


def report_closed_positions(live_tickets, state, logger):
    """
    ไม้ที่เคยจำไว้แต่ไม่อยู่ในรายการที่เปิดอยู่ = ปิดไปแล้ว ต้องรู้ว่าจบยังไง

    เดิมบอทเงียบสนิทตอนโดน SL หรือ TP ซึ่งเป็นเหตุการณ์ที่ควรรู้ที่สุด
    ผลอ่านจากประวัติดีลจริง ไม่ใช่เดาจากราคา เพราะกำไรที่นับได้ต้องรวม commission
    กับ swap ด้วย ไม่งั้นไม้ที่ชนะเฉียดฉิวจะรายงานกลับทาง

    คืน set ของ ticket ที่ยังรอประวัติอยู่ ผู้เรียกต้องเก็บ meta ของพวกนี้ไว้ก่อน
    ประวัติดีลไม่ได้ลงทันทีที่ไม้ปิดเสมอไป ถ้าล้าง meta ทิ้งเลยก็ไม่มีวันได้รายงาน
    แต่รอตลอดกาลก็ไม่ได้ ไม้ที่หา 10 รอบแล้วยังไม่เจอถือว่าแพ้แล้วปล่อยไป
    """
    pending = set()

    for ticket, meta in list(state.get("position_meta", {}).items()):
        if ticket in live_tickets:
            continue

        # key ใน state เป็น string แต่ MT5 ต้องการ ticket เป็นตัวเลข
        deals = trade.closing_deals(int(ticket), logger)
        closed = trade.summarize_position_close(deals)

        if closed is None:
            attempts = meta.get("close_lookups", 0) + 1
            meta["close_lookups"] = attempts

            if attempts < CLOSE_LOOKUP_ATTEMPTS:
                logger.debug("ticket %s ปิดแล้วแต่ประวัติยังไม่ลง (ครั้งที่ %d)",
                             ticket, attempts)
                pending.add(ticket)
            else:
                logger.warning(
                    "ticket %s ปิดไปแล้วแต่หาดีลขาออกไม่เจอครบ %d รอบ เลิกรอ",
                    ticket, attempts,
                )

            continue

        logger.info(
            "ไม้ปิดแล้ว ticket %s: %.2f lot กำไรสุทธิ %.2f ที่ราคา %.2f",
            ticket, closed["volume"], closed["profit"], closed["price"],
        )
        NOTIFIER.position_closed(
            SYMBOL, ticket, meta, closed["profit"],
            meta.get("currency", ""), closed["price"],
        )

    return pending


def manage_positions(context, state, logger):
    """เรียกทุกรอบ ไม่ใช่แค่ตอนแท่งปิด — ราคาวิ่งระหว่างแท่งก็ต้องดูแล SL"""
    positions = trade.open_positions(SYMBOL, MAGIC)

    # ไม้ที่หายไปจากรายการแปลว่าปิดไปแล้ว — รายงานผลก่อน แล้วค่อยล้าง state ทิ้ง
    live_tickets = {str(position.ticket) for position in positions}
    pending = report_closed_positions(live_tickets, state, logger)

    for bucket in ("position_risk", "partial_taken", "position_meta"):
        records = state.setdefault(bucket, {})
        # meta ของไม้ที่ยังรอประวัติต้องอยู่ต่อ ไม่งั้นรอบหน้าไม่เหลืออะไรให้รายงาน
        keep = pending if bucket == "position_meta" else set()

        for ticket in list(records):
            if ticket not in live_tickets and ticket not in keep:
                del records[ticket]

    if not positions:
        return

    for position in positions:
        logger.debug(
            "ถืออยู่ ticket %s %s %.2f lot เข้าที่ %.2f SL %.2f TP %.2f กำไร %.2f",
            position.ticket,
            "BUY" if position.type == mt5.POSITION_TYPE_BUY else "SELL",
            position.volume, position.price_open, position.sl, position.tp, position.profit,
        )

    info = mt5.symbol_info(SYMBOL)
    tick = mt5.symbol_info_tick(SYMBOL)

    if info is None or tick is None:
        return

    minimum = trade.min_stop_distance(info, tick)

    for position in positions:
        initial_risk = _remembered_risk(state, position)

        if not initial_risk:
            continue

        # ราคาที่ใช้ปิดไม้คือฝั่งที่เสียเปรียบ จึงเป็นตัววัดกำไรที่ถูกต้อง
        price = tick.bid if position.type == mt5.POSITION_TYPE_BUY else tick.ask

        take_partial_profit(position, price, initial_risk, info, state, logger)

        candidate = _best_candidate(position, position.price_open, price, initial_risk, context["atr"])
        new_sl = trade.better_stop(position.type, position.sl, candidate)

        if new_sl is None:
            continue

        if not trade.stop_is_far_enough(position.type, price, new_sl, minimum):
            continue

        result = trade.modify_stops(position, new_sl, position.tp, logger)

        if result is not None and result.retcode == trade.RETCODE_DONE:
            logger.info(
                "ขยับ SL ticket %s: %.2f -> %.2f (เข้าที่ %.2f, ราคาตอนนี้ %.2f)",
                position.ticket, position.sl, new_sl, position.price_open, price,
            )
            NOTIFIER.stop_moved(
                SYMBOL, position.ticket, position.sl, new_sl,
                position.price_open, price, _stop_reason(position, new_sl),
                tp=getattr(position, "tp", None),
                risk=_remembered_risk(state, position), signal=_signal_of(position),
            )


# ---------- ตัวตัดวงจร ----------

def trading_allowed(state, logger):
    """
    คืน (อนุญาตหรือไม่, เหตุผล, สรุปของวัน)

    อ่านผลจากประวัติจริงใน MT5 ทุกครั้ง จึงไม่พังเมื่อบอทถูก restart กลางวัน
    """
    summary = trade.deals_today(SYMBOL, MAGIC)
    start_balance = state.get("day_start_balance")

    if summary["trades"] >= MAX_TRADES_PER_DAY:
        return False, f"ครบโควตา {MAX_TRADES_PER_DAY} ไม้ของวันนี้แล้ว", summary

    if summary["consecutive_losses"] >= MAX_CONSECUTIVE_LOSSES:
        return False, f"แพ้ติดกัน {summary['consecutive_losses']} ไม้ หยุดพักถึงพรุ่งนี้", summary

    if start_balance:
        limit = -abs(start_balance * MAX_DAILY_LOSS_PERCENT / 100)
        if summary["profit"] <= limit:
            return False, (
                f"ขาดทุนวันนี้ {summary['profit']:.2f} ถึงเพดาน "
                f"{MAX_DAILY_LOSS_PERCENT}% ({limit:.2f}) หยุดเทรดทั้งวัน"
            ), summary

    return True, "", summary


def heartbeat(state, account, logger, force=False):
    """
    แจ้งเป็นระยะว่ายังทำงานอยู่ — บอทที่ตายเงียบคือบอทที่แย่ที่สุด

    เก็บเวลาส่งล่าสุดไว้ใน state จึงไม่สแปมซ้ำหลัง restart
    """
    if not force and not HEARTBEAT_EVERY_HOURS:
        return

    now = datetime.now()
    last = state.get("last_heartbeat")

    if last and not force:
        try:
            if now - datetime.fromisoformat(last) < timedelta(hours=HEARTBEAT_EVERY_HOURS):
                return
        except ValueError:
            pass

    state["last_heartbeat"] = now.isoformat(timespec="seconds")

    positions = trade.open_positions(SYMBOL, MAGIC)
    summary = state.get("day_summary") or {}

    logger.info("ส่ง heartbeat: ถืออยู่ %d ไม้ equity %.2f", len(positions), account.equity)
    NOTIFIER.heartbeat(
        SYMBOL, account.equity, account.currency,
        [position_view(position, state) for position in positions], summary,
        state.get("last_candle_time", "-"), time.time() - STARTED_AT,
        stats=SESSION_STATS, paused=bool(state.get("entries_paused")),
    )


def handle_commands(state, account, logger):
    """
    ทำตามปุ่มที่กดมาจาก Telegram

    หยุด/กลับมาเก็บลงไฟล์สถานะ เพราะ "หยุดเข้าไม้" ที่หายไปหลัง restart คือคำสั่ง
    ที่ไม่ได้ทำตาม ส่วนคำสั่งทั้งหมดเป็นการตั้งสถานะ ไม่มีอันไหนส่งคำสั่งซื้อขาย
    """
    for command in NOTIFIER.take_commands():
        action = command["action"]
        logger.info("รับคำสั่งจาก Telegram: %s", notify.CONTROL_ACTIONS.get(action, action))

        if action == "status":
            NOTIFIER.acknowledge(command["callback_id"], "กำลังส่งสรุป")
            heartbeat(state, account, logger, force=True)
            continue

        paused = action == "pause"

        if bool(state.get("entries_paused")) == paused:
            NOTIFIER.acknowledge(command["callback_id"],
                                 "หยุดอยู่แล้ว" if paused else "ยังเข้าไม้ได้อยู่แล้ว")
            continue

        state["entries_paused"] = paused
        core.save_state(STATE_FILE, state)

        NOTIFIER.acknowledge(command["callback_id"],
                             "หยุดเข้าไม้ใหม่แล้ว" if paused else "กลับมาเข้าไม้แล้ว")
        NOTIFIER.entries_paused(SYMBOL, paused)


def _signal_of(position):
    """ทิศของไม้เป็นคำที่คนอ่านออก — notify ไม่ควรต้องรู้จักค่าคงที่ของ MT5"""
    return "BUY" if getattr(position, "type", None) == mt5.POSITION_TYPE_BUY else "SELL"


def position_view(position, state=None):
    """
    ย่อ position ของ MT5 เป็น dict ธรรมดาก่อนส่งให้ notify

    notify.py ต้องไม่รู้จักโครงสร้างของแพ็กเกจ MT5 ไม่งั้นเทสออฟไลน์ต้องปลอม
    object ของ broker ขึ้นมาทั้งตัวเพื่อทดสอบการจัดข้อความหนึ่งบรรทัด
    """
    return {
        "ticket": getattr(position, "ticket", "-"),
        "entry": getattr(position, "price_open", None),
        "sl": getattr(position, "sl", None),
        "tp": getattr(position, "tp", None),
        "price": getattr(position, "price_current", None),
        "signal": _signal_of(position),
        # 1R ตอนเข้าไม้ ไม่ใช่ระยะ SL ปัจจุบัน — SL ขยับหนีไปแล้วตั้งแต่ breakeven
        "risk": _remembered_risk(state, position) if state is not None else None,
    }


def roll_over_day(state, account, logger):
    """ตัดวันใหม่ — สรุปวันเก่าส่ง Telegram แล้วรีเซ็ตฐานทุนของวัน"""
    today = datetime.now().strftime("%Y-%m-%d")

    if state.get("day") == today:
        return

    previous = state.get("day")
    if previous:
        summary = state.get("day_summary") or {}
        logger.info(
            "สรุปวัน %s: %s ไม้ ชนะ %s แพ้ %s กำไรสุทธิ %.2f",
            previous, summary.get("trades", 0), summary.get("wins", 0),
            summary.get("losses", 0), summary.get("profit", 0.0),
        )
        NOTIFIER.daily_summary(
            SYMBOL, previous, summary, account.balance, account.currency,
        )

    state["day"] = today
    state["day_start_balance"] = account.balance
    logger.info("เริ่มวันใหม่ %s ทุนต้นวัน %.2f", today, account.balance)


# ---------- ลูปหลัก ----------

def guard_account(account, trade_enabled, logger):
    kind = "Demo" if core.is_demo(account) else "บัญชีจริง"
    logger.info(
        "บัญชี %s (%s) server %s balance %.2f %s",
        account.login, kind, account.server, account.balance, account.currency,
    )

    if trade_enabled and not core.is_demo(account) and not ALLOW_LIVE_ACCOUNT:
        raise core.MT5Error(
            "นี่คือบัญชีจริง แต่ ALLOW_LIVE_ACCOUNT ยังเป็น False — หยุดเพื่อความปลอดภัย"
        )


def run(trade_enabled=False):
    """ลูปเดียวที่ทำทุกอย่าง — ดูแลไม้ที่เปิดอยู่ บันทึกข้อมูล ตัดสินใจ และเทรดถ้าเปิดไว้"""
    # ไฟล์เก็บ DEBUG ทั้งหมด จอเห็นแค่ INFO เวลามีปัญหาจะได้ย้อนดูได้ทีละรอบ
    logger = core.setup_logging(LOG_FILE, level=logging.DEBUG, console_level=logging.INFO)

    account = core.connect()
    guard_account(account, trade_enabled, logger)
    info = core.prepare_symbol(SYMBOL)

    state = core.load_state(STATE_FILE)
    last_candle_time = state.get("last_candle_time")

    logger.info("โหมด: %s", "เทรดจริง" if trade_enabled else "เฝ้าดูอย่างเดียว ไม่ส่งคำสั่ง")
    logger.info("ตลาด: %s | เข้า M15 | เทรนด์ H1 | ยืนยัน M5", SYMBOL)
    logger.info("ตัวกรองที่เปิด: %s", ", ".join(strategy.active_filters()))
    logger.info(
        "ความเสี่ยง: %s | SL %.1fxATR | TP %.1fxATR",
        f"{FIXED_LOT} lot คงที่" if USE_FIXED_LOT else f"{RISK_PERCENT}% ต่อไม้",
        SL_ATR_MULT, TP_ATR_MULT,
    )
    logger.info(
        "ดูแลไม้: %s | ตัวตัดวงจร: ขาดทุน %.1f%%/วัน, %d ไม้/วัน, แพ้ติดกัน %d",
        _management_summary(), MAX_DAILY_LOSS_PERCENT, MAX_TRADES_PER_DAY, MAX_CONSECUTIVE_LOSSES,
    )

    logger.info(
        "เก็บกำไรบางส่วน: %s | heartbeat ทุก %s",
        f"{PARTIAL_TP_FRACTION:.0%} ที่ {PARTIAL_TP_AT_R}R" if USE_PARTIAL_TP else "ปิด",
        f"{HEARTBEAT_EVERY_HOURS} ชั่วโมง" if HEARTBEAT_EVERY_HOURS else "ปิด",
    )
    logger.info(
        "Symbol: %d หลัก, point %s, lot %.2f-%.2f ก้าว %.2f, stops level %s points",
        info.digits, info.point, info.volume_min, info.volume_max,
        info.volume_step, info.trade_stops_level,
    )

    offset = core.broker_gmt_offset(SYMBOL)
    if offset is not None:
        logger.info("เวลาเซิร์ฟเวอร์ broker = GMT%+d (ใช้ตั้ง SESSION_HOURS)", offset)

    logger.info(
        "แจ้งเตือน Telegram: %s",
        ", ".join(notify.active_categories()) if NOTIFIER.configured
        else "ปิดอยู่ (ไม่ได้ตั้ง TELEGRAM_TOKEN / TELEGRAM_CHAT_ID)",
    )

    risk_text = f"{FIXED_LOT} lot คงที่" if USE_FIXED_LOT else f"{RISK_PERCENT}% ต่อไม้"
    risk_text += f" · SL {SL_ATR_MULT:.1f}xATR · TP {TP_ATR_MULT:.1f}xATR"

    # แจ้งทุกครั้งที่เริ่ม ไม่ใช่เฉพาะโหมดเทรด — โหมดเฝ้าดูก็ต้องรู้ว่ามันเริ่มแล้วจริง
    NOTIFIER.bot_started(
        SYMBOL,
        f"{account.login} · {'Demo' if core.is_demo(account) else 'บัญชีจริง'} · "
        f"{account.balance:,.2f} {account.currency}",
        "เทรดจริง" if trade_enabled else "เฝ้าดูอย่างเดียว ไม่ส่งคำสั่ง",
        strategy.active_filters(),
        risk_text,
        _management_summary(),
        f"ตัวตัดวงจร: ขาดทุน {MAX_DAILY_LOSS_PERCENT:.1f}%/วัน · "
        f"{MAX_TRADES_PER_DAY} ไม้/วัน · แพ้ติดกัน {MAX_CONSECUTIVE_LOSSES} ไม้",
    )

    halted_reason = None
    market_was_closed = False

    while True:
        if not connection_is_alive():
            NOTIFIER.connection_lost(SYMBOL)

            if not reconnect(logger):
                time.sleep(RECONNECT_DELAY)
                continue

            NOTIFIER.reconnected(SYMBOL)

        account = mt5.account_info()
        if account is None:
            time.sleep(RECONNECT_DELAY)
            continue

        roll_over_day(state, account, logger)
        handle_commands(state, account, logger)
        heartbeat(state, account, logger)

        info = mt5.symbol_info(SYMBOL)
        if info is not None and not trade.symbol_is_tradable(info):
            logger.info("ตลาด %s ปิดอยู่ รอ %d วินาที", SYMBOL, MARKET_CLOSED_SLEEP)
            NOTIFIER.market_closed(SYMBOL, MARKET_CLOSED_SLEEP)
            market_was_closed = True
            core.save_state(STATE_FILE, state)
            time.sleep(MARKET_CLOSED_SLEEP)
            continue

        if market_was_closed:
            market_was_closed = False
            NOTIFIER.market_reopened(SYMBOL)

        context, candle = build_context()

        if context is None:
            logger.warning("ดึงแท่งราคาไม่สำเร็จ: %s", core.last_error_text())
            time.sleep(CHECK_EVERY_SECONDS)
            continue

        # ดูแลไม้ที่เปิดอยู่ทุกรอบ ราคาวิ่งระหว่างแท่งก็ต้องขยับ SL ตาม
        if trade_enabled:
            manage_positions(context, state, logger)

        candle_time = str(context["candle_time"])

        logger.debug(
            "รอบตรวจ: แท่งล่าสุด %s close %.2f spread %s %s",
            candle_time, context["close"], context["spread_points"],
            "(ประมวลผลแล้ว)" if candle_time == last_candle_time else "(แท่งใหม่)",
        )

        if candle_time == last_candle_time:
            core.save_state(STATE_FILE, state)
            time.sleep(CHECK_EVERY_SECONDS)
            continue

        last_candle_time = candle_time
        state["last_candle_time"] = candle_time

        decision = strategy.evaluate(context)

        log_signal(context)
        log_features(context, candle, decision)

        logger.info(
            "แท่งปิด %s | Close %.2f | H1 %s | M5 %s | RSI %.1f | ADX %.1f | ATR %.2f | Spread %s",
            candle_time, context["close"], context["h1_trend"], context["m5_trend"],
            context["rsi"], context["adx"], context["atr"], context["spread_points"],
        )
        logger.info("คำตัดสิน: %s", verdict_line(decision))

        # เหตุผลเต็มลงไฟล์เสมอ จะได้ย้อนดูได้ว่าตัวกรองไหนบล็อกและด้วยตัวเลขอะไร
        for check in decision.checks:
            logger.debug("  [%s] %s: %s", "ผ่าน" if check.passed else "ไม่ผ่าน",
                         check.name, check.detail)

        # แจ้งทุกแท่ง แล้วให้ notify.py เป็นคนตัดสินว่าแท่งนี้ควรส่งไหม
        # ผ่านครบต้องรู้เสมอ ติดตัวกรองก็น่าดู ส่วน HOLD ปิดไว้เป็นค่าเริ่มต้น
        record_candle(decision, context)

        NOTIFIER.candle_verdict(
            decision, context, SYMBOL,
            adx_min=strategy.ADX_MIN, watch_mode=not trade_enabled,
            max_spread=strategy.MAX_SPREAD_POINTS,
        )

        if decision.enter:
            if not trade_enabled:
                logger.info("โหมดเฝ้าดู — ถ้าเปิด trade ไว้จะเข้า %s ตรงนี้", decision.signal)
            elif state.get("entries_paused"):
                # สั่งหยุดไว้จาก Telegram — ไม้ที่ถืออยู่ยังถูกดูแลตามปกติข้างบน
                logger.info("สั่งหยุดเข้าไม้ไว้ — ข้ามสัญญาณ %s", decision.signal)
            else:
                allowed, reason, summary = trading_allowed(state, logger)
                state["day_summary"] = summary

                if allowed:
                    if halted_reason:
                        NOTIFIER.resumed(SYMBOL, state.get("day", "ใหม่"))

                    halted_reason = None
                    execute(decision, state, logger)
                elif reason != halted_reason:
                    # แจ้งครั้งเดียวต่อเหตุผล ไม่ใช่ทุกแท่ง
                    halted_reason = reason
                    logger.warning("ไม่เข้าไม้: %s", reason)
                    NOTIFIER.halted(SYMBOL, reason, summary)

        core.save_state(STATE_FILE, state)
        time.sleep(CHECK_EVERY_SECONDS)


def _management_summary():
    parts = []

    if USE_BREAKEVEN:
        parts.append(f"เสมอทุนที่ {BREAKEVEN_AT_R}R")
    if USE_TRAILING:
        parts.append(f"ไล่ stop จาก {TRAIL_START_R}R ห่าง {TRAIL_ATR_MULT}xATR")

    return ", ".join(parts) if parts else "ไม่ขยับ SL หลังเปิดไม้"
