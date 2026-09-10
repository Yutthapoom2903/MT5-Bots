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

`run.py menu` is a second door onto the same subcommands for when the command names are
the thing in the way — it dispatches through `run.COMMANDS`, so a capability wired into
`command_all()` and the parser needs one line in `menu.items()` and nothing else.
`test_every_menu_entry_points_at_a_command_that_exists` catches a stale name. It is not
a replacement for the bare invocation and must never become one. Everything in `menu.py`
except `run()` is pure — `items()`, `render()`, `choose()`, `status_lines()` take values
and return values, so the tests press keys without a terminal. No new dependency: a
curses or textual TUI would install on one of the two machines and not the other.

**Bare `python run.py` must do the whole job end to end** — resolve the symbol, check the
account, backtest, sweep, then watch — because that is what the user asked for and keeps
asking for. Adding a capability means wiring it into `command_all()`, not handing the user
another command to remember. Analysis phases go through `_try_phase()` so a failure there
(thin history, say) still falls through to the live loop. The top-level parser carries
defaults for every flag `command_all` reads, so the bare invocation works with no
subcommand.

```
run.py          CLI: menu | check | symbols | signal | watch | trade | backtest
                     sweep | report | review | outcomes | notify | test | all
runner.py       the one loop — fetch once per candle, then log + decide + optionally trade
strategy.py     pure decision engine: context dict in, Decision out. No MT5 imports.
notify.py       Telegram: categories, formatting, anti-spam. No MT5 imports either.
mt5_core.py     connect, symbol setup, rates, indicators, the crossover rule, logging, state
mt5_trade.py    broker-facing only: price/volume normalization, stop distance, filling
                mode, risk sizing, order send/close. Imported by runner.py alone.
outcomes.py     labels each logged candle with what the market did next, then measures
                the filters against those labels. Pure, no MT5.
backtest_engine.py  scores the hand-labelled columns in market_training_data.csv
backtest.py     historical simulation — pure, mirrors the live rules
menu.py         numbered menu over the same subcommands. Pure except run().
report.py       offline digest of the CSVs and bot.log
tests/          166 logic tests, no MT5 required
```

## Running

The `MetaTrader5` package is **Windows-only** and talks to an already-running, logged-in
terminal with Algo Trading enabled. The repo lives under WSL, so most commands cannot run
from the Linux side.

`run.py` defers all MT5 imports into the individual command functions, so `backtest` and
`test` work anywhere pandas is installed. Keep it that way — do not add a module-level
`import MetaTrader5` to `run.py`.

`requirements.txt` pins `MetaTrader5`, which has no Linux wheel, so `pip install -r` fails
under WSL. Install the rest into a venv instead — `.venv/` is gitignored:

```bash
python3 -m venv .venv && .venv/bin/pip install pandas requests python-dotenv
.venv/bin/python run.py test
```

```bash
python run.py test         # 166 logic tests, runs under WSL
python run.py review       # runs under WSL
python run.py notify --dry # prints every notification shape, runs under WSL
python run.py report       # runs under WSL (backtest/sweep need MT5 for history)
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
so a restart mid-candle cannot re-fire an order. It also holds `position_risk` (1R per
ticket, since the live SL moves after entry), `day` / `day_start_balance`, and the last
daily summary. Entries are pruned when their ticket closes.

**Stops only ever move toward profit.** `trade.better_stop()` is the only way a stop is
updated; a candidate that would widen risk returns `None`. Never bypass it.

**Two cadences in one loop.** Position management runs every cycle (30s) because price
moves between candles; entry decisions run only when the closed-candle timestamp changes.
Do not collapse them.

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

Over-budget behaviour differs by account type: a demo account logs a warning and proceeds
(the point of demo is to see the bot trade), a live account refuses unless
`ALLOW_RISK_OVER_BUDGET`. `run.py check` prints this arithmetic against the live account and
is the fastest way to answer "why is the bot not entering anything".

## Notifications

`notify.py` is the only place that talks to Telegram. It mirrors `strategy.py`'s shape on
purpose: pure formatting functions plus a registry with one switch per item, so a category
can be turned off to measure its noise the way a filter can be turned off to measure its
effect. It imports no MT5 and is fully covered by the offline suite.

Eight categories, each with its own `SEND_*` switch, icon, loudness, and cooldown:
`lifecycle`, `market`, `signal`, `entry`, `manage`, `exit`, `risk`, `summary`.
`SEND_HOLD` and `SEND_NEAR_MISS` split the `signal` category further, because a verdict
that passed every filter and a candle that produced no crossover differ in frequency by two
orders of magnitude.

- **`format_*()` is pure, `Notifier` sends.** Anything that builds a string must stay on the
  pure side so a test can assert on it without a token.
- **`format_message()` escapes the title only.** Body lines are HTML by design — callers pass
  `<code>`/`<b>` — so any text coming from the broker, an exception, or a filter's detail
  must go through `notify.escape()` at the call site. `check_lines()` already does.
- **Anti-spam is two rules.** An identical body under the same key is dropped for
  `DEDUP_SECONDS`; a category with a non-zero `cooldown` drops *any* message under the same
  key for that long. Market-closed uses the second one — a weekend would otherwise send a
  message every `MARKET_CLOSED_SLEEP`. Give paired events different keys, or the cooldown on
  one swallows the other (`market-closed` vs `market-open`).
- **State is in memory, not `bot_state.json`.** A restart repeating one message is fine; a
  restart going silent because it wrongly believes it already sent is not.
- **Failures never propagate.** `_post()` retries once, honours a 429 `retry_after`, and on
  400 re-sends with the tags stripped rather than losing the message.

`report_closed_positions()` is the one notification path that cannot be proven offline:
`history_deals_get(position=...)` has never run against a real terminal and the keyword is
not in every package version. So `trade.closing_deals()` swallows the failure and returns
`None`, and the caller keeps the ticket's `position_meta` for `CLOSE_LOOKUP_ATTEMPTS`
cycles before giving up — deal history does not always land the instant a position closes,
and pruning on the first miss means the close is never reported at all. Reporting a closed
trade is a bonus; it must never take down the loop that is managing a live stop.

`run.py notify --dry` renders one sample of every message shape offline. It calls every
`Notifier` event method, so a shape that raises fails there instead of at 3am.

`run.py notify --check` answers "why is nothing arriving". A misconfigured `.env` makes the
bot silent without an error, and the failure is almost never in this repo — it is a token
that was revoked, a `chat_id` that belongs to nothing, or the fact that **a bot cannot open
a conversation**, so the human must press Start before the first message can ever land.
`diagnose()` walks the chain — token shape, `getMe`, `chat_id`, `getUpdates`, a real test
send — and `describe_api_error()` maps Telegram's reply to the thing to go and fix rather
than echoing a status code. It also prints the category switches, because with `SEND_HOLD`
off and no crossover in the data, silence is the correct behaviour and looks identical to a
broken setup. `SEND_HOLD` currently ships **on** for exactly that reason — a message every
candle is the cheapest proof the pipe is open. Turn it off once the setup is trusted.

`misplaced_secret_hint()` covers the trap that actually bit: values typed into
`.env.example` instead of `.env`. python-dotenv reads only `.env`, so the bot goes silent
with no error — and `.env.example` is tracked, so filling it in also queues the secret for
the next commit. Any switch flipped in a test must be restored to what it *was*, not to a
hardcoded default; `SwitchedTo` exists because a `finally` that wrote `False` back masked
this very change to the default.

## Backtesting

`backtest.simulate()` is pure — DataFrames in, results out — and must stay that way so it
runs under the test stub. Two rules keep its numbers honest:

- **No lookahead.** `_align()` maps each M15 bar to the last *closed* higher-timeframe bar
  (H1 lags 45 minutes, M5 leads 10) exactly as `iloc[-2]` does live. Entries fill at the
  *next* bar's open, never the signal bar's close.
- **Worst-case intrabar.** When one bar touches both stop and target, the stop wins. Never
  "improve" this — it is what stops a backtest flattering the strategy.

Stop progression reuses `mt5_trade.breakeven_level` / `trailing_level` / `better_stop`, so
the simulation cannot drift from live behaviour. If you change a stop rule, change it in
`mt5_trade.py` and both paths follow.

**`backtest.signal_series()` is a vectorised copy of the crossover rule** — the one place
the invariant is duplicated, because slicing the frame per bar made the loop O(n²) (a
27-combo sweep took 166s; it now takes 0.3s). `test_vectorised_signal_matches_the_live_rule`
compares it against `core.crossover_signal()` bar by bar. Never edit it without running
that test. The inner position loop takes numpy arrays, not DataFrames, for the same reason.

`sweep()` prepares indicators once and reuses them, and restores `strategy.ADX_MIN` in a
`finally`. Its point is robustness, not optimisation — a grid that is profitable only in one
cell is noise, and `format_sweep()` says so rather than reporting a winner.

Results are in R (risk multiples), not currency — independent of balance and lot size.
`compare()` runs with and without filters; that delta is the point of the tool.

`run.py backtest` is the simulation (needs MT5 for history); `run.py review` scores the
user's hand-labelled CSV (offline). Do not swap those names back.

## Unattended operation

The bot is meant to run at home without a watcher, so the loop is defensive:

- `connection_is_alive()` / `reconnect()` — `terminal_info()` returning `None` means the
  terminal closed or the link dropped; the loop reconnects instead of failing forever.
- Circuit breaker in `trading_allowed()` — daily loss percent, trades per day, consecutive
  losses. It reads MT5 deal history through `trade.deals_today()` every check rather than
  counting in memory, so a mid-day restart does not reset the limits. It blocks new entries
  only; open positions keep being managed. The halt reason is announced once, not per
  candle.
- `roll_over_day()` resets `day_start_balance` and sends the previous day's summary.
- `heartbeat()` posts equity, open positions and the last candle every
  `HEARTBEAT_EVERY_HOURS`, timestamped in state so a restart does not spam.
- `bot.log` records DEBUG (every cycle, every filter check with its numbers, position state)
  while the console stays at INFO. It rotates at 5MB x 5 backups. `run.py report` is the
  intended way to read all of it back — it groups repeated log lines by shape, so a
  thousand identical warnings collapse to one row with a count.
- Market closed (`symbol_is_tradable()` false) backs the poll off to `MARKET_CLOSED_SLEEP`.

**The bot runs in nightly sessions, not around the clock** — it is started on getting home
and stopped on waking, so most of every day has no data by design. `split_sessions()` is
built on that: a gap longer than `SESSION_BREAK_HOURS` starts a new session and is never
counted against the bot, while a shorter gap is a dropout inside a run and is. Measuring
uptime against 24 hours instead reported 29% for a night that actually captured 83% of the
candles it was running for, which is the kind of number that gets a section ignored.

`report.py` answers the daily questions in this order: **did it stay up while it was meant
to** (`summarise_coverage()`), **which hours does the data even cover** (`summarise_hours()`
— one session a day is roughly 11 hours of 24, so every statistic below it describes that
window and not the market), **what changed day to day** (`summarise_by_day()`, which stays hidden
until there are two days to compare), and **how far each candle sat from the thresholds**
(`summarise_filter_margins()` reads `ADX_MIN` / `MAX_SPREAD_POINTS` / the RSI bounds
straight out of `strategy.py`, so the numbers cannot drift from the live filters). That
last one is descriptive on purpose — a pass rate is what the market did, not an argument
for moving a threshold; the sweep is where thresholds get judged.

`trade.summarize_deals()` is pure and takes a deal list so the breaker is testable; only
`deals_today()` touches MT5.

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
RSI/ATR (now Wilder) and lack `adx_14`, `m5_trend`, `bot_decision`, `bot_blockers`. Rows
before 2026-09-10 lack `broker_gmt_offset`.

`candle_time` is **broker server time**, which is usually GMT+2/+3 and shifts with DST —
and brokers are documented to handle those transitions inconsistently. Every row therefore
carries `broker_gmt_offset` from `core.broker_gmt_offset()`. Without it, hour 17 before a
DST change and hour 17 after it are different hours with no way to tell them apart, and
`summarise_hours()` would blend them silently; it now prints the offset and calls out a
file that contains more than one. Anything reading a column that old rows may not have
must go through `report._numeric()` / the guard in `outcomes.numbers()` — `frame.get()`
alone returns `None`, and `pd.to_numeric(None)` is a bare float with no `.dropna()`.

`core.append_csv()` handles that drift. Writing the header only when the file was absent
left `market_training_data.csv` with a 22-column header above 26-column rows, so pandas
shifted every value in the file and `backtest_engine.py` scored the wrong columns. The
append now compares the file's header against the row's keys and calls
`align_csv_columns()` to rewrite the file — old rows padded with blanks, rows already
written under the newer schema re-mapped positionally — before appending. Adding a column
to a log row is therefore safe; renaming one still orphans the old column's data.

## Measuring the filters against collected data

`outcomes.py` answers the question the hand-labelled columns cannot answer for months:
**does a filter actually separate anything?** `market_training_data.csv` already holds OHLC
for every closed M15 candle, so the rows after a candle *are* its future — no MT5, no
waiting for a human to fill in `trade_result`.

The point is density. Crossovers arrive two or three times a day, so signal-outcome pairs
accumulate at perhaps 60 a month; every candle gets a forward label, so those accumulate at
2,800 a month. Filters are what the bot spends almost all of its time doing, and this is the
only path that measures them at the rate they actually run.

- **Labels are in R, not currency** (`ATR × SL_ATR_MULT`), the same unit `backtest.py`
  reports, so a day with ATR 13 and a day with ATR 6 can sit in the same average.
  `outcomes.SL_ATR_MULT` must equal `runner.SL_ATR_MULT`;
  `test_the_forward_label_uses_the_same_stop_as_the_live_bot` pins it.
- **`_usable_rows()` never counts forward across a gap.** If the bot was down for an hour,
  the next row in the file is not the next candle in the market, and a window that straddles
  the hole labels a candle with someone else's future. Those rows are dropped, not guessed —
  which is why 39 collected candles yielded only 14 labels.
- **`fwd_trend_r` is signed by the H1 trend**, so positive always means "the trend
  continued" whichever way price went. Flip that sign and every trend filter looks harmful.
- **This is not a backtest.** It measures the raw market after a candle: no spread, no
  stop progression, no TP, no position management. `backtest.simulate()` models all of
  that. Never compare the two numbers directly, and never quote an `outcomes` R as a
  profit.
- **`MIN_SAMPLE` is the honesty guard.** `format_split()` still prints the difference when
  a side is thin, but labels it noise. A 0.5R edge over four candles is four candles.

## Deliberately not built

- **Multiple symbols.** Every risk limit, the circuit breaker, and the state file are
  single-symbol. Adding symbols multiplies exposure and needs per-symbol config and
  correlation handling; it should wait until the single-symbol version has a proven record.
- **Session filter is off by default.** `SESSION_HOURS` is broker server time, which is
  usually GMT+2/+3 and shifts with DST. `core.broker_gmt_offset()` reports it (shown by
  `run.py check` and at startup); do not enable the filter without setting hours from that.

## Conventions

Console output, log messages, docstrings, and comments are Thai; identifiers, config
constants, and CSV column names are English. Config lives as UPPERCASE module-level
constants at the top of `runner.py` (market, risk, files) and `strategy.py` (filter
switches) — no config file. Strategy filters must stay individually toggleable so their
effect can be measured one at a time.

Unrecoverable MT5 failures raise `core.MT5Error`; `run.py` catches it and puts
`mt5.shutdown()` in a `finally`.

Broker symbol names vary (`XAUUSD`, `XAUUSD.m`, `GOLD`…). `SYMBOL` in `runner.py` defaults
to `"XAUUSD"`, and `core.resolve_symbol()` falls back to a ranked search when the broker
does not have it; `command_all` assigns the result to `runner.SYMBOL` for the session and
prints the alternatives it rejected.

That ranking is load-bearing: `XAUEUR.m` and `XAUUSD.m` are the same length and share the
`XAU` prefix, so ordering by name alone picks gold-against-the-euro — a different market.
Candidates starting with the full requested name win first.
`test_gold_against_another_currency_is_not_mistaken_for_the_dollar_pair` pins it.

## Outstanding

- Never verified against a live MT5 terminal. Broker behaviour — retcodes, stop levels,
  filling modes — is unproven, and `python run.py` alone will never prove it: watch mode
  writes `signal_log.csv` and `market_training_data.csv` on every closed candle but never
  sends an order, so `trade_log.csv` stays empty. Only `run.py --trade` on a demo account
  exercises that path. `report.py` says so while the file is missing.

## Closed

- **The leaked Telegram token is retired.** It sat in `.env` in the first two commits.
  On 2026-09-10 `git filter-branch --index-filter` dropped the file from every commit and
  `main` was force-pushed (all SHAs below `2700dec` changed; the old `a30efaf` is gone),
  and the user then revoked the token at @BotFather the same day. The revocation is the
  part that actually retired it — old clones still carry the string and GitHub may keep
  pre-rewrite commits reachable by SHA, but the string no longer opens anything. Nothing
  further is owed here; do not reopen it.
