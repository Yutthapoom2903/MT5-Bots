"""
ปฏิทินข่าวเศรษฐกิจ — ใช้หยุดเข้าไม้ใหม่รอบเวลาข่าวแรง

ทำไมต้องมี: ตอน FOMC/CPI/NFP ออก spread ถ่างเป็นสิบเท่าและราคากระชากสองทางก่อน
จะเลือกทาง  MAX_SPREAD_POINTS ที่มีอยู่กันได้ *หลัง* spread ถ่างแล้วเท่านั้น มันไม่รู้
ว่าอีกสองนาทีข้างหน้าจะมีตัวเลขออก  ตัวกรองนี้จึงไม่ได้ทำนายอะไรเลย แค่หลบช่วงที่
กลไกตลาดเสียรูปชั่วคราว

ทำไมไม่ใช้ปฏิทินของ MT5 เอง: MQL5 มี CalendarValueHistory() แต่แพ็กเกจ Python
ไม่ได้เปิดฟังก์ชันชุดนั้นออกมา ดึงจาก ForexFactory ผ่าน HTTP แทน

โมดูลนี้ไม่ import MetaTrader5 และฟังก์ชันที่ตัดสินใจทั้งหมดเป็นฟังก์ชันบริสุทธิ์
รับ list ของ event เข้าไปตรงๆ เทสจึงไม่ต้องต่อเน็ตและไม่ต้องต่อ terminal
เหมือนที่ strategy.py กับ notify.py ทำ

เวลาในโมดูลนี้เป็น UTC ทั้งหมด ไม่ใช่เวลาเซิร์ฟเวอร์ broker และไม่ใช่เวลาไทย
ตัวแปลงอยู่ที่ขอบเดียว — parse_events() แปลงเข้า, runner ส่ง datetime.now(timezone.utc)
เข้ามา  จงใจไม่ยุ่งกับ broker_gmt_offset ตรงนี้เลย เพราะช่วงห้ามเทรดคือ "ตอนนี้"
ไม่ใช่ "เวลาที่เขียนบนแท่ง" และการแปลงที่ไม่จำเป็นคือบั๊กที่รอเกิดตอน DST เปลี่ยน
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import requests

from bot import paths

# ---------- แหล่งข้อมูล ----------
# ฟีดรายสัปดาห์ของ ForexFactory ไม่ต้องใช้ API key
FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
HTTP_TIMEOUT = 20

# ForexFactory จำกัดการโหลดไฟล์รายสัปดาห์ไว้ 2 ครั้งต่อ 5 นาที ยิงถี่กว่านั้นโดนบล็อก
# ไฟล์เป็นข้อมูลทั้งสัปดาห์อยู่แล้ว โหลดวันละครั้งก็เกินพอ
REFRESH_HOURS = 6
MIN_REFRESH_SECONDS = 600      # กันไว้อีกชั้น ต่อให้มีใครตั้ง REFRESH_HOURS เป็น 0

# ---------- ข่าวแบบไหนที่นับ ----------
# ทองวิ่งตามดอลลาร์เป็นหลัก ข่าวสกุลอื่นสะเทือนน้อยกว่ามากจนไม่คุ้มจะหยุดเทรด
WATCH_CURRENCIES = ("USD",)
WATCH_IMPACTS = ("High",)

# ช่วงห้ามเข้าไม้ใหม่รอบเวลาข่าว (นาที)
MINUTES_BEFORE = 30
MINUTES_AFTER = 30

logger = logging.getLogger(__name__)


# ---------- ฝั่งบริสุทธิ์ ----------

def parse_events(payload, currencies=None, impacts=None):
    """
    แปลง JSON ดิบของฟีดเป็นรายการ event ที่กรองแล้ว เรียงตามเวลา

    แต่ละ event เป็น dict: time (datetime UTC), title, currency, impact
    แถวที่อ่านเวลาไม่ได้ถูกทิ้งเงียบๆ — ฟีดของคนอื่นจะเพิ่มหรือเปลี่ยนฟิลด์เมื่อไหร่
    ก็ได้ และข่าวหนึ่งบรรทัดที่อ่านไม่ออกไม่ควรทำให้ทั้งตัวกรองใช้ไม่ได้
    """
    currencies = WATCH_CURRENCIES if currencies is None else currencies
    impacts = WATCH_IMPACTS if impacts is None else impacts

    wanted_currency = {value.upper() for value in currencies}
    wanted_impact = {value.lower() for value in impacts}

    events = []

    for raw in payload or []:
        if not isinstance(raw, dict):
            continue

        currency = str(raw.get("country") or "").upper()
        impact = str(raw.get("impact") or "").lower()

        if currency not in wanted_currency or impact not in wanted_impact:
            continue

        when = _parse_time(raw.get("date"))

        if when is None:
            continue

        events.append({
            "time": when,
            "title": str(raw.get("title") or "").strip(),
            "currency": currency,
            "impact": str(raw.get("impact") or "").strip(),
        })

    events.sort(key=lambda event: event["time"])
    return events


def _parse_time(text):
    """อ่านเวลาแบบ ISO ที่มี offset ติดมา (เช่น 2026-09-16T14:00:00-04:00) ให้เป็น UTC"""
    if not text:
        return None

    try:
        when = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None

    # ฟีดใส่ offset มาเสมอ แต่ถ้าวันหนึ่งไม่ใส่ อย่าเดา — ถือว่าเป็น UTC ไปตรงๆ
    if when.tzinfo is None:
        return when.replace(tzinfo=timezone.utc)

    return when.astimezone(timezone.utc)


def window_for(event, before=None, after=None):
    """ช่วงเวลาห้ามเข้าไม้ของข่าวหนึ่งตัว คืน (เริ่ม, จบ) เป็น datetime UTC"""
    before = MINUTES_BEFORE if before is None else before
    after = MINUTES_AFTER if after is None else after

    return (
        event["time"] - timedelta(minutes=before),
        event["time"] + timedelta(minutes=after),
    )


def blocking_event(events, when, before=None, after=None):
    """
    ข่าวตัวแรกที่ครอบเวลา when อยู่ — None ถ้าไม่ติดอะไรเลย

    events ต้องเป็นผลจาก parse_events() แล้ว  when เป็น datetime UTC
    """
    if not events or when is None:
        return None

    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    for event in events:
        start, end = window_for(event, before, after)

        if start <= when <= end:
            return event

    return None


def describe(event):
    """ข้อความสั้นๆ สำหรับใส่ใน log, CSV และ Telegram"""
    if event is None:
        return ""

    return f"{event['currency']} {event['title']} {event['time'].strftime('%H:%M')} UTC"


def next_events(events, when, limit=5):
    """ข่าวถัดไปนับจากเวลา when ใช้พิมพ์ให้คนอ่านว่าคืนนี้มีอะไรรออยู่"""
    if when is None:
        return []

    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    return [event for event in events if event["time"] >= when][:limit]


def is_stale(fetched_at, now, hours=None):
    """ข้อมูลที่โหลดไว้เก่าเกินจะใช้ต่อหรือยัง — ไม่รู้ว่าโหลดเมื่อไหร่ถือว่าเก่า"""
    hours = REFRESH_HOURS if hours is None else hours

    if fetched_at is None:
        return True

    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)

    return (now - fetched_at) >= timedelta(hours=hours)


# ---------- ฝั่งที่แตะดิสก์กับเน็ต ----------

def read_cache(path=None):
    """
    อ่านไฟล์ที่โหลดเก็บไว้ คืน (events, fetched_at) — ([], None) ถ้าไม่มีหรืออ่านไม่ได้

    ไฟล์เสียหายถือเท่ากับไม่มีไฟล์ ไม่โยน error ขึ้นไป เพราะปลายทางคือลูปเทรด
    """
    path = paths.NEWS_CACHE if path is None else path

    try:
        with open(path, "r", encoding="utf-8") as handle:
            stored = json.load(handle)
    except (OSError, ValueError):
        return [], None

    if not isinstance(stored, dict):
        return [], None

    return parse_events(stored.get("events")), _parse_time(stored.get("fetched_at"))


def write_cache(payload, now, path=None):
    """เขียนทับไฟล์ที่โหลดเก็บไว้ — คืน True ถ้าเขียนสำเร็จ"""
    path = paths.NEWS_CACHE if path is None else path
    paths.ensure_parent(path)

    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"fetched_at": now.isoformat(), "events": payload}, handle)
    except (OSError, TypeError) as error:
        logger.warning("เขียนแคชปฏิทินข่าวไม่สำเร็จ: %s", error)
        return False

    return True


def download(url=None, timeout=HTTP_TIMEOUT):
    """โหลดฟีดมาดิบๆ คืน list หรือ None ถ้าโหลดไม่ได้ — ไม่โยน error ออกไปไหน"""
    url = FEED_URL if url is None else url

    try:
        response = requests.get(url, timeout=timeout,
                                headers={"User-Agent": "MT5-Bots/1.0"})
    except requests.RequestException as error:
        logger.warning("โหลดปฏิทินข่าวไม่สำเร็จ: %s", error)
        return None

    if not response.ok:
        logger.warning("ปฏิทินข่าวตอบ %s: %s", response.status_code, response.text[:200])
        return None

    try:
        payload = response.json()
    except ValueError as error:
        logger.warning("ปฏิทินข่าวส่งข้อมูลที่อ่านไม่ออก: %s", error)
        return None

    if not isinstance(payload, list):
        logger.warning("ปฏิทินข่าวส่งรูปแบบที่ไม่รู้จัก: %s", type(payload).__name__)
        return None

    return payload


class Calendar:
    """
    ตัวถือปฏิทินข่าวสำหรับลูปหลัก — เก็บไว้ในหน่วยความจำ โหลดใหม่เมื่อข้อมูลเก่า

    พฤติกรรมตอนพัง เรียงจากดีไปแย่:
        โหลดได้            ใช้ของใหม่
        โหลดไม่ได้ มีแคช    ใช้แคช ฟีดเป็นข้อมูลทั้งสัปดาห์ ของเมื่อวานยังใช้ได้
        โหลดไม่ได้ ไม่มีแคช  ปล่อยผ่าน พร้อม log WARNING

    ข้อสุดท้ายคือ fail-open และจงใจ: เน็ตบ้านหลุดไม่ควรทำให้บอทหยุดเทรดเงียบๆ
    ทั้งคืนโดยที่คนนอนอยู่ไม่รู้เรื่อง  ตัวกรองนี้เป็นของแถม ไม่ใช่ของที่ระบบ
    ความปลอดภัยแขวนอยู่ — ส่วนที่กันความเสียหายจริงคือ SL กับตัวตัดวงจร
    """

    def __init__(self, path=None, url=None):
        self.path = paths.NEWS_CACHE if path is None else path
        self.url = FEED_URL if url is None else url
        self.events = []
        self.fetched_at = None
        self.last_attempt = None
        self.loaded = False
        self.warned = False

    def _now(self):
        return datetime.now(timezone.utc)

    def refresh(self, now=None, force=False):
        """โหลดใหม่ถ้าถึงเวลา — คืน True ถ้าตอนนี้มีข้อมูลใช้งานได้"""
        now = self._now() if now is None else now

        if not self.loaded:
            self.events, self.fetched_at = read_cache(self.path)
            self.loaded = True

        if not force and not is_stale(self.fetched_at, now):
            return bool(self.events)

        # กันยิงถี่เกินขีดของฟีด แม้จะยังไม่มีข้อมูลเลยก็ตาม
        if self.last_attempt is not None:
            waited = (now - self.last_attempt).total_seconds()
            if waited < MIN_REFRESH_SECONDS:
                return bool(self.events)

        self.last_attempt = now
        payload = download(self.url)

        if payload is None:
            if self.events:
                logger.info("ใช้ปฏิทินข่าวจากแคชต่อไปก่อน (โหลดใหม่ไม่ผ่าน)")
            return bool(self.events)

        self.events = parse_events(payload)
        self.fetched_at = now
        self.warned = False
        write_cache(payload, now, self.path)
        logger.info("โหลดปฏิทินข่าวแล้ว: ข่าวแรง %d รายการในสัปดาห์นี้", len(self.events))

        return True

    def blocking(self, now=None, before=None, after=None):
        """
        ข่าวที่ครอบเวลานี้อยู่ — None แปลว่าเทรดได้

        ไม่มีข้อมูลเลยก็คืน None (ปล่อยผ่าน) พร้อมเตือนหนึ่งครั้งต่อหนึ่งช่วงที่ล่ม
        ไม่ใช่ทุกครั้งที่ถูกถาม — ลูปเดินทุก 30 วินาที เตือนทุกรอบคือ 2,880 บรรทัดต่อวัน
        ที่ไม่ได้บอกอะไรเพิ่มจากบรรทัดแรก  รีเซ็ตเมื่อโหลดสำเร็จ ครั้งหน้าที่ล่มจึงเตือนอีก
        """
        now = self._now() if now is None else now
        self.refresh(now)

        if not self.events:
            if not self.warned:
                logger.warning("ไม่มีข้อมูลปฏิทินข่าว ตัวกรองข่าวจึงปล่อยผ่านไปก่อน")
                self.warned = True
            return None

        return blocking_event(self.events, now, before, after)

    def upcoming(self, now=None, limit=5):
        now = self._now() if now is None else now
        self.refresh(now)
        return next_events(self.events, now, limit)


def status_lines(calendar, now=None):
    """สถานะปฏิทินข่าวแบบอ่านด้วยตา ใช้ทั้งตอนบอทเริ่มและใน run.py news"""
    now = datetime.now(timezone.utc) if now is None else now
    lines = []

    if not calendar.events:
        lines.append("ปฏิทินข่าว: ไม่มีข้อมูล — ตัวกรองข่าวจะปล่อยผ่านทุกแท่ง")
        return lines

    age = "ไม่ทราบ"
    if calendar.fetched_at is not None:
        hours = (now - calendar.fetched_at).total_seconds() / 3600
        age = f"{hours:.1f} ชม.ที่แล้ว"

    lines.append(f"ปฏิทินข่าว: {len(calendar.events)} รายการ (โหลดเมื่อ {age})")
    lines.append(f"ช่วงห้ามเข้าไม้: ก่อน {MINUTES_BEFORE} นาที ถึงหลัง {MINUTES_AFTER} นาที")

    blocked = blocking_event(calendar.events, now)
    if blocked is not None:
        lines.append(f"ตอนนี้ติดข่าว: {describe(blocked)}")

    for event in next_events(calendar.events, now, 5):
        local = event["time"] + timedelta(hours=7)
        lines.append(
            f"  {event['time'].strftime('%a %d %b %H:%M')} UTC "
            f"(ไทย {local.strftime('%H:%M')}) — {event['title']}"
        )

    return lines
