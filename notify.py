"""
ชั้นแจ้งเตือน Telegram — แยกหมวด จัดรูปแบบ และกันสแปม

เดิมทุกจุดในลูปเรียก send_telegram() ส่งข้อความเปล่าเหมือนกันหมด ผลคือแยกไม่ออก
ว่าอันไหนต้องรีบดู อันไหนแค่รายงาน และตอนตลาดนิ่งก็ส่งเรื่องเดิมซ้ำทุกแท่ง
ไฟล์นี้แก้สามเรื่องนั้นพร้อมกัน: ติดป้ายหมวดให้ทุกข้อความ จัดรูปแบบให้อ่านจบใน
สายตาเดียว และจำสิ่งที่เพิ่งส่งไปเพื่อไม่ส่งซ้ำ

แบ่งเป็นสองชั้นโดยตั้งใจ:
    format_*()  ฟังก์ชันบริสุทธิ์ ประกอบข้อความอย่างเดียว ไม่แตะเน็ตเวิร์ก เทสได้
    Notifier    ตัวส่งจริง ถือสวิตช์หมวด คูลดาวน์ ตัวกันซ้ำ และการลองส่งใหม่

ไม่ import MetaTrader5 โดยตั้งใจ เช่นเดียวกับ strategy.py — เทสจึงรันได้ทุกเครื่อง
ข้อความใช้ parse_mode HTML เพราะ Telegram ยอมให้ <code> รักษาการจัดคอลัมน์ไว้ได้
ซึ่ง MarkdownV2 ทำไม่ได้โดยไม่ต้อง escape จนอ่านโค้ดไม่ออก
"""

import html
import time
from collections import namedtuple
from datetime import datetime

import requests

# ---------- สวิตช์รายหมวด ปิดได้ทีละหมวดเวลารำคาญ ----------
SEND_LIFECYCLE = True     # บอทเริ่ม/หยุด/พัง/การเชื่อมต่อหลุด
SEND_MARKET = True        # ตลาดปิด-เปิด
SEND_SIGNAL = True        # คำตัดสินรายแท่ง
SEND_ENTRY = True         # ส่งคำสั่งเข้าไม้ สำเร็จหรือไม่ก็ตาม
SEND_MANAGE = True        # ขยับ SL เก็บกำไรบางส่วน
SEND_EXIT = True          # ไม้ปิด ไม่ว่าด้วยเหตุใด
SEND_RISK = True          # เกินงบความเสี่ยง ตัวตัดวงจรทำงาน
SEND_SUMMARY = True       # สรุปรายวัน heartbeat

# สองอันนี้อยู่ในหมวด signal แต่แยกสวิตช์ เพราะความถี่ต่างกันคนละโลก
SEND_HOLD = True          # ทุกแท่งที่ยังไม่มีสัญญาณ — ได้ข้อความทุก 15 นาที ปิดได้เมื่อเบื่อ
SEND_NEAR_MISS = True     # มีสัญญาณตัดกันแต่ติดตัวกรอง — นี่คือของที่น่าดู

# ---------- พฤติกรรมการส่ง ----------
# บอทรันข้ามคืนข้างเตียง ชั่วโมงพวกนี้ยังส่งครบแต่ไม่ปลุก
QUIET_HOURS = tuple(range(0, 7))   # เที่ยงคืน–ก่อนเจ็ดโมง

# HOLD มาทุก 15 นาทีตลอดคืน = ~44 ข้อความต่อรอบ รวมเป็นสรุปทุกกี่แท่งแทน
# 4 แท่ง M15 = ชั่วโมงละครั้ง ตั้งเป็น 1 คือกลับไปส่งทุกแท่งแบบเดิม
HOLD_DIGEST_CANDLES = 4
DEDUP_SECONDS = 900       # เนื้อความเดิมในหมวดเดิมภายในกี่วินาทีถือว่าซ้ำ ไม่ต้องส่ง
MAX_MESSAGE_CHARS = 3900  # Telegram ตัดที่ 4096 เผื่อไว้หน่อย
HTTP_TIMEOUT = 10
MAX_RETRY_WAIT = 30       # โดน rate limit แล้วยอมรอนานสุดกี่วินาที
REMEMBER_LAST = 50        # เก็บข้อความล่าสุดไว้ในหน่วยความจำกี่ชิ้น (เทส/โหมด dry ใช้)


# ---------- ทะเบียนหมวด ----------

# cooldown = วินาทีที่ห้ามส่งหมวดนี้ซ้ำด้วย key เดิม 0 = ไม่จำกัด
# loud = True ให้เด้งมีเสียง False ส่งเงียบ
Category = namedtuple("Category", "key icon label loud cooldown enabled")

CATEGORIES = {
    "lifecycle": Category("lifecycle", "🤖", "ระบบ", True, 0, lambda: SEND_LIFECYCLE),
    "market": Category("market", "🕒", "ตลาด", False, 21600, lambda: SEND_MARKET),
    "signal": Category("signal", "📊", "สัญญาณ", False, 0, lambda: SEND_SIGNAL),
    "entry": Category("entry", "🚀", "เข้าไม้", True, 0, lambda: SEND_ENTRY),
    "manage": Category("manage", "🛡️", "ดูแลไม้", False, 0, lambda: SEND_MANAGE),
    "exit": Category("exit", "🏁", "ปิดไม้", True, 0, lambda: SEND_EXIT),
    "risk": Category("risk", "⚠️", "ความเสี่ยง", True, 0, lambda: SEND_RISK),
    "summary": Category("summary", "📅", "สรุป", False, 0, lambda: SEND_SUMMARY),
}


def active_categories():
    """ชื่อหมวดที่เปิดอยู่ ใช้พิมพ์ตอนบอทเริ่มทำงาน — คู่กับ strategy.active_filters()"""
    return [
        f"{category.icon} {category.label}"
        for category in CATEGORIES.values()
        if category.enabled()
    ]


# ---------- ลูกเล่นการจัดรูปแบบ ----------

BLOCK_FULL = "█"
BLOCK_EMPTY = "░"

DIRECTION_ICON = {"BUY": "🟢 BUY ▲", "SELL": "🔴 SELL ▼", "HOLD": "⚪ HOLD"}
TREND_ICON = {"UPTREND": "📈", "DOWNTREND": "📉"}


def escape(text):
    """กัน <, > ในข้อความจาก broker ไม่ให้ทำ HTML ของ Telegram พัง"""
    return html.escape(str(text), quote=False)


def is_number(value):
    """None และ NaN ถือว่าไม่ใช่ตัวเลข — indicator คำนวณไม่ได้จะโผล่มาเป็น NaN"""
    return value is not None and value == value


def bar(value, low, high, width=10):
    """แถบวัดค่าแบบตัวอักษร — เห็นตำแหน่งของค่าในช่วงได้โดยไม่ต้องคิดเลข"""
    if not is_number(value):
        return BLOCK_EMPTY * width

    span = high - low
    ratio = 0.0 if span <= 0 else (value - low) / span
    ratio = max(0.0, min(1.0, ratio))
    filled = int(round(ratio * width))

    return BLOCK_FULL * filled + BLOCK_EMPTY * (width - filled)


def r_blocks(r_multiple, width=6):
    """แปลง R เป็นบล็อกสี — ขนาดกำไร/ขาดทุนเห็นได้ก่อนอ่านตัวเลข"""
    if not is_number(r_multiple):
        return "—"

    count = min(width, max(1, int(round(abs(r_multiple)))))
    return ("🟩" if r_multiple >= 0 else "🟥") * count


# จุดสีบอกสถานะของค่าหนึ่งตัว วางไว้นอก <code> เพราะ emoji กว้างไม่เท่าตัวอักษร
# monospace ใส่ในแถบแล้วคอลัมน์จะเลื่อนกันทั้งบล็อก
DOT_GOOD = "🟢"
DOT_WARN = "🟡"
DOT_BAD = "🔴"

SPARK = "▁▂▃▄▅▆▇█"

TRACK_LINE = "─"
TRACK_ENTRY = "┼"
TRACK_PRICE = "●"


def spark(values):
    """กราฟจิ๋วบรรทัดเดียว — เห็นรูปร่างของราคาที่ผ่านมาโดยไม่ต้องส่งรูป"""
    numbers = [value for value in values if is_number(value)]

    if len(numbers) < 2:
        return ""

    low, high = min(numbers), max(numbers)
    span = high - low

    if span <= 0:
        return SPARK[0] * len(numbers)

    return "".join(SPARK[min(len(SPARK) - 1, int((value - low) / span * len(SPARK)))]
                   for value in numbers)


def r_now(entry, price, risk, signal):
    """
    กำไร/ขาดทุนตอนนี้เป็น R

    `risk` คือระยะ 1R ตอนเข้าไม้ ไม่ใช่ระยะ SL ปัจจุบัน — SL ถูกขยับไป breakeven
    แล้วไล่ตามราคาเรื่อยๆ คิดจากระยะปัจจุบันจึงได้ตัวเลขมหาศาลที่ไม่มีความหมาย
    (ไม้ที่กำไรอยู่ 1R จริงๆ เคยแสดงเป็น -10R มาแล้วด้วยวิธีนั้น)
    runner เก็บค่านี้ไว้ใน state["position_risk"] ต่อ ticket ด้วยเหตุผลเดียวกัน
    """
    if not (is_number(entry) and is_number(price) and is_number(risk)) or risk <= 0:
        return None

    move = price - entry if signal == "BUY" else entry - price
    return move / risk


def position_track(entry, sl, tp, price, width=13):
    """
    ราคาอยู่ตรงไหนระหว่าง SL กับ TP

    สัดส่วนคิดจากระยะ SL→TP ซึ่งกลับเครื่องหมายพร้อมกันทั้งคู่เมื่อเป็นฝั่งขาย
    0 คือชน SL และ 1 คือชน TP เหมือนกันทั้งสองฝั่ง
    """
    if not (is_number(sl) and is_number(tp) and is_number(price)) or tp == sl:
        return ""

    def index(value):
        ratio = (value - sl) / (tp - sl)
        return max(0, min(width - 1, int(round(ratio * (width - 1)))))

    track = [TRACK_LINE] * width

    if is_number(entry):
        track[index(entry)] = TRACK_ENTRY

    track[index(price)] = TRACK_PRICE
    return "".join(track)


def position_lines(entry, sl, tp, price, risk=None, signal=None):
    """
    แถบ SL→TP พร้อม R ตอนนี้ — ไม้ใกล้อะไรมากกว่ากัน เห็นได้โดยไม่ต้องคิดเลข

    ไม่รู้ 1R ก็ยังวาดแถบให้ แค่ไม่บอก R เพราะเดาเอาแล้วจะผิดทุกครั้งที่ SL ขยับ
    """
    track = position_track(entry, sl, tp, price)

    if not track:
        return []

    r_multiple = r_now(entry, price, risk, signal)

    if not is_number(r_multiple):
        return [f"<code>SL ├{track}┤ TP</code>"]

    dot = DOT_GOOD if r_multiple > 0 else DOT_BAD if r_multiple < 0 else DOT_WARN
    return [f"<code>SL ├{track}┤ TP</code> {dot} <b>{r_multiple:+.2f}R</b>"]


def threshold_dot(value, limit, higher_is_better=True):
    """จุดสีของค่าที่มีเกณฑ์ตายตัว — ว่างเปล่าถ้ายังไม่รู้เกณฑ์ ไม่ใช่เดาให้"""
    if limit is None or not is_number(value):
        return ""

    passed = value >= limit if higher_is_better else value <= limit
    return DOT_GOOD if passed else DOT_BAD


def money(amount, currency=""):
    """ใส่เครื่องหมายหน้าเสมอ กำไรกับขาดทุนจะได้แยกออกด้วยการกวาดตา"""
    if not is_number(amount):
        return "—"

    sign = "+" if amount >= 0 else "-"
    return f"{sign}{abs(amount):,.2f} {currency}".strip()


def direction(signal):
    return DIRECTION_ICON.get(signal, str(signal))


def trend(name):
    return f"{TREND_ICON.get(name, '➖')} {name}"


def win_rate_bar(wins, losses, width=5):
    total = wins + losses

    if not total:
        return "ยังไม่มีไม้ที่ปิด"

    filled = int(round(wins / total * width))
    return "🟩" * filled + "🟥" * (width - filled) + f"  {wins / total * 100:.0f}%"


def duration(seconds):
    """อายุการทำงานแบบอ่านออก — ใช้ใน heartbeat"""
    seconds = int(max(0, seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60

    if days:
        return f"{days} วัน {hours} ชม."
    if hours:
        return f"{hours} ชม. {minutes} นาที"

    return f"{minutes} นาที"


def metric_line(label, value, low, high, unit="", note="", dot=""):
    """หนึ่งบรรทัดของ indicator: ชื่อ ตัวเลข แถบ จุดสี แล้วค่อยหมายเหตุ"""
    if not is_number(value):
        return f"<code>{label:<6}    —</code> {escape(note)}".rstrip()

    parts = [f"<code>{label:<6}{value:7.1f}{unit} {bar(value, low, high)}</code>", dot, escape(note)]
    return " ".join(part for part in parts if part)


def check_lines(checks):
    """ทุกตัวกรองพร้อมตัวเลขที่ใช้ตัดสิน — invariant ของโปรเจกต์คือเหตุผลต้องติดไปด้วยเสมอ"""
    return [
        f"{'✅' if check.passed else '❌'} {escape(check.name)} · <i>{escape(check.detail)}</i>"
        for check in checks
    ]


def market_lines(context, adx_min=None, rsi_low=30.0, rsi_high=70.0, max_spread=None):
    """สภาพตลาดย่อหนึ่งบล็อก ใช้ซ้ำได้ทั้งข้อความสัญญาณและข้อความเข้าไม้"""
    rsi = context.get("rsi")
    adx = context.get("adx")

    if not is_number(rsi):
        rsi_note = ""
    elif rsi >= rsi_high:
        rsi_note = "ซื้อมากเกิน"
    elif rsi <= rsi_low:
        rsi_note = "ขายมากเกิน"
    else:
        rsi_note = "กลางๆ"

    adx_note = ""
    if adx_min is not None and is_number(adx):
        adx_note = "มีเทรนด์" if adx >= adx_min else f"sideway (ต้อง ≥ {adx_min:.0f})"

    # จุดสีบอกว่า "ตัวกรองตัวนี้ผ่านไหม" ไม่ใช่ "ค่าดีไหม" — เกณฑ์เดียวกับ strategy.py
    rsi_dot = "" if not is_number(rsi) else (
        DOT_WARN if rsi >= rsi_high or rsi <= rsi_low else DOT_GOOD
    )

    return [
        f"<code>ราคา  {context.get('close', 0):10,.2f}</code>",
        f"<code>H1</code> {trend(context.get('h1_trend', '?'))}   "
        f"<code>M5</code> {trend(context.get('m5_trend', '?'))}",
        metric_line("RSI", rsi, 0, 100, note=rsi_note, dot=rsi_dot),
        metric_line("ADX", adx, 0, 50, note=adx_note, dot=threshold_dot(adx, adx_min)),
        metric_line("ATR", context.get("atr"), 0, 25, note="ความผันผวนต่อแท่ง"),
        metric_line("Spread", context.get("spread_points"), 0, 60, note="points",
                    dot=threshold_dot(context.get("spread_points"), max_spread,
                                      higher_is_better=False)),
    ]


def hold_digest_lines(window, adx_min=None, max_spread=None):
    """
    สรุปช่วง HOLD หลายแท่งเป็นบล็อกเดียว — สภาพล่าสุดเต็มรูปแบบ แล้วต่อด้วยช่วงที่ผ่านมา

    แท่งเดียวไม่มีอะไรให้สรุป ส่งหน้าตาเดิมไปเลย
    """
    if not window:
        return []

    latest = market_lines(window[-1], adx_min, max_spread=max_spread)

    if len(window) == 1:
        return latest

    def span(key, digits=1):
        values = [item.get(key) for item in window]
        values = [value for value in values if is_number(value)]
        if not values:
            return "—"
        return f"{min(values):,.{digits}f} – {max(values):,.{digits}f}"

    closes = [item.get("close") for item in window]
    trend_line = spark(closes)

    return latest + [
        "",
        f"<b>{len(window)} แท่งที่ผ่านมา</b>",
        f"<code>ราคา   {span('close', 2)}</code>" + (f"  {trend_line}" if trend_line else ""),
        f"<code>RSI    {span('rsi')}</code>",
        f"<code>ADX    {span('adx')}</code>",
        f"<code>Spread {span('spread_points')}</code>",
    ]


def night_lines(stats):
    """
    สรุปว่ารอบนี้บอทเห็นอะไรมาบ้าง — ตอบ "คืนนี้เป็นไง" โดยไม่ต้องรอเปิด report ตอนเช้า

    ตัวเลขนับในหน่วยความจำของรอบที่กำลังรัน restart แล้วเริ่มใหม่ ซึ่งตรงกับคำว่า
    "รอบนี้" อยู่แล้ว
    """
    if not stats:
        return []

    candles = stats.get("candles", 0)
    lines = [f"<code>รอบนี้   {candles} แท่ง · crossover {stats.get('crossovers', 0)} ครั้ง</code>"]

    closes = stats.get("closes") or []
    trend_line = spark(closes)
    if trend_line:
        lines.append(f"<code>ราคา   {trend_line}</code>")

    blockers = stats.get("blockers") or {}
    if blockers:
        top = sorted(blockers.items(), key=lambda item: (-item[1], item[0]))[:3]
        lines.append("ติดบ่อยสุด: " + " · ".join(
            f"{escape(name)} <b>{count}</b>" for name, count in top
        ))

    return lines


def format_message(category_key, title, lines, footer=None):
    """
    ประกอบข้อความสุดท้าย — ฟังก์ชันบริสุทธิ์ เทสได้โดยไม่ต้องมี token

    หัวเรื่องมีไอคอนหมวดเสมอ เพื่อให้กวาดตาในห้องแชทแล้วรู้ทันทีว่าเรื่องอะไร
    """
    category = CATEGORIES[category_key]
    body = "\n".join(str(line) for line in lines if line is not None)

    message = f"{category.icon} <b>{escape(title)}</b>"

    if body:
        message += "\n" + body

    if footer:
        message += f"\n\n<i>{escape(footer)}</i>"

    if len(message) > MAX_MESSAGE_CHARS:
        message = message[:MAX_MESSAGE_CHARS - 1] + "…"

    return message


def strip_tags(message):
    """ข้อความสำรองเมื่อ Telegram ปฏิเสธ HTML — ยอมเสียการจัดรูปแบบดีกว่าเสียข้อความ"""
    plain = message.replace("<br>", "\n")

    while "<" in plain and ">" in plain:
        start = plain.index("<")
        end = plain.find(">", start)

        if end == -1:
            break

        plain = plain[:start] + plain[end + 1:]

    return html.unescape(plain)


# ---------- ตัวส่ง ----------

class Notifier:
    """
    ตัวส่งจริง ถือสถานะที่ทำให้ไม่สแปม: เวลาส่งล่าสุดและเนื้อความล่าสุดของแต่ละ key

    สถานะอยู่ในหน่วยความจำอย่างเดียว ไม่ลง bot_state.json โดยตั้งใจ — restart แล้ว
    ได้ข้อความซ้ำหนึ่งครั้งไม่เป็นไร แต่ restart แล้วเงียบเพราะจำผิดว่าเคยส่งแล้วนั้นแย่กว่า

    transport แยกออกมาเป็นพารามิเตอร์เพื่อให้เทสดักข้อความได้โดยไม่ต้องต่อเน็ต
    """

    def __init__(self, token, chat_id, logger, transport=None, clock=time.time):
        self.token = token
        self.chat_id = chat_id
        self.logger = logger
        self.transport = transport or self._post
        self.clock = clock

        self.sent = []          # (หมวด, ข้อความ) ล่าสุด สำหรับเทสและ run.py notify
        self.skipped = 0
        self._last_at = {}
        self._last_body = {}
        self._hold_window = []  # แท่ง HOLD ที่รอรวมเป็นสรุปเดียว

    @property
    def configured(self):
        return bool(self.token and self.chat_id)

    def send(self, category_key, title, lines, key=None, loud=None, footer=None, force=False):
        """
        คืน True เมื่อส่งออกไปจริง — False เมื่อหมวดปิด ซ้ำ ติดคูลดาวน์ หรือส่งไม่สำเร็จ

        key ใช้จัดกลุ่มข้อความชนิดเดียวกันที่เนื้อหาต่างกันเล็กน้อย เช่น heartbeat
        ที่ตัวเลขขยับทุกครั้ง แต่ไม่ควรถี่กว่าคูลดาวน์ของหมวด
        """
        category = CATEGORIES[category_key]

        if not category.enabled():
            return False

        message = format_message(category_key, title, lines, footer)
        stamp = (category_key, key or title)
        now = self.clock()

        if not force and self._is_repeat(stamp, message, now, category.cooldown):
            self.skipped += 1
            self.logger.debug("ข้ามแจ้งเตือนซ้ำ [%s] %s", category_key, title)
            return False

        self._last_at[stamp] = now
        self._last_body[stamp] = message
        self._remember(category_key, message)

        if not self.configured:
            self.logger.debug("ไม่ได้ตั้ง TELEGRAM_TOKEN/CHAT_ID จึงไม่ส่ง [%s] %s",
                              category_key, title)
            return False

        loud = category.loud if loud is None else loud
        return bool(self.transport(message, quiet=not loud or self._in_quiet_hours()))

    def _is_repeat(self, stamp, message, now, cooldown):
        last_at = self._last_at.get(stamp)

        if last_at is None:
            return False

        if cooldown and now - last_at < cooldown:
            return True

        return self._last_body.get(stamp) == message and now - last_at < DEDUP_SECONDS

    def _in_quiet_hours(self):
        return datetime.now().hour in QUIET_HOURS

    def _remember(self, category_key, message):
        self.sent.append((category_key, message))

        if len(self.sent) > REMEMBER_LAST:
            del self.sent[:-REMEMBER_LAST]

    def _post(self, message, quiet=False):
        """
        ส่งจริง — ล้มเหลวได้โดยไม่ทำให้บอทหยุด แต่ต้องเห็นใน log เสมอ

        429 คือ Telegram บอกให้รอ ไม่ใช่ความผิดพลาด จึงรอตามที่บอกแล้วลองใหม่
        400 มักแปลว่า HTML ผิด ส่งซ้ำแบบเดิมก็ผิดเหมือนเดิม จึงถอดแท็กแล้วส่งใหม่แทน
        """
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": quiet,
        }

        for attempt in (1, 2):
            try:
                response = requests.post(url, data=payload, timeout=HTTP_TIMEOUT)
            except requests.RequestException as error:
                self.logger.warning("ส่ง Telegram ไม่สำเร็จ (ครั้งที่ %d): %s", attempt, error)
                continue

            if response.ok:
                return True

            if response.status_code == 429:
                time.sleep(min(_retry_after(response), MAX_RETRY_WAIT))
                continue

            if response.status_code == 400 and payload.get("parse_mode"):
                self.logger.warning("Telegram ปฏิเสธ HTML จึงส่งแบบข้อความเปล่าแทน: %s",
                                    response.text[:200])
                payload.pop("parse_mode")
                payload["text"] = strip_tags(message)
                continue

            self.logger.warning("Telegram ตอบ %s: %s", response.status_code, response.text[:200])
            return False

        return False

    # ---------- ระบบ ----------

    def bot_started(self, symbol, account, mode, filters, risk_text, management, extra=None):
        self.send("lifecycle", f"บอท {symbol} เริ่มทำงาน", [
            f"<code>โหมด   </code> {escape(mode)}",
            f"<code>บัญชี  </code> {escape(account)}",
            f"<code>ตลาด   </code> {escape(symbol)} · M15 เข้า / H1 ทิศ / M5 ยืนยัน",
            "",
            "<b>ตัวกรองที่เปิด</b>",
            "\n".join(f"• {escape(name)}" for name in filters),
            "",
            "<b>ความเสี่ยงและการดูแลไม้</b>",
            f"• {escape(risk_text)}",
            f"• {escape(management)}",
            f"• {escape(extra)}" if extra else None,
        ], force=True)

    def bot_stopped(self, symbol, reason):
        self.send("lifecycle", f"บอท {symbol} หยุดทำงาน", [
            f"เหตุผล: {escape(reason)}",
            "ไม้ที่เปิดค้างอยู่จะไม่มีใครดูแล SL ให้จนกว่าจะเริ่มบอทใหม่",
        ], force=True)

    def crashed(self, symbol, error_text):
        self.send("lifecycle", f"บอท {symbol} หยุดเพราะข้อผิดพลาด", [
            f"<pre>{escape(error_text)}</pre>",
            "ดูรายละเอียดเต็มใน bot.log",
        ], force=True)

    def connection_lost(self, symbol):
        self.send("lifecycle", "การเชื่อมต่อ MT5 หลุด", [
            f"{escape(symbol)} — terminal ปิดหรือลิงก์ขาด กำลังลองต่อใหม่",
        ], key="connection")

    def reconnected(self, symbol):
        self.send("lifecycle", "ต่อ MT5 กลับมาได้แล้ว", [
            f"{escape(symbol)} — ลูปทำงานต่อตามปกติ",
        ], key="connection")

    # ---------- ตลาด ----------

    def market_closed(self, symbol, sleep_seconds):
        self.send("market", f"ตลาด {symbol} ปิดอยู่", [
            f"พักการ poll เหลือทุก {sleep_seconds // 60} นาที จนกว่าจะเปิด",
        ], key="market-closed")

    def market_reopened(self, symbol):
        self.send("market", f"ตลาด {symbol} เปิดแล้ว", [
            "กลับไปเฝ้าตามรอบปกติ",
        ], key="market-open", loud=True)

    # ---------- สัญญาณ ----------

    def candle_verdict(self, decision, context, symbol, adx_min=None, watch_mode=False,
                       max_spread=None):
        """
        คำตัดสินหนึ่งแท่ง — ความถี่ต่างกันสามระดับจึงคุมด้วยสวิตช์คนละตัว

        ผ่านครบ = ต้องรู้เสมอ, ติดตัวกรอง = น่าดูว่าติดอะไร, HOLD = ปิดไว้เป็นค่าเริ่มต้น
        """
        candle_time = context.get("candle_time", "-")
        footer = f"{symbol} · แท่ง {candle_time}"

        if decision.signal == "HOLD":
            if not SEND_HOLD:
                return False

            # เก็บสะสมแล้วส่งทีเดียว — HOLD ทุกแท่งคือข้อความทุก 15 นาทีตลอดคืน
            self._hold_window.append(context)

            if len(self._hold_window) < max(1, HOLD_DIGEST_CANDLES):
                return False

            window, self._hold_window = self._hold_window, []
            title = ("ยังไม่มีสัญญาณ" if len(window) == 1
                     else f"{len(window)} แท่งที่ผ่านมายังไม่มีสัญญาณ")

            return self.send("signal", title,
                             hold_digest_lines(window, adx_min, max_spread),
                             key="hold", loud=False, footer=footer)

        if decision.enter:
            head = "สัญญาณผ่านตัวกรองครบ" + (" (โหมดเฝ้าดู ไม่ส่งคำสั่ง)" if watch_mode else "")
            return self.send("signal", head, [
                f"<b>{direction(decision.signal)}</b>",
                "",
                *market_lines(context, adx_min, max_spread=max_spread),
                "",
                *check_lines(decision.checks),
            ], key="pass", loud=True, footer=footer)

        if not SEND_NEAR_MISS:
            return False

        blockers = ", ".join(check.name for check in decision.blockers)
        return self.send("signal", f"เกือบเข้า {decision.signal} แต่ติด {blockers}", [
            f"<b>{direction(decision.signal)}</b>",
            "",
            *market_lines(context, adx_min, max_spread=max_spread),
            "",
            *check_lines(decision.checks),
        ], key=f"near-miss-{decision.signal}", loud=False, footer=footer)

    # ---------- เข้าไม้ ----------

    def entry_filled(self, symbol, signal, lots, price, sl, tp, risk_text, currency, ticket):
        distance = abs(price - sl)
        reward = abs(tp - price)
        ratio = reward / distance if distance else 0

        self.send("entry", f"เข้าไม้แล้ว · {signal}", [
            f"<b>{direction(signal)} {lots} lot</b>",
            "",
            f"<code>เข้าที่  {price:10,.2f}</code>",
            f"<code>SL      {sl:10,.2f}  (ห่าง {distance:,.2f})</code>",
            f"<code>TP      {tp:10,.2f}  (ห่าง {reward:,.2f})</code>",
            f"<code>R:R     {ratio:10,.1f}</code>",
            "",
            f"เสี่ยงราว <b>{escape(risk_text)} {escape(currency)}</b> ถ้าโดน SL",
        ], footer=f"{symbol} · ticket {ticket}")

    def entry_failed(self, symbol, signal, detail):
        self.send("entry", f"เข้า {signal} ไม่สำเร็จ", [
            f"<pre>{escape(detail)}</pre>",
            "บันทึกไว้ใน trade_log.csv แล้ว",
        ], footer=symbol)

    # ---------- ดูแลไม้ ----------

    def stop_moved(self, symbol, ticket, old_sl, new_sl, entry, price, reason,
                   tp=None, risk=None, signal=None):
        moved = abs(new_sl - old_sl) if old_sl else None
        distance = abs(price - new_sl)

        self.send("manage", f"ขยับ SL · {reason}", [
            f"<code>SL เดิม  {old_sl:10,.2f}</code>",
            f"<code>SL ใหม่  {new_sl:10,.2f}</code>" + (f"  ↗ {moved:,.2f}" if moved else ""),
            f"<code>เข้าที่  {entry:10,.2f}</code>",
            f"<code>ราคา    {price:10,.2f}</code>",
            *position_lines(entry, new_sl, tp, price, risk, signal),
            "",
            f"ตอนนี้ SL ห่างราคา {distance:,.2f}",
        ], key=f"stop-{ticket}", footer=f"{symbol} · ticket {ticket}")

    def partial_taken(self, symbol, ticket, closed_volume, total_volume, at_r):
        self.send("manage", f"เก็บกำไรบางส่วนที่ {at_r}R", [
            f"ปิด <b>{closed_volume}</b> จาก {total_volume} lot",
            "ส่วนที่เหลือปล่อยวิ่งต่อโดยมี SL คุ้มอยู่",
        ], footer=f"{symbol} · ticket {ticket}")

    def partial_too_small(self, symbol, ticket, volume, at_r):
        self.send("manage", "แบ่งปิดไม่ได้ ไม้เล็กเกิน", [
            f"ticket นี้ {volume} lot ถึง {at_r}R แล้วแต่แบ่งครึ่งไม่ลงตัวกับ volume_step",
            "ปล่อยเต็มไม้ต่อ — เรื่องปกติของพอร์ตที่เปิดขั้นต่ำ",
        ], key=f"partial-small-{ticket}", footer=f"{symbol} · ticket {ticket}")

    # ---------- ปิดไม้ ----------

    def closed_on_reverse(self, symbol, ticket, new_signal):
        self.send("exit", "ปิดไม้เพราะสัญญาณกลับทาง", [
            f"สัญญาณใหม่เป็น <b>{direction(new_signal)}</b> จึงปิดไม้เดิมก่อนเปิดใหม่",
        ], footer=f"{symbol} · ticket {ticket}")

    def position_closed(self, symbol, ticket, meta, profit, currency, price=None):
        """ไม้ปิดเอง (โดน SL/TP หรือปิดจาก terminal) — เหตุการณ์ที่ต้องรู้ที่สุด"""
        risk_money = meta.get("risk_money")
        r_multiple = profit / risk_money if risk_money else None
        won = profit >= 0

        lines = [
            f"<b>{'✅ กำไร' if won else '❌ ขาดทุน'} {money(profit, currency)}</b>",
            r_blocks(r_multiple) + (f"  <b>{r_multiple:+.2f}R</b>" if is_number(r_multiple) else ""),
            "",
            f"<code>ทาง</code>     {direction(meta.get('signal', '?'))}",
            f"<code>เข้าที่  {meta.get('entry', 0):10,.2f}</code>",
        ]

        if price:
            lines.append(f"<code>ปิดที่   {price:10,.2f}</code>")

        if meta.get("opened_at"):
            lines.append(f"<code>เปิดเมื่อ {escape(meta['opened_at'])}</code>")

        self.send("exit", "ไม้ปิดแล้ว", lines, footer=f"{symbol} · ticket {ticket}")

    # ---------- ความเสี่ยง ----------

    def entry_over_budget(self, symbol, signal, minimum_loss, budget, currency, needed):
        self.send("risk", f"ไม่เข้า {signal} เพราะไม้ขั้นต่ำเสี่ยงเกินงบ", [
            f"<code>ไม้ขั้นต่ำเสี่ยง  {minimum_loss:10,.2f} {escape(currency)}</code>",
            f"<code>งบต่อไม้        {budget:10,.2f} {escape(currency)}</code>",
            "",
            f"ต้องมีทุนราว <b>{needed:,.0f} {escape(currency)}</b> ถึงจะเทรดตามงบนี้ได้",
            "หรือลด SL_ATR_MULT หรือเปิด ALLOW_RISK_OVER_BUDGET ถ้ายอมรับความเสี่ยงนี้",
        ], key="over-budget", footer=symbol)

    def halted(self, symbol, reason, summary):
        self.send("risk", "บอทหยุดเข้าไม้ชั่วคราว", [
            f"<b>{escape(reason)}</b>",
            "",
            f"<code>วันนี้    {summary.get('trades', 0)} ไม้</code>",
            f"<code>ชนะ/แพ้   {summary.get('wins', 0)} / {summary.get('losses', 0)}</code>",
            f"<code>กำไรสุทธิ {summary.get('profit', 0.0):+,.2f}</code>",
            "",
            "ไม้ที่เปิดอยู่ยังถูกดูแลตามปกติ หยุดแค่การเข้าไม้ใหม่",
        ], key="halt", footer=symbol)

    def resumed(self, symbol, day):
        self.send("risk", "กลับมาเข้าไม้ได้แล้ว", [
            f"เพดานของวันถูกรีเซ็ตเมื่อขึ้นวัน {escape(day)}",
        ], key="halt", footer=symbol)

    # ---------- สรุป ----------

    def daily_summary(self, symbol, day, summary, balance, currency):
        trades = summary.get("trades", 0)
        wins = summary.get("wins", 0)
        losses = summary.get("losses", 0)
        profit = summary.get("profit", 0.0)

        self.send("summary", f"สรุปวัน {day}", [
            f"<b>{'✅' if profit >= 0 else '❌'} {money(profit, currency)}</b>",
            "",
            f"<code>เทรด     {trades} ไม้</code>",
            f"<code>ชนะ/แพ้  {wins} / {losses}</code>",
            f"อัตราชนะ {win_rate_bar(wins, losses)}",
            "",
            f"<code>ทุนปิดวัน {balance:,.2f} {escape(currency)}</code>",
        ], key=f"day-{day}", loud=True, footer=symbol)

    def heartbeat(self, symbol, equity, currency, positions, summary, last_candle,
                  uptime_seconds, stats=None):
        lines = [
            f"<code>Equity   {equity:10,.2f} {escape(currency)}</code>",
            f"<code>ถืออยู่   {len(positions)} ไม้</code>",
            f"<code>วันนี้    {summary.get('trades', 0)} ไม้ "
            f"{summary.get('profit', 0.0):+,.2f}</code>",
            f"<code>แท่งล่าสุด {escape(last_candle)}</code>",
            f"<code>รันมาแล้ว {escape(duration(uptime_seconds))}</code>",
        ]

        # ไม้ที่เปิดอยู่ได้แถบของตัวเอง — heartbeat เดิมบอกแค่จำนวน ซึ่งไม่ตอบว่า
        # ตอนนี้ไม้กำลังไปทางไหน ตัวที่ไม่ใช่ dict (เทสเก่า) ข้ามไปเงียบๆ
        for position in positions:
            if not isinstance(position, dict):
                continue

            bars = position_lines(position.get("entry"), position.get("sl"),
                                  position.get("tp"), position.get("price"),
                                  position.get("risk"), position.get("signal"))
            if not bars:
                continue

            lines += ["", f"<code>ticket {escape(position.get('ticket', '-'))}</code> "
                          f"{direction(position.get('signal', 'HOLD'))}", *bars]

        night = night_lines(stats)
        if night:
            lines += ["", *night]

        self.send("summary", "ยังทำงานอยู่", lines,
                  key="heartbeat", loud=False, footer=symbol)


# ---------- วินิจฉัยตอนแจ้งเตือนไม่มา ----------

# (status, คำที่อยู่ในคำอธิบายของ Telegram, สิ่งที่ต้องไปแก้)
API_HINTS = (
    (401, "", "token ผิดหรือถูก revoke ไปแล้ว — ขอใหม่ที่ @BotFather แล้วใส่ .env"),
    (403, "initiate conversation",
     "บอทเริ่มบทสนทนาเองไม่ได้ — เปิดแชทบอทใน Telegram แล้วกด Start หนึ่งครั้งก่อน"),
    (403, "blocked", "แชทนี้บล็อกบอทไว้ — ปลดบล็อกก่อน"),
    (403, "kicked", "บอทถูกเตะออกจากกลุ่มนี้แล้ว"),
    (400, "chat not found",
     "TELEGRAM_CHAT_ID ไม่ตรงกับแชทไหนเลย — ดู chat id ที่ถูกต้องจากขั้น getUpdates"),
    (400, "chat_id is empty", "TELEGRAM_CHAT_ID ว่างอยู่"),
    (400, "can't parse entities", "HTML ในข้อความผิด — บั๊กของโค้ด ไม่ใช่ค่าคอนฟิก"),
    (429, "", "โดนจำกัดอัตราการส่ง รอสักครู่แล้วลองใหม่"),
)


def describe_api_error(status_code, description):
    """
    แปลคำตอบที่ผิดพลาดของ Telegram เป็นสิ่งที่ต้องไปแก้ — ฟังก์ชันบริสุทธิ์ เทสได้

    "Telegram ตอบ 403" ไม่ช่วยอะไร แต่ "ต้องกด Start ก่อน" คือคำตอบจริง
    """
    text = (description or "").lower()

    for code, needle, hint in API_HINTS:
        if status_code == code and needle in text:
            return hint

    return f"Telegram ตอบ {status_code}: {description or 'ไม่มีรายละเอียด'}"


def misplaced_secret_hint(env_text, example_text):
    """
    ดักกรณีกรอกค่าจริงลง .env.example แทน .env — กับดักที่เสียเวลาหานาน

    python-dotenv อ่านเฉพาะ .env ไฟล์ตัวอย่างจึงไม่มีผลอะไรเลยและบอทเงียบสนิท
    ที่แย่กว่านั้นคือ .env.example ติดตาม git อยู่ การกรอก token ลงไปไม่ได้แค่
    ไม่ทำงาน แต่เท่ากับเตรียม commit ความลับขึ้น repo รอบใหม่
    """
    if _has_filled_secret(env_text):
        return None

    if _has_filled_secret(example_text):
        return (
            ".env.example มีค่ากรอกไว้ แต่ .env ไม่มี — โปรแกรมอ่านเฉพาะ .env เท่านั้น\n"
            "     คัดลอกเป็นไฟล์จริง แล้วล้างค่าใน .env.example ให้ว่างเหมือนเดิม\n"
            "     (.env.example ติดตาม git อยู่ ถ้า commit ไปคือ token รั่วอีกรอบ)"
        )

    return None


def _has_filled_secret(text):
    """มีบรรทัด TELEGRAM_* ที่กรอกค่าไว้จริงไหม — ลงท้ายด้วย = เฉยๆ ถือว่ายังว่าง"""
    for line in (text or "").splitlines():
        line = line.strip()

        if line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")

        if key.strip().startswith("TELEGRAM") and value.strip().strip("\"'"):
            return True

    return False


def token_looks_valid(token):
    """รูปแบบ token ของ BotFather คือ <ตัวเลข>:<ตัวอักษรยาวๆ> ตรวจก่อนยิงจะได้ไม่งง"""
    if not token:
        return False

    head, _, tail = token.strip().partition(":")
    return head.isdigit() and len(tail) >= 30


def call_api(token, method, payload=None):
    """ยิง Telegram API ตรงๆ คืน (status_code, dict) — None แปลว่าต่อไม่ถึงเลย"""
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/{method}",
            data=payload or {}, timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as error:
        return None, {"description": f"ต่อ api.telegram.org ไม่ได้: {error}"}

    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, {"description": response.text[:200]}


def diagnose(token, chat_id, send_test=True):
    """
    ไล่ตรวจทีละข้อว่าโซ่ขาดตรงไหน คืน list ของ (ผ่านไหม, หัวข้อ, รายละเอียด)

    แยกจาก Notifier เพราะตอนแจ้งเตือนเงียบ สิ่งที่ต้องการคือ "ไปแก้ตรงไหน"
    ไม่ใช่ log บรรทัดเดียวว่าส่งไม่สำเร็จ
    """
    steps = []

    if not token:
        steps.append((False, "TELEGRAM_TOKEN", "ไม่พบใน .env — บอทจะเงียบสนิทโดยไม่ error"))
        return steps

    if not token_looks_valid(token):
        steps.append((False, "รูปแบบ TELEGRAM_TOKEN",
                      "ไม่ใช่รูปแบบ <ตัวเลข>:<ตัวอักษร> ของ BotFather"))
        return steps

    steps.append((True, "รูปแบบ TELEGRAM_TOKEN", "ถูกต้อง"))

    status, body = call_api(token, "getMe")

    if status != 200:
        steps.append((False, "getMe", describe_api_error(status, body.get("description"))))
        return steps

    bot = body.get("result", {})
    steps.append((True, "getMe", f"token ใช้ได้ — บอทชื่อ @{bot.get('username', '?')}"))

    if not chat_id:
        steps.append((False, "TELEGRAM_CHAT_ID", "ไม่พบใน .env"))
    else:
        steps.append((True, "TELEGRAM_CHAT_ID", f"ตั้งไว้เป็น {chat_id}"))

    status, body = call_api(token, "getUpdates", {"limit": 20})
    seen = _chat_ids_from_updates(body.get("result") if status == 200 else None)

    if seen:
        matched = str(chat_id).strip() in seen
        steps.append((matched, "แชทที่เคยคุยกับบอท",
                      ", ".join(f"{cid} ({name})" for cid, name in seen.items())
                      + ("" if matched else "  ← ไม่มีอันไหนตรงกับ TELEGRAM_CHAT_ID")))
    else:
        steps.append((False, "แชทที่เคยคุยกับบอท",
                      "ยังไม่มีใครทักบอทเลย หรือ getUpdates ถูก webhook ยึดไว้ — "
                      "เปิดแชทบอทแล้วกด Start / พิมพ์อะไรก็ได้หนึ่งข้อความ แล้วรันใหม่"))

    if send_test and chat_id:
        status, body = call_api(token, "sendMessage", {
            "chat_id": chat_id,
            "text": "✅ ทดสอบจาก run.py notify --check",
        })

        if status == 200:
            steps.append((True, "ส่งข้อความทดสอบ", "ส่งสำเร็จ — ไปดูในแชทได้เลย"))
        else:
            steps.append((False, "ส่งข้อความทดสอบ",
                          describe_api_error(status, body.get("description"))))

    return steps


def _chat_ids_from_updates(updates):
    """ดึง chat id ที่เคยคุยกับบอทออกจากผล getUpdates — ฟังก์ชันบริสุทธิ์ เทสได้"""
    found = {}

    for update in updates or []:
        for key in ("message", "edited_message", "channel_post", "my_chat_member"):
            chat = (update.get(key) or {}).get("chat")

            if not chat:
                continue

            name = chat.get("title") or chat.get("username") or chat.get("first_name") or "?"
            found[str(chat.get("id"))] = name

    return found


def _retry_after(response):
    """Telegram บอกเวลารอมาใน JSON — ถ้าอ่านไม่ได้ก็เดาสั้นๆ ไว้ก่อน"""
    try:
        return float(response.json()["parameters"]["retry_after"])
    except (ValueError, KeyError, TypeError):
        return 3.0
