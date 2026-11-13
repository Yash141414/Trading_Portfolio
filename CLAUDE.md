# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**Tidy-up (2026-10-22):** all result folders (`results/` incl. `ANALYSIS.md`, `Q2_Report.pdf`, `development/`, `archive/`; `results_latest/`, `23-25_dhan/`, `23-26/`, `test_2025_26/`), the unused scripts (`build_strategy_data.py`, `build_universe_data.py`, `combine_dhan_options.py`, `dhan_g.py`, `explore.py`, `inspect_parquet.py`), unused data (`data/dhan_nifty_options/`, `data/tradeable_universe.parquet`, `data/universe/`, `data/nifty_opt_bhavcopy.csv`, both `nifty_options_1min.parquet` lookup files) and the logs / `interview_questions.txt` were moved to `_unused/`, keeping their relative paths: a path `X` mentioned below that no longer exists is now `_unused/X`. Nothing was deleted. The backtest still reproduces `_unused/results/stats.csv` and `_unused/results/development/stats.csv` exactly. Running `run_backtest.py` recreates `results/`; `make_pdf.py` needs `results/ANALYSIS.md`, so copy it back from `_unused/results/` first.

Quant Camp Q2 assignment: backtest three intraday NIFTY weekly-option strategies (Mean Reversion,
Directional, Semi-Directional) on 1-minute data over the last year, combine them into one portfolio
(target: combined Calmar >= 5), and submit equity curves, code, data and an analysis report.

## Environment

- Windows. Use the project venv (Python 3.10): `venv/Scripts/python`. No pytest, no linter configured.
- Run everything from this directory. All scripts use relative paths (`data/...`, `results/...`).
- This folder is **not its own git repo**: the enclosing repo root is the user's home directory
  (`C:/Users/yashc`). Never `git add -A` or commit from here without checking with the user.
- Dhan scripts need a paid Dhan Data API plan. `dhan_nifty_options_1y.py` only needs
  `DHAN_ACCESS_TOKEN` (the `/v2/charts/rollingoption` endpoint doesn't take a client-id per Dhan's
  docs); `dhan_expired.py` also wants `DHAN_CLIENT_ID`. The token lives in `.env` (not auto-loaded --
  source it yourself, e.g. `export DHAN_ACCESS_TOKEN=$(sed -n 's/^DHAN_ACCESS_TOKEN=[[:space:]]*"\(.*\)"[[:space:]]*$/\1/p' .env)`).

## Commands

`test_done.txt` is the register of every backtest run (folder, rule set, data, in/out-of-sample, result);
add a row whenever a new results folder is produced. `manual.md` is the user-facing guide: run order, what every script does, how they connect, and an
improvement list. Keep it in sync when scripts, outputs or the pipeline change.

`Analysis/` is the **email submission package** (rebuilt 2026-10-03 for the redesign, 12 MB):
`1_Report/Strategy_Report.pdf` (+ .md; theory, how the rules were chosen, development and test results,
strengths/weaknesses, robustness) and `Appendix_Full_Backtest_Output.pdf` (A = test year, B = development),
`2_Equity_Curves/` (test year + `development/`), `Code_and_Data.zip` (code and docs only), and
`3_Code_and_Data/` (runnable code incl. `research/`, one-month samples of each period, the slim bhavcopy,
`data/calendar_2023_2025.parquet`, and `results/` = test year with robustness + stress test,
`results/development/`). It is a snapshot: if strategies or results change, rebuild it (copy code, rerun
the package's own `run_backtest.py` for both periods, regenerate the PDFs with `make_pdf.py <md> --out <pdf>`)
and re-verify the package reproduces `results/stats.csv` and `results/development/stats.csv` exactly. Never
put `.env` in it. The package's code is a docstring-stripped copy (house style there), with a stress test in
place of before/after comparisons, and it presents only the current rules: no `MeanReversionV1`,
`DirectionalPriceFollow` or `*Fit2026` classes, no before/after; the earlier rule set is mentioned only as
the reason the test year is not perfectly clean.

```
# end-of-day NSE bhavcopy (lot sizes, real expiry dates, cross-checks)
venv/Scripts/python data.py bhavcopy --days 365
venv/Scripts/python read_parquet.py data/bhavcopy --rows 0 --out data/nifty_opt_bhavcopy.parquet
venv/Scripts/python explore.py                           # profile the combined bhavcopy

# (unused: backtest is Dhan-only) 1-minute NIFTY options from the Strategy repo; its output
# data/nifty_opt_1min.parquet was deleted 2026-09-29 and is rebuildable with:
venv/Scripts/python build_strategy_data.py                # -> data/nifty_opt_1min.parquet
# re-run this whenever nifty_options_clean.parquet / nifty_spot_minute.parquet in
# Strategy/data/research grow (e.g. 2026 data gets appended)

# (legacy, unused unless the Strategy source is unavailable) 1-minute expired options from Dhan
venv/Scripts/python dhan_expired.py check                # which expiryCode = nearest weekly (still unconfirmed)
venv/Scripts/python dhan_expired.py download --years 1   # resumable, one file per 30-day window
venv/Scripts/python dhan_expired.py combine              # -> data/nifty_opt_1min.parquet

# ATM+-10 rolling-offset Dhan data, 1 year, downloaded into data/final
venv/Scripts/python dhan_nifty_options_1y.py --days 365 --strikes 10 --out data/final
venv/Scripts/python combine_dhan_options.py --out data/final                    # real-strike lookup file (no offset/expiry)
venv/Scripts/python combine_dhan_options.py --skip-combine --at 2026-01-02 09:15   # option-chain snapshot at an exact minute
venv/Scripts/python build_dhan_backtest_data.py                                 # -> data/final/nifty_opt_1min.parquet (backtest-ready, adds expiry/dte/offset)
venv/Scripts/python run_backtest.py --data data/final/nifty_opt_1min.parquet --out results_dhan
venv/Scripts/python inspect_parquet.py <path>                                    # generic parquet profiler

# backtest -> results/ (report.md, charts, trades.csv, stats.csv). Default data is now the Dhan file
# (data/final/nifty_opt_1min.parquet, backtest.data.DATA); --split sets the development/held-out date.
venv/Scripts/python run_backtest.py --robustness
venv/Scripts/python run_backtest.py --end 2026-03-31 --out <scratch>/dev   # development half only, no held-out data loaded
venv/Scripts/python make_pdf.py        # results/ANALYSIS.md + results/report.md -> results/Q2_Report.pdf (reportlab)
# re-run make_pdf.py after every run_backtest.py or ANALYSIS.md edit so the PDF matches

# tests
venv/Scripts/python tests/test_engine.py                 # all known-answer tests
venv/Scripts/python -c "import tests.test_engine as t; t.test_short_leg_signs()"   # one test

# look-ahead check: synthetic random walk + fairly priced options, zero costs, several seeds
venv/Scripts/python tests/check_lookahead.py              # seeds 1-4 by default; --seeds 1 2 3 4 5 6
```

After any engine or strategy change, run `tests/check_lookahead.py` and confirm mean P&L per trade is
statistically zero (|t| < 2, sign varying across seeds). A consistently positive result means future
information is leaking. Synthetic data has no OI, so it doesn't cover Directional's put/call-ratio
path; for that, use a stale-signal check (ratio from a few bars earlier, same entry time).

## Data flow

```
data.py bhavcopy -> data/bhavcopy/nifty_opt_YYYYMMDD.parquet (raw NSE UDiFF columns)
  read_parquet.py -> data/nifty_opt_bhavcopy.parquet (+ .csv), readable column names

<Strategy>/data/research/nifty_options/nifty_options_clean.parquet (real strikes, expiry_code, CE/PE, 2023-01 on)
<Strategy>/data/research/nifty_spot/nifty_spot_minute.parquet (1-min NIFTY spot, same range)
  build_strategy_data.py -> data/nifty_opt_1min.parquet  (front-week contract only, adds expiry/dte/spot/lot_size)

backtest/data.py load_days() -> list[Day] -> run_backtest.py -> results/
```

- **Data source (current):** `build_strategy_data.py` reads two files from a sibling repo, `Strategy`
  (path `C:/Users/yashc/OneDrive/Desktop/Quant Camp/Strategy/data/research`, override with `--src`):
  a cleaned options dataset with real per-bar strikes and a real `expiry_code` (parsed as `%y%b%d`,
  e.g. `23JAN05` -> 2023-01-05), and a separate 1-minute NIFTY spot series. For each trading day it
  keeps only the front-week contract (nearest expiry on/after that date, picked from the expiry
  codes actually present in the data, not inferred from a weekday rule). Re-run it whenever those
  source files grow (the user is adding 2026 data to them over time).
- Because real strikes and real per-bar spot are available directly, this source carries **no**
  `offset` column (unlike the Dhan path below); `backtest/data.py load_days()` treats `offset` as
  optional and, when absent, resolves ATM via `Day.strike()`'s fallback (round spot to `strike_step`)
  instead of an ATM-relative pivot.
- Lot size: real per-date values from the bhavcopy where it overlaps (2025-09-29 on, 65/75 coexist);
  earlier dates use a fixed calendar cross-checked in the Strategy repo's `duck.py` (50 through
  2024-03, 25 through 2024-11, 75 through 2025-12) -- see `build_strategy_data.py:LOT_SIZE_CALENDAR`.
- The bhavcopy is also a lot-size dependency for the newest dates in this path, same as before.
- `read_parquet.py` renames NSE columns; the last-traded price is `ltp`, deliberately not `last`,
  because `df.last` resolves to the pandas method and silently breaks comparisons.
- Bhavcopy `close` is NSE's end-of-day close, not the last trade (it matches `ltp` only ~25% of the
  time), and 41% of rows are zero-volume with `close` carried over from the day before.
- **Legacy Dhan path (kept working, not the default):** `dhan_expired.py combine` produces the same
  `data/nifty_opt_1min.parquet` shape from Dhan's rolling (ATM-offset) API and *does* carry an
  `offset` column, which `load_days()` pivots into a real-strike grid. It also takes real expiry
  dates from the bhavcopy (falling back to a Thu-then-Tue weekday rule before its range).
- `dhan_nifty_options_1y.py` is a **second, independent** Dhan downloader (own script, own output),
  downloaded complete into `data/final/` (2026-09-29): 588 API calls, ATM+-10 CE/PE, 1 year
  (2025-09-29 -> 2026-09-29), 39,11,377 rows, 248 trading days, 104 distinct real strikes. Only
  needs `DHAN_ACCESS_TOKEN` (see Environment). Raw per-call chunks are in `data/final/raw/`;
  `combine_dhan_options.py` (separate from the script's own end-of-run combine, so it can be rerun
  anytime) merges them keyed on real `strike` (not the ATM-relative `strike_label` Dhan uses
  internally) into `data/final/nifty_options_1min.parquet`, and its `--at DATE TIME` mode prints +
  saves an option-chain snapshot (every real strike at that exact minute, CE/PE side by side).
  Columns: `iv, oi, strike, spot, open, high, low, close, volume, ts, type` -- no `date`/`expiry`/
  `dte`/`opt_type`, and critically **no expiry information at all** (the rollingoption calls here
  didn't request an expiry, unlike `dhan_expired.py`'s `infer_expiry`). `combine_dhan_options.py`'s
  own output is for lookups/snapshots only, not a backtest input as-is.
- **Wired into the backtest:** `build_dhan_backtest_data.py` reads the same `data/final/raw/`
  chunks (keeping `strike_label`, parsed into an `offset` column this time, and `oi`, which
  `load_days()` pivots into `Day.oi` for the Directional strategy's put/call ratio) and reuses
  `dhan_expired.py`'s `infer_expiry`/`_validate_expiry` (imported directly, not duplicated) to
  assign each day its nearest weekly expiry -- real bhavcopy expiry where the date falls in its
  range (245/248 days here), the Thu/Tue weekday rule elsewhere (3 days, all past the bhavcopy's
  upper bound). Writes `data/final/nifty_opt_1min.parquet` in the same shape `dhan_expired.py
  combine` produces, so `run_backtest.py --data data/final/nifty_opt_1min.parquet` reads it
  directly. `load_days()` skips 4 of 248 days on this file: the known 2025-10-21 short session,
  `2026-09-28`/`29` (past the bhavcopy's date range), and `2026-02-01` -- a Sunday with a full
  375-bar session in Dhan's data (likely an NSE special live-trading session) that the bhavcopy
  doesn't cover, so it has no lot size and is conservatively skipped rather than guessed.
  Known gaps (`data/final/incomplete_days.csv`, 44 of 10,416 series-days): 42 are all
  **2025-10-21**, a genuinely shortened NSE session (bars only 13:45-15:29, cross-confirmed by
  `build_strategy_data.py` independently skipping the same date) -- not a download defect, nothing
  to refetch. The other 2 are single-strike, single-day shortfalls on the deep-OTM `ATM-10` CE leg
  (2026-09-07, 2026-09-09), plausibly illiquid-strike gaps in Dhan's own data.
- The download was interrupted twice mid-run by Claude Code's own idle-memory reaper (not a script
  bug); it resumed cleanly both times since each 28-day/strike/side chunk is a separate file in
  `raw/` and already-existing files are skipped on rerun.

## Backtest architecture (`backtest/`)

- **Rolling data -> real contracts.** Dhan returns series by strike *offset* from ATM (ATM, ATM+1...),
  with the actual strike on each bar, so one series switches contracts as spot moves. `data.py` pivots
  bars back to real strikes into per-day `Day` arrays `[bar, strike]` (`close`, `open`), forward-filled
  within the day only. Positions are held in fixed strikes via `Day.px(i, opt, strike)`.
- **Timing rule (the no-look-ahead guarantee):** strategies decide on the close of bar `i` and
  `Position` fills at the open of bar `i+1`; exits likewise fill at the next open; everything is
  flat at the open of the 15:15 bar. New strategy code must keep this convention.
- **Strategy interface:** a class with `name` and `run_day(day, equity, cfg) -> list[Trade]` that
  loops over bars itself (one position at a time). To add one, register it in
  `strategies.default_strategies()` and give it a parameter grid in `run_backtest.GRID`.
- **Strategies (current) are rule-based and IN-SAMPLE.** `strategies.RuleStrategy` runs one `Rule` per
  day type (`far` = 3+ DTE, `dte1`, `exp`): a view from spot's move since the open or previous close up
  to `sig_t` (or, with `ref="pcr"`, from the put/call OI ratio over ATM+-5 strikes via `Day.pcr`),
  `fade`/`follow`, expressed as long / debit / credit spreads (`legs_for`), with exit time, optional range
  stop and a risk multiplier. Mean Reversion = fade rules, Directional = follow the OI ratio (PCR >= 1 ->
  bullish), Semi-Directional = credit spreads. Each trades **every day**. Rules and multipliers were
  selected on the full Dhan year (user's choice, 2026-09-29) to make all three profitable in both halves
  and reach combined Calmar >= 5 (achieved: 9.42, CAGR 47.9%, MDD -5.1%). An earlier design fixed on the
  first half only lost money on the second half, so the second half is not a clean out-of-sample test.
  Directional was switched from price-follow (fragile: every +-10 min shift lost) to the OI ratio,
  chosen for plateau robustness (grid Sharpe 1.56-2.45, still works with a 5-15 min stale signal).
  The old version is kept as `strategies.DirectionalPriceFollow` (not traded, not in
  `default_strategies()`).
- **Mean Reversion is v2** (robustness change, 2026-09-29): the dte1 rule is three tranches at
  10:20/10:35/10:50 (1x risk each) instead of one 10:30 entry at 3x, and far days buy ATM instead of
  1-strike OTM. Worst one-at-a-time stress Sharpe 1.15 -> 1.54, worst trade -50k -> -21k; cost: Calmar
  7.78 -> 7.04 (combined 9.59 -> 9.42). **To revert** (if the user asks): set `MeanReversion.rules =
  MeanReversionV1.rules` in `backtest/strategies.py` and re-run; v1's full results are archived in
  `results/archive/2026-09-29_mean_reversion_v1/` (incl. a copy of strategies.py).
- A day type's rule spec may be a tuple of `Rule`s = tranches (independent positions). Before/after
  pairs live in `run_backtest.COMPARISONS`; every run re-runs both versions and writes a
  "<Strategy>: before vs after" section plus `<slug>_before_after.csv/.png`. With `--robustness` it also
  runs `stress_variants()` (one-at-a-time: sig +-10/20m, exit +-30/60m, stop, kind) - takes ~2 min.
  Don't delete the reference classes (`DirectionalPriceFollow`, `MeanReversionV1`).
  Synthetic data has no OI, so `ref="pcr"` falls back to the move since the open there - the synthetic
  look-ahead check doesn't cover the OI path; use a stale-signal check instead. ~76% of Mean Reversion's
  profit is on dte1 days. The written analysis is `results/ANALYSIS.md`.
  `run_backtest.GRID` perturbs every rule's `sig_t` (+-10 min) and `risk` (x0.5, x1.5).
- **Sizing** (`strategies.size`, `strategies.worst_case`): lots so that the position's *worst-case* loss
  (premium for a long option, width minus credit for spreads, incl. round-trip slippage) is
  `risk_per_trade` (2%) x the rule's `risk` multiplier of the sleeve's equity, with a 1-lot floor as long
  as one lot's worst case is under 5% of equity (that floor is what guarantees a trade every day).
- **Costs** all live in `config.Costs`. The STT rate (0.1% on sells) is unverified for 2026 (Budget
  2026 may have raised it) and affects about half the backtest year.
- **Portfolio:** equal, fixed Rs 10 lakh sleeves; the combined curve is the sum of daily sleeve P&L.
- `report.py` regenerates `results/report.md` on every run. The written analysis goes in
  `results/ANALYSIS.md`, which the code never touches.

## Out-of-sample result (2026-10-02)

The user supplied `data/tradeable_universe.parquet` (NIFTY weeklies, 1-min, 2023-01-02 -> 2026-09-28,
ATM +-14, nearest expiry kept, lot sizes included, **no open interest**). `build_universe_data.py`
converts it to `data/universe/nifty_opt_1min_universe.parquet`. Running the unchanged strategies over
it (results in `23-26/`): out of sample (2023-01 -> 2025-09) the combined portfolio made CAGR -2.1%,
Sharpe -0.06, MDD -23.1%; the fitted year reproduces the Dhan results for Mean Reversion and
Semi-Directional, so it's a genuine out-of-sample failure, not a data issue. Without OI, Directional
runs its price fallback there. Robustness on 908 days exceeds the 10-minute background limit.

**Dhan 2023-2025 with OI (2026-10-02):** `dhan_nifty_options_1y.py --start 2023-01-01 --end 2025-09-29`
downloaded into `data/dhan_2023_2025/raw/` (1,512 chunks, 681 days, OI on every row; prices match the
universe file, median diff 0%). `build_dhan_backtest_data.py --raw ... --out data/dhan_2023_2025/nifty_opt_1min.parquet
--calendar data/universe/nifty_opt_1min_universe.parquet` takes expiry and lot size from the universe file (the
bhavcopy starts 2025-09-29) and drops 6 special-session days. Results in `23-25_dhan/`: the real OI Directional is
also flat out of sample (CAGR 0.8%, Sharpe 0.16, MDD -33%; 5 long-put trades in Dec 2023-Mar 2024 make all
the profit, 2025 lost); combined CAGR 1.5%, Calmar 0.07. Only dte1 days stay profitable across all three.

**Redesign (2026-10-02/03, user's choice: design on 2023-25, test on 2025-26).** `research/` holds the search
(`rule_search.py simulate` -> `data/dhan_2023_2025/search/`, `score_rules.py`, `portfolio.py`, `weight_search.py`,
chosen set `research/specs/v2.json`). `strategies.simulate()`/`view()` were split out of `_run_rule` (verified
identical stats). The **main classes `MeanReversion`/`Directional`/`SemiDirectional` now hold the redesigned rules**;
the previous (2025-26-fitted) rules are `MeanReversionFit2026`/`DirectionalFit2026`/`SemiDirectionalFit2026`, and
`run_backtest.COMPARISONS` compares those. Development: combined Calmar 5.53, all three positive every year.
Test year (`test_2025_26/`): combined CAGR -0.2%, Calmar -0.02; only Semi-Directional positive (Calmar 1.11).
The user chose (2026-10-03) to submit the redesign: `results/` is now the test year (`run_backtest.py
--robustness`), `results/development/` the development period, `results/ANALYSIS.md` + `Q2_Report.pdf` rewritten,
and `Analysis/` rebuilt. The old rules' outputs are archived in `results/archive/2026-10-03_rules_fit_2025_26/`.
`run_backtest.sample_note()` labels each run development (in-sample), test (out-of-sample) or mixed.

## Reporting standards for this project

- Report every Sharpe with its 95% CI and PSR (already in `metrics.summary`); one year is ~250 days.
- The user chose (2026-09-29) to tune on the backtest year to reach Calmar >= 5. That is allowed, but
  every tuned result must be labelled **in-sample** in the report and ANALYSIS.md, and the H1/H2 table
  and `--robustness` grid must stay in the report as the evidence on fragility. Never present tuned
  numbers as out-of-sample.
- Label anything computed on synthetic data as such; it says nothing about real performance.
