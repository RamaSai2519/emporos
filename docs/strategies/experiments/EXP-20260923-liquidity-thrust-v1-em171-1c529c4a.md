# EXP-20260923-liquidity-thrust-v1-em171-1c529c4a

**REJECTED** | strategy | liquidity-thrust-v1-em171 | declared 2026-09-23T06:24:50+05:30

## Declaration (made before the run)

- Pre-declared: no (backfilled after the fact)
- Hypothesis: backfilled from docs/strategies/em171/liquidity_thrust_v1.json: liquidity_thrust_v1 curated walk-forward
- Economic rationale: backfilled: not pre-declared
- Falsified by: backfilled: not recorded

Parameter grid:

| parameter | values |
|---|---|
| candidate | surge25_t3, surge25_t4, surge25_t5, surge40_t3, surge40_t4, surge40_t5 |

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
| validation (walk-forward out-of-sample) | 2022-12-09 to 2026-08-14 |
| holdout (reserved) | n/a |

## Headline metrics

| metric | value |
|---|---|
| gross P&L | -3455.09 |
| net P&L | -18574.98 |
| expectancy per trade | n/a |
| profit factor | 0.320 |
| max drawdown (worst window) | 1.7% |
| Sharpe (annualised) | -6.205 |
| Deflated Sharpe | 0.000 |
| PBO | n/a |
| trades | 1134 |
| win rate | 24.1% |

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
| 0 | 2022-12-09 | 2023-01-20 | -624.19 |
| 1 | 2023-01-20 | 2023-03-03 | -638.44 |
| 2 | 2023-03-03 | 2023-04-14 | -634.31 |
| 3 | 2023-04-14 | 2023-05-26 | -584.73 |
| 4 | 2023-05-26 | 2023-07-07 | -602.80 |
| 5 | 2023-07-07 | 2023-08-18 | -616.08 |
| 6 | 2023-08-18 | 2023-09-29 | -610.71 |
| 7 | 2023-09-29 | 2023-11-10 | -550.15 |
| 8 | 2023-11-10 | 2023-12-22 | -528.73 |
| 9 | 2023-12-22 | 2024-02-02 | -719.43 |
| 10 | 2024-02-02 | 2024-03-15 | -334.03 |
| 11 | 2024-03-15 | 2024-04-26 | -631.58 |
| 12 | 2024-04-26 | 2024-06-07 | -420.13 |
| 13 | 2024-06-07 | 2024-07-19 | -693.57 |
| 14 | 2024-07-19 | 2024-08-30 | -609.52 |
| 15 | 2024-08-30 | 2024-10-11 | -528.92 |
| 16 | 2024-10-11 | 2024-11-22 | -569.23 |
| 17 | 2024-11-22 | 2025-01-03 | -661.88 |
| 18 | 2025-01-03 | 2025-02-14 | -610.69 |
| 19 | 2025-02-14 | 2025-03-28 | -396.06 |
| 20 | 2025-03-28 | 2025-05-09 | -628.30 |
| 21 | 2025-05-09 | 2025-06-20 | -632.79 |
| 22 | 2025-06-20 | 2025-08-01 | -621.36 |
| 23 | 2025-08-01 | 2025-09-12 | -623.18 |
| 24 | 2025-09-12 | 2025-10-24 | -503.48 |
| 25 | 2025-10-24 | 2025-12-05 | -660.84 |
| 26 | 2025-12-05 | 2026-01-16 | -663.34 |
| 27 | 2026-01-16 | 2026-02-27 | -521.54 |
| 28 | 2026-02-27 | 2026-04-10 | -646.30 |
| 29 | 2026-04-10 | 2026-05-22 | -613.24 |
| 30 | 2026-05-22 | 2026-07-03 | -353.49 |
| 31 | 2026-07-03 | 2026-08-14 | -541.94 |

## Reasons

| code | rule | outcome | evidence |
|---|---|---|---|
| net_pnl_not_real | profit after costs is real | fail | net -18574.98, 95% interval -20686.15 to -16440.76, P(net > 0) 0.000 |
| adverse_costs | profit survives adverse costs | unknown | net -18574.98 as run, -35976.90 under adverse costs |
| too_few_windows_profitable | profitable in most walk-forward windows | fail | 0 of 32 windows |
| too_few_trades | enough trades | pass | 1134 trades, 150 needed to validate |
| too_little_history | enough history | pass | 1080 trading days, 500 needed |
| drawdown_over_budget | drawdown within the risk budget | pass | worst window 1.7%, budget 10.0% |
| profit_concentrated | profit is not concentrated | unknown | no profit to attribute |
| parameter_unstable | survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| dsr_below_threshold | beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 717 trials, 0.95 needed |
| below_baseline | beats the always-long baseline | pass | strategy net -18574.98, baseline -21066.37 |
| too_few_regimes | evidence spans enough distinct regimes | pass | 5 regime(s); diversity not required |

## Notes

- BACKFILLED_NOT_PREDECLARED
- backfilled from docs/strategies/em171/liquidity_thrust_v1.json, first committed 2026-09-23: the run predates experiment reports, so what it did not record is n/a
