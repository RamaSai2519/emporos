# Backtests

## momentum_v1, 2025-09-22 .. 2026-09-18 (Phase 10 acceptance run)

`momentum_v1_2025-09-22_2026-09-18.json` is the full report (metrics, every assumption, every
trade). The same document is the golden file `tests/fixtures/backtest/momentum_v1_real_year.golden.json`
and a test asserts they are identical.

```
pipenv run emporos backtest run config/strategies/momentum_v1.yaml \
    --from 2025-09-22 --to 2026-09-18 --cash 100000 \
    --assume-current-universe --assume-earliest-fees \
    --report docs/backtests/momentum_v1_2025-09-22_2026-09-18.json
```

### Result (real data, shipped config, conservative fills)

| | |
|---|---|
| Start / end equity | ₹100,000.00 → ₹71,583.46 over 245 trading days |
| Total return / CAGR | −28.42% / −28.61% |
| Max drawdown | 28.51% |
| Sharpe / Sortino / Calmar | −6.02 / −6.46 / −1.00 |
| Trades | 223 (win rate 23.77%, profit factor 0.34, expectancy −0.26% per trade) |
| Net P&L | −₹28,416.54 = gross −₹14,015.76 and charges −₹14,400.78 |
| Streaks | 5 wins / 12 losses at most |
| Time in market / turnover | 28.61% / 260.6× a year |

**momentum_v1 loses money on this data.** It was never judged for profitability before this
(EM-99 H6); this is the first evidence, and it is not good.

What the loss is made of, from three runs of the same data:

| run | gross P&L | charges | net |
|---|---|---|---|
| as shipped (5 bps marketable-limit buffer) | −₹14,015.76 | ₹14,400.78 | −₹28,416.54 |
| no buffer (`limit_buffer_bps: 0`) | −₹4,465.64 | ₹14,208.73 | −₹18,674.37 |
| paper-broker fill defaults (touch, 50% of volume) | identical to the first row | | |

So the strategy has no edge before costs (slightly negative), and charges plus the buffer turn that
into a large loss. The fill-model assumptions (touch vs strictly-through, participation) make **no
difference** here: its limits sit 5 bps through the market and the bars are liquid. That bears on
EM-99 G3 for this strategy, not in general.

### The data

Two symbols only (RELIANCE-EQ, TCS-EQ, the shipped universe), 36,488 5-minute bars, fetched from
Angel One's read-only history API on 2026-09-20 (`emporos history fetch-bars`: 1m bars derived to
5m with the same `derive` the live pipeline uses, so no 1m bar is stored and no S3 bucket is
needed). Frozen in `tests/fixtures/backtest/` (SHA-256 in `manifest.json`); the golden test runs on
the frozen file, and an integration test checks the Atlas read path gives the same answer.

* 245 sessions per symbol. 210 have all 75 bars. One (2025-10-21) has 12: the Diwali Muhurat
  evening session. **The most recent 34 sessions have 73 bars: the broker's history has no bars
  for 15:15 and 15:20 and only a partial (zero-volume) 15:25 bar**, for both its 1m and its native
  5m endpoints. In those sessions no order can fill after 15:15, so anything open at the square-off
  time is closed by the forced square-off (15 such closes in the year).
* The first bar of every session is stamped 09:15 IST: broker bar times are the bar's OPEN.
* Weekdays with no bars (15) look like NSE holidays; that list was not checked against the
  exchange's calendar.

### Assumptions the report repeats

* **No risk engine** (Phase 11): every signal is traded as sized. No position, loss or exposure
  limit, and **no margin or funds check**. Every report says so; the golden carries it.
* Signal → order pricing is a minimal stand-in (`limit_buffer_bps` from the YAML, tick-rounded);
  Phase 12 owns the real one. Intraday square-off at `session.square_off_at` and the broker's
  forced close (10 bps against) are backtest stand-ins for risk/execution.
* **Fee schedule did not exist yet**: `config/fees/angelone-2026-09-20.yaml` is dated the day it
  was read, not the day rates began. All 146 traded days predate it and were priced with it
  (`--assume-earliest-fees`; without it the run refuses). Charges are about half of the loss, so
  this assumption matters. The schedule was `verified: false` when this run was made; the operator
  reconciled its rates against a real contract note on 2026-09-24 (EM-197), and the four provenance
  lines in the JSON were updated to say so. No number in the run changed: the rates are the same.
* **Universe**: the instrument master only began recording history on 2026-09-19, so both
  instruments were resolved from their earliest recorded definition (`--assume-current-universe`).
  A delisting or rename before then is not represented (survivorship, EM-99 H8).
* Fill model: a limit fills only when the bar trades **strictly through** it, at the limit price, up
  to 10% of the bar's volume; partial bars never fill.

### Checked independently

`223/223` trades were re-derived from the raw bars without engine code: each entry is the
tick-rounded marketable limit from an earlier bar's close and traded through on its fill bar; each
exit is the same or one of the 15 forced closes (last close × 0.999); none is overnight. The
metrics are checked against series worked by hand and exact fractions (`tests/unit/backtest/metrics`).

### Regenerating a golden

Never to make a red build green. `python -m tests.regression.regenerate_goldens` prints the diff and
writes nothing; add `--write` once the diff is what you intend, and explain it in the commit.
