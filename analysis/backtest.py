"""
จำลองกลยุทธ์ย้อนหลังบนข้อมูลจริงจาก MT5

ตอบคำถามที่การเฝ้าดูสดต้องใช้เวลาเป็นเดือนกว่าจะรู้: กลยุทธ์นี้ทำเงินได้ไหม
และตัวกรองที่ใส่ไว้ช่วยจริงหรือแค่ทำให้เทรดน้อยลง

simulate() เป็นฟังก์ชันบริสุทธิ์ รับ DataFrame เข้าไปเท่านั้น จึงเทสได้โดยไม่ต้องต่อ MT5

วัดผลเป็น R (หน่วยความเสี่ยงต่อไม้) ไม่ใช่เงิน เพราะ R ไม่ขึ้นกับขนาดพอร์ตหรือ lot
ที่เลือก ทำให้เทียบข้ามช่วงเวลาและข้ามการตั้งค่าได้ตรงๆ  +1R คือกำไรเท่ากับที่เสี่ยงไป

ข้อจำกัดที่ต้องรู้ก่อนเชื่อผลลัพธ์:
  - ไม่รู้ลำดับราคาภายในแท่ง ถ้าแท่งเดียวชนทั้ง SL และ TP จะนับว่า **SL ชนก่อนเสมอ**
    เป็นสมมติฐานฝั่งแย่ ผลจริงจึงมักดีกว่าที่จำลองเล็กน้อย
  - การไล่ stop จำลองที่ราคาปิดของแต่ละแท่ง M15 แต่บอทจริงขยับทุก 30 วินาที
  - spread ใช้ค่าคงที่ ของจริงกว้างขึ้นตอนข่าวและตอนตลาดเปิด/ปิด
  - ไม่มี slippage และไม่มี requote
  - ข้อมูลย้อนหลังของ broker ไม่ได้สะท้อนสภาพคล่องจริงในอดีตทั้งหมด
"""

import numpy as np
import pandas as pd

from bot import core
from bot import strategy
from bot.screen import pad

DEFAULTS = {
    "fast_ma": 20,
    "slow_ma": 50,
    "rsi_period": 14,
    "atr_period": 14,
    "adx_period": 14,

    "sl_atr_mult": 1.5,
    "tp_atr_mult": 3.0,

    "use_breakeven": True,
    "breakeven_at_r": 1.0,
    "breakeven_buffer_r": 0.1,
    "use_trailing": True,
    "trail_start_r": 1.5,
    "trail_atr_mult": 2.0,

    "spread_points": 30.0,
    "point": 0.01,

    # เวลาบนแท่งจาก MT5 เป็นเวลาเซิร์ฟเวอร์ broker ส่วน strategy.SESSION_UTC_HOURS
    # เขียนเป็น UTC ตัวเลขนี้คือตัวเชื่อมสองอันนั้น ผู้เรียกที่ต่อ MT5 อยู่ส่ง
    # core.broker_gmt_offset() เข้ามา ส่วน simulate() ยังเป็นฟังก์ชันบริสุทธิ์เหมือนเดิม
    # ค่าเริ่มต้น 3 คือค่าที่พบจริงในข้อมูลที่เก็บมา และมีผลก็ต่อเมื่อเปิด USE_SESSION_FILTER
    "gmt_offset": 3,
    "fvg_lookback": core.FVG_LOOKBACK,

    "use_filters": True,
}


# ---------- เตรียมข้อมูล ----------

def _add_trend_column(df, fast, slow):
    """ทิศของ MA ทุกแท่ง ใช้แทน core.ma_trend() ตอนต้องอ่านหลายแท่ง"""
    core.add_moving_averages(df, fast, slow)

    trend = pd.Series("SIDEWAY", index=df.index, dtype=object)
    trend[df["ma_fast"] > df["ma_slow"]] = "UPTREND"
    trend[df["ma_fast"] < df["ma_slow"]] = "DOWNTREND"
    trend[df["ma_fast"].isna() | df["ma_slow"].isna()] = "UNKNOWN"

    df["trend"] = trend
    return df


def _align(source, target_times, lag):
    """
    หาแท่งที่ปิดแล้วล่าสุดของ timeframe ใหญ่กว่า ณ เวลาที่แท่ง M15 ปิด

    lag คือระยะเวลาย้อนหลังที่ทำให้ได้แท่งที่ "ปิดแล้วจริง" ไม่ใช่แท่งที่กำลังก่อตัว
    ป้องกัน lookahead bias ซึ่งเป็นสาเหตุอันดับหนึ่งที่ backtest ออกมาสวยเกินจริง
    """
    positions = source["time"].searchsorted(target_times + lag, side="right") - 1
    values = source["trend"].to_numpy()

    return [values[position] if position >= 0 else "UNKNOWN" for position in positions]


def signal_series(bars):
    """
    สัญญาณ crossover ของทุกแท่งในครั้งเดียว

    ให้ผลเท่ากับการเรียก core.crossover_signal() ทีละแท่ง แต่เร็วกว่ามาก เพราะการ
    slice DataFrame ในลูปทำให้เวลาเป็น O(n^2)  ความเท่ากันถูกยืนยันด้วยเทส
    test_vectorised_signal_matches_the_live_rule ห้ามแก้ฟังก์ชันนี้โดยไม่รันเทสนั้น
    """
    fast, slow = bars["ma_fast"], bars["ma_slow"]
    previous_fast, previous_slow = fast.shift(1), slow.shift(1)

    signals = np.where(
        (previous_fast <= previous_slow) & (fast > slow), "BUY",
        np.where((previous_fast >= previous_slow) & (fast < slow), "SELL", "HOLD"),
    )
    return pd.Series(signals, index=bars.index, dtype=object)


def prepare(m15, h1, m5, config):
    """คำนวณ indicator ทั้งหมดและจับคู่ timeframe ให้ตรงเวลา"""
    fast, slow = config["fast_ma"], config["slow_ma"]

    core.add_moving_averages(m15, fast, slow)
    m15["rsi"] = core.calculate_rsi(m15["close"], config["rsi_period"])
    m15["atr"] = core.calculate_atr(m15, config["atr_period"])
    m15["adx"] = core.calculate_adx(m15, config["adx_period"])

    # แท่ง M15 ที่เวลา t ปิดตอน t+15m ตอนนั้นแท่ง H1 ที่ปิดแล้วล่าสุดคือ t-45m
    # และแท่ง M5 ที่ปิดแล้วล่าสุดคือ t+10m
    if h1 is not None and len(h1):
        _add_trend_column(h1, fast, slow)
        m15["h1_trend"] = _align(h1, m15["time"], -pd.Timedelta(minutes=45))
    else:
        m15["h1_trend"] = "UNKNOWN"

    if m5 is not None and len(m5):
        _add_trend_column(m5, fast, slow)
        m15["m5_trend"] = _align(m5, m15["time"], pd.Timedelta(minutes=10))
    else:
        m15["m5_trend"] = "UNKNOWN"

    # FVG คำนวณจาก OHLC ล้วน จึงจำลองย้อนหลังได้จริง ต่างจากข่าวกับ DXY
    m15["fvg"] = core.fvg_series(m15, config["fvg_lookback"])

    m15["signal"] = signal_series(m15)
    return m15


# ---------- จำลองไม้เดียว ----------

def _simulate_position(series, start, direction, entry, initial_risk, target, config):
    """
    เดินไปข้างหน้าทีละแท่งจนกว่าไม้จะปิด

    รับ series เป็น dict ของ numpy array ไม่ใช่ DataFrame เพราะการอ่านทีละแถวด้วย
    .iloc ช้ากว่าหลายสิบเท่า ทำให้การกวาดค่าใช้เวลาเป็นนาทีแทนที่จะเป็นวินาที

    คืน (ผลลัพธ์เป็น R, index ที่ปิด, เหตุผลที่ปิด)
    """
    high, low, close, atr = series["high"], series["low"], series["close"], series["atr"]
    is_buy = direction == "BUY"
    stop = entry - initial_risk if is_buy else entry + initial_risk

    for index in range(start, len(close)):
        if is_buy:
            # สมมติฝั่งแย่: ชนทั้งคู่ในแท่งเดียวให้ SL ชนก่อน
            if low[index] <= stop:
                return (stop - entry) / initial_risk, index, "SL"
            if high[index] >= target:
                return (target - entry) / initial_risk, index, "TP"
        else:
            if high[index] >= stop:
                return (entry - stop) / initial_risk, index, "SL"
            if low[index] <= target:
                return (entry - target) / initial_risk, index, "TP"

        stop = _updated_stop(is_buy, entry, close[index], atr[index], stop, initial_risk, config)

    result = (close[-1] - entry) if is_buy else (entry - close[-1])
    return result / initial_risk, len(close) - 1, "ยังไม่ปิด"


def _updated_stop(is_buy, entry, price, atr, stop, initial_risk, config):
    """ขยับ stop ตามราคาปิดแท่งนี้ ใช้กติกาเดียวกับบอทจริง"""
    from bot import trade
    import MetaTrader5 as mt5

    position_type = mt5.POSITION_TYPE_BUY if is_buy else mt5.POSITION_TYPE_SELL
    best = stop

    if config["use_breakeven"]:
        candidate = trade.breakeven_level(
            position_type, entry, price, initial_risk,
            config["breakeven_at_r"], config["breakeven_buffer_r"],
        )
        improved = trade.better_stop(position_type, best, candidate)
        if improved is not None:
            best = improved

    if config["use_trailing"]:
        candidate = trade.trailing_level(
            position_type, entry, price, initial_risk,
            config["trail_start_r"], atr, config["trail_atr_mult"],
        )
        improved = trade.better_stop(position_type, best, candidate)
        if improved is not None:
            best = improved

    return best


# ---------- ลูปจำลอง ----------

def warmup_bars(config):
    """แท่งแรกๆ ที่ indicator ยังไม่นิ่ง ห้ามนับเป็นช่วงที่เทรดได้

    walk_forward() ใช้ค่าเดียวกันนี้เติมหน้าช่วงทดสอบ ถ้าสองที่คิดคนละแบบ
    ไม้แรกของแต่ละช่วงจะเลื่อน แล้วผลนอกช่วงฝึกจะเทียบกับช่วงอื่นไม่ได้
    """
    return config["slow_ma"] + max(config["rsi_period"], config["adx_period"]) + 5


def simulate(m15=None, h1=None, m5=None, config=None, prepared=None):
    """
    เดินทีละแท่งตั้งแต่ต้นจนจบ ใช้ข้อมูลถึงแท่งปัจจุบันเท่านั้น

    เข้าไม้ที่ราคาเปิดของแท่งถัดไป ซึ่งตรงกับพฤติกรรมจริง: บอทเห็นแท่งปิดแล้วยิงคำสั่ง
    ส่ง prepared เข้ามาได้ถ้าคำนวณ indicator ไว้แล้ว ใช้ตอนกวาดค่าเพื่อไม่คำนวณซ้ำ

    FVG จำลองได้เต็มที่ (คำนวณจาก OHLC ล้วน) sweep กับ walk_forward จึงตัดสินมันได้
    สองตัวกรองที่จำลองไม่ได้และจงใจปล่อยผ่านตรงนี้:
        ข่าว — ฟีดที่ใช้มีแค่ข้อมูลสัปดาห์ปัจจุบัน ย้อนหลังไปประกอบหน้าต่างข่าวไม่ได้
        DXY  — ไม่ได้ดึงราคาดัชนีดอลลาร์ย้อนหลังมาด้วย
    ผลที่ออกมาจึงเป็นของกลยุทธ์ "ก่อนหักสองตัวนี้" ไม่ใช่ของบอทตัวเต็ม
    """
    config = {**DEFAULTS, **(config or {})}
    bars = prepared if prepared is not None else prepare(m15.copy(), h1, m5, config)

    spread = config["spread_points"] * config["point"]
    warmup = warmup_bars(config)

    series = {
        name: bars[name].to_numpy()
        for name in ("open", "high", "low", "close", "atr", "adx", "rsi")
    }
    signals = bars["signal"].to_numpy()
    h1_trend = bars["h1_trend"].to_numpy()
    m5_trend = bars["m5_trend"].to_numpy()
    fvg = bars["fvg"].to_numpy()
    times = bars["time"].to_numpy()
    hours = bars["time"].dt.hour.to_numpy()

    trades = []
    blocked = {}
    busy_until = -1

    for index in range(warmup, len(bars) - 1):
        if index <= busy_until:
            continue

        signal = signals[index]

        if signal == "HOLD":
            continue

        if config["use_filters"]:
            decision = strategy.evaluate({
                "m15_signal": signal,
                "h1_trend": h1_trend[index],
                "m5_trend": m5_trend[index],
                "fvg_state": fvg[index],
                "adx": series["adx"][index],
                "rsi": series["rsi"][index],
                "spread_points": config["spread_points"],
                "server_hour": int(hours[index]),
                "gmt_offset": config["gmt_offset"],
                "fast_ma": config["fast_ma"],
                "slow_ma": config["slow_ma"],
            })

            if not decision.enter:
                for check in decision.blockers:
                    blocked[check.name] = blocked.get(check.name, 0) + 1
                continue

        atr = series["atr"][index]
        if not atr or atr != atr or atr <= 0:
            continue

        # เข้าที่ราคาเปิดแท่งถัดไป บวก spread เป็นต้นทุนการเข้า
        next_open = series["open"][index + 1]
        entry = next_open + spread if signal == "BUY" else next_open - spread

        initial_risk = atr * config["sl_atr_mult"]
        target = (entry + atr * config["tp_atr_mult"]) if signal == "BUY" \
            else (entry - atr * config["tp_atr_mult"])

        result, exit_index, reason = _simulate_position(
            series, index + 1, signal, entry, initial_risk, target, config,
        )

        trades.append({
            "entry_time": pd.Timestamp(times[index + 1]),
            "exit_time": pd.Timestamp(times[exit_index]),
            "direction": signal,
            "entry": round(float(entry), 2),
            "r": round(float(result), 3),
            "reason": reason,
            "bars_held": exit_index - index,
            "h1_trend": h1_trend[index],
            "adx": round(float(series["adx"][index]), 1),
        })

        busy_until = exit_index

    return {
        "trades": trades,
        "blocked": blocked,
        "bars": len(bars),
        "from": bars.iloc[warmup]["time"] if len(bars) > warmup else None,
        "to": bars.iloc[-1]["time"] if len(bars) else None,
        "config": config,
    }


# ---------- สรุปผล ----------

def metrics(result):
    """ตัวเลขสรุปจากรายการเทรด"""
    trades = result["trades"]

    if not trades:
        return {"trades": 0}

    values = [trade["r"] for trade in trades]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]

    equity = []
    total = 0.0
    peak = 0.0
    drawdown = 0.0

    for value in values:
        total += value
        equity.append(total)
        peak = max(peak, total)
        drawdown = min(drawdown, total - peak)

    streak = 0
    worst_streak = 0
    for value in values:
        streak = streak + 1 if value <= 0 else 0
        worst_streak = max(worst_streak, streak)

    gross_loss = abs(sum(losses))

    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades) * 100,
        "total_r": total,
        "expectancy_r": total / len(trades),
        "profit_factor": (sum(wins) / gross_loss) if gross_loss else float("inf"),
        "max_drawdown_r": drawdown,
        "worst_losing_streak": worst_streak,
        "avg_bars_held": sum(t["bars_held"] for t in trades) / len(trades),
        "equity": equity,
    }


def format_report(result, title="ผลจำลอง"):
    """รายงานอ่านง่ายสำหรับพิมพ์ลง console"""
    stats = metrics(result)
    lines = [f"--- {title} ---"]

    if result["from"] is not None:
        lines.append(f"ช่วงข้อมูล: {result['from']} ถึง {result['to']} ({result['bars']} แท่ง)")

    if not stats["trades"]:
        lines.append("ไม่มีไม้ที่เข้าเงื่อนไขเลยในช่วงนี้")
        if result["blocked"]:
            lines.append("ตัวกรองที่บล็อกไว้: " + ", ".join(
                f"{name} {count} ครั้ง" for name, count in sorted(
                    result["blocked"].items(), key=lambda item: -item[1])
            ))
        return "\n".join(lines)

    lines += [
        f"จำนวนไม้: {stats['trades']}  (ชนะ {stats['wins']} แพ้ {stats['losses']})",
        f"Win rate: {stats['win_rate']:.1f}%",
        f"กำไรรวม: {stats['total_r']:+.2f}R",
        f"คาดหวังต่อไม้: {stats['expectancy_r']:+.3f}R",
        f"Profit factor: {stats['profit_factor']:.2f}",
        f"Drawdown สูงสุด: {stats['max_drawdown_r']:.2f}R",
        f"แพ้ติดกันมากสุด: {stats['worst_losing_streak']} ไม้",
        f"ถือเฉลี่ย: {stats['avg_bars_held']:.0f} แท่ง M15",
    ]

    exits = {}
    for trade in result["trades"]:
        exits[trade["reason"]] = exits.get(trade["reason"], 0) + 1
    lines.append("ปิดด้วย: " + ", ".join(f"{name} {count}" for name, count in exits.items()))

    if result["blocked"]:
        lines.append("")
        lines.append("สัญญาณที่ถูกตัวกรองบล็อก:")
        for name, count in sorted(result["blocked"].items(), key=lambda item: -item[1]):
            lines.append(f"  {name}: {count} ครั้ง")

    return "\n".join(lines)


def compare(m15, h1, m5, config=None):
    """
    เทียบ "ใส่ตัวกรอง" กับ "crossover เปล่าๆ"

    ตอบคำถามสำคัญที่สุดตอนจูน: ตัวกรองช่วยจริง หรือแค่ทำให้เทรดน้อยลงเฉยๆ
    """
    base = {**DEFAULTS, **(config or {})}

    prepared = prepare(m15.copy(), h1, m5, base)

    with_filters = simulate(config={**base, "use_filters": True}, prepared=prepared)
    without = simulate(config={**base, "use_filters": False}, prepared=prepared)

    return with_filters, without


# ตัวกรองที่สลับเปิด/ปิดแล้ววัดได้จริงในการจำลอง เรียงตามลำดับที่มันทำงานใน strategy.py
# ข่าวกับ DXY ไม่อยู่ในนี้เพราะจำลองย้อนหลังไม่ได้ (ดู docstring ของ simulate())
# ช่วงเวลามี session_scan() ของตัวเองเพราะคำถามคือ "หน้าต่างไหน" ไม่ใช่ "เปิดหรือปิด"
SCANNABLE_FILTERS = (
    ("USE_H1_TREND_FILTER", "เทรนด์ H1"),
    ("USE_ADX_FILTER", "ADX"),
    ("USE_RSI_FILTER", "RSI"),
    ("USE_M5_CONFIRM", "M5 ยืนยัน"),
    ("USE_FVG_FILTER", "FVG"),
)


def filter_scan(m15=None, h1=None, m5=None, base=None, filters=None, prepared=None):
    """
    สลับตัวกรองทีละตัวแล้ววัดว่ามันเปลี่ยนผลไปเท่าไหร่

    นี่คือเหตุผลที่สวิตช์ทุกตัวแยกจากกัน: ตัวที่เปิดอยู่จะถูกปิดเพื่อดูว่ามัน *ให้* อะไร
    ตัวที่ปิดอยู่จะถูกเปิดเพื่อดูว่าการเพิ่มมันเข้ามา *จะ* ให้อะไร คำถามสองข้อนี้คือ
    คำถามเดียวกันมองจากคนละด้าน และทั้งคู่ตอบได้ก็ต่อเมื่อเทียบกับค่าตั้งต้นชุดเดียวกัน

    คืนแถวแรกเป็นค่าตั้งต้น (สวิตช์ตามที่ตั้งไว้ตอนนี้) แถวที่เหลือคือผลของการสลับทีละตัว
    """
    filters = SCANNABLE_FILTERS if filters is None else filters
    base = {**DEFAULTS, **(base or {}), "use_filters": True}

    if prepared is None:
        prepared = prepare(m15.copy(), h1, m5, base)

    # คืนค่าที่เคยเป็นทุกตัว ไม่ใช่ค่าที่เขียนตายไว้ — บทเรียนเดียวกับ SwitchedTo
    original = {name: getattr(strategy, name) for name, _ in filters}
    rows = []

    try:
        baseline = metrics(simulate(config=base, prepared=prepared))
        rows.append({"label": "ค่าตั้งต้นตอนนี้", "action": "", **_scan_stats(baseline)})

        for name, label in filters:
            was = original[name]
            setattr(strategy, name, not was)

            try:
                stats = metrics(simulate(config=base, prepared=prepared))
            finally:
                setattr(strategy, name, was)

            rows.append({
                "label": label,
                "action": "ปิด" if was else "เพิ่ม",
                **_scan_stats(stats),
                "delta_r": stats.get("expectancy_r", 0.0) - baseline.get("expectancy_r", 0.0),
            })
    finally:
        for name, value in original.items():
            setattr(strategy, name, value)

    return rows


def _scan_stats(stats):
    return {
        "trades": stats.get("trades", 0),
        "expectancy_r": stats.get("expectancy_r", 0.0),
        "total_r": stats.get("total_r", 0.0),
        "win_rate": stats.get("win_rate", 0.0),
    }


def format_filter_scan(rows, min_trades=None):
    """ตารางผลของการสลับตัวกรองทีละตัว พร้อมบอกว่าแถวไหนยังสรุปไม่ได้"""
    if not rows:
        return "ไม่มีผลลัพธ์"

    min_trades = MIN_TEST_TRADES if min_trades is None else min_trades

    lines = [
        "--- ตัวกรองแต่ละตัวให้อะไรบ้าง (สลับทีละตัว) ---",
        f"{pad('ตัวกรอง', 16)} {pad('ทำอะไร', 7)} {pad('ไม้', 5, '>')} "
        f"{pad('ชนะ%', 7, '>')} {pad('ต่อไม้', 9, '>')} {pad('ต่างจากเดิม', 12, '>')}",
    ]

    baseline = rows[0]
    lines.append(
        f"{pad(baseline['label'], 16)} {pad('', 7)} {baseline['trades']:>5} "
        f"{baseline['win_rate']:>6.1f}% {baseline['expectancy_r']:>+9.3f} {pad('—', 12, '>')}"
    )

    for row in rows[1:]:
        thin = "  (ไม้น้อยเกินจะสรุป)" if row["trades"] < min_trades else ""
        lines.append(
            f"{pad(row['label'], 16)} {pad(row['action'], 7)} {row['trades']:>5} "
            f"{row['win_rate']:>6.1f}% {row['expectancy_r']:>+9.3f} "
            f"{row['delta_r']:>+12.3f}{thin}"
        )

    lines.append("")
    lines.append("'ต่างจากเดิม' คือผลของการสลับตัวนั้น บวกแปลว่าการสลับดีกว่าค่าที่ตั้งไว้ตอนนี้")

    solid = [row for row in rows[1:] if row["trades"] >= min_trades]
    helpful = [row for row in solid if row["delta_r"] > 0]

    if not solid:
        lines.append("ทุกแถวไม้น้อยเกินจะเทียบ — ขยายช่วงข้อมูลด้วย --months")
    elif not helpful:
        lines.append("ไม่มีการสลับไหนดีกว่าค่าที่ตั้งไว้ตอนนี้ — ปล่อยสวิตช์ไว้อย่างเดิม")
    else:
        for row in sorted(helpful, key=lambda item: item["delta_r"], reverse=True):
            lines.append(
                f"  {row['action']} {row['label']} ดีขึ้น {row['delta_r']:+.3f}R ต่อไม้"
            )
        lines.append("ข้อมูลชุดเดียวยังไม่ใช่ข้อสรุป — ยืนยันด้วย walkforward ก่อนสลับจริง")

    return "\n".join(lines)


# ---------- กวาดหาค่าพารามิเตอร์ ----------

DEFAULT_GRID = {
    "sl_atr_mult": (1.0, 1.5, 2.0),
    "tp_atr_mult": (2.0, 3.0, 4.0),
    "adx_min": (15.0, 20.0, 25.0),
}


def sweep(m15=None, h1=None, m5=None, base=None, grid=None, progress=None, prepared=None):
    """
    รันจำลองหลายชุดค่าแล้วเรียงตามคาดหวังต่อไม้

    จุดประสงค์ไม่ใช่หา "ค่าที่ดีที่สุด" ไปใช้ตรงๆ แต่ดูว่าผลลัพธ์ **ทน** ต่อการเปลี่ยนค่าไหม
    ถ้ามีแค่ชุดเดียวที่กำไรและชุดข้างเคียงติดลบหมด แปลว่าฟลุค ไม่ใช่ขอบจริง
    ค่าที่ควรเลือกคือค่าที่อยู่กลางย่านที่กำไรทั้งย่าน
    """
    from itertools import product

    grid = grid or DEFAULT_GRID
    base = {**DEFAULTS, **(base or {})}

    keys = list(grid)
    combinations = list(product(*(grid[key] for key in keys)))

    # คำนวณ indicator ครั้งเดียวแล้วใช้ซ้ำทุกชุดค่า — ค่าที่กวาดไม่กระทบ indicator
    # walk_forward() ส่ง prepared ที่หั่นเป็นช่วงแล้วเข้ามา จะได้ไม่คำนวณซ้ำทุก fold
    if prepared is None:
        prepared = prepare(m15.copy(), h1, m5, base)

    original_adx = strategy.ADX_MIN
    rows = []

    try:
        for number, values in enumerate(combinations, start=1):
            settings = dict(zip(keys, values))

            if progress:
                progress(number, len(combinations), settings)

            # ADX_MIN อยู่ใน strategy ไม่ใช่ config ของ backtest
            if "adx_min" in settings:
                strategy.ADX_MIN = settings["adx_min"]

            config = {**base, **{k: v for k, v in settings.items() if k != "adx_min"}}
            stats = metrics(simulate(config=config, prepared=prepared))

            rows.append({**settings, **{
                "trades": stats.get("trades", 0),
                "expectancy_r": stats.get("expectancy_r", 0.0),
                "total_r": stats.get("total_r", 0.0),
                "win_rate": stats.get("win_rate", 0.0),
                "max_drawdown_r": stats.get("max_drawdown_r", 0.0),
                "profit_factor": stats.get("profit_factor", 0.0),
            }})
    finally:
        strategy.ADX_MIN = original_adx

    return sorted(rows, key=lambda row: row["expectancy_r"], reverse=True)


# ช่วงเวลาที่เอามาเทียบกัน เขียนเป็น UTC เหมือน strategy.SESSION_UTC_HOURS
# ไม่ยัดรวมใน DEFAULT_GRID เพราะมันจะคูณจำนวนชุดค่าทั้งกริด ทั้งที่คำถามคือ
# "จำกัดชั่วโมงแล้วดีขึ้นไหม" ซึ่งวัดทีละตัวได้ตรงกว่า ตามที่ทำกับตัวกรองอื่น
SESSION_WINDOWS = (
    ("ไม่จำกัดชั่วโมง", None),
    ("London/NY ซ้อนกัน", range(13, 17)),
    ("New York ทั้งช่วง", range(12, 21)),
    ("ค่าที่ตั้งไว้ตอนนี้", range(13, 21)),
    ("London + New York", range(7, 21)),
    ("London อย่างเดียว", range(8, 17)),
)


def session_scan(m15=None, h1=None, m5=None, base=None, windows=None, prepared=None):
    """
    เทียบผลของแต่ละหน้าต่างเวลากับผลตอนไม่จำกัดชั่วโมงเลย

    คำถามที่ตอบคือ "การตัดชั่วโมงทิ้งช่วยหรือแค่ทำให้ไม้น้อยลง" ไม่ใช่ "ชั่วโมงไหนดีที่สุด"
    หน้าต่างที่ชนะเพราะเหลือไม้สิบไม้ไม่ได้ชนะ มันแค่สุ่มน้อยลง MIN_TEST_TRADES
    จึงติดป้ายให้เอง แทนที่จะปล่อยให้ตัวเลขบนสุดของตารางดูน่าเชื่อ

    ต้องรู้ gmt_offset ของ broker ถึงจะเทียบได้ ใส่มาทาง base["gmt_offset"]
    """
    windows = SESSION_WINDOWS if windows is None else windows
    base = {**DEFAULTS, **(base or {})}

    if prepared is None:
        prepared = prepare(m15.copy(), h1, m5, base)

    # คืนค่าที่ *เคยเป็น* ไม่ใช่ค่า default ที่เขียนตายไว้ — สวิตช์ที่ถูกปิดไว้ชั่วคราว
    # แล้วโดนเขียนทับด้วย True ตอนจบ คือบั๊กที่หาไม่เจอจนกว่าเทสตัวถัดไปจะพัง
    original_switch = strategy.USE_SESSION_FILTER
    original_hours = strategy.SESSION_UTC_HOURS
    rows = []

    try:
        for label, window in windows:
            strategy.USE_SESSION_FILTER = window is not None

            if window is not None:
                strategy.SESSION_UTC_HOURS = window

            stats = metrics(simulate(config=base, prepared=prepared))
            rows.append({
                "label": label,
                "hours": "ทั้งวัน" if window is None
                         else f"{window.start:02d}-{window.stop - 1:02d} UTC",
                "trades": stats.get("trades", 0),
                "expectancy_r": stats.get("expectancy_r", 0.0),
                "total_r": stats.get("total_r", 0.0),
                "win_rate": stats.get("win_rate", 0.0),
            })
    finally:
        strategy.USE_SESSION_FILTER = original_switch
        strategy.SESSION_UTC_HOURS = original_hours

    return rows


def format_session_scan(rows, offset=None):
    """ตารางเทียบหน้าต่างเวลา พร้อมบอกว่าอันไหนยังสรุปไม่ได้"""
    if not rows:
        return "ไม่มีผลลัพธ์"

    lines = ["--- จำกัดชั่วโมงเทรดแล้วดีขึ้นไหม ---"]

    if offset is not None:
        lines.append(f"broker อยู่ GMT{offset:+d} — ชั่วโมง UTC ข้างล่างถูกแปลงให้ตอนใช้งานจริง")

    lines.append(
        f"{pad('หน้าต่าง', 20)} {pad('ชั่วโมง', 10)} {pad('ไม้', 5, '>')} "
        f"{pad('ชนะ%', 7, '>')} {pad('ต่อไม้', 9, '>')} {pad('รวม', 9, '>')}"
    )

    baseline = next((row for row in rows if row["hours"] == "ทั้งวัน"), None)

    for row in rows:
        thin = "  (ไม้น้อยเกินจะสรุป)" if row["trades"] < MIN_TEST_TRADES else ""
        lines.append(
            f"{pad(row['label'], 20)} {pad(row['hours'], 10)} {row['trades']:>5} "
            f"{row['win_rate']:>6.1f}% {row['expectancy_r']:>+9.3f} "
            f"{row['total_r']:>+9.2f}{thin}"
        )

    lines.append("")

    if baseline is None:
        return "\n".join(lines)

    solid = [row for row in rows
             if row["hours"] != "ทั้งวัน" and row["trades"] >= MIN_TEST_TRADES]
    better = [row for row in solid if row["expectancy_r"] > baseline["expectancy_r"]]

    if not solid:
        lines.append("ทุกหน้าต่างเหลือไม้น้อยเกินจะเทียบ — เก็บข้อมูลเพิ่มก่อน")
    elif not better:
        lines.append("ไม่มีหน้าต่างไหนดีกว่าการไม่จำกัดชั่วโมงเลย ปล่อย USE_SESSION_FILTER "
                     "ไว้ที่ False ต่อไป")
    else:
        best = max(better, key=lambda row: row["expectancy_r"])
        lines.append(
            f"ดีที่สุดคือ {best['label']} ({best['hours']}) "
            f"{best['expectancy_r']:+.3f}R ต่อไม้ เทียบกับ {baseline['expectancy_r']:+.3f}R "
            f"ตอนไม่จำกัด"
        )
        lines.append("ตัวเลขนี้มาจากข้อมูลชุดเดียว ยังไม่ใช่ข้อสรุป — "
                     "ยืนยันด้วย walk_forward ก่อนเปิดใช้จริง")

    return "\n".join(lines)


def format_sweep(rows, top=15):
    """ตารางผลการกวาดค่า เรียงจากดีที่สุด"""
    if not rows:
        return "ไม่มีผลลัพธ์"

    # หัวตารางผ่าน pad() ไม่ใช่ f-string ปกติ — คำไทยมีสระซ้อนที่ len() นับแต่จอไม่กินที่
    lines = [
        "--- ผลการกวาดค่าพารามิเตอร์ ---",
        f"{pad('SL', 5, '>')} {pad('TP', 5, '>')} {pad('ADX', 5, '>')} "
        f"{pad('ไม้', 5, '>')} {pad('ชนะ%', 7, '>')} {pad('ต่อไม้', 9, '>')} "
        f"{pad('รวม', 9, '>')} {pad('DD', 8, '>')} {pad('PF', 6, '>')}",
    ]

    for row in rows[:top]:
        lines.append(
            f"{row.get('sl_atr_mult', 0):>5.1f} {row.get('tp_atr_mult', 0):>5.1f} "
            f"{row.get('adx_min', 0):>5.0f} {row['trades']:>5} {row['win_rate']:>6.1f}% "
            f"{row['expectancy_r']:>+9.3f} {row['total_r']:>+9.2f} "
            f"{row['max_drawdown_r']:>8.2f} {row['profit_factor']:>6.2f}"
        )

    profitable = [row for row in rows if row["expectancy_r"] > 0]
    lines.append("")
    lines.append(f"ชุดค่าที่คาดหวังเป็นบวก: {len(profitable)} จาก {len(rows)}")

    if not profitable:
        lines.append("ไม่มีชุดไหนเป็นบวกเลย — กลยุทธ์นี้ยังไม่มีขอบในช่วงข้อมูลนี้")
    elif len(profitable) < len(rows) * 0.3:
        lines.append("บวกแค่ไม่กี่ชุด ระวังฟลุค อย่าเลือกค่าที่ดีที่สุดไปใช้ตรงๆ")
    else:
        lines.append("บวกเป็นย่านกว้าง สัญญาณว่ามีขอบจริง เลือกค่ากลางย่านจะทนกว่าค่าที่ดีที่สุด")

    return "\n".join(lines)


# ---------- เดินหน้าทีละช่วง ----------
#
# sweep() ตอบได้แค่ว่ากริดทั้งชุดทนไหม แต่มันวัดผลบนข้อมูลชุดเดียวกับที่ใช้เลือกค่า
# ตัวเลขที่ได้จึงเป็นของ "ค่าที่เข้ากับอดีตชุดนี้ที่สุด" เสมอ ไม่ได้แปลว่าพรุ่งนี้จะดีด้วย
#
# ตรงนี้แบ่งเวลาเป็นช่วง เลือกค่าจากช่วงก่อนหน้าเท่านั้น แล้ววัดบนช่วงถัดไปที่ยังไม่เคยเห็น
# และวัดค่า default บนช่วงเดียวกันซ้ำอีกรอบ เพราะคำถามจริงไม่ใช่ "จูนแล้วกำไรไหม"
# แต่คือ "จูนแล้วดีกว่าไม่จูนไหม"

WALK_FORWARD_FOLDS = 4
MIN_TRAIN_TRADES = 8      # ชุดค่าที่ได้ไม้น้อยกว่านี้ในช่วงฝึก ไม่มีสิทธิ์ถูกเลือก
MIN_TEST_TRADES = 30      # ไม้นอกช่วงฝึกรวมน้อยกว่านี้ ถือว่ายังสรุปอะไรไม่ได้


def fold_bounds(total, folds, warmup):
    """ขอบของแต่ละ fold เป็น (ท้ายช่วงฝึก, ท้ายช่วงทดสอบ)

    แบ่งเป็น folds+1 ช่วงเท่าๆ กัน ช่วงแรกไว้ฝึกอย่างเดียว ที่เหลือเป็นช่วงทดสอบทีละช่วง
    หน้าต่างฝึกขยายไปเรื่อยๆ (ช่วง 1 ถึง i) เหมือนคนจริงที่จูนใหม่จากทุกอย่างที่มีถึงวันนี้
    แล้วเอาค่าที่ได้ไปใช้กับช่วงถัดไป

    คืนลิสต์ว่างถ้าข้อมูลสั้นเกินจะแบ่ง — ดีกว่าคืนตัวเลขจากช่วงที่มีแค่แท่งอุ่นเครื่อง
    """
    block = total // (folds + 1)

    if folds < 1 or block <= warmup + 20:
        return []

    return [(number * block, total if number == folds else (number + 1) * block)
            for number in range(1, folds + 1)]


def _run_fold(config, adx_min, prepared):
    """จำลองหนึ่งช่วงด้วยค่าชุดหนึ่ง — ผู้เรียกต้องคืนค่า strategy.ADX_MIN เองใน finally"""
    strategy.ADX_MIN = adx_min
    return simulate(config=config, prepared=prepared)


def walk_forward(m15=None, h1=None, m5=None, base=None, grid=None,
                 folds=WALK_FORWARD_FOLDS, min_train_trades=MIN_TRAIN_TRADES,
                 prepared=None, progress=None):
    """จูนค่าจากอดีต แล้ววัดผลบนช่วงที่ยังไม่เคยเห็น

    indicator คำนวณครั้งเดียวจากทั้งไฟล์ได้โดยไม่ลักหน้า เพราะทุกตัวเป็น rolling
    ที่ใช้เฉพาะแท่งถึงแท่งนั้น (MA, Wilder RSI/ATR/ADX) ค่าที่แท่ง i ไม่เคยเห็นแท่ง i+1
    ส่วนการ "เลือกค่า" ซึ่งเป็นที่ที่อนาคตรั่วได้จริง ถูกกันไว้ในช่วงฝึกเท่านั้น
    """
    grid = grid or DEFAULT_GRID
    base = {**DEFAULTS, **(base or {})}
    bars = prepared if prepared is not None else prepare(m15.copy(), h1, m5, base)

    warmup = warmup_bars(base)
    bounds = fold_bounds(len(bars), folds, warmup)

    result = {
        "bars": len(bars),
        "folds": [],
        "grid_keys": list(grid),
        "tuned": {"trades": 0},
        "baseline": {"trades": 0},
        "reason": None,
    }

    if not bounds:
        result["reason"] = (f"ข้อมูล {len(bars)} แท่ง แบ่งเป็น {folds + 1} ช่วงไม่ได้ "
                            f"ต้องการช่วงละมากกว่า {warmup + 20} แท่ง "
                            f"(รวมอย่างน้อย {(warmup + 21) * (folds + 1)} แท่ง)")
        return result

    original_adx = strategy.ADX_MIN
    tuned_trades = []
    baseline_trades = []

    try:
        for number, (train_end, test_end) in enumerate(bounds, start=1):
            if progress:
                progress(number, len(bounds))

            train = bars.iloc[:train_end]
            # เติมแท่งอุ่นเครื่องไว้หน้าช่วงทดสอบ ไม้แรกจะได้เริ่มที่ต้นช่วงพอดี
            # ไม่ใช่หลังจากนั้นอีก warmup แท่ง แต่ก็ไม่เข้าไม้ในช่วงฝึกด้วย
            test = bars.iloc[train_end - warmup:test_end]

            fold = {
                "number": number,
                "train_bars": train_end,
                "test_bars": test_end - train_end,
                "test_from": bars["time"].iloc[train_end],
                "test_to": bars["time"].iloc[test_end - 1],
                "chosen": None,
                "train": {"trades": 0},
                "tuned": {"trades": 0},
                "baseline": {"trades": 0},
                "first_entry": None,
                "skipped": None,
            }

            plain = _run_fold(base, original_adx, test)
            fold["baseline"] = metrics(plain)
            baseline_trades += plain["trades"]

            rows = sweep(base=base, grid=grid, prepared=train)
            eligible = [row for row in rows if row["trades"] >= min_train_trades]

            if not eligible:
                fold["skipped"] = f"ไม่มีชุดค่าไหนได้ถึง {min_train_trades} ไม้ในช่วงฝึก"
                result["folds"].append(fold)
                continue

            best = eligible[0]
            chosen = {key: best[key] for key in grid}

            fold["chosen"] = chosen
            fold["train"] = {key: best[key]
                             for key in ("trades", "expectancy_r", "total_r", "win_rate")}

            tuned = _run_fold(
                {**base, **{key: value for key, value in chosen.items() if key != "adx_min"}},
                chosen.get("adx_min", original_adx),
                test,
            )
            fold["tuned"] = metrics(tuned)
            # ไม้แรกของช่วง — พิสูจน์ว่าแท่งอุ่นเครื่องที่เติมไว้หน้าช่วงไม่ได้ถูกเทรด
            fold["first_entry"] = tuned["trades"][0]["entry_time"] if tuned["trades"] else None
            tuned_trades += tuned["trades"]

            result["folds"].append(fold)
    finally:
        strategy.ADX_MIN = original_adx

    result["tuned"] = metrics({"trades": tuned_trades})
    result["baseline"] = metrics({"trades": baseline_trades})
    return result


def _chosen_text(chosen, keys):
    if not chosen:
        return "-"

    short = {"sl_atr_mult": "SL", "tp_atr_mult": "TP", "adx_min": "ADX"}
    return " ".join(f"{short.get(key, key)}{chosen[key]:g}" for key in keys)


def format_walk_forward(result, min_trades=MIN_TEST_TRADES):
    """ตารางผลนอกช่วงฝึก พร้อมคำตัดสินว่าการจูนช่วยจริงไหม"""
    lines = ["--- เดินหน้าทีละช่วง (walk-forward) ---"]

    if result["reason"]:
        lines.append(result["reason"])
        return "\n".join(lines)

    folds = result["folds"]
    keys = result["grid_keys"]

    lines.append(f"แบ่ง {result['bars']} แท่งเป็น {len(folds) + 1} ช่วง — เลือกค่าจากช่วงก่อนหน้า")
    lines.append("แล้ววัดผลบนช่วงถัดไปที่ยังไม่เคยเห็น เทียบกับค่า default บนช่วงเดียวกัน")
    lines.append("")
    lines.append(f"{pad('ช่วง', 4, '>')} {pad('ทดสอบถึง', 12, '>')} "
                 f"{pad('ค่าที่เลือก', 18, '>')} {pad('ไม้', 5, '>')} "
                 f"{pad('จูน', 9, '>')} {pad('default', 9, '>')}")

    for fold in folds:
        tuned = fold["tuned"]
        plain = fold["baseline"]

        if fold["skipped"]:
            lines.append(f"{fold['number']:>4} {fold['test_to']:%Y-%m-%d} "
                         f"ข้ามช่วงนี้: {fold['skipped']}")
            continue

        lines.append(
            f"{fold['number']:>4} {fold['test_to']:%Y-%m-%d} "
            f"{pad(_chosen_text(fold['chosen'], keys), 18, '>')} "
            f"{tuned.get('trades', 0):>5} "
            f"{tuned.get('expectancy_r', 0):>+9.3f} {plain.get('expectancy_r', 0):>+9.3f}"
        )

    tuned = result["tuned"]
    plain = result["baseline"]

    lines.append("")
    lines.append("รวมทุกช่วงที่ไม่เคยเห็น (นับเฉพาะไม้นอกช่วงฝึก):")
    lines.append(f"  ค่าที่จูนเอง : {tuned.get('trades', 0):>4} ไม้  "
                 f"{tuned.get('expectancy_r', 0):+.3f}R/ไม้  รวม {tuned.get('total_r', 0):+.2f}R")
    lines.append(f"  ค่า default  : {plain.get('trades', 0):>4} ไม้  "
                 f"{plain.get('expectancy_r', 0):+.3f}R/ไม้  รวม {plain.get('total_r', 0):+.2f}R")
    lines.append("")
    lines += _walk_forward_verdict(result, min_trades)

    return "\n".join(lines)


def _walk_forward_verdict(result, min_trades):
    """บอกตรงๆ ว่าเชื่อผลนี้ได้แค่ไหน — ไม่มีไม้พอก็ต้องบอกว่าไม่มีไม้พอ"""
    tuned = result["tuned"]
    plain = result["baseline"]
    picked = [tuple(sorted(fold["chosen"].items())) for fold in result["folds"] if fold["chosen"]]

    if not picked:
        return ["ทุกช่วงเลือกค่าไม่ได้เลย — ข้อมูลฝึกยังมีไม้ไม่พอให้เลือกอะไร"]

    lines = []

    if not tuned.get("trades"):
        lines.append("ค่าที่จูนแล้วไม่เข้าไม้เลยในช่วงที่ไม่เคยเห็น — ยังไม่มีอะไรให้สรุป")
        return lines

    delta = tuned.get("expectancy_r", 0.0) - plain.get("expectancy_r", 0.0)

    if tuned["trades"] < min_trades:
        lines.append(f"ไม้นอกช่วงฝึกรวมแค่ {tuned['trades']} ไม้ (เกณฑ์ {min_trades}) — "
                     "ผลต่างขนาดนี้ยังเป็น noise อ่านไว้เฉยๆ อย่าเพิ่งเอาไปเปลี่ยนค่า")
    elif delta <= 0:
        lines.append(f"จูนแล้วไม่ดีขึ้นนอกช่วงฝึก ({delta:+.3f}R/ไม้) — ใช้ค่า default ต่อไป")
        lines.append("ค่าที่ชนะบน sweep คือค่าที่เข้ากับอดีตชุดนั้น ไม่ใช่ค่าที่ทำเงินต่อไปได้")
    else:
        lines.append(f"จูนแล้วดีขึ้น {delta:+.3f}R/ไม้ นอกช่วงฝึก")

    unique = len(set(picked))
    if unique == 1:
        lines.append(f"ทุกช่วงเลือกค่าชุดเดียวกัน ({_chosen_text(dict(picked[0]), result['grid_keys'])}) "
                     "— สัญญาณว่าค่านี้นิ่งจริง ไม่ใช่ฟลุคของช่วงใดช่วงหนึ่ง")
    elif unique >= max(2, len(picked) * 0.75):
        lines.append(f"ค่าที่ชนะเปลี่ยนเกือบทุกช่วง ({unique} ชุดจาก {len(picked)} ช่วง) — "
                     "แปลว่ากำลังจับ noise ไม่ใช่ขอบจริง")

    return lines
