"""พิมพ์สัญญาณล่าสุดครั้งเดียวแล้วจบ — ใช้เช็คเร็วๆ ว่าตอนนี้กราฟให้สัญญาณอะไร"""

import MetaTrader5 as mt5

import mt5_core as core

SYMBOL = "XAUUSD"
TIMEFRAME = mt5.TIMEFRAME_M15
FAST_MA = 20
SLOW_MA = 50
BARS = 250


def main():
    core.connect()
    core.prepare_symbol(SYMBOL)

    df = core.get_rates(SYMBOL, TIMEFRAME, BARS, min_bars=SLOW_MA + 3)

    if df is None:
        raise core.MT5Error(f"ข้อมูลแท่งราคาไม่เพียงพอ: {core.last_error_text()}")

    core.add_moving_averages(df, FAST_MA, SLOW_MA)

    candle = core.closed_candle(df)
    signal = core.crossover_signal(df)

    print(f"Symbol: {SYMBOL}")
    print("Timeframe: M15")
    print(f"แท่งล่าสุดที่ปิด: {candle['time']}")
    print(f"Close: {candle['close']:.2f}")
    print(f"MA {FAST_MA}: {candle['ma_fast']:.2f}")
    print(f"MA {SLOW_MA}: {candle['ma_slow']:.2f}")
    print(f"Spread: {core.spread_points(SYMBOL)} points")
    print(f"Signal: {signal}")


if __name__ == "__main__":
    try:
        main()
    except core.MT5Error as error:
        print(error)
        raise SystemExit(1)
    finally:
        mt5.shutdown()
