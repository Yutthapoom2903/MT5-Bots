# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

MetaTrader 5 scripts for XAUUSD (gold) on M15, built around a single MA20/MA50 crossover
rule. Two shared modules hold the logic; every other `.py` file is a thin top-level script
that connects to MT5, does one job, and shuts down.

- `mt5_core.py` — connection, symbol setup, rate fetching, indicators, the crossover rule,
  logging setup, and restart-safe state persistence. Everything imports this.
- `mt5_trade.py` — everything that talks to the broker: price/volume normalization, stop
  distance, filling-mode selection, risk-based lot sizing, order send/close. Only
  `bot_integrated.py` imports it; the logger scripts must stay free of it.

## Running

Every script talks to a **running MT5 terminal on the same machine** through the
`MetaTrader5` package, which is **Windows-only**. The repo lives under WSL, but the bots
must run with Windows Python against an open, logged-in MT5 terminal — they cannot execute
from the Linux side. `backtest_engine.py` is the exception: it only reads a CSV and runs
anywhere pandas is installed.

```powershell
pip install -r requirements.txt

python check_mt5.py            # verify connection + print account info
python list_symbols.py         # find the broker's actual gold/EURUSD/BTC symbol names
python bot_signal.py           # one-shot: print current signal and exit
python bot_monitor.py          # loop: append signals to signal_log.csv (no orders)
python bot_feature_logger.py   # loop: append features to market_training_data.csv (no orders)
python bot_integrated.py       # loop: LIVE — sends real market orders + Telegram alerts
python backtest_engine.py      # score the hand-labelled decisions in the training CSV
```

There is no test suite and no linter config.

**`bot_integrated.py` places real orders.** It refuses to start on a non-demo account
unless `ALLOW_LIVE_ACCOUNT` is set to `True` in the file — do not flip that flag on the
user's behalf, and never run the script without explicit confirmation.

## Signal logic — the invariant to preserve

`mt5_core.crossover_signal()` is the single implementation; no script may re-derive it.

- Compare `df.iloc[-3]` (`core.PREVIOUS`) against `df.iloc[-2]` (`core.CLOSED`).
- `df.iloc[-1]` (`core.FORMING`) is the candle still building and is deliberately never
  used — reading it would make signals flip mid-candle.
- Fast crossing above slow → `BUY`; below → `SELL`; otherwise `HOLD`. NaN warm-up rows
  yield `HOLD`.

All loops poll every 30s and guard on the closed candle's timestamp so a candle is acted on
once. `bot_integrated.py` persists that timestamp to `bot_state.json`, so a restart
mid-candle does not re-fire an order — the in-memory-only guard used by the logger scripts
is fine for them because they never place orders.

RSI and ATR in `mt5_core.py` use Wilder smoothing (`ewm(alpha=1/period)`) to match what MT5
and TradingView display. Rows logged before 2026-09-09 used a simple rolling mean instead,
so early `rsi_14` / `atr_14` values in `market_training_data.csv` are not comparable to
later ones.

## Risk model in bot_integrated.py

Do not reintroduce fixed point-based stops. On XAUUSD `point` is `0.01`, so the old
`300 * point` stop was **$3.00** against an ATR around `$11` — inside the noise, and
usually inside the broker's minimum stop distance as well.

- SL/TP are `ATR × SL_ATR_MULT` / `ATR × TP_ATR_MULT`, floored at
  `mt5_trade.min_stop_distance()` (the larger of `trade_stops_level`, 3× spread, and 10
  points — brokers that report `trade_stops_level = 0` use a dynamic spread-based limit).
- Lot size is derived from `RISK_PERCENT` of balance and the SL distance via
  `trade_tick_value` / `trade_tick_size`, then floored to `volume_step`. Set
  `USE_FIXED_LOT = True` to bypass.
- Levels are computed from the live tick, never from the closed candle's `close`.
- `pick_filling_modes()` reads the symbol's `filling_mode` bitmask and the order retries
  down the list on retcode 10030; retcodes are numeric constants in `mt5_trade.py` because
  their names vary across package versions.
- Before entering: symbol tradable, spread under `MAX_SPREAD_POINTS`, ATR valid, and no
  existing position with the same `MAGIC` (an opposite one is closed first when
  `CLOSE_ON_REVERSE`).

## Data files

CSVs are appended in place (`mode="a"`, header only when absent) and committed to git —
accumulating data, not build output. `bot.log` and `bot_state.json` are runtime artifacts
and are gitignored.

- `signal_log.csv` — one row per closed candle from `bot_monitor.py`.
- `trade_log.csv` — one row per order attempt from `bot_integrated.py`, successes and
  failures alike.
- `market_training_data.csv` — one row per closed candle from `bot_feature_logger.py`. The
  trailing columns (`your_decision`, `your_reason`, `entry_price`, `stop_loss`,
  `take_profit`, `trade_result`) are intentionally blank for the user to fill in by hand in
  Excel. `backtest_engine.py` reads only those hand-labelled columns — it scores the
  human's decisions, not the bot's signal.

## Conventions

Console output, log messages, and comments are Thai; identifiers, config constants, and CSV
column names are English. Config lives as UPPERCASE module-level constants at the top of
each script — keep new settings in that form rather than introducing a config file. Scripts
put their work in `main()` behind `if __name__ == "__main__":` with `mt5.shutdown()` in a
`finally`, and raise `core.MT5Error` for unrecoverable MT5 failures.

Broker symbol names vary (`XAUUSD`, `XAUUSD.m`, `GOLD`…). `SYMBOL` is hardcoded to
`"XAUUSD"` everywhere; use `list_symbols.py` to confirm what the connected broker exposes.

## Outstanding

The Telegram token in the first two commits (`git show a30efaf:.env`) has not been
confirmed rotated. `.env` is untracked now, but the old value is still readable in history.
