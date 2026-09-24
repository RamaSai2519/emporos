# EXP-20260920-vwap-reversion-v1-first-curation-df5d8deb

**REJECTED** | strategy | vwap-reversion-v1-first-curation | declared 2026-09-20T21:06:13+05:30

## Declaration (made before the run)

- Pre-declared: no (backfilled after the fact)
- Hypothesis: backfilled from docs/strategies/vwap_reversion_v1.json: vwap_reversion_v1 curated walk-forward
- Economic rationale: backfilled: not pre-declared
- Falsified by: backfilled: not recorded

Parameter grid:

| parameter | values |
|---|---|
| candidate | z20_x30_s15, z20_x25_s25, z25_x30_s15, z25_x25_s25, z30_x30_s25, z30_x25_s15 |

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
| gross P&L | -1152.29 |
| net P&L | -4241.34 |
| expectancy per trade | n/a |
| profit factor | 0.269 |
| max drawdown (worst window) | 1.5% |
| Sharpe (annualised) | n/a |
| Deflated Sharpe | n/a |
| PBO | n/a |
| trades | 56 |
| win rate | 33.9% |

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
| 0 | 2026-01-30 | 2026-03-13 | -1066.01 |
| 1 | 2026-03-13 | 2026-04-24 | -559.47 |
| 2 | 2026-04-24 | 2026-06-05 | -1139.32 |
| 3 | 2026-06-05 | 2026-07-17 | -816.11 |
| 4 | 2026-07-17 | 2026-08-28 | -660.43 |

## Reasons

| code | rule | outcome | evidence |
|---|---|---|---|
| n/a | net profit is positive | fail | -4241.34 (needs > 0) |
| n/a | enough trades to mean something | fail | 56 (needs >= 150) |
| n/a | profit factor | fail | 0.269 (needs >= 1.2) |
| n/a | profitable in most out-of-sample windows | fail | 0 of 5 (needs >= 60% of windows) |
| n/a | no window's drawdown is too deep | pass | 1.5% (needs <= 10%) |
| n/a | profitable across instruments, not a few | fail | 9 of 27 (needs >= 50% of instruments) |
| n/a | still profitable without its best window | fail | -3681.87 (needs > 0) |
| n/a | still profitable if charges were twice as high | fail | -7330.39 (needs > 0) |

## Notes

- BACKFILLED_NOT_PREDECLARED
- backfilled from docs/strategies/vwap_reversion_v1.json, first committed 2026-09-20: the run predates experiment reports, so what it did not record is n/a
