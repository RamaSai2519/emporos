# EXP-20260922-ema-pullback-v1-benchmark-50k-ec99524b

**REJECTED** | strategy | ema-pullback-v1-benchmark-50k | declared 2026-09-22T13:02:14+05:30

## Declaration (made before the run)

- Pre-declared: no (backfilled after the fact)
- Hypothesis: backfilled from docs/strategies/benchmark_50k/ema_pullback_v1.json: ema_pullback_v1 curated walk-forward
- Economic rationale: backfilled: not pre-declared
- Falsified by: backfilled: not recorded

Parameter grid:

| parameter | values |
|---|---|
| candidate | nov_s10_t15, nov_s15_t20, nov_s15_t30, vwap_s10_t15, vwap_s15_t20, vwap_s15_t30 |

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
| gross P&L | -1247.61 |
| net P&L | -3312.55 |
| expectancy per trade | n/a |
| profit factor | 0.115 |
| max drawdown (worst window) | 1.6% |
| Sharpe (annualised) | -4.608 |
| Deflated Sharpe | 0.000 |
| PBO | n/a |
| trades | 155 |
| win rate | 12.3% |

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
| 0 | 2026-01-30 | 2026-03-13 | -674.89 |
| 1 | 2026-03-13 | 2026-04-24 | -600.93 |
| 2 | 2026-04-24 | 2026-06-05 | -662.15 |
| 3 | 2026-06-05 | 2026-07-17 | -665.18 |
| 4 | 2026-07-17 | 2026-08-28 | -709.40 |

## Reasons

| code | rule | outcome | evidence |
|---|---|---|---|
| net_pnl_not_real | profit after costs is real | fail | net -3312.55, 95% interval -3794.03 to -2787.38, P(net > 0) 0.000 |
| adverse_costs | profit survives adverse costs | unknown | net -3312.55 as run, -5682.47 under adverse costs |
| too_few_windows_profitable | profitable in most walk-forward windows | fail | 0 of 5 windows |
| too_few_trades | enough trades | pass | 155 trades, 150 needed to validate |
| too_little_history | enough history | unknown | 260 trading days, 500 needed |
| drawdown_over_budget | drawdown within the risk budget | pass | worst window 1.6%, budget 10.0% |
| profit_concentrated | profit is not concentrated | unknown | no profit to attribute |
| parameter_unstable | survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| dsr_below_threshold | beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 318 trials, 0.95 needed |
| below_baseline | beats the always-long baseline | fail | strategy net -3312.55, baseline -3062.66 |

## Notes

- BACKFILLED_NOT_PREDECLARED
- backfilled from docs/strategies/benchmark_50k/ema_pullback_v1.json, first committed 2026-09-22: the run predates experiment reports, so what it did not record is n/a
