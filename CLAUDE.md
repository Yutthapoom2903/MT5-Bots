# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A multi-timeframe XAUUSD bot on MetaTrader 5. MA20/MA50 crossover on M15 is the trigger;
H1 sets direction, M5 confirms, and ADX/RSI/spread veto. It logs every closed candle with
its own verdict and reasoning, and places orders only when explicitly told to.

`run.py` is the single entry point — every capability is a subcommand of it. Do not add
standalone top-level scripts; add a subcommand instead. Earlier revisions had four separate
bot scripts each opening its own MT5 connection and re-implementing the crossover; they
were consolidated for exactly that reason.

```
run.py          CLI: check | symbols | signal | watch | trade | backtest | test | all
runner.py       the one loop — fetch once per candle, then log + decide + optionally trade
strategy.py     pure decision engine: context dict in, Decision out. No MT5 imports.
mt5_core.py     connect, symbol setup, rates, indicators, the crossover rule, logging, state
mt5_trade.py    broker-facing only: price/volume normalization, stop distance, filling
                mode, risk sizing, order send/close. Imported by runner.py alone.
backtest_engine.py  scores the hand-labelled columns in market_training_data.csv
tests/          40 logic tests, no MT5 required
```

## Running

The `MetaTrader5` package is **Windows-only** and talks to an already-running, logged-in
terminal with Algo Trading enabled. The repo lives under WSL, so most commands cannot run
from the Linux side.

`run.py` defers all MT5 imports into the individual command functions, so `backtest` and
`test` work anywhere pandas is installed. Keep it that way — do not add a module-level
`import MetaTrader5` to `run.py`.

```bash
python run.py test         # 40 logic tests, runs under WSL
python run.py backtest     # runs under WSL
pytest tests/              # same tests, if pytest is installed
```

`tests/stubs/MetaTrader5.py` supplies constants when the real package is missing and is
skipped when it is present, so the suite tests against real constants on Windows. When you
reference a new `mt5.CONSTANT` in library code, add it to the stub or the suite breaks
under WSL.

**`run.py trade` places real orders.** It refuses to start on a non-demo account unless
`ALLOW_LIVE_ACCOUNT` is `True` in `runner.py` — never flip that flag on the user's behalf,
and never run the command without explicit confirmation.

## Invariants

**Closed candles only.** `df.iloc[-2]` (`core.CLOSED`) against `df.iloc[-3]`
(`core.PREVIOUS`). `df.iloc[-1]` (`core.FORMING`) is the candle still building and must
never reach a decision — reading it makes signals flip mid-candle. `core.crossover_signal()`
and `core.ma_trend()` are the only implementations; nothing may re-derive them.

**One fetch per cycle.** `runner.build_context()` pulls M15/H1/M5 once and returns a plain
dict. `strategy.evaluate()` takes that dict and nothing else — keep it free of MT5 imports
so it stays testable.

**Every verdict carries its reasons.** `Decision.checks` records each filter's pass/fail
and the numbers behind it; blockers land in `bot_blockers` in the feature CSV and in
`bot.log`. A filter that silently returns a boolean is a regression.

**The candle timestamp guard persists.** `bot_state.json` holds the last processed candle
so a restart mid-candle cannot re-fire an order.

## Risk model

Do not reintroduce fixed point-based stops. On XAUUSD `point` is `0.01`, so the original
`300 * point` stop was **$3.00** against an ATR near `$11` — inside the noise and usually
inside the broker's minimum stop distance.

- SL/TP are `ATR × SL_ATR_MULT` / `ATR × TP_ATR_MULT`, floored at
  `trade.min_stop_distance()` (max of `trade_stops_level`, 3× spread, 10 points — brokers
  reporting `trade_stops_level = 0` use a dynamic spread-based limit).
- Levels come from the live tick, never the closed candle's `close`.
- Lot size derives from `RISK_PERCENT` and the SL distance via `trade_tick_value` /
  `trade_tick_size`, floored to `volume_step`.
- **`normalize_volume()` clamps up to `volume_min`, which can silently exceed the risk
  budget on a small account** — 0.01 lot with a $16 stop risks $16, which is 1.6% of a
  $1,000 account, not the configured 1%. `trade.lot_exceeds_budget()` catches this and
  `runner.execute()` refuses the trade unless `ALLOW_RISK_OVER_BUDGET`. Any new sizing path
  must keep that check.
- `pick_filling_modes()` reads the symbol's `filling_mode` bitmask and retries down the list
  on retcode 10030. Retcodes are numeric constants in `mt5_trade.py` because their names
  vary across package versions.

`run.py check` prints this arithmetic against the live account and is the fastest way to
answer "why is the bot not entering anything".

## Data files

CSVs are appended in place (`mode="a"`, header only when absent) and committed to git —
accumulating data, not build output. `bot.log` and `bot_state.json` are gitignored.

- `signal_log.csv` — one row per closed candle.
- `trade_log.csv` — one row per order attempt, successes and failures alike.
- `market_training_data.csv` — full market state plus `bot_decision` / `bot_blockers`, with
  `your_decision`, `your_reason`, `entry_price`, `stop_loss`, `take_profit`, `trade_result`
  left blank for the user to fill in by hand. `backtest_engine.py` reads only the
  hand-labelled columns — it scores the human, not the bot.

Column sets have changed over time: rows before 2026-09-09 used a simple rolling mean for
RSI/ATR (now Wilder) and lack `adx_14`, `m5_trend`, `bot_decision`, `bot_blockers`.

## Conventions

Console output, log messages, docstrings, and comments are Thai; identifiers, config
constants, and CSV column names are English. Config lives as UPPERCASE module-level
constants at the top of `runner.py` (market, risk, files) and `strategy.py` (filter
switches) — no config file. Strategy filters must stay individually toggleable so their
effect can be measured one at a time.

Unrecoverable MT5 failures raise `core.MT5Error`; `run.py` catches it and puts
`mt5.shutdown()` in a `finally`.

Broker symbol names vary (`XAUUSD`, `XAUUSD.m`, `GOLD`…). `SYMBOL` in `runner.py` is
hardcoded to `"XAUUSD"`; `run.py symbols` confirms what the connected broker exposes.

## Outstanding

- Never verified against a live MT5 terminal. Broker behaviour — retcodes, stop levels,
  filling modes — is unproven. Demo first.
- The Telegram token in the first two commits (`git show a30efaf:.env`) has not been
  confirmed rotated. `.env` is untracked now, but the old value remains in history.
