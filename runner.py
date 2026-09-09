"""
ลูปหลักของโปรเจกต์ — ดึงข้อมูลรอบเดียวต่อหนึ่งแท่ง แล้วป้อนให้ทุกงานพร้อมกัน

เดิมมีสามสคริปต์แยกกันที่ต่าง connect MT5 และ poll ราคาของตัวเอง ทั้งที่ใช้ข้อมูล
ชุดเดียวกัน ตอนนี้รวมเป็นลูปเดียว: ดึงครั้งเดียว -> บันทึก signal log -> บันทึก
feature log -> ให้ strategy ตัดสินใจ -> ส่งคำสั่งถ้าเปิดโหมดเทรดไว้

โหมดเทรดปิดเป็นค่าเริ่มต้น ต้องสั่ง --trade เองเท่านั้นถึงจะส่งคำสั่งจริง
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

def send_telegram(message, logger):
    """ส่งเข้า Telegram — ล้มเหลวได้โดยไม่ทำให้บอทหยุด แต่ต้องเห็นใน log"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return

    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=10,
        )
    except requests.RequestException as error:
        logger.warning("ส่ง Telegram ไม่สำเร็จ: %s", error)


def append_csv(path, row):
    pd.DataFrame([row]).to_csv(path, mode="a", header=not os.path.exists(path), index=False)


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
    }

    return context, candle


# ---------- บันทึกข้อมูล ----------

def log_signal(context):
    append_csv(SIGNAL_LOG, {
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
    append_csv(FEATURE_LOG, {
        "logged_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "candle_time": context["candle_time"],
        "symbol": SYMBOL,

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

        send_telegram(f"ปิดไม้เดิม ticket {position.ticket} เพราะสัญญาณกลับเป็น {signal}", logger)

    return True


def execute(decision, logger):
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
        over_budget = trade.lot_exceeds_budget(info, account, sl_distance, RISK_PERCENT)

        if over_budget and not ALLOW_RISK_OVER_BUDGET:
            minimum_loss = trade.estimated_loss(info, info.volume_min, sl_distance)
            logger.error(
                "ไม่เข้าไม้: ไม้ขั้นต่ำ %.2f lot เสี่ยง %.2f %s แต่งบต่อไม้มีแค่ %.2f "
                "(%.2f%% ของ %.2f) — ต้องเพิ่มทุน ลด SL_ATR_MULT หรือเปิด ALLOW_RISK_OVER_BUDGET",
                info.volume_min, minimum_loss, account.currency,
                trade.risk_budget(account, RISK_PERCENT), RISK_PERCENT, account.balance,
            )
            send_telegram(
                f"ข้าม {signal}: ไม้ขั้นต่ำเสี่ยง {minimum_loss:.2f} เกินงบ "
                f"{trade.risk_budget(account, RISK_PERCENT):.2f}",
                logger,
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
        send_telegram(
            f"เข้า {signal} {lots} lot\nราคา: {result.price:.2f}\n"
            f"SL: {sl:.2f}  TP: {tp:.2f}\nเสี่ยงราว {risk_text} {account.currency}",
            logger,
        )
    else:
        logger.error("เข้าไม้ไม่สำเร็จ: %s", trade.describe_result(result))
        send_telegram(f"เข้า {signal} ไม่สำเร็จ\n{trade.describe_result(result)}", logger)

    append_csv(TRADE_LOG, {
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
    """ลูปเดียวที่ทำทุกอย่าง — บันทึกข้อมูล ตัดสินใจ และเทรดถ้าเปิดไว้"""
    logger = core.setup_logging(LOG_FILE)

    account = core.connect()
    guard_account(account, trade_enabled, logger)
    core.prepare_symbol(SYMBOL)

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

    if trade_enabled:
        send_telegram(f"บอท {SYMBOL} เริ่มเทรด ({'Demo' if core.is_demo(account) else 'บัญชีจริง'})", logger)

    while True:
        context, candle = build_context()

        if context is None:
            logger.warning("ดึงแท่งราคาไม่สำเร็จ: %s", core.last_error_text())
            time.sleep(CHECK_EVERY_SECONDS)
            continue

        candle_time = str(context["candle_time"])

        if candle_time == last_candle_time:
            time.sleep(CHECK_EVERY_SECONDS)
            continue

        last_candle_time = candle_time
        core.save_state(STATE_FILE, {"last_candle_time": candle_time})

        decision = strategy.evaluate(context)

        log_signal(context)
        log_features(context, candle, decision)

        logger.info(
            "แท่งปิด %s | Close %.2f | H1 %s | M5 %s | RSI %.1f | ADX %.1f | ATR %.2f | Spread %s",
            candle_time, context["close"], context["h1_trend"], context["m5_trend"],
            context["rsi"], context["adx"], context["atr"], context["spread_points"],
        )
        logger.info("คำตัดสิน: %s", decision.summary())

        if decision.enter:
            if trade_enabled:
                execute(decision, logger)
            else:
                logger.info("โหมดเฝ้าดู — ถ้าเปิด --trade ไว้จะเข้า %s ตรงนี้", decision.signal)
                send_telegram(f"[เฝ้าดู] สัญญาณ {decision.signal} ผ่านตัวกรองครบที่ {candle_time}", logger)

        time.sleep(CHECK_EVERY_SECONDS)
