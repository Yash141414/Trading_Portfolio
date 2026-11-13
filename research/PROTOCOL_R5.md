# R5 protocol (frozen 2026-10-22, before any R5 data is looked at)

Goal: three NIFTY weekly-option strategies (Mean Reversion, Directional, Semi-Directional), one trade
per strategy per day, combined into one portfolio. Targets: combined CAGR >= 30%, Calmar >= 3.5,
Sharpe reported with 95% CI and PSR.

## Data

Dhan 1-minute ATM+-10 data with OI: 2023-10-02 -> 2026-09-28.
(Amended 2026-10-22, user, before any data was loaded: start moved from 2024-09-30 to 2023-10-02 so the
12-month walk-forward window is full from the first Research month. 2023-10-02 -> 2024-09-27 is
**warm-up only**: used as trailing fitting data, never traded or scored. It was R4's design data.)
- 2025-09-29 -> 2026-09-28: `data/final/nifty_opt_1min.parquet` (already in the repo).
- 2023-10-02 -> 2025-09-26: to download with `dhan_nifty_options_1y.py --start 2023-10-02 --end 2025-09-29 --strikes 10 --out data/dhan_2024_2025`
  (`--end` is exclusive),
  then `build_dhan_backtest_data.py` (expiry + lot size from `Analysis/3_Code_and_Data/data/calendar_2023_2025.parquet`).

## Split (by date, never reshuffled)

| Block      | Dates                    | ~Days | Use                                                    |
|------------|--------------------------|-------|--------------------------------------------------------|
| Research   | 2024-09-30 -> 2025-12-31 | ~315  | rule search, parameter choice, sleeve weights          |
| Validation | 2026-01-01 -> 2026-06-30 | ~123  | walk-forward check of the fixed procedure; may reject  |
| Held-out   | 2026-07-01 -> 2026-09-28 | ~62   | run ONCE at the end, reported as-is (user, 2026-10-22) |

Targets (30% / 3.5) are judged on Research. Validation and Held-out are reported, not optimised.
If Validation fails, at most one revision is allowed, made on Research only, and it is recorded here;
Validation is then no longer clean and is labelled as such. Held-out is never used to revise anything.

## Regime adaptation (fixed in advance, not chosen after seeing results)

Walk-forward: at the start of each month, re-select each strategy's rule from the fixed candidate grid
using the trailing 12 months, and trade it for that month. The grid, the scoring rule and the 12-month
window are fixed here; only the selected parameters change month to month. Validation and Held-out are
both traded this way.

## Selection rule

- Small grids only (signal time, fade/follow, structure, exit time, stop), every combination logged
  with the total count reported.
- Score = median Sharpe over the candidate and its immediate grid neighbours (choose plateaus, not peaks).
- A candidate is admissible only if it is positive in both halves of its fitting window.
- Sleeve weights: fitted on Research, then frozen.

## Grid and selection details (frozen 2026-10-22, approved by the user before any data was loaded; no day-type split)

One `Rule` per strategy, applied to every day type (no far/dte1/exp split), one trade per strategy per
day, `risk = 1.0` always (position scale comes only from sleeve weights).

| Strategy         | mode           | ref               | kind                    | sig_t (7)                                   | exit_t (3)          | stop (3)      | Count |
|------------------|----------------|-------------------|-------------------------|---------------------------------------------|---------------------|---------------|-------|
| Mean Reversion   | fade           | open, pc          | long, debit4            | 09:30 09:45 10:00 10:15 10:30 10:45 11:00   | 12:00 13:30 15:15   | 0, 0.5, 1.0   | 252   |
| Directional      | follow         | open, pc, pcr     | long, debit4            | same                                        | same                | 0, 0.5, 1.0   | 378   |
| Semi-Directional | fade, follow   | open, pc, pcr     | credit3, otmcredit3     | same                                        | same                | 0 only        | 252   |

Total 882 candidates per monthly re-selection; every candidate's score is logged every month.

- **Cells and neighbours.** A cell is one (mode, ref, kind) combination. Neighbours exist only within a
  cell, along the ordered axes: one step in sig_t, exit_t or stop (at most 6 neighbours). Score = median
  annualised daily Sharpe over the candidate plus its neighbours.
- **Window.** For month M, fit on the 12 calendar months ending the day before M's first trading day.
  The code must assert that no fitting day is >= M's first day.
- **Admissible** = total P&L > 0 in both halves (first 6 / last 6 months) of the window.
- **Pick** = admissible candidate with the highest score; ties broken by grid order (earliest sig_t,
  then exit_t, then stop, then table order of mode/ref/kind). If nothing is admissible, keep the previous
  month's rule, and log it as a fallback month.
- **No hand-written rules.** The grid is the only source of rules. R3 and R4 rules are not added
  separately (some may also be grid points; that is allowed and logged).
- **Sleeve weights** are fitted on the walk-forward Research P&L (2024-10 -> 2025-12), not on a static
  fit. Each weight is chosen from {0.5, 1, 1.5, 2} (x the 2% base risk); objective = combined Calmar;
  then frozen. CAGR is mostly set by these weights, so the 30% target is a leverage check, while Calmar
  3.5 is the real test.
- **Validation fails** if the combined walk-forward Sharpe < 0, or any sleeve's Calmar < 0.
- **Look-ahead:** `tests/check_lookahead.py` plus a stale-signal check for `ref = pcr` (it has no
  synthetic OI) on the walk-forward runner before any Research result is read.

## Known weaknesses (stated up front)

- The grid was drafted by someone who had seen R3/R4 results (e.g. that dte1 days carried most of the
  profit). Dropping the day-type split works against that knowledge rather than using it, but the axes
  and values themselves are not knowledge-free.
- 2025-09-29 -> 2025-12-31 is inside Research and was also R3's fitting data.

- 2025-09-29 -> 2026-09-28 was used to fit the earlier R3 rules, so Validation and Held-out are not
  data nobody has looked at. Rules from R3 are not reused, but knowledge of that year may leak in.
- Held-out is ~62 days: Sharpe standard error ~ sqrt(252/62) ~ 2.0. It can reject a strategy, not confirm one.
- Trading every day forces trades on days with no edge (1-lot floor in `strategies.size`).
- Earlier history: R3 Calmar 9.42 in-sample -> 0.07 out of sample; R4 5.53 -> -0.02.
