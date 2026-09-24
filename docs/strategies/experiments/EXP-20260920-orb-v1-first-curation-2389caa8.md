# EXP-20260920-orb-v1-first-curation-2389caa8

**REJECTED** | strategy | orb-v1-first-curation | declared 2026-09-20T21:06:13+05:30

## Declaration (made before the run)

- Pre-declared: no (backfilled after the fact)
- Hypothesis: backfilled from docs/strategies/orb_v1.json: orb_v1 curated walk-forward
- Economic rationale: backfilled: not pre-declared
- Falsified by: backfilled: not recorded

Parameter grid:

| parameter | values |
|---|---|
| candidate | r3_t15_s10, r3_t30_s10, r6_t15_s10, r6_t30_s10, r6_t20_s15, r3_t20_s15 |

Feature versions:

| feature | version |
|---|---|
| n/a |  |

## Versions

| what | value |
|---|---|
| behaviour hash (base config) | n/a |
| dataset universe hash | n/a |
| dataset calendar | n/a |
| dataset quarantine hash | n/a |
| dataset span | n/a |
| fee schedule | n/a |
| slippage (bps per side) | n/a |
| benchmark hash | n/a |
| code revision | n/a |

## Periods

| period | days |
|---|---|
| train | n/a |
| validation (walk-forward out-of-sample) | 2026-01-30 to 2026-08-28 |
| holdout (reserved) | n/a |

## Headline metrics

| metric | value |
|---|---|
| gross P&L | -1595.50 |
| net P&L | -3769.43 |
| expectancy per trade | n/a |
| profit factor | 0.217 |
| max drawdown (worst window) | 1.2% |
| Sharpe (annualised) | n/a |
| Deflated Sharpe | n/a |
| PBO | n/a |
| trades | 40 |
| win rate | 32.5% |

## Cost breakdown

| component | amount |
|---|---|
| brokerage | n/a |
| statutory charges | n/a |
| spread | n/a |
| slippage | n/a |
| total | n/a |
| per trade (bps of notional) | n/a |
| per trade (INR) | n/a |

## By regime

| regime | trades | net P&L | win rate |
|---|---|---|---|
| n/a |  |  |  |

## By walk-forward window

| window | first | last | net P&L |
|---|---|---|---|
| 0 | 2026-01-30 | 2026-03-13 | -1021.76 |
| 1 | 2026-03-13 | 2026-04-24 | -653.98 |
| 2 | 2026-04-24 | 2026-06-05 | -697.91 |
| 3 | 2026-06-05 | 2026-07-17 | -823.37 |
| 4 | 2026-07-17 | 2026-08-28 | -572.41 |

## Reasons

| code | rule | outcome | evidence |
|---|---|---|---|
| n/a | net profit is positive | fail | -3769.43 (needs > 0) |
| n/a | enough trades to mean something | fail | 40 (needs >= 150) |
| n/a | profit factor | fail | 0.217 (needs >= 1.2) |
| n/a | profitable in most out-of-sample windows | fail | 0 of 5 (needs >= 60% of windows) |
| n/a | no window's drawdown is too deep | pass | 1.2% (needs <= 10%) |
| n/a | profitable across instruments, not a few | fail | 4 of 19 (needs >= 50% of instruments) |
| n/a | still profitable without its best window | fail | -3197.02 (needs > 0) |
| n/a | still profitable if charges were twice as high | fail | -5943.36 (needs > 0) |

## Notes

- BACKFILLED_NOT_PREDECLARED
- backfilled from docs/strategies/orb_v1.json, first committed 2026-09-20: the run predates experiment reports, so what it did not record is n/a
