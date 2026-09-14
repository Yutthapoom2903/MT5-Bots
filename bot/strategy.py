"""
เครื่องตัดสินใจของบอท — รวมข้อมูลหลาย timeframe แล้วสรุปเองว่าจะเข้าไม้หรือไม่

หน้าที่ของแต่ละ timeframe:
    H1  = กำหนดทิศ    เทรดตามเทรนด์ใหญ่เท่านั้น ไม่สวนเทรนด์
    M15 = จุดชนวน     MA20 ตัด MA50 คือจังหวะเข้า
    M5  = ยืนยัน      โมเมนตัมระยะสั้นต้องไม่สวนกับไม้ที่จะเปิด
    ADX / RSI / spread = ตัวยับยั้ง ตลาดไม่มีเทรนด์หรือราคาไล่ไปไกลแล้วให้ข้าม
    ข่าว / ช่วงเวลา / DXY = ตัวยับยั้งจากนอกกราฟ กลไกตลาดเสียรูปหรือสภาพคล่องบาง

evaluate() เป็นฟังก์ชันบริสุทธิ์ รับ dict ที่รวบรวมมาแล้วเท่านั้น ไม่ยุ่งกับ MT5
เพื่อให้เทสได้โดยไม่ต้องต่อ terminal และเพื่อให้เหตุผลการตัดสินใจตรวจสอบย้อนหลังได้

ทุกเงื่อนไขบันทึกผลไว้ทั้งที่ผ่านและไม่ผ่าน บอทจึงอธิบายได้เสมอว่า "ทำไมถึงไม่เข้า"
ไม่ใช่แค่บอกว่าไม่เข้า
"""

from collections import namedtuple

# ---------- สวิตช์ตัวกรอง เปิด/ปิดได้ทีละตัวเพื่อวัดผล ----------
USE_H1_TREND_FILTER = True    # เทรดตามเทรนด์ H1 เท่านั้น
USE_ADX_FILTER = True         # ข้ามตลาด sideway
USE_RSI_FILTER = True         # ไม่ไล่ราคาที่ยืดเกินไปแล้ว
USE_M5_CONFIRM = True         # M5 ต้องไม่สวนทาง
USE_SESSION_FILTER = False    # จำกัดชั่วโมงเทรด (ดู SESSION_UTC_HOURS)
USE_NEWS_FILTER = True        # หยุดเข้าไม้รอบเวลาข่าวแรง
USE_DXY_FILTER = False        # ดอลลาร์ต้องไม่สวนกับไม้ทองที่จะเปิด — ยังไม่พิสูจน์
USE_FVG_FILTER = False        # Fair Value Gap ต้องไม่สวนทาง — ยังไม่พิสูจน์

ADX_MIN = 20.0                # ต่ำกว่านี้ถือว่าไม่มีเทรนด์
RSI_MAX_FOR_BUY = 70.0        # RSI สูงกว่านี้แล้วไม่ตาม BUY
RSI_MIN_FOR_SELL = 30.0       # RSI ต่ำกว่านี้แล้วไม่ตาม SELL
MAX_SPREAD_POINTS = 50.0

# ชั่วโมงที่อนุญาต — เขียนเป็น **UTC** แล้วค่อยแปลงเป็นเวลาเซิร์ฟเวอร์ตอนใช้
#
# เดิมตรงนี้เป็น range(9, 22) เวลาเซิร์ฟเวอร์ตรงๆ ซึ่งผูกกับ broker ที่ตั้งค่าไว้ตอนนั้น
# ย้าย broker หรือเจอ DST เข้าไป หน้าต่างก็เลื่อนไปทั้งชุดโดยไม่มีใครรู้ และค่านั้น
# กินช่วงเอเชียที่ทองแทบไม่ขยับ (UTC 6-13) แต่ตัดปลายช่วง New York ทิ้ง
#
# UTC 13:00-16:59 คือช่วง London/NY ซ้อนกัน ซึ่งเป็นที่ที่ทองเดินจริง และตัวเลข
# เศรษฐกิจสหรัฐออกเวลา 13:30 UTC  ต่อท้ายถึง 20:59 ให้ครอบช่วง New York ที่เหลือ
# ตัวเลขนี้เป็นแค่จุดตั้งต้นจากลักษณะตลาด ไม่ใช่ข้อสรุป — ให้ sweep ตัดสิน
SESSION_UTC_HOURS = range(13, 21)

Check = namedtuple("Check", "name passed detail")


class Decision:
    """ผลการตัดสินใจหนึ่งครั้ง พร้อมเหตุผลทุกข้อที่ใช้ตัดสิน"""

    def __init__(self, signal, enter, checks, context):
        self.signal = signal          # BUY / SELL / HOLD — ทิศที่สัญญาณชี้
        self.enter = enter            # เข้าไม้จริงหรือไม่ หลังผ่านตัวกรองทั้งหมด
        self.checks = checks
        self.context = context

    @property
    def blockers(self):
        """ตัวกรองที่ไม่ผ่าน — ว่างเปล่าแปลว่าผ่านหมด"""
        return [check for check in self.checks if not check.passed]

    def summary(self):
        """บรรทัดเดียวสำหรับ log"""
        if self.signal == "HOLD":
            return "HOLD — ยังไม่มีสัญญาณตัดกัน"

        if self.enter:
            return f"{self.signal} — ผ่านตัวกรองครบ {len(self.checks)} ข้อ"

        blocked = ", ".join(check.name for check in self.blockers)
        return f"{self.signal} แต่ไม่เข้า — ติด: {blocked}"

    def report(self):
        """รายงานเต็มทีละบรรทัด ใช้ตอนสั่ง signal ด้วยมือ"""
        lines = [f"สัญญาณ: {self.signal}", f"เข้าไม้: {'ใช่' if self.enter else 'ไม่'}", ""]

        for check in self.checks:
            mark = "ผ่าน  " if check.passed else "ไม่ผ่าน"
            lines.append(f"  [{mark}] {check.name}: {check.detail}")

        return "\n".join(lines)


# ---------- ตัวกรองแต่ละตัว ----------

def _check_h1_trend(signal, context):
    trend = context.get("h1_trend", "UNKNOWN")
    wanted = "UPTREND" if signal == "BUY" else "DOWNTREND"
    passed = trend == wanted
    return Check("เทรนด์ H1", passed, f"H1 = {trend}, ต้องการ {wanted}")


def _check_adx(signal, context):
    adx = context.get("adx")

    if adx is None or adx != adx:  # None หรือ NaN
        return Check("ความแรงเทรนด์ ADX", False, "คำนวณ ADX ไม่ได้")

    passed = adx >= ADX_MIN
    return Check("ความแรงเทรนด์ ADX", passed, f"ADX = {adx:.1f}, ต้อง >= {ADX_MIN:.0f}")


def _check_rsi(signal, context):
    rsi = context.get("rsi")

    if rsi is None or rsi != rsi:
        return Check("RSI ไม่ยืดเกิน", False, "คำนวณ RSI ไม่ได้")

    if signal == "BUY":
        passed = rsi <= RSI_MAX_FOR_BUY
        return Check("RSI ไม่ยืดเกิน", passed, f"RSI = {rsi:.1f}, BUY ต้อง <= {RSI_MAX_FOR_BUY:.0f}")

    passed = rsi >= RSI_MIN_FOR_SELL
    return Check("RSI ไม่ยืดเกิน", passed, f"RSI = {rsi:.1f}, SELL ต้อง >= {RSI_MIN_FOR_SELL:.0f}")


def _check_m5(signal, context):
    trend = context.get("m5_trend", "UNKNOWN")
    opposite = "DOWNTREND" if signal == "BUY" else "UPTREND"

    # ขอแค่ "ไม่สวนทาง" ไม่ได้บังคับให้ตรงทิศ เพราะ M5 มักตามหลังจุดตัดของ M15
    passed = trend != opposite
    return Check("M5 ไม่สวนทาง", passed, f"M5 = {trend}, ห้ามเป็น {opposite}")


def _check_spread(signal, context):
    spread = context.get("spread_points")

    if spread is None:
        return Check("Spread", False, "อ่านค่า spread ไม่ได้")

    passed = spread <= MAX_SPREAD_POINTS
    return Check("Spread", passed, f"{spread:.1f} points, ต้อง <= {MAX_SPREAD_POINTS:.0f}")


def server_hours_for(utc_hours, offset):
    """
    แปลงหน้าต่างเวลา UTC เป็นชั่วโมงของเซิร์ฟเวอร์ broker

    เวลาบนแท่งราคาเป็นเวลาเซิร์ฟเวอร์ ส่วนช่วงตลาดที่เราสนใจนิยามด้วย UTC
    ตัวแปลงจึงต้องอยู่ที่เดียว ไม่ใช่ให้คนมานั่งบวกเองแล้วพิมพ์ผิดตอน DST ขยับ
    คืนเป็น set เพราะหน้าต่างที่ข้ามเที่ยงคืนจะไม่ใช่ช่วงต่อเนื่องอีกต่อไป
    """
    return {(hour + offset) % 24 for hour in utc_hours}


def _check_session(signal, context):
    hour = context.get("server_hour")
    offset = context.get("gmt_offset")

    if hour is None:
        return Check("ช่วงเวลาเทรด", False, "ไม่ทราบเวลาเซิร์ฟเวอร์")

    # ปิดกั้นไว้ก่อนเมื่อแปลงเวลาไม่ได้ เพราะคนเปิดสวิตช์นี้ตั้งใจจะจำกัดชั่วโมง
    # การเดาแล้วปล่อยผ่านคือทำตรงข้ามกับที่สั่ง  เหตุผลไปโผล่ใน log และ CSV อยู่แล้ว
    if offset is None:
        return Check("ช่วงเวลาเทรด", False, "ไม่ทราบ GMT offset ของ broker จึงเทียบเวลาไม่ได้")

    allowed = server_hours_for(SESSION_UTC_HOURS, offset)
    passed = hour in allowed
    utc_hour = (hour - offset) % 24

    return Check(
        "ช่วงเวลาเทรด", passed,
        f"เซิร์ฟเวอร์ {hour:02d}:xx = {utc_hour:02d}:xx UTC, "
        f"อนุญาต {SESSION_UTC_HOURS.start:02d}-{SESSION_UTC_HOURS.stop - 1:02d} UTC"
    )


def _check_news(signal, context):
    """
    ไม่เข้าไม้ใหม่ตอนใกล้ข่าวแรง

    context["news_event"] เป็นข้อความอธิบายข่าวที่ครอบเวลานี้อยู่ หรือ None ถ้าว่าง
    runner เป็นคนไปถาม bot/news.py มา ตรงนี้จึงไม่ต้องต่อเน็ตและยังเป็นฟังก์ชันบริสุทธิ์
    ไม่มีข้อมูล = ไม่มีข่าว = ผ่าน  ปฏิทินโหลดไม่ได้ไม่ควรทำให้บอทหยุดเทรดเงียบๆ
    """
    event = context.get("news_event")

    if not event:
        return Check("เลี่ยงข่าวแรง", True, "ไม่มีข่าวแรงในช่วงนี้")

    return Check("เลี่ยงข่าวแรง", False, f"ติดข่าว: {event}")


def _check_dxy(signal, context):
    """
    ดอลลาร์ต้องไม่สวนกับไม้ทองที่จะเปิด

    ทองกับดัชนีดอลลาร์วิ่งสวนกันเป็นปกติ (DXY ขึ้น ทองมักลง) งานวิจัยชี้ว่า DXY เป็น
    ตัวทำนายผลตอบแทนทองที่เสถียรที่สุดเมื่อเทียบกับน้ำมัน เงินเฟ้อ หรือ VIX ซึ่งมา
    เป็นช่วงๆ  ขอแค่ "ไม่สวน" เหมือน M5 ไม่ได้บังคับให้ยืนยันทิศ

    broker หลายเจ้าไม่มี symbol ดัชนีดอลลาร์ให้ดึง ตรงนั้นคืน UNKNOWN แล้วปล่อยผ่าน
    — ต่างจาก session ตรงที่ผู้ใช้ไม่ได้สั่งให้จำกัดอะไร แค่ไม่มีข้อมูลมาเสริมเท่านั้น
    """
    trend = context.get("dxy_trend", "UNKNOWN")

    if trend in (None, "", "UNKNOWN"):
        return Check("ดอลลาร์ไม่สวนทาง", True, "ไม่มีข้อมูล DXY จึงไม่นำมาตัดสิน")

    # ทองขึ้นคู่กับดอลลาร์อ่อน ดังนั้น BUY ทองจะสวนกับดอลลาร์ที่กำลังแข็ง
    opposite = "UPTREND" if signal == "BUY" else "DOWNTREND"
    passed = trend != opposite

    return Check("ดอลลาร์ไม่สวนทาง", passed, f"DXY = {trend}, ห้ามเป็น {opposite}")


def _check_fvg(signal, context):
    """
    Fair Value Gap ที่ยังค้างอยู่ต้องไม่สวนกับไม้ที่จะเปิด

    แนวคิดนี้มาจากสาย ICT/SMC ไม่มีงานวิจัยรองรับเหมือน ADX หรือ RSI แต่ต่างจาก
    ของอื่นในสายเดียวกันตรงที่นิยามมันชัดพอจะเขียนเป็นฟังก์ชันเดียวได้ ที่นี่จึงวัดมัน
    ได้จริงแทนที่จะเถียงกัน  ปิดไว้จนกว่า outcomes กับ sweep จะบอกว่าแยกอะไรได้

    ขอแค่ "ไม่สวน" เหมือน M5 กับ DXY ไม่ได้บังคับว่าต้องมี FVG หนุน — NONE จึงผ่าน
    """
    state = context.get("fvg_state", "NONE")

    if state in (None, "", "NONE"):
        return Check("FVG ไม่สวนทาง", True, "ไม่มีช่องที่ยังค้างอยู่")

    opposite = "BEAR" if signal == "BUY" else "BULL"
    passed = state != opposite

    return Check("FVG ไม่สวนทาง", passed, f"FVG = {state}, ห้ามเป็น {opposite}")


# ตัวกรองที่เปิดใช้ พร้อมสวิตช์ของมัน
FILTERS = (
    (lambda: USE_H1_TREND_FILTER, _check_h1_trend),
    (lambda: USE_ADX_FILTER, _check_adx),
    (lambda: USE_RSI_FILTER, _check_rsi),
    (lambda: USE_M5_CONFIRM, _check_m5),
    (lambda: True, _check_spread),          # spread ตรวจเสมอ
    (lambda: USE_SESSION_FILTER, _check_session),
    (lambda: USE_NEWS_FILTER, _check_news),
    (lambda: USE_DXY_FILTER, _check_dxy),
    (lambda: USE_FVG_FILTER, _check_fvg),
)


def evaluate(context):
    """
    ตัดสินใจจากข้อมูลที่รวบรวมมาแล้ว

    context ต้องมีอย่างน้อย: m15_signal, h1_trend, m5_trend, adx, rsi, spread_points
    """
    signal = context.get("m15_signal", "HOLD")

    trigger = Check(
        "สัญญาณตัดกัน M15",
        signal in ("BUY", "SELL"),
        f"MA{context.get('fast_ma', 20)}/MA{context.get('slow_ma', 50)} ให้ {signal}",
    )

    if signal not in ("BUY", "SELL"):
        return Decision("HOLD", False, [trigger], context)

    checks = [trigger]
    for enabled, rule in FILTERS:
        if enabled():
            checks.append(rule(signal, context))

    return Decision(signal, all(check.passed for check in checks), checks, context)


def active_filters():
    """ชื่อตัวกรองที่เปิดอยู่ ใช้พิมพ์ตอนบอทเริ่มทำงาน"""
    names = []

    if USE_H1_TREND_FILTER:
        names.append("เทรนด์ H1")
    if USE_ADX_FILTER:
        names.append(f"ADX >= {ADX_MIN:.0f}")
    if USE_RSI_FILTER:
        names.append(f"RSI {RSI_MIN_FOR_SELL:.0f}-{RSI_MAX_FOR_BUY:.0f}")
    if USE_M5_CONFIRM:
        names.append("M5 ไม่สวนทาง")
    names.append(f"spread <= {MAX_SPREAD_POINTS:.0f}")
    if USE_SESSION_FILTER:
        names.append(f"ชั่วโมง {SESSION_UTC_HOURS.start}-{SESSION_UTC_HOURS.stop - 1} UTC")
    if USE_NEWS_FILTER:
        names.append("เลี่ยงข่าวแรง")
    if USE_DXY_FILTER:
        names.append("DXY ไม่สวนทาง")
    if USE_FVG_FILTER:
        names.append("FVG ไม่สวนทาง")

    return names
