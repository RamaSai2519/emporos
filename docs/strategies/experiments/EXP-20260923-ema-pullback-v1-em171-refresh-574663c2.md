# EXP-20260923-ema-pullback-v1-em171-refresh-574663c2

**REJECTED** | strategy | ema-pullback-v1-em171-refresh | declared 2026-09-23T17:31:31+05:30

## Declaration (made before the run)

- Pre-declared: no (backfilled after the fact)
- Hypothesis: backfilled from docs/strategies/em171/refresh_em114.json: ema_pullback_v1 curated walk-forward
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
| validation (walk-forward out-of-sample) | 2022-12-09 to 2025-09-12 |
| holdout (reserved) | n/a |

## Headline metrics

| metric | value |
|---|---|
| gross P&L | -3763.83 |
| net P&L | -14740.07 |
| expectancy per trade | n/a |
| profit factor | 0.110 |
| max drawdown (worst window) | 1.5% |
| Sharpe (annualised) | -5.162 |
| Deflated Sharpe | 0.000 |
| PBO | n/a |
| trades | 824 |
| win rate | 16.9% |

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
| 0 | 2022-12-09 | 2023-01-20 | -615.96 |
| 1 | 2023-01-20 | 2023-03-03 | -495.01 |
| 2 | 2023-03-03 | 2023-04-14 | -631.85 |
| 3 | 2023-04-14 | 2023-05-26 | -618.79 |
| 4 | 2023-05-26 | 2023-07-07 | -695.03 |
| 5 | 2023-07-07 | 2023-08-18 | -691.77 |
| 6 | 2023-08-18 | 2023-09-29 | -595.84 |
| 7 | 2023-09-29 | 2023-11-10 | -700.75 |
| 8 | 2023-11-10 | 2023-12-22 | -606.35 |
| 9 | 2023-12-22 | 2024-02-02 | -671.02 |
| 10 | 2024-02-02 | 2024-03-15 | -565.25 |
| 11 | 2024-03-15 | 2024-04-26 | -576.21 |
| 12 | 2024-04-26 | 2024-06-07 | -550.88 |
| 13 | 2024-06-07 | 2024-07-19 | -649.18 |
| 14 | 2024-07-19 | 2024-08-30 | -622.64 |
| 15 | 2024-08-30 | 2024-10-11 | -635.46 |
| 16 | 2024-10-11 | 2024-11-22 | -635.87 |
| 17 | 2024-11-22 | 2025-01-03 | -435.41 |
| 18 | 2025-01-03 | 2025-02-14 | -577.91 |
| 19 | 2025-02-14 | 2025-03-28 | -658.46 |
| 20 | 2025-03-28 | 2025-05-09 | -509.18 |
| 21 | 2025-05-09 | 2025-06-20 | -665.14 |
| 22 | 2025-06-20 | 2025-08-01 | -665.57 |
| 23 | 2025-08-01 | 2025-09-12 | -670.54 |

## Reasons

| code | rule | outcome | evidence |
|---|---|---|---|
| net_pnl_not_real | profit after costs is real | fail | net -14740.07, 95% interval -15755.92 to -13690.95, P(net > 0) 0.000 |
| adverse_costs | profit survives adverse costs | unknown | net -14740.07 as run, -27324.79 under adverse costs |
| too_few_windows_profitable | profitable in most walk-forward windows | fail | 0 of 24 windows |
| too_few_trades | enough trades | pass | 824 trades, 150 needed to validate |
| too_little_history | enough history | pass | 820 trading days, 500 needed |
| drawdown_over_budget | drawdown within the risk budget | pass | worst window 1.5%, budget 10.0% |
| profit_concentrated | profit is not concentrated | unknown | no profit to attribute |
| parameter_unstable | survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| dsr_below_threshold | beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 504 trials, 0.95 needed |
| below_baseline | beats the always-long baseline | pass | strategy net -14740.07, baseline -16219.40 |
| too_few_regimes | evidence spans enough distinct regimes | pass | 1 regime(s); diversity not required |

## Notes

- BACKFILLED_NOT_PREDECLARED
- backfilled from docs/strategies/em171/refresh_em114.json, first committed 2026-09-23: the run predates experiment reports, so what it did not record is n/a
