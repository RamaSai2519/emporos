# EXP-20260923-donchian-v1-em171-refresh-aca8c5f9

**REJECTED** | strategy | donchian-v1-em171-refresh | declared 2026-09-23T17:31:31+05:30

## Declaration (made before the run)

- Pre-declared: no (backfilled after the fact)
- Hypothesis: backfilled from docs/strategies/em171/refresh_em114.json: donchian_v1 curated walk-forward
- Economic rationale: backfilled: not pre-declared
- Falsified by: backfilled: not recorded

Parameter grid:

| parameter | values |
|---|---|
| candidate | l20_tr2, l20_tr3, l30_tr2, l30_tr3, l40_tr2, l40_tr3 |

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
| validation (walk-forward out-of-sample) | 2022-12-09 to 2025-09-12 |
| holdout (reserved) | n/a |

## Headline metrics

| metric | value |
|---|---|
| gross P&L | -4284.20 |
| net P&L | -14971.10 |
| expectancy per trade | n/a |
| profit factor | 0.164 |
| max drawdown (worst window) | 1.6% |
| Sharpe (annualised) | -5.730 |
| Deflated Sharpe | 0.000 |
| PBO | n/a |
| trades | 802 |
| win rate | 16.7% |

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
| 0 | 2022-12-09 | 2023-01-20 | -681.64 |
| 1 | 2023-01-20 | 2023-03-03 | -635.61 |
| 2 | 2023-03-03 | 2023-04-14 | -690.13 |
| 3 | 2023-04-14 | 2023-05-26 | -664.01 |
| 4 | 2023-05-26 | 2023-07-07 | -624.68 |
| 5 | 2023-07-07 | 2023-08-18 | -479.90 |
| 6 | 2023-08-18 | 2023-09-29 | -592.16 |
| 7 | 2023-09-29 | 2023-11-10 | -607.87 |
| 8 | 2023-11-10 | 2023-12-22 | -533.95 |
| 9 | 2023-12-22 | 2024-02-02 | -649.76 |
| 10 | 2024-02-02 | 2024-03-15 | -626.27 |
| 11 | 2024-03-15 | 2024-04-26 | -718.83 |
| 12 | 2024-04-26 | 2024-06-07 | -580.56 |
| 13 | 2024-06-07 | 2024-07-19 | -643.98 |
| 14 | 2024-07-19 | 2024-08-30 | -748.69 |
| 15 | 2024-08-30 | 2024-10-11 | -591.91 |
| 16 | 2024-10-11 | 2024-11-22 | -653.71 |
| 17 | 2024-11-22 | 2025-01-03 | -664.44 |
| 18 | 2025-01-03 | 2025-02-14 | -657.13 |
| 19 | 2025-02-14 | 2025-03-28 | -636.16 |
| 20 | 2025-03-28 | 2025-05-09 | -600.87 |
| 21 | 2025-05-09 | 2025-06-20 | -529.09 |
| 22 | 2025-06-20 | 2025-08-01 | -544.46 |
| 23 | 2025-08-01 | 2025-09-12 | -615.29 |

## Reasons

| code | rule | outcome | evidence |
|---|---|---|---|
| net_pnl_not_real | profit after costs is real | fail | net -14971.10, 95% interval -16309.44 to -13615.73, P(net > 0) 0.000 |
| adverse_costs | profit survives adverse costs | unknown | net -14971.10 as run, -27239.52 under adverse costs |
| too_few_windows_profitable | profitable in most walk-forward windows | fail | 0 of 24 windows |
| too_few_trades | enough trades | pass | 802 trades, 150 needed to validate |
| too_little_history | enough history | pass | 820 trading days, 500 needed |
| drawdown_over_budget | drawdown within the risk budget | pass | worst window 1.6%, budget 10.0% |
| profit_concentrated | profit is not concentrated | unknown | no profit to attribute |
| parameter_unstable | survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| dsr_below_threshold | beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 336 trials, 0.95 needed |
| below_baseline | beats the always-long baseline | pass | strategy net -14971.10, baseline -16219.40 |
| too_few_regimes | evidence spans enough distinct regimes | pass | 1 regime(s); diversity not required |

## Notes

- BACKFILLED_NOT_PREDECLARED
- backfilled from docs/strategies/em171/refresh_em114.json, first committed 2026-09-23: the run predates experiment reports, so what it did not record is n/a
