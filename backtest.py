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

import mt5_core as core
import strategy

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
    import mt5_trade as trade
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

def simulate(m15=None, h1=None, m5=None, config=None, prepared=None):
    """
    เดินทีละแท่งตั้งแต่ต้นจนจบ ใช้ข้อมูลถึงแท่งปัจจุบันเท่านั้น

    เข้าไม้ที่ราคาเปิดของแท่งถัดไป ซึ่งตรงกับพฤติกรรมจริง: บอทเห็นแท่งปิดแล้วยิงคำสั่ง
    ส่ง prepared เข้ามาได้ถ้าคำนวณ indicator ไว้แล้ว ใช้ตอนกวาดค่าเพื่อไม่คำนวณซ้ำ
    """
    config = {**DEFAULTS, **(config or {})}
    bars = prepared if prepared is not None else prepare(m15.copy(), h1, m5, config)

    spread = config["spread_points"] * config["point"]
    warmup = config["slow_ma"] + max(config["rsi_period"], config["adx_period"]) + 5

    series = {
        name: bars[name].to_numpy()
        for name in ("open", "high", "low", "close", "atr", "adx", "rsi")
    }
    signals = bars["signal"].to_numpy()
    h1_trend = bars["h1_trend"].to_numpy()
    m5_trend = bars["m5_trend"].to_numpy()
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
                "adx": series["adx"][index],
                "rsi": series["rsi"][index],
                "spread_points": config["spread_points"],
                "server_hour": int(hours[index]),
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


# ---------- กวาดหาค่าพารามิเตอร์ ----------

DEFAULT_GRID = {
    "sl_atr_mult": (1.0, 1.5, 2.0),
    "tp_atr_mult": (2.0, 3.0, 4.0),
    "adx_min": (15.0, 20.0, 25.0),
}


def sweep(m15, h1=None, m5=None, base=None, grid=None, progress=None):
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


def format_sweep(rows, top=15):
    """ตารางผลการกวาดค่า เรียงจากดีที่สุด"""
    if not rows:
        return "ไม่มีผลลัพธ์"

    lines = [
        "--- ผลการกวาดค่าพารามิเตอร์ ---",
        f"{'SL':>5} {'TP':>5} {'ADX':>5} {'ไม้':>5} {'ชนะ%':>7} "
        f"{'ต่อไม้':>9} {'รวม':>9} {'DD':>8} {'PF':>6}",
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
