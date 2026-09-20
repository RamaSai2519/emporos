# Creating a strategy and backtesting it

How to add a new intraday strategy to Emporos, run it over history, and read the result. Everything
here is a command or a file in this repository; there is no dashboard for it yet (EM-131 to EM-136).

Run every command from the repository root. If your shell has another project's virtualenv active,
prefix them with `PIPENV_IGNORE_VIRTUALENVS=1 PIPENV_VERBOSITY=-1` (the project's own environment
is `.venv`, Python 3.12).

## The short version

1. Write the strategy class in `src/emporos/strategies/builtin/`.
2. Add its config in `config/strategies/`, with `enabled: false`.
3. Write tests for it, then run the five quality gates.
4. Have the data locally: `emporos backtest cache warm ...`.
5. Look at it quickly with `emporos backtest run` (no risk rules: a sketch, not a verdict).
6. Judge it properly: write down the hypothesis and parameter grid, **commit them**, then run
   `emporos backtest curate` and read the verdict.

## 1. Write the strategy

A strategy is a pure class: it is shown closed bars and answers with signals. It never touches the
network, the database, a clock or a broker (import-linter enforces it: `emporos.strategies` cannot
import `broker`, `persistence`, `risk`, `execution` or `backtest`).

Copy the shape of an existing one. `src/emporos/strategies/builtin/orb_v1.py` (opening-range
breakout) and `gap_go_v1.py` are short and typical. The pieces:

- **A parameters model**: a `StrategyParameters` subclass. Money and rates are `ExactDecimal`
  (never a float), counts are `PositiveInt`. Validate consistency in a `model_validator`.
- **The class**: subclass `IntradayStrategy` (`src/emporos/strategies/intraday.py`) and set
  `name: ClassVar[str]` and `parameters_model`. You supply two things:
  - `_new_track()`: the per-instrument state (indicators, today's flags).
  - `_observe(track, bar, live)`: advance the state with one closed bar; when `live`, apply the
    rules. `live` is `False` for warm-up bars, so indicators warm up without ever trading.
- **Helpers you get**: `self._held(bar)` (current position), `self._entries_open(bar)` (before
  `no_new_entries_after`), `self._enter(bar, side, why)` (sized to `risk.max_position_value`),
  `self._exit(bar, held, why)`, `DayTrack` (day counters and the session VWAP), and the
  indicators in `emporos.strategies.indicators` (SMA, EMA, RSI, ATR).
- **Discovery is automatic**: any concrete `Strategy` subclass defined in
  `emporos.strategies.builtin` is registered by `build_registry()`. Nothing to wire up.

Rules the platform enforces, so a strategy cannot break them:

- **Limit orders only.** `OrderType` has no MARKET or IOC. Signals carry a limit price.
- **Intraday only.** Whatever is open at `session.square_off_at` is flattened by the session.
- **No look-ahead.** A signal comes from a completed bar and fills on a LATER bar.
- **Everything is `Decimal`**; the money-carrying packages are scanned for floats.

## 2. Add its config

Create `config/strategies/<name>.yaml`. Copy `orb_v1.yaml` or `gap_go_v1.yaml`. Fields: `name`
(must equal the class's `name`), `enabled`, `timeframe`, `universe` (a static list of symbols like
`NSE:SBIN-EQ`), `parameters` (your model), `risk`, `execution`, `session`. Rates and money are
integers or **quoted strings**; a bare YAML float is refused. Times are quoted `"HH:MM"` (IST).

**Ship it with `enabled: false`.** A test (`test_a_strategy_that_has_not_been_curated_is_not_enabled`)
fails if anything shipped is enabled: turning a strategy on is a decision recorded in
`docs/strategies/`, never a default.

## 3. Test it

Put tests in `tests/unit/strategies/`. The pattern to copy is
`tests/unit/strategies/test_intraday_strategies.py` (`Harness`, `bar`, `flat_bars`) and
`test_em114_strategies.py`: build a few hand-made days whose outcome you can check by eye, feed the
bars one at a time, and assert the signals. Cover: an entry, no entry when the condition is absent,
shorting switched off, the exit paths, the entry cut-off, and that nothing trades during warm-up.

Then the definition of done (all five must pass):

```bash
pipenv run lint            # ruff
pipenv run typecheck       # mypy --strict
pipenv run test            # the full suite (about 15 minutes, it includes live Atlas checks)
pipenv run coverage-gate
pipenv run lint-imports
```

## 4. Have the data

Backtests read candles only through `CandleRepository`. Today the database holds one year of 5-minute
bars (2025-09-22 to 2026-09-18) for 29 liquid NSE symbols. More history is EM-132; until then a
backtest cannot span more than that.

To fetch bars for other symbols or dates (read-only Angel One history, needs credentials in `.env`):

```bash
pipenv run emporos history fetch-bars -s SBIN-EQ -s INFY-EQ -t 5m --from 2025-09-22 --to 2026-09-18
```

### The local cache: why the second run is fast

Reading candles from Atlas is the slow part of a backtest (a 6-week run took 386 s, most of it
waiting on the network). Backtests therefore keep a local Parquet copy of every **closed** month, in
`~/.cache/emporos/candles` (override with `CANDLE_CACHE_DIR`). The same run then takes about 65 s.

```bash
pipenv run emporos backtest cache warm config/strategies/orb_v1.yaml --from 2025-09-22 --to 2026-09-18
pipenv run emporos backtest cache status      # where, how many files, its fingerprint
pipenv run emporos backtest cache clear       # safe: only makes the next run slower
```

`warm` reads each month once, two instruments at a time (`--parallel`; the shared dev Atlas stalls
completely at four or more large reads at once, so do not raise it, and do not start many
backtest processes against a cold cache either), so you can do it before starting long runs. It is a derived copy: **after you fetch or repair bars for a month that is already cached,
run `cache clear`** (or delete that month's file), or the new bars will not appear. The current
month and any month that ended less than two days ago are never cached, and neither is an empty
month.

## 5. A quick look: `backtest run`

```bash
pipenv run emporos backtest run config/strategies/<name>.yaml \
    --from 2026-06-01 --to 2026-07-15 --cash 50000 \
    --assume-current-universe --assume-earliest-fees \
    --report /tmp/<name>.json
```

Prints the metrics report (return, Sharpe, drawdown, trades, costs, monthly table) and writes the
full document. **This applies no risk rules**: it shows what the strategy would do with unlimited
freedom, which is not what it would be allowed to do. Use it to see whether the logic does what you
meant, never to judge profitability. The two `--assume-*` flags are explicit opt-ins that are listed
in the report: the instrument master has no history before it was first loaded, and Angel One's fee
schedule is dated from one point only.

## 6. Judge it properly: `backtest curate`

`curate` is the honest path. It runs a walk-forward (tune on a training window, then run the chosen
parameters ONCE on the next unseen window), under the platform's own risk rules, at the ₹50,000
benchmark capital, with dated charges, then classifies the strategy **validated / inconclusive /
rejected** and shows the evidence for each gate.

**Write it down BEFORE you run it** (this is what keeps the result honest):

1. `docs/strategies/<name>-hypotheses.md` (or add to an existing one): what you believe, what the
   strategy does, what is deliberately not tested.
2. A plan file (copy `config/curation/plan_em114.yaml`): the strategy, its config file, and a small
   grid of candidate parameter sets (six is typical). Each candidate is a name and `overrides` of
   the strategy's `parameters`. Every override must be a parameter your model has: copy
   `tests/unit/strategies/test_em114_plan.py` for your plan file, so a typo in a grid fails in
   seconds instead of after hours of running.
3. **Commit both.** Do not edit the grid or the thresholds after you have seen a result.

Then run it (`--only` runs one strategy from the plan, so a dropped connection loses nothing else):

```bash
pipenv run emporos backtest curate --plan config/curation/<your_plan>.yaml --only <name> \
    --from 2025-09-22 --to 2026-09-18 --assume-earliest-fees \
    --experiment <label> \
    --report docs/strategies/benchmark_50k/<name>.md --json docs/strategies/benchmark_50k/<name>.json
```

Several strategies can run at once (one process each) once the cache is warm: they then read local
files, not Atlas. Warm it first; several processes fetching a cold cache at once stall Atlas. `--no-record` keeps the run out of the trial ledger (it still counts within the
run); `--cash` overrides the benchmark capital.

What it runs: 6 candidates on each of 5 training windows, the 5 chosen ones on their test windows,
then (for the verdict) the neighbouring candidates on each test window and the always-long baseline
(`hold_baseline_v1`). Expect about 65 backtests per strategy.

### Reading the result

The report (`.md`) opens with the classification and one row per gate: PASS, FAIL or UNKNOWN, with
the numbers. The rule: **any FAIL rejects; otherwise any UNKNOWN is inconclusive; validated needs
every gate to pass.** Thin evidence (few trades, short history, luck not ruled out) can only hold a
strategy back from validation; it never rejects and never validates.

| gate | what it asks |
|---|---|
| profit after costs is real | Monte Carlo: is P(net profit > 0) high, and is the interval clear of zero? |
| survives adverse costs | still profitable at 1.5x charges and +10 bps slippage? |
| profitable in most windows | at least 60% of the out-of-sample windows |
| enough trades / enough history | 150 trades; 500 trading days (this alone keeps one year from validating) |
| drawdown within the budget | worst window under 10% of capital |
| profit is not concentrated | not one instrument, month or handful of trades |
| survives parameter changes | do the neighbouring parameter sets also profit? |
| beats the luck of the search | Deflated Sharpe, given how many things have ever been tried |
| beats the always-long baseline | better than just being long from the open to the close |

Thresholds live in `config/robustness/benchmark.yaml` (also the ₹50,000 capital, 10% position size,
2% daily loss and the cost scenarios). They were fixed before any result and are only changed by
adding a new benchmark and re-running everything.

The `.json` next to it has the same evidence in full: every gate, Monte Carlo intervals, the Deflated
Sharpe, concentration shares, the cost scenarios and the neighbour runs, next to the out-of-sample
totals, per-window nets and per-instrument nets. `emporos backtest trials list` shows how many experiments the ledger holds; every
candidate on every window is appended there (with the cache fingerprint of the bars it read), and
nothing can edit or delete an entry.

## What exists and what does not

- **Paper trading on live data is not wired yet.** The worker (`emporos run`) is composed for paper
  mode and has been exercised in virtual time, not on live ticks (EM-99 G1/H14). A strategy can be
  backtested and unit-tested today; running it on a live market in paper mode cannot be done yet.
- **No dashboard for any of this** (EM-131 to EM-136 are the plan for it).
- **Not testable yet**: a NIFTY market-direction filter (no index bars are stored), a
  cross-sectional strategy such as relative-strength ranking (a strategy sees one instrument at a
  time), and long and short results reported separately (EM-119, EM-127).
- **Do not re-tune on windows you have already looked at.** For the first three strategies the
  out-of-sample windows are spent; a new idea needs its own written plan and data it has not seen.

## When something goes wrong

- *"no bars"/missing data*: check `emporos backtest cache status`, then `emporos history reconcile`,
  then `cache clear`.
- *A backtest is unexpectedly slow*: the first run over a range fills the cache from Atlas; run it
  again, or `cache warm` first.
- *`pipenv run ...` cannot find ruff/mypy/pytest*: another project's virtualenv is active; use the
  two environment variables at the top of this page.
- *A golden-file regression test fails after a code change*: a metric that moved is a real
  behaviour change to investigate, not a file to refresh. Goldens are regenerated only deliberately,
  with the regenerator described in `docs/backtests/README.md`.
