# Intraday NIFTY options: three strategies and a combined portfolio

Backtest of a Mean Reversion, a Directional and a Semi-Directional intraday strategy on NIFTY weekly
options, using 1-minute data from Dhan (2025-09-30 → 2026-09-25, 243 trading days), and their
equal-capital portfolio.

**Full instructions:** [manual.md](manual.md) explains how to run every script, how the scripts
connect, and what could be improved. **Written analysis:** [results/ANALYSIS.md](results/ANALYSIS.md).
**Everything in one PDF:** `results/Q2_Report.pdf`.

## Results (in-sample)

| | Mean Reversion | Directional | Semi-Directional | **Combined** |
|---|---|---|---|---|
| CAGR | 70.9% | 41.4% | 31.6% | **47.9%** |
| Max drawdown | −10.1% | −8.5% | −4.4% | **−5.1%** |
| Calmar | 7.04 | 4.84 | 7.17 | **9.42** |
| Sharpe (95% CI) | 2.17 (0.46 – 3.89) | 1.89 (0.11 – 3.67) | 2.64 (0.62 – 4.66) | 2.80 (1.18 – 4.42) |
| Win rate | 44.1% | 50.6% | 57.6% | 50.1% |

> **These results are in-sample.** The rules and position sizes were selected on the same year they
> are reported on, to make all three strategies profitable and reach a combined Calmar ≥ 5. An earlier
> design whose parameters were fixed on the first half only lost money on the second half. Read the
> numbers as a fitted description of this year, not a forecast. See `results/ANALYSIS.md`.

## Running it

From this folder, using the project's Python environment:

```
# Dhan access token (stored in .env, expires daily)
export DHAN_ACCESS_TOKEN=$(sed -n 's/^DHAN_ACCESS_TOKEN=[[:space:]]*"\(.*\)"[[:space:]]*$/\1/p' .env)

venv/Scripts/python dhan_nifty_options_1y.py --days 365 --strikes 10 --out data/final   # download (resumable)
venv/Scripts/python build_dhan_backtest_data.py        # -> data/final/nifty_opt_1min.parquet
venv/Scripts/python run_backtest.py --robustness       # -> results/ (report, charts, CSVs)
venv/Scripts/python make_pdf.py                        # -> results/Q2_Report.pdf

venv/Scripts/python tests/test_engine.py               # fills and charges vs hand calculations
venv/Scripts/python tests/check_lookahead.py           # no-look-ahead check on synthetic data
```

The backtest also needs the NSE end-of-day bhavcopy (`data/nifty_opt_bhavcopy.parquet`), which
supplies each day's lot size and true expiry date. `data.py bhavcopy` and `read_parquet.py` build it
(see `manual.md`).

## Strategies

Each strategy trades **every day**, on its own Rs 10 lakh sleeve. It uses a different rule for each
of three day types: expiry day, the day before expiry, and days 3+ days out. Decisions are made on a
1-minute bar's close and filled at the next bar's open. Every position is closed by 15:15.

| | Idea | Day before expiry (main earner) | Expiry day | 3+ days to expiry |
|---|---|---|---|---|
| **Mean Reversion** | Fade the day's move | Buy ATM against the move since the previous close, in 3 entries (10:20 / 10:35 / 10:50), range stop | 6-strike debit spread against the morning move, 10:00 → 12:00 | Buy ATM against the morning move, 10:00 → 15:15, half risk |
| **Directional** | Trade with option writers' positioning (put/call OI ratio ≥ 1 → bullish) | Buy ATM in that direction at 10:15, range stop, 2× risk | Sell an ATM 5-wide credit spread on the opposite side (bullish → put spread) at 09:55, hold to 15:15, half risk | Buy ATM in that direction at 10:00 |
| **Semi-Directional** | Credit spreads tilted by a directional view | Bet the move since the previous close reverses: sell an ATM 5-wide spread on the side the market moved toward, 10:30 → 15:15, 3× risk | Same bet on the morning move, 10:00 → 12:00, 3× risk | Bet the gap holds: sell a 2-strike-OTM 3-wide spread on the side opposite the gap, 09:30 → 12:00, half risk |

**Sizing:** each position's worst-case loss is 2% of the sleeve's equity, times the rule's risk
multiplier, with a 1-lot minimum. **Costs:** slippage of max(0.25 pts, 0.5% of premium) per fill, plus
brokerage, STT, exchange, SEBI, stamp duty and GST on every order.

**Robustness changes, with before/after in the report:**
- Directional moved from following the price move to the OI ratio. Its robustness-grid Sharpe went
  from −1.40…0.57 to 1.56…2.45.
- Mean Reversion's pre-expiry trade was split into three entries. Its worst stress-test Sharpe went
  from 1.15 to 1.54.

The previous versions are kept in code (`MeanReversionV1`, `DirectionalPriceFollow`) and are re-run on
every backtest for comparison.

## Layout

| Path | What |
|---|---|
| `dhan_nifty_options_1y.py` | Downloads 1-minute expired NIFTY options from Dhan (ATM ± 10, CE + PE) |
| `build_dhan_backtest_data.py` | Adds expiry, days to expiry, ATM offset and open interest to build the backtest input |
| `run_backtest.py` | Runs the strategies, portfolio, halves, robustness grid, stress test and before/after comparisons |
| `make_pdf.py` | Builds `results/Q2_Report.pdf` from `ANALYSIS.md` and `report.md` |
| `combine_dhan_options.py`, `inspect_parquet.py` | Option-chain lookup at any minute; generic parquet profiler |
| `data.py`, `read_parquet.py`, `explore.py` | NSE end-of-day bhavcopy: download, combine, profile |
| `backtest/config.py` | Capital, sizing, session times, slippage and every F&O charge |
| `backtest/data.py` | Loads the data into per-day arrays (prices and OI by real strike) |
| `backtest/engine.py` | Fills (decide on close, fill next open), slippage, charges |
| `backtest/strategies.py` | The rule engine and the three strategies |
| `backtest/metrics.py` | CAGR, drawdown, Sharpe with 95% CI and P(Sharpe > 0), Calmar, trade statistics |
| `backtest/report.py` | Charts and `results/report.md` |
| `tests/` | Engine known-answer tests, synthetic data, look-ahead check |
| `results/` | Final outputs, `ANALYSIS.md`, `Q2_Report.pdf`; `archive/` holds Mean Reversion v1 |
| `dhan_expired.py`, `build_strategy_data.py`, `dhan_g.py` | Alternative/legacy downloaders, not used by the final backtest (`dhan_expired.py` still supplies the expiry-inference function) |

## Checks

- **No look-ahead:** `tests/check_lookahead.py` runs the strategies on synthetic data (random-walk spot,
  fairly priced options) with all costs set to zero, where no edge is possible. Across 4 seeds, every
  strategy's mean P&L per trade is statistically zero (all |t| < 2, signs varying). Synthetic data has
  no open interest, so Directional's OI signal was checked separately: computed 5 and 15 minutes stale,
  it still gives Sharpe 2.28 and 1.81.
- **Fills and charges** match hand calculations (`tests/test_engine.py`).
- **Data gaps:** 2025-10-21 was a genuinely short NSE session and is excluded. A Sunday special session
  (2026-02-01) and the last two days lack a bhavcopy lot size and are skipped.
