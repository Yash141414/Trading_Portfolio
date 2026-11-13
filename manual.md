# Q2 Project Manual

**Tidy-up (2026-10-22):** all result folders (`results/` incl. `ANALYSIS.md`, `Q2_Report.pdf`, `development/`, `archive/`; `results_latest/`, `23-25_dhan/`, `23-26/`, `test_2025_26/`), the unused scripts (`build_strategy_data.py`, `build_universe_data.py`, `combine_dhan_options.py`, `dhan_g.py`, `explore.py`, `inspect_parquet.py`), unused data (`data/dhan_nifty_options/`, `data/tradeable_universe.parquet`, `data/universe/`, `data/nifty_opt_bhavcopy.csv`, both `nifty_options_1min.parquet` lookup files) and the logs / `interview_questions.txt` were moved to `_unused/`, keeping their relative paths: a path `X` mentioned below that no longer exists is now `_unused/X`. Nothing was deleted. The backtest still reproduces `_unused/results/stats.csv` and `_unused/results/development/stats.csv` exactly. Running `run_backtest.py` recreates `results/`; `make_pdf.py` needs `results/ANALYSIS.md`, so copy it back from `_unused/results/` first.

How to run every script in this project, what each one does, how they connect, and what could be
improved. The final deliverable is a backtest of three intraday NIFTY weekly-option strategies (Mean
Reversion, Directional, Semi-Directional) and their equal-weight portfolio, on one year of 1-minute
Dhan data (2025-09-30 → 2026-09-25).

> **All results are in-sample.** The strategy rules and sizes were selected on the same year they
> are reported on. See `results/ANALYSIS.md` before quoting any number.

---

## 1. Quick start: reproduce the final results

Run everything from the project folder (`Quant Camp/Q2`) using the project's Python environment.

```
# 0. one-off: Dhan access token in the environment (see section 2)
export DHAN_ACCESS_TOKEN=$(sed -n 's/^DHAN_ACCESS_TOKEN=[[:space:]]*"\(.*\)"[[:space:]]*$/\1/p' .env)

# 1. download 1 year of 1-minute NIFTY options from Dhan   (skip if data/final/raw/ is complete)
venv/Scripts/python dhan_nifty_options_1y.py --days 365 --strikes 10 --out data/final

# 2. build the backtest input file (adds expiry, days to expiry, ATM offset, open interest)
venv/Scripts/python build_dhan_backtest_data.py

# 3. run the backtest, robustness grid, stress tests and before/after comparisons -> results/
venv/Scripts/python run_backtest.py --robustness

# 4. build the PDF (written analysis + generated report) -> results/Q2_Report.pdf
venv/Scripts/python make_pdf.py

# checks after any change to the engine or a strategy
venv/Scripts/python tests/test_engine.py
venv/Scripts/python tests/check_lookahead.py
```

Steps 1 and 2 only need re-running when the raw data changes. Step 3 takes about 2 minutes with
`--robustness` (about 1 minute without). Step 4 takes a few seconds. `results/ANALYSIS.md` is written
by hand and is **never** overwritten by the scripts. Update it if the numbers change, then re-run step 4.

In PowerShell, set the token with:

```
$env:DHAN_ACCESS_TOKEN = (Get-Content .env | Select-String 'DHAN_ACCESS_TOKEN=\s*"(.*)"').Matches.Groups[1].Value
```

---

## 2. Environment

| Item | Detail |
|---|---|
| Python | The project venv, Python 3.10: always call `venv/Scripts/python`, not the system Python. |
| Packages | pandas, numpy, pyarrow, requests, matplotlib, reportlab (PDF), python-dotenv and tqdm (`dhan_g.py` only). There is no `requirements.txt` yet (see section 7). |
| Dhan token | `DHAN_ACCESS_TOKEN` in `.env`. It expires daily; regenerate it on Dhan's site. Only `dhan_g.py` reads `.env` by itself. The other Dhan scripts read the environment variable, so export it first (section 1). |
| Dhan client id | Not needed by `dhan_nifty_options_1y.py` (the expired-options endpoint takes only the token). `dhan_expired.py` (legacy) still asks for `DHAN_CLIENT_ID`. |
| Data plan | Downloading expired options needs a paid Dhan Data API subscription. |
| Git | This folder is not its own git repository (the enclosing repo is the home folder). Don't `git add -A` from here. |
| Tests | No pytest. The test scripts are run directly with Python. |

---

## 3. How the pieces connect

```
                  NSE bhavcopy (end of day)                         Dhan expired-options API (1-minute)
                            |                                                      |
            data.py bhavcopy --days 365                  dhan_nifty_options_1y.py --out data/final
                            |                                                      |
             data/bhavcopy/*.parquet (daily)                    data/final/raw/*.parquet (588 chunks)
                            |                                           |                        |
     read_parquet.py data/bhavcopy --out ...          build_dhan_backtest_data.py    combine_dhan_options.py
                            |                         (expiry via dhan_expired.infer_expiry,       |
         data/nifty_opt_bhavcopy.parquet ------------> lot sizes from the bhavcopy)   data/final/nifty_options_1min.parquet
           (lot sizes, true expiries)                           |                     (lookup / snapshots only)
                            |                                   v
                      explore.py          data/final/nifty_opt_1min.parquet   <-- the backtest's input
                                                                |
                                          backtest/ package  <--+-- run_backtest.py --robustness
                                          (data, engine,        |
                                           strategies,          v
                                           metrics, report)  results/  (report.md, charts, CSVs)
                                                                |
                                          results/ANALYSIS.md --+-- make_pdf.py --> results/Q2_Report.pdf
```

The bhavcopy is a real dependency of the final pipeline, not only exploration. The backtest takes
each day's **lot size** from it, and `build_dhan_backtest_data.py` takes each day's **true expiry
date** from it. The Dhan data has no expiry field, and outside the bhavcopy's date range a
Tuesday/Thursday weekday rule is used instead.

---

## 4. Script reference

### 4.1 Final pipeline

#### `dhan_nifty_options_1y.py`: download the 1-minute option data

- **What:** Calls Dhan's expired-options endpoint (`POST /v2/charts/rollingoption`) for every
  strike label from ATM−N to ATM+N, CE and PE, in 28-day chunks. Dhan's data is *rolling*: each series
  is an offset from ATM (ATM, ATM+1, …), and the real strike is reported on every bar.
- **Run:** `venv/Scripts/python dhan_nifty_options_1y.py --days 365 --strikes 10 --out data/final`
- **Options:** `--days` (history length, default 365), `--strikes` (ATM ± N, max 10),
  `--expiry WEEK|MONTH`, `--expiry-code` (1 = nearest expiry), `--sleep` (seconds between calls),
  `--out` (folder, default `data/dhan`).
- **Output:** One file per call in `<out>/raw/`, named `<CALL|PUT>_<label>_<start>.parquet`, and a
  combined `<out>/nifty_options_1min.parquet`. Series-days with fewer than 370 bars are listed in
  `<out>/incomplete_days.csv`.
- **Resumable:** Chunks already on disk are skipped, so an interrupted run can simply be restarted.
  The full run is 588 calls. It was interrupted twice by Claude Code's idle-memory reaper, not by any
  script fault.
- **Feeds:** `build_dhan_backtest_data.py` and `combine_dhan_options.py` both read `<out>/raw/`.
- **Known data gaps:** 2025-10-21 (a genuinely short NSE session, 13:45–15:29) and two single-strike
  shortfalls. See `data/final/incomplete_days.csv`.

#### `build_dhan_backtest_data.py`: make the backtest input file

- **What:** Reads `data/final/raw/`.
  - Turns `strike_label` into a numeric `offset` and keeps open interest.
  - Assigns each day's nearest weekly expiry, reusing `dhan_expired.py`'s `infer_expiry` and
    `_validate_expiry`: bhavcopy expiry where available (245 of 248 days), weekday rule otherwise.
  - Adds the days to expiry (`dte`).
- **Run:** `venv/Scripts/python build_dhan_backtest_data.py` (defaults: `--raw data/final/raw`,
  `--out data/final/nifty_opt_1min.parquet`).
  - `--calendar <parquet>` takes per-date expiry and lot size from any file with `date`, `expiry`,
    `lot_size` columns instead of the bhavcopy. It drops days missing from that file. This is used
    for the 2023-2025 download, which predates the bhavcopy:
    `--raw data/dhan_2023_2025/raw --out data/dhan_2023_2025/nifty_opt_1min.parquet
    --calendar data/universe/nifty_opt_1min_universe.parquet` (results in `23-25_dhan/`).
- **Output:** `data/final/nifty_opt_1min.parquet`, the file `run_backtest.py` reads by default
  (`backtest.data.DATA`).
- **Depends on:** `dhan_expired.py` (import only) and `data/nifty_opt_bhavcopy.parquet`.

#### `run_backtest.py`: the backtest

- **What:**
  - Loads the days, runs the three strategies (each on its own Rs 10 lakh sleeve) and builds the
    combined portfolio.
  - Computes all statistics, splits the year into two halves (`--split`, default 2026-04-01) and
    measures correlations.
  - Runs the before/after comparisons (`COMPARISONS`). With `--robustness` it also runs the parameter
    grid (`GRID`) and the one-at-a-time stress test (`stress_variants`).
- **Run:** `venv/Scripts/python run_backtest.py --robustness`
- **Options:**
  - `--data`: input file.
  - `--start` / `--end`: dates. The default is the last year of the data.
  - `--out`: output folder, default `results`.
  - `--robustness`: also run the grid and the stress test.
  - `--split`: the date dividing the two halves.
- **Output (`results/`):**
  - Report and charts: `report.md`, `equity_curves.png`, and one chart per strategy.
  - Data: `trades.csv`, `daily_equity.csv`, `stats.csv`, `robustness.csv`.
  - One `<strategy>_before_after.csv/.png` pair per comparison.
- **Uses:** the whole `backtest/` package.
- **Development-only run** (no data after the split loaded):
  `run_backtest.py --end 2026-03-31 --out <scratch>/dev`

#### `make_pdf.py`: the PDF deliverable

- **What:** Converts `results/ANALYSIS.md` (Part 1) and `results/report.md` (Part 2) into one A4
  PDF, with a cover page, tables, charts and page numbers. Uses Windows Arial so characters like − ≥ ±
  → render correctly.
- **Run:** `venv/Scripts/python make_pdf.py` (optional `--out path.pdf`).
- **Output:** `results/Q2_Report.pdf`. Re-run it after every backtest or analysis edit.
- **Limits:** It handles only the Markdown these two files use: headings, paragraphs, bullets,
  numbered lists, block quotes, pipe tables, images, and inline bold / italic / code / links.

#### `combine_dhan_options.py`: option-chain lookup

- **What:**
  - Merges `data/final/raw/` into one file keyed on the **real** strike, without the ATM label.
  - Optionally prints and saves the option chain at one exact minute: every strike with CE and PE
    close, OI, volume and IV.
- **Run:**
  - `venv/Scripts/python combine_dhan_options.py`: rebuilds the lookup file.
  - `venv/Scripts/python combine_dhan_options.py --skip-combine --at 2026-01-02 09:15`: snapshot.
- **Output:** `data/final/nifty_options_1min.parquet`, plus `data/final/snapshot_<date>_<time>.csv`
  for each snapshot. It has no expiry column, so the backtest can't use it. It's for looking things
  up.

#### `inspect_parquet.py`: profile any parquet file

- **What:** Shape, dtypes, nulls, cardinality, constant columns, numeric summary, date ranges,
  value counts, a sample of rows, and duplicates.
- **Run:** `venv/Scripts/python inspect_parquet.py <path> [--rows 10] [--cols a,b,c]`. With no path
  it opens `data/dhan_nifty_options/nifty_option_contracts.parquet`.

### 4.2 End-of-day bhavcopy (lot sizes and true expiries)

| Script | What it does | Run | Output |
|---|---|---|---|
| `data.py` | Downloads NSE's end-of-day F&O bhavcopy and keeps the NIFTY options (`bhavcopy`). It can also poll the live option chain every minute (`snapshot`); NSE serves no intraday history, so that only builds data from today forward. It uses unofficial NSE endpoints that can change without notice. | `venv/Scripts/python data.py bhavcopy --days 365` | `data/bhavcopy/nifty_opt_YYYYMMDD.parquet` (raw NSE UDiFF columns) |
| `read_parquet.py` | Reads the daily files, renames NSE columns to readable names and writes the combined file. Last-traded price is `ltp`, not `last`, because `df.last` is a pandas method. | `venv/Scripts/python read_parquet.py data/bhavcopy --rows 0 --out data/nifty_opt_bhavcopy.parquet` | `data/nifty_opt_bhavcopy.parquet` (+ `.csv` with `--out ...csv`) |
| `explore.py` | Profiles the combined bhavcopy: data quality, lot sizes by period, stale prices, spot volatility, expiry weekdays, volume by DTE and moneyness, ATM straddle cost, put-call parity. | `venv/Scripts/python explore.py [--start --end]` | Printed report |

The bhavcopy currently covers 2025-09-29 → 2026-09-25. Extending the backtest past that date needs
`data.py bhavcopy` and `read_parquet.py` re-run first. Otherwise new days have no lot size and are
skipped.

### 4.3 The `backtest/` package

| Module | Responsibility | Key names |
|---|---|---|
| `config.py` | Every number that affects results: capital per sleeve (Rs 10 lakh), `risk_per_trade` (2%), `max_lots` (20), session times (entries until 14:30, square-off 15:15), slippage and all statutory charges. | `Config`, `Costs`, `DEFAULT` |
| `data.py` | Loads the input file into one `Day` per trading day: bar times, spot, and `[bar, strike]` arrays of close, open and open interest, forward-filled within the day only. Rolling ATM-offset data is pivoted back to real strikes. | `load_days`, `Day.px`, `Day.strike`, `Day.pcr` |
| `engine.py` | Execution model. **Timing rule:** decide on the close of bar *i*, fill at the open of bar *i+1*. Applies slippage and charges and builds `Trade` records. | `Position`, `Leg`, `Trade`, `Session`, `run` |
| `strategies.py` | The rule engine and the strategies (section 5). | `Rule`, `RuleStrategy`, `MeanReversion`, `Directional`, `SemiDirectional`, `MeanReversionV1`, `DirectionalPriceFollow` |
| `metrics.py` | CAGR, max drawdown, Sharpe with 95% CI and P(true Sharpe > 0), Sortino, Calmar, and trade statistics. | `summary`, `daily_equity`, `table` |
| `report.py` | Charts and `results/report.md`, including the before/after sections. | `write_report`, `plot_equity`, `plot_before_after` |

### 4.4 Tests and checks

| Script | What it checks | Run |
|---|---|---|
| `tests/test_engine.py` | Hand-computed fills, slippage and charges for simple trades. | `venv/Scripts/python tests/test_engine.py` |
| `tests/make_synthetic.py` | Builds synthetic data: a random-walk spot and Black-Scholes option prices at the true volatility, so no strategy can have an edge. | `venv/Scripts/python tests/make_synthetic.py --out <path> --seed 1` |
| `tests/check_lookahead.py` | Runs the current strategies on synthetic data for several seeds with all costs set to zero. **Pass:** every \|t\| is below 2 on (nearly) every seed and the sign varies. A strategy positive on every seed means future information is leaking. | `venv/Scripts/python tests/check_lookahead.py [--seeds 1 2 3 4 5 6]` |

Synthetic data has no open interest, so Directional's put/call-ratio signal falls back to a price
signal there, and the look-ahead check does not cover it. To check that signal, compute the ratio
from an earlier bar (e.g. five minutes stale) while keeping the same entry time. Results should barely
change. The run on 2026-09-29 gave Sharpe 1.89 fresh, 2.28 five minutes stale, and 1.81 fifteen
minutes stale.

### 4.5 Alternative and legacy scripts (not used by the final backtest)

| Script | What it is | Status |
|---|---|---|
| `dhan_expired.py` | The first Dhan downloader (`check` / `download` / `combine`), same endpoint as `dhan_nifty_options_1y.py`, one file per 30-day window. | Legacy. Its `infer_expiry` / `_validate_expiry` / `THURSDAY_LAST` are still **imported** by `build_dhan_backtest_data.py`, so don't delete it. It needs `DHAN_CLIENT_ID` as well as the token. |
| `build_strategy_data.py` | Builds 1-minute data from a sibling repo (`Quant Camp/Strategy/data/research/`): real strikes and expiries, 2023-01 → 2025-12. | Not used: the backtest now uses Dhan only. Its output was deleted on 2026-09-29 and can be rebuilt with `venv/Scripts/python build_strategy_data.py` if needed. |
| `run_latest.py` | Tests the frozen strategies on the newest data in one go: refreshes the bhavcopy, downloads Dhan 1-min options from `--start` (default 2026-09-28) to today into `data/latest/`, builds the backtest file, checks it against `data/final/` on overlapping days, then runs `run_backtest.py` on the new days alone and on the test year extended to the newest day. Token from `DHAN_ACCESS_TOKEN`, then `.env`, then a hidden prompt (`--token`, `--save-token`). `--skip-download` reruns steps 2-4. Takes ~20 min, mostly Dhan's API. | Writes `results_latest/new_days/` and `results_latest/extended_year/` and adds rows to `test_done.txt`. Rerun any time to bring the test up to date. |
| `dhan_g.py` | Your own downloader: fetches Dhan's instrument master, keeps the currently listed NIFTY option contracts, and pulls 1-minute bars per contract via `/v2/charts/intraday`. Reads `.env` itself. Dates are hard-coded (`START_DATE` / `END_DATE`). | Not used by the backtest. Output is in `data/dhan_nifty_options/` (532 files, including `nifty_option_contracts.parquet`). It only covers contracts listed today, so it can't reconstruct past weekly expiries. |

---

## 5. Changing the strategies

All three strategies are in `backtest/strategies.py` as `RuleStrategy` subclasses, with one rule (or a
tuple of rules, called *tranches*) per day type. The day types are `far` (3+ days to expiry), `dte1`
(the day before expiry) and `exp` (expiry day).

A `Rule` has these fields:

| Field | Meaning |
|---|---|
| `ref` | Where the view comes from: `"open"` (the move since the day's first bar), `"pc"` (the move since the previous close), or `"pcr"` (the put/call open-interest ratio over the 11 strikes around ATM). |
| `mode` | `"fade"` bets against the move; `"follow"` bets with it. |
| `sig_t` | Decision time. The strategy decides on the close of the bar before it and fills at its open. |
| `kind` | The option structure: `long`, `long_otm1`, `debitW`, `creditW` or `otmcreditW`. W is the width in strikes. |
| `exit_t` | Exit time (15:15 = hold to square-off). |
| `stop` | 0 = no stop; *m* = exit if spot closes *m* × the morning range beyond it. Applies to long-premium structures only. |
| `risk` | Multiplier on the 2% worst-case risk per trade. |

**To change a strategy:** edit its `rules = {...}`, then re-run tests, the backtest, and the PDF (checklist below).

**To revert Mean Reversion to v1:** set `MeanReversion.rules = MeanReversionV1.rules`. The full v1
results are archived in `results/archive/2026-09-29_mean_reversion_v1/`.

**To record a new before/after comparison:** keep the old version as a reference class (like
`MeanReversionV1`) and add a row to `COMPARISONS` in `run_backtest.py`. Every run then re-runs both
versions and adds a section and chart to the report. Don't delete the reference classes.

**Checklist after any strategy or engine change:**

1. `venv/Scripts/python tests/test_engine.py`
2. `venv/Scripts/python tests/check_lookahead.py`, and the stale-signal check if the change touches the OI signal
3. `venv/Scripts/python run_backtest.py --robustness`
4. Update `results/ANALYSIS.md` with the new numbers (it is written by hand)
5. `venv/Scripts/python make_pdf.py`

---

## 6. Folder contents after clean-up (2026-09-29)

| Path | Contents |
|---|---|
| `data/final/raw/` | The 588 raw Dhan chunks (the source of truth for the backtest) |
| `data/final/nifty_opt_1min.parquet` | Backtest input (from `build_dhan_backtest_data.py`) |
| `data/final/nifty_options_1min.parquet` | Real-strike lookup file (from `combine_dhan_options.py`) |
| `data/final/incomplete_days.csv` | Series-days with fewer than 370 bars |
| `data/bhavcopy/`, `data/nifty_opt_bhavcopy.parquet` / `.csv` | End-of-day bhavcopy (lot sizes, expiries) |
| `data/latest/` | Newest Dhan download (`run_latest.py`): `raw/`, `nifty_opt_1min.parquet` (new days), `nifty_opt_1min_extended.parquet` (test year + new days) |
| `results_latest/` | Backtests on the newest data: `new_days/`, `extended_year/` |
| `data/dhan_nifty_options/` | Output of `dhan_g.py` (not used by the backtest) |
| `results/` | Final backtest outputs, `ANALYSIS.md` and `Q2_Report.pdf` |
| `results/archive/2026-09-29_mean_reversion_v1/` | Mean Reversion v1 results and code, kept for reverting |

Deleted as not part of the final Dhan backtest:
- The four `results_cmp_*` folders: old strategies, and the Strategy-repo comparison.
- `data/dhan/`: the 4-day download smoke test.
- `data/nifty_opt_1min.parquet`: the Strategy-repo data.
- A test snapshot CSV.

---

## 7. Improvements worth making

### Reproducibility and hygiene

- **Add `requirements.txt`**, pinning pandas, numpy, pyarrow, requests, matplotlib, reportlab,
  python-dotenv and tqdm, so the environment can be rebuilt.
- **Load `.env` automatically** in `dhan_nifty_options_1y.py` and `dhan_expired.py` (`python-dotenv`
  is already installed), so the token export step disappears. Note that `.env` has a space after `=`,
  which breaks `source .env` in bash.
- **Update `README.md`.** It still describes the original strategies (z-score fade, opening-range
  breakout, 10:15 credit spread) and the `dhan_expired.py` pipeline. It should point to this manual.
- **Bring the research scripts into the project.** The data scans, rule searches and signal studies
  that chose the current rules were run as temporary scripts during development and are not in the
  project. Recreating them under `research/` would make the rule selection auditable.
- **Add the stale-signal check to `tests/`** as a script, like the look-ahead check.

### Data scripts

- **`dhan_nifty_options_1y.py`:**
  - It writes an *empty* file when a request fails after retries, so a rerun skips that chunk
    forever. Only write on success, and log the failures.
  - Its end date is `date.today()`, so reruns on different days change the chunk boundaries and
    re-download. Pass fixed `--start/--end` dates.
  - It sends one request at a time, with no parallelism and no rate-limit backoff beyond simple
    retries.
- **`build_dhan_backtest_data.py`:** Flag days
  whose expiry comes from the weekday rule. Special sessions like the Sunday 2026-02-01 have no
  bhavcopy lot size and are skipped silently.
- **`data.py`:** Its NSE endpoints are unofficial. Keep the bhavcopy refreshed (it currently ends
  2026-09-25), or the newest days get no lot size.
- **`combine_dhan_options.py` / `inspect_parquet.py`:** Fine for lookups. Add `--date` filtering so the
  3.9M-row file isn't loaded in full.

### Backtest and strategies

- **Out-of-sample evidence is the biggest gap.** Download 3–4 more years from Dhan (the API goes back
  five years), fit on the older years, and test these exact rules on 2025-26. Or run a walk-forward
  that refits every few months on past data only.
- **Concentration.** About 76% of Mean Reversion's profit, and 65% of Directional's, comes from the
  45 days before expiry. A cap on risk per day type, or on combined exposure across strategies on
  those days, would limit the damage if that edge fades.
- **Mean Reversion and Semi-Directional overlap.** Both fade the move near expiry (daily-return
  correlation 0.75). A genuinely different signal for one of them would diversify the portfolio more
  than any parameter change.
- **Costs.** STT (0.1% on sells) is unverified for 2026, and slippage is a fixed model. Calibrate both
  against real contract notes and fills.
- **Margin.** Sizing is by worst-case loss, not by broker margin. Adding a SPAN-style margin check
  would confirm that the positions are actually fundable on Rs 10 lakh per sleeve.
- **Speed.** Each run reloads the 3.9M-row file (about 25–50 s). Caching the loaded days (for example
  as a pickle keyed on the file's modification time) would make the stress tests much faster.
- **`make_pdf.py`:** A table of contents with page numbers would help. Wide tables (10 columns) use a
  6.4 pt font, so the halves table could be split in two.

## Redesign research (`research/`, 2026-10-02)

The redesign picks rules on the 2023-2025 development data only, then tests them on 2025-26 (`test_2025_26/README.md`).

- `research/rule_search.py simulate`: simulates every candidate rule on every development day, in both
  directions, using the backtest's own `strategies.simulate()`. It covers signal time, structure, exit and
  stop. Output: `data/dhan_2023_2025/search/` (about 6 min). It refuses data past 2025-09-29.
- `research/score_rules.py`: ranks rules per strategy family and day type. It keeps only rules that are
  positive in every year and whose signal-time neighbours are positive. Writes `scores.csv`.
- `research/portfolio.py <spec.json>` and `research/weight_search.py <spec.json>`: quick linear portfolio
  stats, and a search over risk multipliers ranked by neighbour-averaged Calmar.
- The chosen set is `research/specs/v2.json`. Its rules are in `backtest/strategies.py`'s main classes; the
  previous rules are kept as `*Fit2026`.

## Results layout (since 2026-10-03)

- `results/`: the test year 30 Sep 2025 - 25 Sep 2026 (`run_backtest.py --robustness`), with `ANALYSIS.md`
  and `Q2_Report.pdf` (`make_pdf.py`).
- `results/development/`: the development period
  (`run_backtest.py --data data/dhan_2023_2025/nifty_opt_1min.parquet --start 2023-01-01 --split 2024-05-01 --out results/development`).
- `results/archive/2026-10-03_rules_fit_2025_26/`: the previous rules' results.
- Each generated `report.md` labels its period as development (in-sample) or test (out-of-sample).
