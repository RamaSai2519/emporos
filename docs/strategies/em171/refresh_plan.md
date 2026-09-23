# Strategy curation

Selection criteria (fixed before any result was seen):

- at least 150 out-of-sample trades, profit factor >= 1.2, net profit > 0
- profitable in >= 60% of out-of-sample windows and >= 50% of instruments
- worst window drawdown <= 10%
- still profitable without its best window, and with charges doubled

## orb_v1: FAILED

Out-of-sample: 781 trades, net -14823.89 (gross -4414.26, charges 10409.63), win rate 20.7%, profit factor 0.112, compounded return -25.80%.

| check | actual | required | |
|---|---|---|---|
| net profit is positive | -14823.89 | > 0 | FAIL |
| enough trades to mean something | 781 | >= 150 | ok |
| profit factor | 0.112 | >= 1.2 | FAIL |
| profitable in most out-of-sample windows | 0 of 24 | >= 60% of windows | FAIL |
| no window's drawdown is too deep | 1.5% | <= 10% | ok |
| profitable across instruments, not a few | 0 of 27 | >= 50% of instruments | FAIL |
| still profitable without its best window | -14336.25 | > 0 | FAIL |
| still profitable if charges were twice as high | -25233.52 | > 0 | FAIL |

Classification: **REJECTED**

| gate | outcome | evidence |
|---|---|---|
| profit after costs is real | fail | net -14823.89, 95% interval -15888.72 to -13738.20, P(net > 0) 0.000 |
| profit survives adverse costs | unknown | net -14823.89 as run, -26789.37 under adverse costs |
| profitable in most walk-forward windows | fail | 0 of 24 windows |
| enough trades | pass | 781 trades, 150 needed to validate |
| enough history | pass | 820 trading days, 500 needed |
| drawdown within the risk budget | pass | worst window 1.5%, budget 10.0% |
| profit is not concentrated | unknown | no profit to attribute |
| survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 168 trials, 0.95 needed |
| beats the always-long baseline | pass | strategy net -14823.89, baseline -16219.40 |
| evidence spans enough distinct regimes | pass | 1 regime(s); diversity not required |

By direction (out-of-sample, 781 trades total):

| side | trades | net P&L |
|---|---|---|
| long | 388 | -7656.88 |
| short | 393 | -7167.01 |

Net P&L if costs were different (first-order re-pricing of the same trades):

- no_slippage: -11443.56
- benchmark: -14823.89
- slippage_plus5: -18204.22
- fees_x1_5: -20028.70
- fees_x2: -25233.52
- adverse: -26789.37

Parameters chosen per test window (each on its own training data):

- 2022-12-09 to 2023-01-20: `r3_t30_s10`
- 2023-01-20 to 2023-03-03: `r6_t15_s10`
- 2023-03-03 to 2023-04-14: `r3_t30_s10`
- 2023-04-14 to 2023-05-26: `r6_t15_s10`
- 2023-05-26 to 2023-07-07: `r6_t15_s10`
- 2023-07-07 to 2023-08-18: `r3_t15_s10`
- 2023-08-18 to 2023-09-29: `r3_t15_s10`
- 2023-09-29 to 2023-11-10: `r3_t15_s10`
- 2023-11-10 to 2023-12-22: `r3_t30_s10`
- 2023-12-22 to 2024-02-02: `r3_t15_s10`
- 2024-02-02 to 2024-03-15: `r6_t15_s10`
- 2024-03-15 to 2024-04-26: `r3_t15_s10`
- 2024-04-26 to 2024-06-07: `r6_t15_s10`
- 2024-06-07 to 2024-07-19: `r6_t20_s15`
- 2024-07-19 to 2024-08-30: `r6_t20_s15`
- 2024-08-30 to 2024-10-11: `r3_t15_s10`
- 2024-10-11 to 2024-11-22: `r3_t30_s10`
- 2024-11-22 to 2025-01-03: `r6_t15_s10`
- 2025-01-03 to 2025-02-14: `r3_t15_s10`
- 2025-02-14 to 2025-03-28: `r6_t15_s10`
- 2025-03-28 to 2025-05-09: `r3_t15_s10`
- 2025-05-09 to 2025-06-20: `r3_t15_s10`
- 2025-06-20 to 2025-08-01: `r3_t20_s15`
- 2025-08-01 to 2025-09-12: `r6_t20_s15`

## vwap_reversion_v1: FAILED

Out-of-sample: 811 trades, net -15027.45 (gross -4203.63, charges 10823.82), win rate 14.9%, profit factor 0.077, compounded return -26.10%.

| check | actual | required | |
|---|---|---|---|
| net profit is positive | -15027.45 | > 0 | FAIL |
| enough trades to mean something | 811 | >= 150 | ok |
| profit factor | 0.077 | >= 1.2 | FAIL |
| profitable in most out-of-sample windows | 0 of 24 | >= 60% of windows | FAIL |
| no window's drawdown is too deep | 1.5% | <= 10% | ok |
| profitable across instruments, not a few | 0 of 27 | >= 50% of instruments | FAIL |
| still profitable without its best window | -14524.49 | > 0 | FAIL |
| still profitable if charges were twice as high | -25851.27 | > 0 | FAIL |

Classification: **REJECTED**

| gate | outcome | evidence |
|---|---|---|
| profit after costs is real | fail | net -15027.45, 95% interval -16159.29 to -13891.00, P(net > 0) 0.000 |
| profit survives adverse costs | unknown | net -15027.45 as run, -27540.18 under adverse costs |
| profitable in most walk-forward windows | fail | 0 of 24 windows |
| enough trades | pass | 811 trades, 150 needed to validate |
| enough history | pass | 820 trading days, 500 needed |
| drawdown within the risk budget | pass | worst window 1.5%, budget 10.0% |
| profit is not concentrated | unknown | no profit to attribute |
| survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 336 trials, 0.95 needed |
| beats the always-long baseline | pass | strategy net -15027.45, baseline -16219.40 |
| evidence spans enough distinct regimes | pass | 1 regime(s); diversity not required |

By direction (out-of-sample, 811 trades total):

| side | trades | net P&L |
|---|---|---|
| long | 372 | -7082.46 |
| short | 439 | -7944.99 |

Net P&L if costs were different (first-order re-pricing of the same trades):

- no_slippage: -11477.04
- benchmark: -15027.45
- slippage_plus5: -18577.86
- fees_x1_5: -20439.36
- fees_x2: -25851.27
- adverse: -27540.18

Parameters chosen per test window (each on its own training data):

- 2022-12-09 to 2023-01-20: `z25_x30_s15`
- 2023-01-20 to 2023-03-03: `z20_x30_s15`
- 2023-03-03 to 2023-04-14: `z20_x30_s15`
- 2023-04-14 to 2023-05-26: `z20_x30_s15`
- 2023-05-26 to 2023-07-07: `z20_x30_s15`
- 2023-07-07 to 2023-08-18: `z30_x25_s15`
- 2023-08-18 to 2023-09-29: `z20_x25_s25`
- 2023-09-29 to 2023-11-10: `z30_x30_s25`
- 2023-11-10 to 2023-12-22: `z25_x30_s15`
- 2023-12-22 to 2024-02-02: `z25_x30_s15`
- 2024-02-02 to 2024-03-15: `z25_x30_s15`
- 2024-03-15 to 2024-04-26: `z20_x30_s15`
- 2024-04-26 to 2024-06-07: `z20_x30_s15`
- 2024-06-07 to 2024-07-19: `z25_x25_s25`
- 2024-07-19 to 2024-08-30: `z20_x30_s15`
- 2024-08-30 to 2024-10-11: `z20_x30_s15`
- 2024-10-11 to 2024-11-22: `z25_x25_s25`
- 2024-11-22 to 2025-01-03: `z25_x30_s15`
- 2025-01-03 to 2025-02-14: `z25_x30_s15`
- 2025-02-14 to 2025-03-28: `z25_x30_s15`
- 2025-03-28 to 2025-05-09: `z20_x30_s15`
- 2025-05-09 to 2025-06-20: `z20_x30_s15`
- 2025-06-20 to 2025-08-01: `z20_x25_s25`
- 2025-08-01 to 2025-09-12: `z30_x25_s15`

## rsi_pullback_v1: FAILED

Out-of-sample: 826 trades, net -14398.29 (gross -3383.84, charges 11014.45), win rate 4.4%, profit factor 0.045, compounded return -25.15%.

| check | actual | required | |
|---|---|---|---|
| net profit is positive | -14398.29 | > 0 | FAIL |
| enough trades to mean something | 826 | >= 150 | ok |
| profit factor | 0.045 | >= 1.2 | FAIL |
| profitable in most out-of-sample windows | 0 of 24 | >= 60% of windows | FAIL |
| no window's drawdown is too deep | 1.4% | <= 10% | ok |
| profitable across instruments, not a few | 0 of 27 | >= 50% of instruments | FAIL |
| still profitable without its best window | -13884.77 | > 0 | FAIL |
| still profitable if charges were twice as high | -25412.74 | > 0 | FAIL |

Classification: **REJECTED**

| gate | outcome | evidence |
|---|---|---|
| profit after costs is real | fail | net -14398.29, 95% interval -15296.87 to -13521.44, P(net > 0) 0.000 |
| profit survives adverse costs | unknown | net -14398.29 as run, -27083.76 under adverse costs |
| profitable in most walk-forward windows | fail | 0 of 24 windows |
| enough trades | pass | 826 trades, 150 needed to validate |
| enough history | pass | 820 trading days, 500 needed |
| drawdown within the risk budget | pass | worst window 1.4%, budget 10.0% |
| profit is not concentrated | unknown | no profit to attribute |
| survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 504 trials, 0.95 needed |
| beats the always-long baseline | pass | strategy net -14398.29, baseline -16219.40 |
| evidence spans enough distinct regimes | pass | 1 regime(s); diversity not required |

By direction (out-of-sample, 826 trades total):

| side | trades | net P&L |
|---|---|---|
| long | 485 | -8506.51 |
| short | 341 | -5891.78 |

Net P&L if costs were different (first-order re-pricing of the same trades):

- no_slippage: -10809.17
- benchmark: -14398.29
- slippage_plus5: -17987.41
- fees_x1_5: -19905.52
- fees_x2: -25412.74
- adverse: -27083.76

Parameters chosen per test window (each on its own training data):

- 2022-12-09 to 2023-01-20: `e10_x55`
- 2023-01-20 to 2023-03-03: `e10_x55`
- 2023-03-03 to 2023-04-14: `e15_x55`
- 2023-04-14 to 2023-05-26: `e15_x55`
- 2023-05-26 to 2023-07-07: `e15_x55`
- 2023-07-07 to 2023-08-18: `e05_x55`
- 2023-08-18 to 2023-09-29: `e15_x70`
- 2023-09-29 to 2023-11-10: `e10_x70`
- 2023-11-10 to 2023-12-22: `e15_x55`
- 2023-12-22 to 2024-02-02: `e15_x70`
- 2024-02-02 to 2024-03-15: `e15_x70`
- 2024-03-15 to 2024-04-26: `e10_x70`
- 2024-04-26 to 2024-06-07: `e10_x55`
- 2024-06-07 to 2024-07-19: `e15_x70`
- 2024-07-19 to 2024-08-30: `e15_x70`
- 2024-08-30 to 2024-10-11: `e10_x55`
- 2024-10-11 to 2024-11-22: `e15_x55`
- 2024-11-22 to 2025-01-03: `e10_x55`
- 2025-01-03 to 2025-02-14: `e15_x70`
- 2025-02-14 to 2025-03-28: `e15_x70`
- 2025-03-28 to 2025-05-09: `e15_x55`
- 2025-05-09 to 2025-06-20: `e10_x70`
- 2025-06-20 to 2025-08-01: `e15_x70`
- 2025-08-01 to 2025-09-12: `e15_x70`

