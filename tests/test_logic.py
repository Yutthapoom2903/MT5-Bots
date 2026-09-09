"""
เทส logic ที่ไม่ต้องต่อ MT5 จริง

รันได้สองแบบ:
    python tests/test_logic.py     (ไม่ต้องติดตั้งอะไรเพิ่ม)
    pytest tests/                  (ถ้ามี pytest อยู่แล้ว)

ครอบคลุมส่วนที่พังเงียบแล้วเสียเงิน: กฎ crossover ต้องใช้แท่งที่ปิดแล้วเท่านั้น
และการคำนวณค่าที่ต้องผ่านการตรวจของ broker (ปัดราคา ปัด lot ระยะ stop ขั้นต่ำ)
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

# ใช้ stub ต่อเมื่อเครื่องนี้ไม่มีแพ็กเกจ MetaTrader5 จริง
try:
    import MetaTrader5  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(REPO_ROOT, "tests", "stubs"))

import pandas as pd
import MetaTrader5 as mt5

import mt5_core as core
import mt5_trade as trade
import strategy


# ---------- ตัวช่วย ----------

class FakeSymbolInfo:
    """ค่าประมาณของ XAUUSD จาก broker ทั่วไป: 2 หลัก, point 0.01, IOC อย่างเดียว"""
    digits = 2
    point = 0.01
    volume_min = 0.01
    volume_max = 50.0
    volume_step = 0.01
    trade_stops_level = 30
    trade_tick_value = 1.0
    trade_tick_size = 0.01
    filling_mode = 2
    trade_mode = mt5.SYMBOL_TRADE_MODE_FULL


class FakeTick:
    ask = 4390.26
    bid = 4390.00


class FakeAccount:
    balance = 10000.0
    currency = "USD"
    trade_mode = mt5.ACCOUNT_TRADE_MODE_DEMO


def ma_frame(fast_values, slow_values):
    return pd.DataFrame({"ma_fast": fast_values, "ma_slow": slow_values})


# ---------- กฎสัญญาณ ----------

def test_crossover_up_on_closed_candle_gives_buy():
    # -3 อยู่ใต้, -2 ตัดขึ้นเหนือ
    df = ma_frame([1, 1, 1, 5, 99], [2, 2, 2, 2, 2])
    assert core.crossover_signal(df) == "BUY"


def test_crossover_down_on_closed_candle_gives_sell():
    df = ma_frame([3, 3, 3, 1, 99], [2, 2, 2, 2, 2])
    assert core.crossover_signal(df) == "SELL"


def test_forming_candle_never_creates_a_signal():
    """แท่ง -1 ตัดขึ้นชัดเจน แต่ยังไม่ปิด จึงต้องได้ HOLD"""
    df = ma_frame([1, 1, 1, 1, 99], [2, 2, 2, 2, 2])
    assert core.crossover_signal(df) == "HOLD"


def test_touching_without_crossing_is_hold():
    df = ma_frame([1, 1, 2, 2, 2], [2, 2, 2, 2, 2])
    assert core.crossover_signal(df) == "HOLD"


def test_nan_warmup_rows_are_hold():
    df = ma_frame([1, 1, float("nan"), 5, 9], [2, 2, 2, 2, 2])
    assert core.crossover_signal(df) == "HOLD"


def test_short_frame_is_hold():
    df = ma_frame([1, 5], [2, 2])
    assert core.crossover_signal(df) == "HOLD"


# ---------- indicator ----------

def _sample_closes():
    return pd.Series([100 + (i % 7) - 3 for i in range(60)], dtype=float)


def test_rsi_stays_within_bounds():
    rsi = core.calculate_rsi(_sample_closes(), 14).dropna()
    assert ((rsi >= 0) & (rsi <= 100)).all()


def test_rsi_is_high_when_price_only_rises():
    rising = pd.Series(range(100, 160), dtype=float)
    assert core.calculate_rsi(rising, 14).iloc[-1] > 95


def test_atr_is_positive():
    closes = _sample_closes()
    ohlc = pd.DataFrame({"high": closes + 2, "low": closes - 2, "close": closes})
    assert (core.calculate_atr(ohlc, 14).dropna() > 0).all()


# ---------- การปัดค่าให้ broker ยอมรับ ----------

def test_price_is_rounded_to_symbol_digits():
    assert trade.normalize_price(FakeSymbolInfo(), 4390.123456) == 4390.12


def test_volume_rounds_down_to_step():
    """ต้องปัดลง ไม่ใช่ปัดใกล้สุด เพื่อไม่ให้ความเสี่ยงเกินที่ตั้งไว้"""
    assert trade.normalize_volume(FakeSymbolInfo(), 0.1789) == 0.17


def test_volume_is_clamped_to_broker_limits():
    info = FakeSymbolInfo()
    assert trade.normalize_volume(info, 0.0001) == info.volume_min
    assert trade.normalize_volume(info, 999) == info.volume_max


def test_min_stop_distance_covers_spread_when_wider_than_stops_level():
    # stops_level 30 point = 0.30 แต่ spread 0.26 x3 = 0.78 จึงต้องได้ 0.78
    assert abs(trade.min_stop_distance(FakeSymbolInfo(), FakeTick()) - 0.78) < 1e-9


def test_min_stop_distance_falls_back_when_broker_reports_zero():
    """broker ที่คืน trade_stops_level = 0 ใช้ระยะ dynamic ต้องไม่ได้ 0"""
    class ZeroLevel(FakeSymbolInfo):
        trade_stops_level = 0

    assert trade.min_stop_distance(ZeroLevel(), FakeTick()) > 0


def test_filling_mode_follows_symbol_bitmask():
    modes = trade.pick_filling_modes(FakeSymbolInfo())
    assert modes[0] == mt5.ORDER_FILLING_IOC
    # ต้องมีตัวสำรองไว้ลองต่อเมื่อโดน retcode 10030
    assert len(modes) > 1


# ---------- ขนาดไม้และความเสี่ยง ----------

def test_lot_is_sized_from_risk_and_stop_distance():
    # ATR 10.87 x 1.5 = 16.305 ; เสี่ยง 0.5% ของ 10000 = 50 USD
    # ขาดทุนต่อ 1 lot = (16.305 / 0.01) * 1.0 = 1630.5 -> 50/1630.5 = 0.0306 -> 0.03
    lots = trade.calculate_lot(FakeSymbolInfo(), FakeAccount(), 10.87 * 1.5, 0.5)
    assert lots == 0.03


def test_estimated_loss_never_exceeds_risk_budget():
    info, account = FakeSymbolInfo(), FakeAccount()
    sl_distance = 10.87 * 1.5
    lots = trade.calculate_lot(info, account, sl_distance, 0.5)

    budget = account.balance * 0.005
    assert trade.estimated_loss(info, lots, sl_distance) <= budget


def test_wider_stop_gives_smaller_lot():
    info, account = FakeSymbolInfo(), FakeAccount()
    narrow = trade.calculate_lot(info, account, 10.0, 1.0)
    wide = trade.calculate_lot(info, account, 40.0, 1.0)
    assert wide < narrow


def test_lot_falls_back_to_minimum_on_unusable_inputs():
    info = FakeSymbolInfo()
    assert trade.calculate_lot(info, FakeAccount(), 0, 0.5) == info.volume_min


def test_old_fixed_point_stop_was_inside_the_noise():
    """
    บันทึกเหตุผลที่เลิกใช้ SL แบบจุดคงที่

    ของเดิมคือ 300 * point = 3.00 USD บน XAUUSD ขณะที่ ATR จริงราว 10.87
    เท่ากับกัน stop ไว้แค่ 0.28 ATR ซึ่งโดน noise กินแทบทุกไม้
    """
    old_stop = 300 * FakeSymbolInfo.point
    observed_atr = 10.87

    assert old_stop == 3.0
    assert old_stop < observed_atr * 0.5


# ---------- state ข้ามการ restart ----------

def test_state_round_trips_through_disk(tmp_path=None):
    import tempfile

    directory = str(tmp_path) if tmp_path else tempfile.mkdtemp()
    path = os.path.join(directory, "state.json")

    assert core.load_state(path) == {}

    core.save_state(path, {"last_candle_time": "2026-09-08 19:15:00"})
    assert core.load_state(path)["last_candle_time"] == "2026-09-08 19:15:00"


def test_corrupt_state_file_is_ignored(tmp_path=None):
    import tempfile

    directory = str(tmp_path) if tmp_path else tempfile.mkdtemp()
    path = os.path.join(directory, "state.json")

    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{ไม่ใช่ json")

    assert core.load_state(path) == {}


# ---------- ADX ----------

def test_adx_is_high_in_a_clean_trend():
    closes = pd.Series(range(100, 200), dtype=float)
    df = pd.DataFrame({"high": closes + 1, "low": closes - 1, "close": closes})
    assert core.calculate_adx(df, 14).iloc[-1] > 40


def test_adx_is_low_in_chop():
    """ตลาดเด้งขึ้นลงสลับ ADX ต้องต่ำ — นี่คือสภาพที่ตัวกรองต้องจับได้"""
    closes = pd.Series([100 + (1 if i % 2 else -1) for i in range(120)], dtype=float)
    df = pd.DataFrame({"high": closes + 0.5, "low": closes - 0.5, "close": closes})
    assert core.calculate_adx(df, 14).iloc[-1] < 25


def test_ma_trend_reads_the_closed_candle():
    df = pd.DataFrame({"ma_fast": [1, 1, 1, 5, 0], "ma_slow": [2, 2, 2, 2, 2]})
    assert core.ma_trend(df) == "UPTREND"


# ---------- งบความเสี่ยงกับพอร์ตเล็ก ----------

def test_minimum_lot_can_exceed_the_risk_budget_on_a_small_account():
    """
    พอร์ต 1000 USD เสี่ยง 0.5% = งบ 5 USD
    แต่ไม้เล็กสุด 0.01 lot กับ SL 16.3 USD เสี่ยงจริง 16.3 USD
    ต้องตรวจจับได้ ไม่ใช่ปล่อยให้ normalize_volume ปัดขึ้นแล้วเทรดเกินงบเงียบๆ
    """
    class SmallAccount(FakeAccount):
        balance = 1000.0

    info = FakeSymbolInfo()
    sl_distance = 10.87 * 1.5

    assert trade.lot_exceeds_budget(info, SmallAccount(), sl_distance, 0.5)
    # normalize_volume ยังปัดขึ้นถึง volume_min อยู่ดี — จึงต้องมีตัวตรวจแยก
    assert trade.calculate_lot(info, SmallAccount(), sl_distance, 0.5) == info.volume_min


def test_budget_is_fine_on_a_large_enough_account():
    info = FakeSymbolInfo()
    assert not trade.lot_exceeds_budget(info, FakeAccount(), 10.87 * 1.5, 1.0)


def test_risk_budget_is_a_percentage_of_balance():
    assert trade.risk_budget(FakeAccount(), 1.0) == 100.0


# ---------- เครื่องตัดสินใจหลาย timeframe ----------

def _passing_context(signal="BUY"):
    return {
        "m15_signal": signal,
        "h1_trend": "UPTREND" if signal == "BUY" else "DOWNTREND",
        "m5_trend": "UPTREND" if signal == "BUY" else "DOWNTREND",
        "adx": 28.0,
        "rsi": 55.0,
        "spread_points": 20.0,
        "server_hour": 14,
    }


def _with_filters(**overrides):
    """ตั้งสวิตช์ตัวกรองชั่วคราวแล้วคืนค่าเดิม"""
    saved = {name: getattr(strategy, name) for name in overrides}
    for name, value in overrides.items():
        setattr(strategy, name, value)
    return saved


def _restore(saved):
    for name, value in saved.items():
        setattr(strategy, name, value)


def test_hold_signal_never_enters():
    decision = strategy.evaluate(_passing_context("HOLD"))
    assert decision.signal == "HOLD"
    assert not decision.enter


def test_all_filters_passing_enters():
    decision = strategy.evaluate(_passing_context("BUY"))
    assert decision.enter, decision.summary()
    assert decision.blockers == []


def test_h1_trend_against_the_signal_blocks_entry():
    context = _passing_context("BUY")
    context["h1_trend"] = "DOWNTREND"

    decision = strategy.evaluate(context)
    assert not decision.enter
    assert any("H1" in check.name for check in decision.blockers)


def test_low_adx_blocks_entry():
    context = _passing_context("BUY")
    context["adx"] = 12.0

    decision = strategy.evaluate(context)
    assert not decision.enter
    assert any("ADX" in check.name for check in decision.blockers)


def test_overbought_rsi_blocks_a_buy():
    context = _passing_context("BUY")
    context["rsi"] = 82.0
    assert not strategy.evaluate(context).enter


def test_oversold_rsi_blocks_a_sell():
    context = _passing_context("SELL")
    context["rsi"] = 18.0
    assert not strategy.evaluate(context).enter


def test_opposite_m5_blocks_entry():
    context = _passing_context("BUY")
    context["m5_trend"] = "DOWNTREND"
    assert not strategy.evaluate(context).enter


def test_sideway_m5_does_not_block():
    """M5 มักตามหลังจุดตัดของ M15 จึงขอแค่ไม่สวนทาง ไม่บังคับให้ตรงทิศ"""
    context = _passing_context("BUY")
    context["m5_trend"] = "SIDEWAY"
    assert strategy.evaluate(context).enter


def test_wide_spread_blocks_entry():
    context = _passing_context("BUY")
    context["spread_points"] = 500.0
    assert not strategy.evaluate(context).enter


def test_session_filter_blocks_outside_hours_when_enabled():
    saved = _with_filters(USE_SESSION_FILTER=True)
    try:
        context = _passing_context("BUY")
        context["server_hour"] = 3
        assert not strategy.evaluate(context).enter

        context["server_hour"] = 14
        assert strategy.evaluate(context).enter
    finally:
        _restore(saved)


def test_disabling_a_filter_lets_the_trade_through():
    context = _passing_context("BUY")
    context["h1_trend"] = "DOWNTREND"
    assert not strategy.evaluate(context).enter

    saved = _with_filters(USE_H1_TREND_FILTER=False)
    try:
        assert strategy.evaluate(context).enter
    finally:
        _restore(saved)


def test_report_explains_every_check():
    context = _passing_context("BUY")
    context["adx"] = 5.0

    report = strategy.evaluate(context).report()
    assert "ไม่ผ่าน" in report
    assert "ADX" in report


# ---------- ดูแลไม้ที่เปิดอยู่ ----------

BUY = mt5.POSITION_TYPE_BUY
SELL = mt5.POSITION_TYPE_SELL


def test_breakeven_waits_until_the_trigger():
    # เข้า 4000 เสี่ยง 16 ต้องกำไรถึง 16 (1R) ถึงจะย้าย
    assert trade.breakeven_level(BUY, 4000, 4010, 16, 1.0, 0.1) is None
    assert trade.breakeven_level(BUY, 4000, 4020, 16, 1.0, 0.1) is not None


def test_breakeven_lands_slightly_above_entry_for_a_buy():
    level = trade.breakeven_level(BUY, 4000, 4020, 16, 1.0, 0.1)
    assert level == 4000 + 1.6


def test_breakeven_lands_slightly_below_entry_for_a_sell():
    level = trade.breakeven_level(SELL, 4000, 3980, 16, 1.0, 0.1)
    assert level == 4000 - 1.6


def test_trailing_waits_for_its_own_trigger():
    # TRAIL_START_R 1.5 -> ต้องกำไร 24 ก่อน
    assert trade.trailing_level(BUY, 4000, 4020, 16, 1.5, 10.0, 2.0) is None
    assert trade.trailing_level(BUY, 4000, 4030, 16, 1.5, 10.0, 2.0) == 4030 - 20


def test_trailing_follows_price_for_a_sell():
    assert trade.trailing_level(SELL, 4000, 3970, 16, 1.5, 10.0, 2.0) == 3970 + 20


def test_stop_never_moves_backwards():
    """SL ที่ถอยหลังคือการขยายความเสี่ยง ต้องถูกปฏิเสธเสมอ"""
    assert trade.better_stop(BUY, 3990, 3995) == 3995
    assert trade.better_stop(BUY, 3990, 3985) is None
    assert trade.better_stop(SELL, 4010, 4005) == 4005
    assert trade.better_stop(SELL, 4010, 4015) is None


def test_stop_is_taken_when_none_is_set_yet():
    assert trade.better_stop(BUY, 0, 3995) == 3995
    assert trade.better_stop(BUY, None, 3995) == 3995


def test_stop_must_keep_the_broker_minimum_distance():
    # ราคา 4000 ระยะขั้นต่ำ 0.78 -> SL ที่ 3999.9 ใกล้เกินไป
    assert not trade.stop_is_far_enough(BUY, 4000, 3999.9, 0.78)
    assert trade.stop_is_far_enough(BUY, 4000, 3998.0, 0.78)
    assert not trade.stop_is_far_enough(SELL, 4000, 4000.1, 0.78)
    assert trade.stop_is_far_enough(SELL, 4000, 4002.0, 0.78)


def test_no_stop_move_without_a_known_initial_risk():
    assert trade.breakeven_level(BUY, 4000, 4100, 0, 1.0, 0.1) is None
    assert trade.trailing_level(BUY, 4000, 4100, 0, 1.5, 10.0, 2.0) is None


# ---------- ตัวตัดวงจรรายวัน ----------

class FakeDeal:
    def __init__(self, profit, entry=None, symbol="XAUUSD", magic=123456):
        self.profit = profit
        self.entry = mt5.DEAL_ENTRY_OUT if entry is None else entry
        self.symbol = symbol
        self.magic = magic


def test_summary_counts_only_closing_deals():
    deals = [
        FakeDeal(0, entry=mt5.DEAL_ENTRY_IN),   # ดีลขาเข้า ไม่นับ
        FakeDeal(12.0),
        FakeDeal(-8.0),
    ]
    summary = trade.summarize_deals(deals, "XAUUSD", 123456)

    assert summary["trades"] == 2
    assert summary["wins"] == 1
    assert summary["losses"] == 1
    assert summary["profit"] == 4.0


def test_summary_ignores_other_bots_and_symbols():
    deals = [
        FakeDeal(100.0, magic=999),
        FakeDeal(100.0, symbol="EURUSD"),
        FakeDeal(-5.0),
    ]
    summary = trade.summarize_deals(deals, "XAUUSD", 123456)

    assert summary["trades"] == 1
    assert summary["profit"] == -5.0


def test_consecutive_losses_counts_only_the_tail():
    deals = [FakeDeal(-5.0), FakeDeal(10.0), FakeDeal(-5.0), FakeDeal(-5.0)]
    assert trade.summarize_deals(deals, "XAUUSD", 123456)["consecutive_losses"] == 2


def test_a_win_resets_the_losing_streak():
    deals = [FakeDeal(-5.0), FakeDeal(-5.0), FakeDeal(1.0)]
    assert trade.summarize_deals(deals, "XAUUSD", 123456)["consecutive_losses"] == 0


def test_empty_history_is_safe():
    summary = trade.summarize_deals(None, "XAUUSD", 123456)
    assert summary["trades"] == 0
    assert summary["profit"] == 0


# ---------- ตัวรันแบบไม่ต้องมี pytest ----------

def _run_all():
    tests = sorted(
        (name, function)
        for name, function in globals().items()
        if name.startswith("test_") and callable(function)
    )

    failed = []

    for name, function in tests:
        try:
            function()
        except AssertionError as error:
            failed.append(name)
            print(f"FAIL  {name}  {error}")
        except Exception as error:  # ข้อผิดพลาดอื่นก็ถือว่าเทสไม่ผ่าน
            failed.append(name)
            print(f"ERROR {name}  {type(error).__name__}: {error}")
        else:
            print(f"PASS  {name}")

    print()
    if failed:
        print(f"ไม่ผ่าน {len(failed)} จาก {len(tests)} ข้อ: {', '.join(failed)}")
        return 1

    print(f"ผ่านทั้งหมด {len(tests)} ข้อ")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
