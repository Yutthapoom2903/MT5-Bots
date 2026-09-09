"""
ประตูเดียวของโปรเจกต์ — ทุกอย่างสั่งผ่านไฟล์นี้

    python run.py              ทำทุกอย่างให้จบในคำสั่งเดียว:
                               หา Symbol -> ตรวจความพร้อม -> จำลองย้อนหลัง
                               -> กวาดค่า -> เฝ้าดูตลาดสด (ไม่ส่งคำสั่ง)
    python run.py --trade      เหมือนข้างบน แต่ส่งคำสั่งจริงในขั้นสุดท้าย
    python run.py check        ตรวจการเชื่อมต่อ บัญชี และคำนวณความเสี่ยงให้ดู
    python run.py symbols      หาชื่อ Symbol จริงที่ broker ใช้
    python run.py signal       ดูคำตัดสินของบอทตอนนี้ครั้งเดียว พร้อมเหตุผลทุกข้อ
    python run.py watch        เฝ้าดูและบันทึกข้อมูลต่อเนื่อง ไม่ส่งคำสั่ง
    python run.py trade        เฝ้าดูและส่งคำสั่งจริง
    python run.py backtest     จำลองกลยุทธ์ย้อนหลังบนข้อมูลจริง
    python run.py sweep        กวาดหลายชุดค่าเพื่อดูว่าผลทนต่อการเปลี่ยนค่าไหม
    python run.py report       สรุปว่าบอททำอะไรไปบ้าง จากไฟล์ที่มันเขียนไว้
    python run.py review       สรุปผลจากข้อมูลที่คุณติดป้ายกำกับไว้เอง
    python run.py notify       ส่งตัวอย่างแจ้งเตือนครบทุกหมวดเข้า Telegram
    python run.py test         รันเทส logic (ไม่ต้องต่อ MT5)
"""

import argparse
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

# ไม่ import MetaTrader5 ที่ระดับโมดูล เพราะคำสั่ง backtest และ test ต้องรันได้
# บนเครื่องที่ไม่มีแพ็กเกจนี้ (เช่น WSL) แต่ละคำสั่งจะ import เองเมื่อจำเป็น


def _print_header(title):
    print(f"\n--- {title} ---")


def command_check(args):
    """ตรวจว่าทุกอย่างพร้อม และบอกตรงๆ ว่าทุนที่มีพอกับความเสี่ยงที่ตั้งไว้หรือไม่"""
    import MetaTrader5 as mt5
    import mt5_core as core
    import mt5_trade as trade
    import runner

    account = core.connect()
    _print_header("การเชื่อมต่อ")
    print(f"MT5 เวอร์ชัน: {mt5.version()}")
    print(f"บัญชี: {account.login} ({'Demo' if core.is_demo(account) else 'บัญชีจริง'})")
    print(f"Server: {account.server}")
    print(f"Balance: {account.balance:.2f} {account.currency}")
    print(f"Equity: {account.equity:.2f} | Leverage: 1:{account.leverage}")

    info = core.prepare_symbol(runner.SYMBOL)
    _print_header(f"Symbol {runner.SYMBOL}")
    print(f"จำนวนหลัก: {info.digits} | point: {info.point}")
    print(f"lot ต่ำสุด/สูงสุด/ก้าว: {info.volume_min} / {info.volume_max} / {info.volume_step}")
    print(f"ระยะ stop ขั้นต่ำ: {info.trade_stops_level} points")
    print(f"spread ตอนนี้: {core.spread_points(runner.SYMBOL)} points")

    offset = core.broker_gmt_offset(runner.SYMBOL)
    if offset is not None:
        print(f"เวลาเซิร์ฟเวอร์ broker: GMT{offset:+d} (ใช้ตั้ง SESSION_HOURS ใน strategy.py)")

    context, _ = runner.build_context()
    if context is None:
        print("\nดึงข้อมูลราคาไม่พอสำหรับคำนวณ — ลองใหม่อีกครั้งเมื่อตลาดเปิด")
        return

    atr = context["atr"]
    sl_distance = atr * runner.SL_ATR_MULT
    minimum_loss = trade.estimated_loss(info, info.volume_min, sl_distance)
    budget = trade.risk_budget(account, runner.RISK_PERCENT)

    _print_header("ความเสี่ยงจริงตามสภาพตลาดตอนนี้")
    print(f"ATR(14) M15: {atr:.2f}")
    print(f"ระยะ SL ที่จะใช้: {sl_distance:.2f} ({runner.SL_ATR_MULT}x ATR)")
    print(f"ไม้เล็กสุด {info.volume_min} lot เสี่ยง: {minimum_loss:.2f} {account.currency}")
    print(f"งบเสี่ยงต่อไม้ที่ตั้งไว้ ({runner.RISK_PERCENT}%): {budget:.2f} {account.currency}")

    if minimum_loss > budget:
        needed = minimum_loss / (runner.RISK_PERCENT / 100)
        actual_percent = minimum_loss / account.balance * 100
        print(
            f"\nทุนไม่พอกับความเสี่ยงที่ตั้งไว้: ไม้เล็กสุดคิดเป็น {actual_percent:.2f}% ของพอร์ต\n"
            f"ถ้าต้องการเสี่ยง {runner.RISK_PERCENT}% จริงๆ ต้องมีทุนราว {needed:,.0f} {account.currency}\n"
            f"ตอนนี้บอทจะข้ามทุกสัญญาณ จนกว่าจะเพิ่มทุน ลด SL_ATR_MULT "
            f"หรือเปิด ALLOW_RISK_OVER_BUDGET ใน runner.py"
        )
    else:
        lots = trade.calculate_lot(info, account, sl_distance, runner.RISK_PERCENT)
        print(f"\nทุนพอ — ขนาดไม้ที่จะใช้ตอนนี้: {lots} lot")


def command_symbols(args):
    import MetaTrader5 as mt5
    import mt5_core as core

    core.connect()
    keywords = tuple(word.upper() for word in (args.keywords or ["XAU", "GOLD", "EURUSD", "BTC"]))

    symbols = mt5.symbols_get()
    if symbols is None:
        raise core.MT5Error(f"ดึงรายชื่อ Symbol ไม่สำเร็จ: {core.last_error_text()}")

    for symbol in symbols:
        if any(keyword in symbol.name.upper() for keyword in keywords):
            print(symbol.name)


def command_signal(args):
    """คำตัดสินครั้งเดียวพร้อมเหตุผลครบทุกข้อ"""
    import mt5_core as core
    import runner
    import strategy

    core.connect()
    core.prepare_symbol(runner.SYMBOL)

    context, _ = runner.build_context()
    if context is None:
        raise core.MT5Error(f"ข้อมูลแท่งราคาไม่พอ: {core.last_error_text()}")

    _print_header(f"{runner.SYMBOL} แท่ง M15 ที่ปิดล่าสุด {context['candle_time']}")
    print(f"Close {context['close']:.2f} | MA{runner.FAST_MA} {context['ma_fast']:.2f} "
          f"| MA{runner.SLOW_MA} {context['ma_slow']:.2f}")
    print(f"RSI {context['rsi']:.1f} | ADX {context['adx']:.1f} | ATR {context['atr']:.2f} "
          f"| spread {context['spread_points']}")
    print(f"เทรนด์ H1 {context['h1_trend']} | M5 {context['m5_trend']}")

    _print_header("คำตัดสิน")
    print(strategy.evaluate(context).report())


def _run_loop(trade_enabled):
    """
    เรียกลูปหลักโดยแจ้ง Telegram เสมอว่าจบด้วยเหตุใด

    บอทที่ตายเงียบคือบอทที่แย่ที่สุด โดยเฉพาะตอนถือไม้อยู่แล้วไม่มีใครขยับ SL ให้
    """
    import runner

    try:
        runner.run(trade_enabled=trade_enabled)
    except KeyboardInterrupt:
        runner.NOTIFIER.bot_stopped(runner.SYMBOL, "ผู้ใช้สั่งหยุด (Ctrl+C)")
        raise
    except Exception as error:
        runner.NOTIFIER.crashed(runner.SYMBOL, f"{type(error).__name__}: {error}")
        raise


def command_watch(args):
    _run_loop(trade_enabled=False)


def command_trade(args):
    _run_loop(trade_enabled=True)


def command_notify(args):
    """
    ส่งตัวอย่างของทุกหมวดเข้า Telegram — ดูหน้าตาข้อความจริงก่อนปล่อยบอทรันยาว

    ไม่ต้องต่อ MT5 เพราะข้อมูลเป็นของสมมติทั้งหมด ใช้ --dry เพื่อพิมพ์ลงจอแทนการส่ง
    """
    import logging

    from dotenv import load_dotenv

    import notify
    import strategy

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logger = logging.getLogger()

    if args.check:
        return _diagnose_notifications(notify)

    if args.dry:
        def transport(message, quiet=False):
            print(f"\n{'-' * 62}\n{'[เงียบ] ' if quiet else ''}{message}")
            return True

        # โหมด dry ไม่ได้ต่อเน็ต แต่ต้องหลอกให้ Notifier คิดว่าตั้งค่าครบ ไม่งั้นมันเงียบ
        token, chat_id = "dry-run", "dry-run"
    else:
        transport = None
        token, chat_id = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")

    sender = notify.Notifier(token, chat_id, logger, transport)

    if not sender.configured:
        print("ยังไม่ได้ตั้ง TELEGRAM_TOKEN / TELEGRAM_CHAT_ID ใน .env — ลองด้วย --dry ก่อนได้")
        return 1

    sent = _sample_notifications(sender, strategy)

    print(f"\nส่งไปทั้งหมด {len(sent)} ข้อความ (ข้ามเพราะซ้ำ {sender.skipped})")
    print("หมวดที่เปิดอยู่: " + ", ".join(notify.active_categories()))
    print("ปิด/เปิดรายหมวดได้ที่สวิตช์ SEND_* บนหัวไฟล์ notify.py")
    return 0


def _diagnose_notifications(notify):
    """ไล่บอกทีละข้อว่าโซ่การแจ้งเตือนขาดตรงไหน แล้วจบด้วยสถานะของสวิตช์หมวด"""
    from dotenv import find_dotenv

    found = find_dotenv(usecwd=True)
    print(f"ไฟล์ .env: {found or 'ไม่พบในโฟลเดอร์นี้หรือโฟลเดอร์แม่'}")

    steps = notify.diagnose(os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID"))

    for passed, title, detail in steps:
        print(f"  [{'ผ่าน  ' if passed else 'ไม่ผ่าน'}] {title}: {detail}")

    print()
    print("หมวดที่เปิดอยู่: " + ", ".join(notify.active_categories()))
    print(f"แจ้งทุกแท่งที่ไม่มีสัญญาณ (SEND_HOLD): {'เปิด' if notify.SEND_HOLD else 'ปิด'}")
    print(f"แจ้งตอนเกือบเข้า (SEND_NEAR_MISS): {'เปิด' if notify.SEND_NEAR_MISS else 'ปิด'}")

    if not notify.SEND_HOLD:
        print("\nถ้าทุกแท่งเป็น HOLD และ SEND_HOLD ปิดอยู่ การเงียบคือพฤติกรรมที่ถูกต้อง")
        print("อยากได้ข้อความทุกแท่งเพื่อพิสูจน์ว่าบอทยังหายใจ ตั้ง SEND_HOLD = True ใน notify.py")

    return 0 if all(passed for passed, _, _ in steps) else 1


def _sample_notifications(sender, strategy):
    """เรียกครบทุกชนิดข้อความด้วยข้อมูลสมมติ — ถ้าชนิดไหนพัง จะพังตรงนี้ก่อนออกสนามจริง"""
    symbol = "XAUUSD"
    context = {
        "candle_time": "2026-09-09 19:15:00", "close": 4401.62, "rsi": 48.4,
        "adx": 24.6, "atr": 12.74, "spread_points": 34.0,
        "h1_trend": "DOWNTREND", "m5_trend": "DOWNTREND",
        "m15_signal": "SELL", "fast_ma": 20, "slow_ma": 50,
    }
    decision = strategy.evaluate(context)
    blocked = strategy.evaluate(dict(context, adx=11.2, spread_points=64.0))
    summary = {"trades": 3, "wins": 2, "losses": 1, "profit": 41.20, "consecutive_losses": 0}
    meta = {"signal": "SELL", "entry": 4401.62, "volume": 0.01,
            "risk_money": 19.10, "currency": "USD", "opened_at": "2026-09-09 19:20:00"}

    sender.bot_started(
        symbol, "12345678 · Demo · 1,000.00 USD", "เฝ้าดูอย่างเดียว ไม่ส่งคำสั่ง",
        strategy.active_filters(), "1.0% ต่อไม้ · SL 1.5xATR · TP 3.0xATR",
        "เสมอทุนที่ 1.0R, ไล่ stop จาก 1.5R ห่าง 2.0xATR",
        "ตัวตัดวงจร: ขาดทุน 3.0%/วัน · 5 ไม้/วัน · แพ้ติดกัน 3 ไม้",
    )
    sender.connection_lost(symbol)
    sender.reconnected(symbol)
    sender.market_closed(symbol, 300)
    sender.market_reopened(symbol)
    sender.candle_verdict(decision, context, symbol, adx_min=strategy.ADX_MIN, watch_mode=True)
    sender.candle_verdict(blocked, dict(context, adx=11.2, spread_points=64.0), symbol,
                          adx_min=strategy.ADX_MIN, watch_mode=True)
    sender.entry_filled(symbol, "SELL", 0.01, 4401.62, 4420.73, 4363.40, "19.10", "USD", 987654)
    sender.entry_failed(symbol, "SELL", "retcode 10030: Unsupported filling mode")
    sender.stop_moved(symbol, 987654, 4420.73, 4399.71, 4401.62, 4382.51, "เสมอทุนแล้ว")
    sender.partial_taken(symbol, 987654, 0.05, 0.10, 1.0)
    sender.partial_too_small(symbol, 987654, 0.01, 1.0)
    sender.closed_on_reverse(symbol, 987654, "BUY")
    sender.position_closed(symbol, 987654, meta, 38.20, "USD", 4363.40)
    sender.entry_over_budget(symbol, "SELL", 19.10, 10.00, "USD", 1910)
    sender.halted(symbol, "แพ้ติดกัน 3 ไม้ หยุดพักถึงพรุ่งนี้", summary)
    sender.resumed(symbol, "2026-09-10")
    sender.daily_summary(symbol, "2026-09-09", summary, 1041.20, "USD")
    sender.heartbeat(symbol, 1041.20, "USD", [1], summary, "2026-09-09 19:15:00", 47100)
    sender.bot_stopped(symbol, "ผู้ใช้สั่งหยุด (Ctrl+C)")

    return list(sender.sent)


def command_report(args):
    """สรุปการทำงานจากไฟล์ที่บอทเขียนไว้ ไม่ต้องต่อ MT5"""
    import report
    print(report.build_report())


def command_sweep(args):
    """กวาดหลายชุดค่า ดูว่าผลลัพธ์ทนต่อการเปลี่ยนค่าหรือแค่ฟลุค"""
    import backtest
    import runner

    m15, h1, m5 = _load_history(args.months)

    grid = dict(backtest.DEFAULT_GRID)
    if args.quick:
        grid = {"sl_atr_mult": (1.0, 1.5, 2.0), "tp_atr_mult": (2.0, 3.0, 4.0)}

    total = 1
    for values in grid.values():
        total *= len(values)
    print(f"กำลังกวาด {total} ชุดค่า ...\n")

    rows = backtest.sweep(m15, h1, m5, {"spread_points": args.spread}, grid)
    print(backtest.format_sweep(rows, top=args.top))
    print("\nอย่าหยิบค่าที่ดีที่สุดไปใช้ตรงๆ ค่าที่อยู่กลางย่านที่กำไรทั้งย่านทนกว่ามาก")


def command_review(args):
    import backtest_engine
    backtest_engine.run_backtest(args.csv)


def _load_history(months):
    """ดึงข้อมูลย้อนหลังทุก timeframe ที่กลยุทธ์ใช้"""
    import mt5_core as core
    import runner

    core.connect()
    core.prepare_symbol(runner.SYMBOL)

    # จำนวนแท่งต่อเดือนโดยประมาณของแต่ละ timeframe
    per_month = {"m15": 2880, "h1": 720, "m5": 8640}

    print(f"กำลังดึงข้อมูลย้อนหลัง {months} เดือนของ {runner.SYMBOL} ...")

    m15 = core.get_rates(runner.SYMBOL, runner.ENTRY_TIMEFRAME, per_month["m15"] * months, 200)
    h1 = core.get_rates(runner.SYMBOL, runner.TREND_TIMEFRAME, per_month["h1"] * months, 60)
    m5 = core.get_rates(runner.SYMBOL, runner.CONFIRM_TIMEFRAME, per_month["m5"] * months, 60)

    if m15 is None:
        raise core.MT5Error(f"ดึงข้อมูลย้อนหลังไม่สำเร็จ: {core.last_error_text()}")

    print(f"ได้ M15 {len(m15)} แท่ง, H1 {len(h1) if h1 is not None else 0}, "
          f"M5 {len(m5) if m5 is not None else 0}\n")

    return m15, h1, m5


def command_backtest(args):
    """จำลองกลยุทธ์ย้อนหลังบนข้อมูลจริงจาก MT5"""
    import backtest
    import runner

    m15, h1, m5 = _load_history(args.months)

    overrides = {
        "sl_atr_mult": runner.SL_ATR_MULT,
        "tp_atr_mult": runner.TP_ATR_MULT,
        "use_breakeven": runner.USE_BREAKEVEN,
        "breakeven_at_r": runner.BREAKEVEN_AT_R,
        "breakeven_buffer_r": runner.BREAKEVEN_BUFFER_R,
        "use_trailing": runner.USE_TRAILING,
        "trail_start_r": runner.TRAIL_START_R,
        "trail_atr_mult": runner.TRAIL_ATR_MULT,
        "spread_points": args.spread,
    }

    if args.no_compare:
        print(backtest.format_report(backtest.simulate(m15, h1, m5, overrides)))
        return

    with_filters, without = backtest.compare(m15, h1, m5, overrides)

    print(backtest.format_report(without, "crossover เปล่าๆ ไม่มีตัวกรอง"))
    print()
    print(backtest.format_report(with_filters, "ใส่ตัวกรองหลาย timeframe"))
    print()
    _print_verdict(backtest.metrics(without), backtest.metrics(with_filters))


def _print_verdict(plain, filtered):
    """บอกตรงๆ ว่าตัวกรองช่วยจริงหรือแค่ทำให้เทรดน้อยลง"""
    _print_header("ตัวกรองช่วยไหม")

    if not plain.get("trades") or not filtered.get("trades"):
        print("ข้อมูลไม่พอให้เทียบ")
        return

    print(f"จำนวนไม้: {plain['trades']} -> {filtered['trades']}")
    print(f"คาดหวังต่อไม้: {plain['expectancy_r']:+.3f}R -> {filtered['expectancy_r']:+.3f}R")
    print(f"กำไรรวม: {plain['total_r']:+.2f}R -> {filtered['total_r']:+.2f}R")
    print(f"Drawdown: {plain['max_drawdown_r']:.2f}R -> {filtered['max_drawdown_r']:.2f}R")

    better_per_trade = filtered["expectancy_r"] > plain["expectancy_r"]
    better_total = filtered["total_r"] > plain["total_r"]

    if better_per_trade and better_total:
        print("\nตัวกรองช่วยทั้งคุณภาพต่อไม้และกำไรรวม")
    elif better_per_trade:
        print("\nตัวกรองทำให้แต่ละไม้ดีขึ้น แต่กำไรรวมลดเพราะเทรดน้อยลง")
        print("ถ้ารับ drawdown ไหว การเทรดถี่กว่าอาจให้กำไรรวมมากกว่า")
    else:
        print("\nตัวกรองยังไม่ช่วย ลองปิดทีละตัวใน strategy.py แล้วรันใหม่")

    print("\nอย่าเชื่อผลนี้ทั้งหมด อ่านข้อจำกัดในหัวไฟล์ backtest.py ก่อน")


def command_test(args):
    return subprocess.call([sys.executable, os.path.join(REPO_ROOT, "tests", "test_logic.py")])


def _phase(number, total, title):
    print(f"\n{'=' * 62}")
    print(f"[{number}/{total}] {title}")
    print("=" * 62)


def _try_phase(number, total, title, function, args):
    """
    รันขั้นตอนหนึ่งโดยไม่ให้ความล้มเหลวหยุดทั้งชุด

    ขั้นตอนวิเคราะห์ (จำลองย้อนหลัง/กวาดค่า) ล้มได้ถ้าข้อมูลย้อนหลังไม่พอ
    ซึ่งไม่ควรทำให้การเฝ้าดูตลาดสดไม่ได้เริ่ม
    """
    import mt5_core as core

    _phase(number, total, title)

    try:
        function(args)
        return True
    except core.MT5Error as error:
        print(f"ข้ามขั้นนี้: {error}")
    except Exception as error:
        print(f"ข้ามขั้นนี้เพราะเกิดข้อผิดพลาด: {type(error).__name__}: {error}")

    return False


def _auto_symbol(args):
    """หาชื่อ Symbol ที่ broker ใช้จริงแล้วตั้งให้ทั้งโปรเจกต์ใช้ตัวเดียวกัน"""
    import mt5_core as core
    import runner

    core.connect()
    resolved, candidates = core.resolve_symbol(runner.SYMBOL)

    if not candidates:
        print(f"ใช้ Symbol: {resolved}")
        return resolved

    print(f"broker นี้ไม่มี {runner.SYMBOL} — เลือกใช้ {resolved}")

    others = [name for name in candidates if name != resolved]
    if others:
        print(f"ตัวเลือกอื่นที่เจอ: {', '.join(others[:6])}")

    print(f"ถ้าไม่ถูก แก้ SYMBOL ใน runner.py เป็นชื่อที่ต้องการ แล้วรันใหม่")
    runner.SYMBOL = resolved
    return resolved


def command_all(args):
    """คำสั่งเดียวจบ — หา Symbol ตรวจความพร้อม วิเคราะห์ย้อนหลัง แล้วเฝ้าดูสด"""
    import runner

    total = 3 if args.skip_backtest else 5

    _phase(1, total, "หา Symbol ที่ broker ใช้")
    _auto_symbol(args)

    _try_phase(2, total, "ตรวจความพร้อมและความเสี่ยง", command_check, args)

    step = 3
    if not args.skip_backtest:
        _try_phase(3, total, "จำลองย้อนหลัง — กลยุทธ์นี้เคยทำเงินได้ไหม",
                   command_backtest, args)
        _try_phase(4, total, "กวาดค่า — ผลทนต่อการเปลี่ยนค่าหรือแค่ฟลุค",
                   command_sweep, args)
        step = 5

    _phase(step, total, "เทรดสด" if args.trade else "เฝ้าดูตลาดสด (ไม่ส่งคำสั่ง)")

    if not args.trade:
        print("โหมดเฝ้าดู ไม่มีการส่งคำสั่งซื้อขาย")
        print("อยากให้เทรดจริงใช้: python run.py --trade")

    print("บันทึกทุกอย่างลง bot.log — เช้ามาสรุปด้วย: python run.py report")
    print("กด Ctrl+C เพื่อหยุด\n")

    _run_loop(trade_enabled=args.trade)


def build_parser():
    parser = argparse.ArgumentParser(
        description="บอท XAUUSD MA crossover หลาย timeframe",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # พิมพ์ run.py เปล่าๆ ต้องทำงานได้ทันที จึงต้องมี default ของทุก flag ที่ all ใช้
    parser.set_defaults(
        command=None, trade=False, months=6, spread=30.0,
        top=15, quick=False, skip_backtest=False, no_compare=False,
        csv="market_training_data.csv", keywords=None, dry=False, check=False,
    )
    parser.add_argument("--trade", action="store_true", help="ส่งคำสั่งจริงในขั้นสุดท้าย")
    parser.add_argument("--skip-backtest", action="store_true",
                        help="ข้ามการจำลองย้อนหลังและการกวาดค่า เข้าเฝ้าดูเลย")

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("check", help="ตรวจการเชื่อมต่อ บัญชี และความเสี่ยง")

    symbols = subparsers.add_parser("symbols", help="หาชื่อ Symbol ที่ broker ใช้")
    symbols.add_argument("keywords", nargs="*", help="คำค้น เช่น XAU GOLD")

    subparsers.add_parser("signal", help="คำตัดสินตอนนี้ครั้งเดียว")
    subparsers.add_parser("watch", help="เฝ้าดูและบันทึก ไม่ส่งคำสั่ง")
    subparsers.add_parser("trade", help="เฝ้าดูและส่งคำสั่งจริง")

    simulate = subparsers.add_parser("backtest", help="จำลองกลยุทธ์ย้อนหลังบนข้อมูลจริง")
    simulate.add_argument("--months", type=int, default=6, help="ย้อนหลังกี่เดือน (ค่าเริ่มต้น 6)")
    simulate.add_argument("--spread", type=float, default=30.0, help="spread สมมติเป็น points")
    simulate.add_argument("--no-compare", action="store_true", help="ไม่ต้องเทียบกับ crossover เปล่า")

    sweep = subparsers.add_parser("sweep", help="กวาดหลายชุดค่าเพื่อดูความทนของผล")
    sweep.add_argument("--months", type=int, default=6)
    sweep.add_argument("--spread", type=float, default=30.0)
    sweep.add_argument("--top", type=int, default=15, help="แสดงกี่แถว")
    sweep.add_argument("--quick", action="store_true", help="กวาดเฉพาะ SL/TP ไม่รวม ADX")

    subparsers.add_parser("report", help="สรุปว่าบอททำอะไรไปบ้าง")

    review = subparsers.add_parser("review", help="สรุปผลจากข้อมูลที่คุณติดป้ายเอง")
    review.add_argument("csv", nargs="?", default="market_training_data.csv")

    sample = subparsers.add_parser("notify", help="ส่งตัวอย่างแจ้งเตือนครบทุกหมวด")
    sample.add_argument("--dry", action="store_true", help="พิมพ์ลงจอแทนการส่งจริง")
    sample.add_argument("--check", action="store_true",
                        help="วินิจฉัยว่าทำไมแจ้งเตือนไม่มา แทนการส่งตัวอย่าง")

    subparsers.add_parser("test", help="รันเทส logic")

    every = subparsers.add_parser("all", help="ทำทุกอย่างในคำสั่งเดียว (ค่าเริ่มต้น)")
    every.add_argument("--trade", action="store_true", help="ส่งคำสั่งจริงในขั้นสุดท้าย")
    every.add_argument("--skip-backtest", action="store_true", help="ข้ามการวิเคราะห์ย้อนหลัง")
    every.add_argument("--months", type=int, default=6)
    every.add_argument("--spread", type=float, default=30.0)
    every.add_argument("--top", type=int, default=10)
    every.add_argument("--quick", action="store_true")

    return parser


COMMANDS = {
    "check": command_check,
    "symbols": command_symbols,
    "signal": command_signal,
    "watch": command_watch,
    "trade": command_trade,
    "backtest": command_backtest,
    "sweep": command_sweep,
    "report": command_report,
    "review": command_review,
    "notify": command_notify,
    "test": command_test,
    "all": command_all,
}

# คำสั่งที่ไม่ต้องต่อ MT5 จึงไม่ต้อง shutdown
OFFLINE_COMMANDS = {"review", "report", "test", "notify"}


def main():
    parser = build_parser()
    args = parser.parse_args()

    command = args.command or "all"

    if command in OFFLINE_COMMANDS:
        return COMMANDS[command](args) or 0

    import MetaTrader5 as mt5
    import mt5_core as core

    try:
        return COMMANDS[command](args) or 0
    except core.MT5Error as error:
        print(error)
        return 1
    except KeyboardInterrupt:
        print("\nหยุดแล้ว")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
