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
import notify
import strategy
import backtest


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


# ---------- เครื่องจำลองย้อนหลัง ----------

def _bars(rows, atr=10.0):
    """สร้าง DataFrame แท่งราคาจาก [(open, high, low, close), ...]"""
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    frame["time"] = pd.date_range("2026-01-01", periods=len(frame), freq="15min")
    frame["atr"] = atr
    return frame


_NO_MANAGEMENT = {**backtest.DEFAULTS, "use_breakeven": False, "use_trailing": False}


def test_backtest_stop_loss_gives_minus_one_r():
    bars = _bars([(4000, 4005, 3985, 3990)])
    result, index, reason = backtest._simulate_position(
        bars, 0, "BUY", 4000, 10.0, 4020, _NO_MANAGEMENT)

    assert reason == "SL"
    assert abs(result - (-1.0)) < 1e-9


def test_backtest_take_profit_gives_the_planned_multiple():
    bars = _bars([(4000, 4025, 3995, 4022)])
    result, index, reason = backtest._simulate_position(
        bars, 0, "BUY", 4000, 10.0, 4020, _NO_MANAGEMENT)

    assert reason == "TP"
    assert abs(result - 2.0) < 1e-9


def test_backtest_assumes_the_stop_wins_when_one_bar_hits_both():
    """ไม่รู้ลำดับราคาในแท่ง จึงต้องสมมติฝั่งแย่เสมอ ไม่งั้น backtest จะสวยเกินจริง"""
    bars = _bars([(4000, 4025, 3985, 4010)])
    result, index, reason = backtest._simulate_position(
        bars, 0, "BUY", 4000, 10.0, 4020, _NO_MANAGEMENT)

    assert reason == "SL"
    assert result < 0


def test_backtest_sell_side_is_mirrored():
    bars = _bars([(4000, 4015, 3995, 4012)])
    result, index, reason = backtest._simulate_position(
        bars, 0, "SELL", 4000, 10.0, 3980, _NO_MANAGEMENT)

    assert reason == "SL"
    assert abs(result - (-1.0)) < 1e-9


def test_backtest_breakeven_turns_a_reversal_into_a_small_win():
    """ราคาไปถึง 1R แล้วย้อนกลับมาชนทุน ต้องได้กำไรนิดหน่อย ไม่ใช่ -1R"""
    config = {**backtest.DEFAULTS, "use_trailing": False, "use_breakeven": True}
    bars = _bars([
        (4000, 4012, 3999, 4011),   # กำไรเกิน 1R (risk 10) -> ย้าย SL ไป 4001
        (4011, 4012, 3980, 3985),   # ย้อนลงมาชน SL ใหม่
    ])

    result, index, reason = backtest._simulate_position(
        bars, 0, "BUY", 4000, 10.0, 4030, config)

    assert reason == "SL"
    assert result > 0


def test_alignment_never_reads_an_unclosed_higher_timeframe_bar():
    """
    lookahead bias คือสาเหตุอันดับหนึ่งที่ backtest ออกมาสวยเกินจริง
    แท่ง M15 ที่เวลา 01:00 ปิดตอน 01:15 ตอนนั้น H1 ที่ปิดแล้วล่าสุดคือแท่ง 00:00
    """
    h1 = pd.DataFrame({
        "time": pd.to_datetime(["2026-01-01 00:00", "2026-01-01 01:00", "2026-01-01 02:00"]),
        "trend": ["UPTREND", "DOWNTREND", "UPTREND"],
    })
    m15_times = pd.Series(pd.to_datetime(["2026-01-01 01:00", "2026-01-01 01:45"]))

    aligned = backtest._align(h1, m15_times, -pd.Timedelta(minutes=45))

    assert aligned[0] == "UPTREND"      # แท่ง 00:00 ไม่ใช่ 01:00 ที่ยังไม่ปิด
    assert aligned[1] == "DOWNTREND"    # 01:45 ปิดตอน 02:00 จึงเห็นแท่ง 01:00 ได้แล้ว


def test_alignment_returns_unknown_before_any_bar_exists():
    h1 = pd.DataFrame({
        "time": pd.to_datetime(["2026-01-01 05:00"]),
        "trend": ["UPTREND"],
    })
    aligned = backtest._align(h1, pd.Series(pd.to_datetime(["2026-01-01 00:00"])),
                              -pd.Timedelta(minutes=45))
    assert aligned == ["UNKNOWN"]


def _trending_market(cycles=6, length=120):
    """ราคาขึ้นลงสลับเป็นรอบ ให้ MA ตัดกันหลายครั้ง"""
    import math

    closes = []
    for index in range(cycles * length):
        phase = index / length * math.pi
        closes.append(4000 + 60 * math.sin(phase) + (index % 5) * 0.4)

    frame = pd.DataFrame({"close": closes})
    frame["open"] = frame["close"].shift(1).fillna(frame["close"])
    frame["high"] = frame[["open", "close"]].max(axis=1) + 2
    frame["low"] = frame[["open", "close"]].min(axis=1) - 2
    frame["time"] = pd.date_range("2026-01-01", periods=len(frame), freq="15min")
    return frame


def test_backtest_runs_end_to_end_and_produces_trades():
    result = backtest.simulate(_trending_market(), config={"use_filters": False})
    stats = backtest.metrics(result)

    assert stats["trades"] > 0
    assert stats["wins"] + stats["losses"] == stats["trades"]
    assert 0 <= stats["win_rate"] <= 100


def test_backtest_enters_at_the_next_bar_open_not_the_signal_bar():
    """เข้าไม้ต้องเป็นราคาที่ยังไม่รู้ตอนตัดสินใจไม่ได้ — ไม่งั้นคือโกงตัวเอง"""
    market = _trending_market()
    result = backtest.simulate(market, config={"use_filters": False, "spread_points": 0})

    assert result["trades"]
    first = result["trades"][0]
    matching = market[market["time"] == first["entry_time"]]

    assert not matching.empty
    assert abs(first["entry"] - matching.iloc[0]["open"]) < 0.01


def test_backtest_never_holds_two_positions_at_once():
    result = backtest.simulate(_trending_market(), config={"use_filters": False})
    trades = result["trades"]

    for earlier, later in zip(trades, trades[1:]):
        assert later["entry_time"] >= earlier["exit_time"]


def test_filters_block_everything_when_the_trend_is_unknown():
    """ไม่มีข้อมูล H1 ให้ดู = ไม่รู้เทรนด์ = ไม่ควรเข้าไม้เลย"""
    result = backtest.simulate(_trending_market(), config={"use_filters": True})

    assert result["trades"] == []
    assert result["blocked"]


def test_metrics_handle_an_empty_run():
    assert backtest.metrics({"trades": []})["trades"] == 0


def test_report_is_readable_when_nothing_traded():
    report = backtest.format_report({
        "trades": [], "blocked": {}, "bars": 0, "from": None, "to": None, "config": {},
    })
    assert "ไม่มีไม้" in report


# ---------- สัญญาณแบบเวคเตอร์ต้องเท่ากับของจริง ----------

def test_vectorised_signal_matches_the_live_rule():
    """
    backtest ใช้ signal_series() เพื่อความเร็ว แต่บอทจริงใช้ core.crossover_signal()
    ถ้าสองอันให้ผลต่างกันเมื่อไหร่ backtest จะวัดกลยุทธ์คนละตัวกับที่รันจริง
    """
    market = _trending_market(cycles=3, length=80)
    prepared = backtest.prepare(market, None, None, backtest.DEFAULTS)

    mismatches = [
        index for index in range(60, len(prepared))
        if core.crossover_signal(prepared.iloc[: index + 2]) != prepared["signal"].iloc[index]
    ]

    assert mismatches == []


# ---------- ปิดไม้บางส่วน ----------

def test_partial_close_splits_a_large_enough_position():
    assert trade.partial_close_volume(FakeSymbolInfo(), 0.10, 0.5) == 0.05


def test_partial_close_refuses_when_the_minimum_lot_cannot_be_split():
    """0.01 lot แบ่งครึ่งไม่ได้ ต้องคืน None ไม่ใช่ส่งคำสั่งที่ broker จะตีกลับ"""
    assert trade.partial_close_volume(FakeSymbolInfo(), 0.01, 0.5) is None


def test_partial_close_refuses_when_nothing_would_be_left():
    """ปิดทั้งไม้ไม่ใช่การปิดบางส่วน ต้องคืน None ไม่ใช่เผลอปิดหมด"""
    assert trade.partial_close_volume(FakeSymbolInfo(), 0.02, 1.0) is None
    # 0.9 ของ 0.02 ปัดลงเหลือ 0.01 และยังเหลือ 0.01 ซึ่งถึงขั้นต่ำพอดี จึงทำได้
    assert trade.partial_close_volume(FakeSymbolInfo(), 0.02, 0.9) == 0.01


def test_partial_close_rounds_down_to_the_volume_step():
    assert trade.partial_close_volume(FakeSymbolInfo(), 0.07, 0.5) == 0.03


# ---------- การกวาดค่า ----------

def test_sweep_covers_the_whole_grid_and_restores_globals():
    market = _trending_market(cycles=3, length=90)
    grid = {"sl_atr_mult": (1.0, 2.0), "tp_atr_mult": (2.0, 4.0)}
    before = strategy.ADX_MIN

    rows = backtest.sweep(market, None, None, {"use_filters": False}, grid)

    assert len(rows) == 4
    assert strategy.ADX_MIN == before
    # เรียงจากคาดหวังสูงสุดลงมา
    assert rows == sorted(rows, key=lambda row: row["expectancy_r"], reverse=True)


def test_sweep_restores_globals_even_if_it_fails():
    before = strategy.ADX_MIN

    try:
        backtest.sweep(None, None, None, {}, {"adx_min": (99.0,)})
    except Exception:
        pass

    assert strategy.ADX_MIN == before


def test_sweep_report_calls_out_a_strategy_with_no_edge():
    rows = [{"sl_atr_mult": 1.5, "tp_atr_mult": 3.0, "adx_min": 20, "trades": 10,
             "expectancy_r": -0.2, "total_r": -2.0, "win_rate": 40.0,
             "max_drawdown_r": -3.0, "profit_factor": 0.5}]

    assert "ไม่มีชุดไหนเป็นบวก" in backtest.format_sweep(rows)


# ---------- รายงานสรุป ----------

def test_report_survives_a_directory_with_no_files():
    import tempfile
    import report

    original = os.getcwd()
    try:
        os.chdir(tempfile.mkdtemp())
        text = report.build_report()
    finally:
        os.chdir(original)

    assert "สรุปการทำงานของบอท" in text
    assert "ยังไม่บันทึกแท่งไหนเลย" in text


def test_report_groups_repeated_log_problems():
    import tempfile
    import report

    directory = tempfile.mkdtemp()
    original = os.getcwd()

    try:
        os.chdir(directory)
        with open("bot.log", "w", encoding="utf-8") as handle:
            for number in range(4):
                handle.write(f"2026-09-09 10:0{number}:00 [WARNING] spread 8{number}.0 กว้างเกิน 50\n")
            handle.write("2026-09-09 10:05:00 [INFO] ปกติ\n")

        lines = []
        report.summarise_log(lines)
    finally:
        os.chdir(original)

    text = "\n".join(lines)
    assert "พบ 4 รายการ" in text
    # ข้อความเดียวกันที่ต่างแค่ตัวเลข ต้องถูกยุบเป็นบรรทัดเดียว
    assert "x4" in text


# ---------- หา Symbol ของ broker ----------

class FakeBroker:
    """แทน mt5.symbols_get / symbol_select / symbol_info ชั่วคราวเพื่อเทสการเลือกชื่อ"""

    def __init__(self, names):
        self.names = list(names)

    def __enter__(self):
        self.saved = (mt5.symbols_get, mt5.symbol_select, mt5.symbol_info)

        class Named:
            def __init__(self, name):
                self.name = name

        mt5.symbols_get = lambda: tuple(Named(name) for name in self.names)
        mt5.symbol_select = lambda name, enable=True: name in self.names
        mt5.symbol_info = lambda name: Named(name) if name in self.names else None
        return self

    def __exit__(self, *exc):
        mt5.symbols_get, mt5.symbol_select, mt5.symbol_info = self.saved


def test_exact_symbol_is_used_without_guessing():
    with FakeBroker(["XAUUSD", "EURUSD"]):
        resolved, candidates = core.resolve_symbol("XAUUSD")

    assert resolved == "XAUUSD"
    assert candidates == []


def test_suffixed_symbol_is_found():
    with FakeBroker(["EURUSD", "XAUUSD.m"]):
        resolved, _ = core.resolve_symbol("XAUUSD")

    assert resolved == "XAUUSD.m"


def test_gold_against_another_currency_is_not_mistaken_for_the_dollar_pair():
    """
    XAUEUR.m ยาวเท่ากับ XAUUSD.m และขึ้นต้น XAU เหมือนกัน
    ถ้าเรียงตามตัวอักษรจะได้ XAUEUR.m ซึ่งเป็นคนละตลาด
    """
    with FakeBroker(["XAUEUR.m", "XAUUSD.m", "GOLD.spot"]):
        resolved, candidates = core.resolve_symbol("XAUUSD")

    assert resolved == "XAUUSD.m"
    assert "XAUEUR.m" in candidates


def test_gold_named_differently_still_resolves():
    with FakeBroker(["EURUSD", "GOLD"]):
        resolved, _ = core.resolve_symbol("XAUUSD")

    assert resolved == "GOLD"


def test_no_gold_at_all_raises_a_useful_error():
    with FakeBroker(["EURUSD", "GBPUSD"]):
        try:
            core.resolve_symbol("XAUUSD")
        except core.MT5Error as error:
            assert "run.py symbols" in str(error)
        else:
            raise AssertionError("ควรโยน MT5Error เมื่อไม่เจอทองเลย")


# ---------- การแจ้งเตือน ----------

class FakeLogger:
    """กลืน log ทิ้ง — เทสสนใจว่าอะไรถูกส่ง ไม่ใช่ว่าอะไรถูกเขียนลง log"""

    def debug(self, *args, **kwargs):
        pass

    info = warning = error = debug


class FakeClosingDeal:
    """ดีลจาก MT5 เท่าที่ summarize_position_close ใช้จริง"""

    def __init__(self, entry, profit=0.0, commission=0.0, swap=0.0, volume=0.01, price=0.0):
        self.entry = entry
        self.profit = profit
        self.commission = commission
        self.swap = swap
        self.volume = volume
        self.price = price


class Recorder:
    """Notifier ที่จับข้อความไว้แทนการยิงออกเน็ต พร้อมนาฬิกาที่ขยับเองได้"""

    def __init__(self):
        self.messages = []
        self.now = 1000.0
        self.notifier = notify.Notifier(
            "token", "chat", FakeLogger(),
            transport=self._capture, clock=lambda: self.now,
        )

    def _capture(self, message, quiet=False):
        self.messages.append((message, quiet))
        return True

    def tick(self, seconds):
        self.now += seconds


def _sample_context(**overrides):
    context = {
        "candle_time": "2026-09-09 19:15:00", "close": 4401.62, "rsi": 48.4,
        "adx": 24.6, "atr": 12.74, "spread_points": 34.0,
        "h1_trend": "DOWNTREND", "m5_trend": "DOWNTREND",
        "m15_signal": "SELL", "fast_ma": 20, "slow_ma": 50,
    }
    context.update(overrides)
    return context


def test_every_category_has_an_icon_and_a_working_switch():
    for key, category in notify.CATEGORIES.items():
        assert category.key == key
        assert category.icon
        assert category.enabled() in (True, False)


def test_text_from_the_broker_cannot_break_the_html():
    """หัวข้อ format_message escape ให้เอง ส่วนบรรทัดเนื้อความผู้เรียกต้องเรียก escape() เอง"""
    message = notify.format_message("entry", "retcode <10030> & ลองใหม่", [
        notify.escape("filling mode <IOC> ไม่รองรับ"),
    ])

    assert "&lt;10030&gt;" in message
    assert "&amp;" in message
    assert "&lt;IOC&gt;" in message
    assert "<10030>" not in message


def test_bar_clamps_values_that_fall_outside_the_range():
    assert notify.bar(-50, 0, 100, width=10) == notify.BLOCK_EMPTY * 10
    assert notify.bar(500, 0, 100, width=10) == notify.BLOCK_FULL * 10


def test_bar_stays_empty_when_the_indicator_could_not_be_calculated():
    assert notify.bar(float("nan"), 0, 100, width=4) == notify.BLOCK_EMPTY * 4
    assert notify.bar(None, 0, 100, width=4) == notify.BLOCK_EMPTY * 4


def test_money_always_carries_a_sign():
    assert notify.money(12.5, "USD").startswith("+")
    assert notify.money(-12.5, "USD").startswith("-")


def test_r_blocks_show_green_for_profit_and_red_for_loss():
    assert notify.r_blocks(2.0) == "🟩🟩"
    assert notify.r_blocks(-1.0) == "🟥"
    # ไม้ที่วิ่งไกลมากต้องไม่ทำให้ข้อความยาวไม่จำกัด
    assert len(notify.r_blocks(99.0, width=6)) == len("🟩" * 6)


def test_win_rate_bar_says_so_when_nothing_has_closed_yet():
    assert notify.win_rate_bar(0, 0) == "ยังไม่มีไม้ที่ปิด"


def test_the_same_message_is_not_sent_twice_in_a_row():
    recorder = Recorder()

    assert recorder.notifier.send("entry", "หัวข้อ", ["เนื้อความเดิม"]) is True
    assert recorder.notifier.send("entry", "หัวข้อ", ["เนื้อความเดิม"]) is False
    assert len(recorder.messages) == 1
    assert recorder.notifier.skipped == 1


def test_the_same_message_sends_again_after_the_dedup_window():
    recorder = Recorder()

    recorder.notifier.send("entry", "หัวข้อ", ["เนื้อความเดิม"])
    recorder.tick(notify.DEDUP_SECONDS + 1)
    recorder.notifier.send("entry", "หัวข้อ", ["เนื้อความเดิม"])

    assert len(recorder.messages) == 2


def test_cooldown_blocks_even_a_message_whose_wording_changed():
    """ตลาดปิดค้างทั้งสุดสัปดาห์ ถ้ากันแค่ข้อความซ้ำจะยังได้เป็นร้อยข้อความ"""
    recorder = Recorder()

    recorder.notifier.send("market", "ตลาดปิด", ["รอ 5 นาที"], key="market-closed")
    recorder.tick(60)
    recorder.notifier.send("market", "ตลาดปิด", ["รอ 10 นาที"], key="market-closed")

    assert len(recorder.messages) == 1


def test_a_closed_category_sends_nothing():
    recorder = Recorder()

    with SwitchedTo("SEND_ENTRY", False):
        assert recorder.notifier.send("entry", "เข้าไม้", ["ควรเงียบ"]) is False

    assert recorder.messages == []


def test_without_a_token_nothing_reaches_the_transport():
    sent = []
    notifier = notify.Notifier(None, None, FakeLogger(), transport=lambda m, quiet=False: sent.append(m))

    assert notifier.send("entry", "เข้าไม้", ["ไม่มี token"]) is False
    assert sent == []


class SwitchedTo:
    """
    ตั้งสวิตช์ของ notify ชั่วคราวแล้วคืนค่าเดิมเสมอ

    เคยเขียน finally ให้คืนเป็น False ตรงๆ ซึ่งไปทับค่าจริงของโมดูล ผลคือเทส
    ที่รันทีหลังเห็นค่าที่เทสก่อนหน้าทิ้งไว้ ไม่ใช่ค่าเริ่มต้นจริง เทสจึงผ่าน
    ทั้งที่ค่าเริ่มต้นเปลี่ยนไปแล้ว
    """

    def __init__(self, name, value):
        self.name = name
        self.value = value

    def __enter__(self):
        self.original = getattr(notify, self.name)
        setattr(notify, self.name, self.value)
        return self

    def __exit__(self, *error):
        setattr(notify, self.name, self.original)


def test_hold_candles_stay_silent_when_the_switch_is_off():
    recorder = Recorder()
    context = _sample_context(m15_signal="HOLD")
    decision = strategy.evaluate(context)

    with SwitchedTo("SEND_HOLD", False):
        assert recorder.notifier.candle_verdict(decision, context, "XAUUSD") is False

    assert recorder.messages == []


def test_hold_candles_are_announced_when_the_switch_is_on():
    recorder = Recorder()
    context = _sample_context(m15_signal="HOLD")
    decision = strategy.evaluate(context)

    with SwitchedTo("SEND_HOLD", True):
        assert recorder.notifier.candle_verdict(decision, context, "XAUUSD") is True


def test_a_hold_message_is_quiet_so_it_does_not_buzz_every_fifteen_minutes():
    recorder = Recorder()
    context = _sample_context(m15_signal="HOLD")
    decision = strategy.evaluate(context)

    with SwitchedTo("SEND_HOLD", True):
        recorder.notifier.candle_verdict(decision, context, "XAUUSD")

    assert recorder.messages[0][1] is True


def test_a_near_miss_message_names_every_blocker():
    """invariant ของโปรเจกต์: คำตัดสินต้องพกเหตุผลไปด้วยเสมอ รวมถึงตอนแจ้งเตือน"""
    recorder = Recorder()
    context = _sample_context(adx=11.2, spread_points=64.0)
    decision = strategy.evaluate(context)
    recorder.notifier.candle_verdict(decision, context, "XAUUSD", adx_min=strategy.ADX_MIN)

    message = recorder.messages[0][0]

    for check in decision.checks:
        assert check.name in message

    assert message.count("❌") == len(decision.blockers)


def test_a_passing_signal_is_announced_loudly():
    recorder = Recorder()
    context = _sample_context()
    decision = strategy.evaluate(context)

    assert decision.enter
    recorder.notifier.candle_verdict(decision, context, "XAUUSD", adx_min=strategy.ADX_MIN)

    message, quiet = recorder.messages[0]
    assert quiet is False
    assert "SELL" in message


def test_the_entry_message_carries_price_stop_and_target():
    recorder = Recorder()
    recorder.notifier.entry_filled("XAUUSD", "SELL", 0.01, 4401.62, 4420.73, 4363.40,
                                   "19.10", "USD", 987654)

    message = recorder.messages[0][0]

    assert "4,401.62" in message
    assert "4,420.73" in message
    assert "4,363.40" in message
    assert "987654" in message


def test_a_closed_position_reports_its_r_multiple():
    recorder = Recorder()
    meta = {"signal": "SELL", "entry": 4401.62, "risk_money": 19.10, "currency": "USD"}
    recorder.notifier.position_closed("XAUUSD", 987654, meta, 38.20, "USD", 4363.40)

    assert "+2.00R" in recorder.messages[0][0]


def test_a_closed_position_without_a_remembered_risk_still_reports_money():
    recorder = Recorder()
    recorder.notifier.position_closed("XAUUSD", 987654, {"signal": "BUY"}, -8.4, "USD")

    message = recorder.messages[0][0]
    assert "-8.40 USD" in message
    assert "R" not in message.split("USD")[0]


def test_stripping_tags_gives_back_readable_text():
    message = notify.format_message("entry", "หัวข้อ", ["<b>ตัวหนา</b> กับ &lt;10030&gt;"])
    plain = notify.strip_tags(message)

    assert "<b>" not in plain
    assert "10030" in plain
    assert "ตัวหนา" in plain


def test_a_very_long_message_is_cut_before_telegram_rejects_it():
    message = notify.format_message("entry", "หัวข้อ", ["x" * 9000])

    assert len(message) <= notify.MAX_MESSAGE_CHARS


# ---------- วินิจฉัยตอนแจ้งเตือนไม่มา ----------

def test_a_token_typed_into_the_example_file_is_pointed_out():
    """python-dotenv อ่านเฉพาะ .env กรอกลง .env.example คือเงียบสนิทโดยไม่มี error"""
    hint = notify.misplaced_secret_hint(
        "TELEGRAM_TOKEN=\nTELEGRAM_CHAT_ID=\n",
        "TELEGRAM_TOKEN=8123456789:AAH\nTELEGRAM_CHAT_ID=111\n",
    )

    assert hint is not None
    assert ".env.example" in hint


def test_nothing_is_flagged_once_the_real_file_is_filled_in():
    assert notify.misplaced_secret_hint(
        "TELEGRAM_TOKEN=8123456789:AAH\nTELEGRAM_CHAT_ID=111\n",
        "TELEGRAM_TOKEN=\nTELEGRAM_CHAT_ID=\n",
    ) is None


def test_two_empty_files_are_not_mistaken_for_a_misplaced_secret():
    assert notify.misplaced_secret_hint("", "") is None
    assert notify.misplaced_secret_hint(None, None) is None


def test_a_commented_out_example_line_does_not_count_as_filled_in():
    assert notify.misplaced_secret_hint("", "# TELEGRAM_TOKEN=ใส่ตรงนี้\n") is None


def test_a_token_that_is_not_in_botfather_shape_is_caught_before_the_network():
    assert notify.token_looks_valid("8123456789:AAH" + "x" * 32)
    assert not notify.token_looks_valid("")
    assert not notify.token_looks_valid(None)
    assert not notify.token_looks_valid("ใส่ token ตรงนี้")
    assert not notify.token_looks_valid("8123456789:สั้นไป")


def test_the_403_that_means_you_never_pressed_start_says_so():
    """อาการที่พบบ่อยสุด: token ถูก chat_id ถูก แต่ยังไม่เคยทักบอท"""
    hint = notify.describe_api_error(
        403, "Forbidden: bot can't initiate conversation with a user",
    )

    assert "Start" in hint


def test_a_revoked_token_is_reported_as_a_token_problem():
    assert "@BotFather" in notify.describe_api_error(401, "Unauthorized")


def test_a_wrong_chat_id_is_reported_as_a_chat_id_problem():
    assert "TELEGRAM_CHAT_ID" in notify.describe_api_error(400, "Bad Request: chat not found")


def test_an_unrecognised_error_still_shows_what_telegram_said():
    hint = notify.describe_api_error(500, "Internal Server Error")

    assert "500" in hint
    assert "Internal Server Error" in hint


def test_chat_ids_are_pulled_out_of_whatever_update_shape_arrives():
    updates = [
        {"message": {"chat": {"id": 111, "first_name": "Yutthapoom"}}},
        {"channel_post": {"chat": {"id": -100222, "title": "ห้องบอท"}}},
        {"edited_message": {"chat": {"id": 111, "first_name": "Yutthapoom"}}},
        {"poll": {"id": "ไม่มี chat"}},
    ]

    assert notify._chat_ids_from_updates(updates) == {
        "111": "Yutthapoom", "-100222": "ห้องบอท",
    }


def test_no_updates_at_all_is_not_a_crash():
    assert notify._chat_ids_from_updates(None) == {}
    assert notify._chat_ids_from_updates([]) == {}


def test_diagnose_stops_before_the_network_when_there_is_no_token():
    steps = notify.diagnose(None, "111")

    assert len(steps) == 1
    assert steps[0][0] is False


# ---------- ผลของไม้ที่ปิดไปแล้ว ----------

def test_closing_profit_includes_commission_and_swap():
    """ไม้ที่ชนะเฉียดฉิวต้องไม่รายงานว่ากำไรทั้งที่หักค่าธรรมเนียมแล้วขาดทุน"""
    deals = [
        FakeClosingDeal(mt5.DEAL_ENTRY_OUT, profit=1.5, commission=-0.8, swap=-1.2, price=4400.0)
    ]

    assert trade.summarize_position_close(deals)["profit"] == -0.5


def test_the_opening_deal_is_not_counted_as_a_result():
    deals = [
        FakeClosingDeal(mt5.DEAL_ENTRY_IN, profit=0.0, price=4401.0),
        FakeClosingDeal(mt5.DEAL_ENTRY_OUT, profit=12.0, price=4380.0),
    ]
    closed = trade.summarize_position_close(deals)

    assert closed["deals"] == 1
    assert closed["profit"] == 12.0


def test_a_position_with_no_closing_deal_yet_reports_nothing():
    assert trade.summarize_position_close([FakeClosingDeal(mt5.DEAL_ENTRY_IN)]) is None
    assert trade.summarize_position_close([]) is None
    assert trade.summarize_position_close(None) is None


def test_a_partially_closed_position_averages_its_exit_price():
    deals = [
        FakeClosingDeal(mt5.DEAL_ENTRY_OUT, profit=10.0, volume=0.05, price=4380.0),
        FakeClosingDeal(mt5.DEAL_ENTRY_OUT, profit=30.0, volume=0.05, price=4360.0),
    ]
    closed = trade.summarize_position_close(deals)

    assert closed["volume"] == 0.10
    assert closed["price"] == 4370.0
    assert closed["profit"] == 40.0


# ---------- การรายงานไม้ที่ปิดไปแล้ว ----------

class ClosedPositionHarness:
    """
    สลับ runner ให้อ่านประวัติดีลปลอมและจับการแจ้งเตือนแทนการส่งจริง

    ทางนี้เป็น path เดียวในโปรเจกต์ที่ยังไม่เคยพิสูจน์กับ terminal จริง
    จึงต้องพิสูจน์ตรรกะรอบๆ มันให้แน่นแทน
    """

    def __init__(self, lookups):
        import runner

        self.runner = runner
        self.lookups = lookups      # ผลที่ closing_deals จะคืนทีละครั้ง
        self.calls = 0
        self.reported = []

    def __enter__(self):
        self._deals = self.runner.trade.closing_deals
        self._notifier = self.runner.NOTIFIER
        self.runner.trade.closing_deals = self._fake_deals
        self.runner.NOTIFIER = self
        return self

    def __exit__(self, *error):
        self.runner.trade.closing_deals = self._deals
        self.runner.NOTIFIER = self._notifier

    def _fake_deals(self, ticket, logger=None):
        result = self.lookups[min(self.calls, len(self.lookups) - 1)]
        self.calls += 1
        return result

    def position_closed(self, symbol, ticket, meta, profit, currency, price=None):
        self.reported.append((ticket, profit))


def test_a_closed_position_is_reported_from_the_deal_history():
    deals = [FakeClosingDeal(mt5.DEAL_ENTRY_OUT, profit=12.0, volume=0.01, price=4380.0)]
    state = {"position_meta": {"111": {"signal": "SELL", "risk_money": 6.0}}}

    with ClosedPositionHarness([deals]) as harness:
        pending = harness.runner.report_closed_positions(set(), state, FakeLogger())

    assert harness.reported == [("111", 12.0)]
    assert pending == set()


def test_a_position_that_is_still_open_is_not_reported_as_closed():
    state = {"position_meta": {"111": {"signal": "SELL"}}}

    with ClosedPositionHarness([None]) as harness:
        harness.runner.report_closed_positions({"111"}, state, FakeLogger())

    assert harness.reported == []
    assert harness.calls == 0


def test_a_history_that_has_not_landed_yet_is_kept_for_the_next_cycle():
    """ประวัติดีลไม่ได้ลงทันทีเสมอ ล้าง meta ทิ้งรอบแรกคือไม่มีวันได้รายงาน"""
    state = {"position_meta": {"111": {"signal": "SELL"}}}

    with ClosedPositionHarness([None]) as harness:
        pending = harness.runner.report_closed_positions(set(), state, FakeLogger())

    assert harness.reported == []
    assert pending == {"111"}
    assert state["position_meta"]["111"]["close_lookups"] == 1


def test_a_history_that_lands_late_is_still_reported():
    deals = [FakeClosingDeal(mt5.DEAL_ENTRY_OUT, profit=-4.0, volume=0.01, price=4420.0)]
    state = {"position_meta": {"111": {"signal": "BUY"}}}

    with ClosedPositionHarness([None, None, deals]) as harness:
        for _ in range(3):
            harness.runner.report_closed_positions(set(), state, FakeLogger())

    assert harness.reported == [("111", -4.0)]


def test_the_bot_stops_waiting_for_a_history_that_never_lands():
    import runner

    state = {"position_meta": {"111": {"signal": "SELL"}}}

    with ClosedPositionHarness([None]) as harness:
        for _ in range(runner.CLOSE_LOOKUP_ATTEMPTS):
            pending = harness.runner.report_closed_positions(set(), state, FakeLogger())

    assert pending == set()


def test_a_broker_that_rejects_the_history_lookup_does_not_stop_the_loop():
    """position= ไม่ได้มีในแพ็กเกจทุกเวอร์ชัน ล้มตรงนี้ต้องไม่ล้มลูปที่กำลังดูแล SL"""
    def explode(*args, **kwargs):
        raise TypeError("unexpected keyword argument 'position'")

    original = mt5.history_deals_get
    mt5.history_deals_get = explode

    try:
        assert trade.closing_deals(111, FakeLogger()) is None
    finally:
        mt5.history_deals_get = original


# ---------- ไฟล์ CSV ที่ชุดคอลัมน์เปลี่ยนไปตามเวลา ----------

def _read_rows(path):
    import csv

    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle))


def test_a_new_column_does_not_shift_the_rows_written_before_it():
    """
    เคยเป็นบั๊กจริง: header ค้างที่ชุดเก่า 22 คอลัมน์ แต่แถวใหม่เขียน 26 ช่อง
    pandas จึงอ่านค่าเลื่อนยกไฟล์ และ backtest_engine ไปให้คะแนนคอลัมน์ผิดคน
    """
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "features.csv")

        core.append_csv(path, {"candle_time": "1", "close": 10.0})
        core.append_csv(path, {"candle_time": "2", "close": 11.0, "adx_14": 25.0})

        rows = _read_rows(path)

        assert rows[0] == ["candle_time", "close", "adx_14"]
        assert rows[1] == ["1", "10.0", ""]
        assert rows[2] == ["2", "11.0", "25.0"]


def test_rows_already_written_with_the_new_columns_are_realigned():
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "features.csv")

        # ไฟล์ที่พังแบบเดียวกับของจริง: header เก่า แต่แถวข้างล่างมีคอลัมน์ใหม่แล้ว
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write("candle_time,close\n")
            handle.write("1,10.0\n")
            handle.write("2,11.0,25.0\n")

        core.align_csv_columns(path, ["candle_time", "close", "adx_14"])
        rows = _read_rows(path)

        assert rows[1] == ["1", "10.0", ""]
        assert rows[2] == ["2", "11.0", "25.0"]


def test_realigning_an_untouched_file_changes_nothing():
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "features.csv")
        core.append_csv(path, {"candle_time": "1", "close": 10.0})
        before = _read_rows(path)

        core.append_csv(path, {"candle_time": "2", "close": 11.0})

        assert _read_rows(path)[:2] == before


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
