"""
เครื่องตัดสินใจของบอท — รวมข้อมูลหลาย timeframe แล้วสรุปเองว่าจะเข้าไม้หรือไม่

หน้าที่ของแต่ละ timeframe:
    H1  = กำหนดทิศ    เทรดตามเทรนด์ใหญ่เท่านั้น ไม่สวนเทรนด์
    M15 = จุดชนวน     MA20 ตัด MA50 คือจังหวะเข้า
    M5  = ยืนยัน      โมเมนตัมระยะสั้นต้องไม่สวนกับไม้ที่จะเปิด
    ADX / RSI / spread = ตัวยับยั้ง ตลาดไม่มีเทรนด์หรือราคาไล่ไปไกลแล้วให้ข้าม

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
USE_SESSION_FILTER = False    # จำกัดชั่วโมงเทรด (เวลาเซิร์ฟเวอร์ ไม่ใช่เวลาไทย)

ADX_MIN = 20.0                # ต่ำกว่านี้ถือว่าไม่มีเทรนด์
RSI_MAX_FOR_BUY = 70.0        # RSI สูงกว่านี้แล้วไม่ตาม BUY
RSI_MIN_FOR_SELL = 30.0       # RSI ต่ำกว่านี้แล้วไม่ตาม SELL
MAX_SPREAD_POINTS = 50.0

# ชั่วโมงที่อนุญาต (เวลาเซิร์ฟเวอร์ broker) ช่วง London/NY ที่ทองมีสภาพคล่องจริง
SESSION_HOURS = range(9, 22)

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


def _check_session(signal, context):
    hour = context.get("server_hour")

    if hour is None:
        return Check("ช่วงเวลาเทรด", False, "ไม่ทราบเวลาเซิร์ฟเวอร์")

    passed = hour in SESSION_HOURS
    return Check("ช่วงเวลาเทรด", passed, f"ชั่วโมงเซิร์ฟเวอร์ {hour}, อนุญาต {SESSION_HOURS.start}-{SESSION_HOURS.stop - 1}")


# ตัวกรองที่เปิดใช้ พร้อมสวิตช์ของมัน
FILTERS = (
    (lambda: USE_H1_TREND_FILTER, _check_h1_trend),
    (lambda: USE_ADX_FILTER, _check_adx),
    (lambda: USE_RSI_FILTER, _check_rsi),
    (lambda: USE_M5_CONFIRM, _check_m5),
    (lambda: True, _check_spread),          # spread ตรวจเสมอ
    (lambda: USE_SESSION_FILTER, _check_session),
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
        names.append(f"ชั่วโมง {SESSION_HOURS.start}-{SESSION_HOURS.stop - 1}")

    return names
