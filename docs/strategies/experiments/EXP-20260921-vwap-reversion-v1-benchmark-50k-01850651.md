# EXP-20260921-vwap-reversion-v1-benchmark-50k-01850651

**REJECTED** | strategy | vwap-reversion-v1-benchmark-50k | declared 2026-09-21T03:42:27+05:30

## Declaration (made before the run)

- Pre-declared: no (backfilled after the fact)
- Hypothesis: backfilled from docs/strategies/benchmark_50k/vwap_reversion_v1.json: vwap_reversion_v1 curated walk-forward
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
| gross P&L | -787.65 |
| net P&L | -3107.08 |
| expectancy per trade | n/a |
| profit factor | 0.127 |
| max drawdown (worst window) | 1.4% |
| Sharpe (annualised) | -4.735 |
| Deflated Sharpe | 0.000 |
| PBO | n/a |
| trades | 174 |
| win rate | 19.0% |

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
| 0 | 2026-01-30 | 2026-03-13 | -543.63 |
| 1 | 2026-03-13 | 2026-04-24 | -608.66 |
| 2 | 2026-04-24 | 2026-06-05 | -626.21 |
| 3 | 2026-06-05 | 2026-07-17 | -709.29 |
| 4 | 2026-07-17 | 2026-08-28 | -619.29 |

## Reasons

| code | rule | outcome | evidence |
|---|---|---|---|
| net_pnl_not_real | profit after costs is real | fail | net -3107.08, 95% interval -3704.17 to -2529.45, P(net > 0) 0.000 |
| adverse_costs | profit survives adverse costs | unknown | net -3107.08 as run, -5774.49 under adverse costs |
| too_few_windows_profitable | profitable in most walk-forward windows | fail | 0 of 5 windows |
| too_few_trades | enough trades | pass | 174 trades, 150 needed to validate |
| too_little_history | enough history | unknown | 260 trading days, 500 needed |
| drawdown_over_budget | drawdown within the risk budget | pass | worst window 1.4%, budget 10.0% |
| profit_concentrated | profit is not concentrated | unknown | no profit to attribute |
| parameter_unstable | survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| dsr_below_threshold | beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 178 trials, 0.95 needed |
| below_baseline | beats the always-long baseline | fail | strategy net -3107.08, baseline -3062.66 |

## Notes

- BACKFILLED_NOT_PREDECLARED
- backfilled from docs/strategies/benchmark_50k/vwap_reversion_v1.json, first committed 2026-09-21: the run predates experiment reports, so what it did not record is n/a
