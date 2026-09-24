# Paper parity: does paper trading behave like the backtest?

EM-185. For every paper session, Emporos replays **the identical strategy config over the identical
day** in the backtest engine (a *shadow backtest*), lines the two up, and reports how far paper
degraded from it. The verdict can block a strategy from graduating (EM-189 reads it through
`emporos.domain.parity.PaperReconciliationEvidence`).

Not to be confused with `portfolio/reconciliation.py` (EM-84), which reconciles the *broker's* books
against ours. This is *parity*: backtest vs paper.

```
paper run (strategy_runs.config_snapshot) ─▶ restore config, prove its hash reproduces
       │                                              │
       │                                              ▼
signals / risk_events / orders /              BacktestEngine over the same session day,
order_events / executions  (Mongo)            same risk limits + fee schedule, journal sink
       │                                              │
       └──────────────▶  SignalMatcher ◀──────────────┘
                              │  MATCHED / MISSED / RISK_REJECTED / PARTIAL / ...
                              ▼
        SessionParity ─▶ degradation metrics ─▶ gates ─▶ VALIDATED | INCONCLUSIVE | REJECTED
```

## What is compared

| | Paper | Backtest (shadow) |
|---|---|---|
| Signal | `signals` (persisted before risk) | `RecordingSink.on_signal` (`backtest/journal.py`) |
| Quote at decision | `signals.quote_*` (the quote risk judged; no second fetch) | none: the signal's price is the reference |
| Order and fill | `orders`, `order_events`, `executions` | the journal's orders and fills, with charges |
| Rejection | `risk_events` | the backtest's own risk gate |
| Round trips | `PaperTrades`: the same `BacktestPortfolio` booking the backtest uses | `ClosedTrade` from the engine |

Signals match one to one on (instrument, side, kind) within a tolerance (default: the same bar).
Anything unmatched on either side is a first-class row, never dropped. Every signal ends with one
`ParityStatus`: `MATCHED`, `PAPER_ONLY_SIGNAL`, `BACKTEST_ONLY_SIGNAL`, `RISK_REJECTED`, `MISSED`,
`PARTIAL`, `FILLED_BACKTEST_NOT_PAPER`, `FILLED_PAPER_NOT_BACKTEST`.

Metrics, each reported as backtest, paper, absolute change and relative change, cut by strategy,
symbol, session and trade: fill rate, slippage (mean and p90, bps), turnover (traded value),
expectancy, win rate, max drawdown, net P&L, charges, cost per trade and in bps, plus signal
agreement. Sign convention: the change is *paper minus backtest*.

Two definitions worth knowing:

* Drawdown is the **realised** drawdown of closed round trips, computed identically on both sides
  (paper has no bar-level marks to build the backtest's bar-level curve from).
* Slippage is measured against the decision quote's mid (else LTP, else the signal's own price). The
  reference kind is stored with each number. Backtest slippage has no quote, so it is measured
  against the signal price: the two sides' slippage answers different questions, and a big paper
  number is the *finding*, not a bug in the matcher.

## Verdict and thresholds

`config/parity.yaml` holds the bounds. It was committed **before any parity report existed** and is
not tuned afterwards. The rule is the robustness verdict's: any FAIL is REJECTED; else any UNKNOWN is
INCONCLUSIVE; all PASS is VALIDATED. A degradation gate answers FAIL only when the sample is big
enough to believe (`min_sessions`, `min_matched_trades`); on a thin sample it answers UNKNOWN.
**At ₹50k with 2–3 positions, expect INCONCLUSIVE for a long while. That is correct. Do not lower
the minimums to get a verdict.**

## Running it

```bash
pipenv run emporos db migrate                                # creates parity_reports (unique index)
pipenv run emporos worker run                                # parity runs itself after close-out
pipenv run emporos paper parity daily --date 2026-09-24      # (re-run or backfill) one session
pipenv run emporos paper parity weekly --date 2026-09-24     # ISO-week report from stored dailies
pipenv run emporos paper parity show --strategy momentum_v1
pipenv run emporos paper parity export --out docs/paper/parity/
```

`worker run --no-parity` turns the automatic job off. The job runs once after square-off/EOD, is
time-boxed (5 minutes), reads the database and holds no broker, and a failure alerts
(`close_out_hook_failed:parity`) and never touches the session. Daily reports keep their session's
rows, so the weekly (written on the ISO week's last session) and cumulative reports aggregate from
storage and never re-run a shadow. A day already reported is skipped for free; reports are
append-only, so the first one for a span stands.

A run whose recorded config cannot be reproduced (hash mismatch, or an unknown strategy) is refused
and listed as skipped with its reason: comparing against a different config would be meaningless.

## Limits, on purpose

* **Paper fills are simulated** (`broker/paper/fills.py`), so paper-vs-backtest measures
  model-vs-model plus real latency and real quotes. Real-broker degradation is only measurable at
  LIVE_CONSERVATIVE; EM-189 should run this same pipeline on live sessions (nothing here assumes a
  paper broker; `worker run` wires it for paper only today).
* **Bar-close vs tick decisions.** Paper decides on live-aggregated candles; the shadow uses the
  candles persisted from the same feed. Differences from late ticks are exactly what parity should
  surface; they are not "fixed" in the matcher.
* Trip pairing is by order of opening within (instrument, direction). The signal-level status
  already explains *why* two trips differ; the pairing only sets them side by side.
* Shadow fees use the earliest fee schedule for days before the oldest one (recorded as an
  assumption), and the instrument universe is resolved as of the session day.
* There is no API/dashboard view yet: adding a route changes the OpenAPI contract, which the
  dashboard's generated client must be regenerated against (`npm run contract:generate`).

## First real run (2026-09-24, against `emporos_dev`)

`emporos paper parity daily` was backfilled over every paper run recorded so far: 2026-09-21
(`rsi_pullback_v1`, `vwap_reversion_v1`) and 2026-09-22 (`vwap_trend_v1`). Rendered samples are in
[`docs/paper/parity/`](paper/parity/) (`emporos paper parity export`).

**There is no usable parity evidence yet, and the reports say so.** All three came out
INCONCLUSIVE, with 1 session, 0 matched trades, and empty signal and trade ledgers on both sides:

* The pipeline itself ran end to end on real Atlas: it restored each run's recorded config and
  reproduced its hash, replayed it through the backtest engine under paper's own risk limits, read
  the paper records, stored a daily and a cumulative report per run, and rendered them.
* The recorded runs had no signals to compare. The 2026-09-22 run was the short market-data smoke
  session (only 58 five-minute bars exist in the universe that day); the 2026-09-21 runs have no
  account id and are scripted-feed test runs. The 99 signals and 36 orders in the database are all
  from 2026-09-18 smoke tests under ephemeral or `manual` run ids, with no recorded strategy run, so
  there is no config to shadow. This was checked directly rather than assumed, to rule out a loader
  bug.
* Every gate is UNKNOWN, which is the correct answer to "nothing happened on either side", and it
  blocks graduation exactly as REJECTED would.

What the real run does not yet show is the interesting part (fill rate, slippage against the
decision quote, latency, MISSED / RISK_REJECTED rows on live data). That needs the next full
market-session run of `emporos worker run` with a strategy started (`--start`); the parity job then
runs by itself after close-out. Those signals will also be the first to carry the decision quote
(`signals.quote_*`), which did not exist before this change.

Verified in tests instead (`tests/unit/parity/`): a backtest compared with itself has zero
degradation on every metric and every signal MATCHED (the identity property); paper round trips equal
the backtest's for identical fills; and scripted degradations appear as MISSED, RISK_REJECTED,
BACKTEST_ONLY_SIGNAL, a slippage breach that REJECTS, and a thin-sample INCONCLUSIVE.
