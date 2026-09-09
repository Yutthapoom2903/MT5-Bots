# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A set of standalone MetaTrader 5 scripts for XAUUSD (gold) on M15, built around a single
MA20/MA50 crossover rule. There is no package, no framework, and no shared module — each
`.py` file is a top-level script that connects to MT5, does one job, and shuts down.

## Running

Every script talks to a **running MT5 terminal on the same machine** through the
`MetaTrader5` Python package, which is **Windows-only**. The repo lives under WSL, but
the scripts must be run with Windows Python against an open, logged-in MT5 terminal —
they cannot be executed from the Linux side.

```powershell
python check_mt5.py            # verify connection + print account info
python list_symbols.py         # find the broker's actual gold/EURUSD/BTC symbol names
python bot_signal.py           # one-shot: print current signal and exit
python bot_monitor.py          # loop: append signals to signal_log.csv (no orders)
python bot_feature_logger.py   # loop: append richer features to market_training_data.csv (no orders)
python bot_integrated.py       # loop: LIVE — sends real market orders + Telegram alerts
python backtest_engine.py      # read market_training_data.csv, print manual-decision win rate
```

There is no test suite, no linter config, and no `requirements.txt`. Dependencies used:
`MetaTrader5`, `pandas`, `requests`, `python-dotenv`.

**`bot_integrated.py` places real orders.** Never run it, or suggest running it, without
explicit confirmation, and verify the connected MT5 account is a demo account first
(`check_mt5.py` prints `trade_mode`; `0` is demo).

## Signal logic — the invariant to preserve

All four bot scripts implement the same crossover and must stay consistent:

- Pull `BARS` of M15 rates, compute `close.rolling(20).mean()` and `.rolling(50).mean()`.
- Compare `df.iloc[-3]` (previous) against `df.iloc[-2]` (last **closed** candle).
  `iloc[-1]` is the candle still forming and is deliberately never used — using it would
  make signals flip mid-candle.
- Fast crossing above slow → `BUY`; below → `SELL`; otherwise `HOLD`.

The loops are all polling loops (`time.sleep(30)`) guarded by a `last_candle_time` /
`last_signal_time` variable so a given closed candle is only acted on once.

`bot_feature_logger.py` layers extra features on the same skeleton: hand-rolled RSI(14)
(simple rolling mean, not Wilder's), ATR(14), an H1 MA20/MA50 trend label, and live spread
in points.

## Data files

Both CSVs are appended to in place (`mode="a"`, header only when the file is absent) and
are committed to git — treat them as accumulating data, not build output.

- `signal_log.csv` — one row per closed M15 candle from `bot_monitor.py`.
- `market_training_data.csv` — one row per closed candle from `bot_feature_logger.py`,
  with trailing columns (`your_decision`, `your_reason`, `entry_price`, `stop_loss`,
  `take_profit`, `trade_result`) intentionally left blank for the user to fill in by hand
  in Excel. `backtest_engine.py` reads only those hand-labelled columns — it scores the
  human's decisions, not the bot's signal.

## Known issues in the current code

- `backtest_engine.py` calls `os.path.exists` but never imports `os` — it raises
  `NameError` on any run.
- `.env` (with a live `TELEGRAM_TOKEN`) is committed to the repo and there is no
  `.gitignore`. If the user touches secrets handling, raise this: the token should be
  rotated and the file untracked.
- `bot_integrated.py` has no `symbol_select` call, no filling-mode fallback, a bare
  `except: pass` around the Telegram send, and hardcodes volume `0.01` and a fixed
  300/600-point SL/TP. Its `except KeyboardInterrupt` only shuts MT5 down on Ctrl+C, not
  on other exits.

## Conventions

Console output and code comments are in Thai; identifiers, config constants, and CSV
column names are English. Config lives as UPPERCASE module-level constants at the top of
each script (`SYMBOL`, `TIMEFRAME`, `FAST_MA`, `SLOW_MA`, `BARS`, `CHECK_EVERY_SECONDS`,
`CSV_FILE`) — keep new settings in that same form rather than introducing a config file.

Broker symbol names vary (`XAUUSD`, `XAUUSD.m`, `GOLD`…). `SYMBOL` is hardcoded to
`"XAUUSD"` everywhere; use `list_symbols.py` to confirm what the connected broker actually
exposes before assuming it works.
