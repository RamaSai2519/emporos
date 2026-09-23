# Strategy curation

Selection criteria (fixed before any result was seen):

- at least 150 out-of-sample trades, profit factor >= 1.2, net profit > 0
- profitable in >= 60% of out-of-sample windows and >= 50% of instruments
- worst window drawdown <= 10%
- still profitable without its best window, and with charges doubled

## vwap_trend_v1: FAILED

Out-of-sample: 815 trades, net -14707.16 (gross -3855.18, charges 10851.98), win rate 16.6%, profit factor 0.116, compounded return -25.62%.

| check | actual | required | |
|---|---|---|---|
| net profit is positive | -14707.16 | > 0 | FAIL |
| enough trades to mean something | 815 | >= 150 | ok |
| profit factor | 0.116 | >= 1.2 | FAIL |
| profitable in most out-of-sample windows | 0 of 24 | >= 60% of windows | FAIL |
| no window's drawdown is too deep | 1.5% | <= 10% | ok |
| profitable across instruments, not a few | 0 of 27 | >= 50% of instruments | FAIL |
| still profitable without its best window | -14237.59 | > 0 | FAIL |
| still profitable if charges were twice as high | -25559.14 | > 0 | FAIL |

Classification: **REJECTED**

| gate | outcome | evidence |
|---|---|---|
| profit after costs is real | fail | net -14707.16, 95% interval -15808.89 to -13548.20, P(net > 0) 0.000 |
| profit survives adverse costs | unknown | net -14707.16 as run, -27124.46 under adverse costs |
| profitable in most walk-forward windows | fail | 0 of 24 windows |
| enough trades | pass | 815 trades, 150 needed to validate |
| enough history | pass | 820 trading days, 500 needed |
| drawdown within the risk budget | pass | worst window 1.5%, budget 10.0% |
| profit is not concentrated | unknown | no profit to attribute |
| survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 168 trials, 0.95 needed |
| beats the always-long baseline | pass | strategy net -14707.16, baseline -16219.40 |
| evidence spans enough distinct regimes | pass | 1 regime(s); diversity not required |

By direction (out-of-sample, 815 trades total):

| side | trades | net P&L |
|---|---|---|
| long | 439 | -8140.10 |
| short | 376 | -6567.06 |

Net P&L if costs were different (first-order re-pricing of the same trades):

- no_slippage: -11211.50
- benchmark: -14707.16
- slippage_plus5: -18202.82
- fees_x1_5: -20133.15
- fees_x2: -25559.14
- adverse: -27124.46

Parameters chosen per test window (each on its own training data):

- 2022-12-09 to 2023-01-20: `pull_s10_t15`
- 2023-01-20 to 2023-03-03: `cont_s10_t15`
- 2023-03-03 to 2023-04-14: `pull_s15_t30`
- 2023-04-14 to 2023-05-26: `cont_s10_t15`
- 2023-05-26 to 2023-07-07: `cont_s10_t15`
- 2023-07-07 to 2023-08-18: `cont_s15_t20`
- 2023-08-18 to 2023-09-29: `cont_s10_t15`
- 2023-09-29 to 2023-11-10: `cont_s15_t30`
- 2023-11-10 to 2023-12-22: `cont_s10_t15`
- 2023-12-22 to 2024-02-02: `cont_s10_t15`
- 2024-02-02 to 2024-03-15: `cont_s15_t30`
- 2024-03-15 to 2024-04-26: `pull_s10_t15`
- 2024-04-26 to 2024-06-07: `pull_s10_t15`
- 2024-06-07 to 2024-07-19: `cont_s10_t15`
- 2024-07-19 to 2024-08-30: `cont_s15_t20`
- 2024-08-30 to 2024-10-11: `pull_s10_t15`
- 2024-10-11 to 2024-11-22: `cont_s10_t15`
- 2024-11-22 to 2025-01-03: `cont_s15_t30`
- 2025-01-03 to 2025-02-14: `pull_s10_t15`
- 2025-02-14 to 2025-03-28: `cont_s10_t15`
- 2025-03-28 to 2025-05-09: `cont_s10_t15`
- 2025-05-09 to 2025-06-20: `cont_s15_t30`
- 2025-06-20 to 2025-08-01: `cont_s10_t15`
- 2025-08-01 to 2025-09-12: `cont_s10_t15`

## donchian_v1: FAILED

Out-of-sample: 802 trades, net -14971.10 (gross -4284.20, charges 10686.90), win rate 16.7%, profit factor 0.164, compounded return -26.02%.

| check | actual | required | |
|---|---|---|---|
| net profit is positive | -14971.10 | > 0 | FAIL |
| enough trades to mean something | 802 | >= 150 | ok |
| profit factor | 0.164 | >= 1.2 | FAIL |
| profitable in most out-of-sample windows | 0 of 24 | >= 60% of windows | FAIL |
| no window's drawdown is too deep | 1.6% | <= 10% | ok |
| profitable across instruments, not a few | 0 of 27 | >= 50% of instruments | FAIL |
| still profitable without its best window | -14491.20 | > 0 | FAIL |
| still profitable if charges were twice as high | -25658.00 | > 0 | FAIL |

Classification: **REJECTED**

| gate | outcome | evidence |
|---|---|---|
| profit after costs is real | fail | net -14971.10, 95% interval -16309.44 to -13615.73, P(net > 0) 0.000 |
| profit survives adverse costs | unknown | net -14971.10 as run, -27239.52 under adverse costs |
| profitable in most walk-forward windows | fail | 0 of 24 windows |
| enough trades | pass | 802 trades, 150 needed to validate |
| enough history | pass | 820 trading days, 500 needed |
| drawdown within the risk budget | pass | worst window 1.6%, budget 10.0% |
| profit is not concentrated | unknown | no profit to attribute |
| survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 336 trials, 0.95 needed |
| beats the always-long baseline | pass | strategy net -14971.10, baseline -16219.40 |
| evidence spans enough distinct regimes | pass | 1 regime(s); diversity not required |

By direction (out-of-sample, 802 trades total):

| side | trades | net P&L |
|---|---|---|
| long | 407 | -7091.68 |
| short | 395 | -7879.42 |

Net P&L if costs were different (first-order re-pricing of the same trades):

- no_slippage: -11508.61
- benchmark: -14971.10
- slippage_plus5: -18433.59
- fees_x1_5: -20314.55
- fees_x2: -25658.00
- adverse: -27239.52

Parameters chosen per test window (each on its own training data):

- 2022-12-09 to 2023-01-20: `l20_tr3`
- 2023-01-20 to 2023-03-03: `l20_tr2`
- 2023-03-03 to 2023-04-14: `l20_tr3`
- 2023-04-14 to 2023-05-26: `l20_tr2`
- 2023-05-26 to 2023-07-07: `l30_tr2`
- 2023-07-07 to 2023-08-18: `l20_tr3`
- 2023-08-18 to 2023-09-29: `l30_tr2`
- 2023-09-29 to 2023-11-10: `l20_tr3`
- 2023-11-10 to 2023-12-22: `l30_tr3`
- 2023-12-22 to 2024-02-02: `l30_tr2`
- 2024-02-02 to 2024-03-15: `l20_tr3`
- 2024-03-15 to 2024-04-26: `l20_tr2`
- 2024-04-26 to 2024-06-07: `l30_tr3`
- 2024-06-07 to 2024-07-19: `l30_tr2`
- 2024-07-19 to 2024-08-30: `l20_tr3`
- 2024-08-30 to 2024-10-11: `l30_tr3`
- 2024-10-11 to 2024-11-22: `l20_tr3`
- 2024-11-22 to 2025-01-03: `l40_tr3`
- 2025-01-03 to 2025-02-14: `l30_tr2`
- 2025-02-14 to 2025-03-28: `l20_tr3`
- 2025-03-28 to 2025-05-09: `l20_tr2`
- 2025-05-09 to 2025-06-20: `l20_tr3`
- 2025-06-20 to 2025-08-01: `l40_tr3`
- 2025-08-01 to 2025-09-12: `l20_tr3`

## ema_pullback_v1: FAILED

Out-of-sample: 824 trades, net -14740.07 (gross -3763.83, charges 10976.24), win rate 16.9%, profit factor 0.110, compounded return -25.67%.

| check | actual | required | |
|---|---|---|---|
| net profit is positive | -14740.07 | > 0 | FAIL |
| enough trades to mean something | 824 | >= 150 | ok |
| profit factor | 0.110 | >= 1.2 | FAIL |
| profitable in most out-of-sample windows | 0 of 24 | >= 60% of windows | FAIL |
| no window's drawdown is too deep | 1.5% | <= 10% | ok |
| profitable across instruments, not a few | 0 of 27 | >= 50% of instruments | FAIL |
| still profitable without its best window | -14304.66 | > 0 | FAIL |
| still profitable if charges were twice as high | -25716.31 | > 0 | FAIL |

Classification: **REJECTED**

| gate | outcome | evidence |
|---|---|---|
| profit after costs is real | fail | net -14740.07, 95% interval -15755.92 to -13690.95, P(net > 0) 0.000 |
| profit survives adverse costs | unknown | net -14740.07 as run, -27324.79 under adverse costs |
| profitable in most walk-forward windows | fail | 0 of 24 windows |
| enough trades | pass | 824 trades, 150 needed to validate |
| enough history | pass | 820 trading days, 500 needed |
| drawdown within the risk budget | pass | worst window 1.5%, budget 10.0% |
| profit is not concentrated | unknown | no profit to attribute |
| survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 504 trials, 0.95 needed |
| beats the always-long baseline | pass | strategy net -14740.07, baseline -16219.40 |
| evidence spans enough distinct regimes | pass | 1 regime(s); diversity not required |

By direction (out-of-sample, 824 trades total):

| side | trades | net P&L |
|---|---|---|
| long | 414 | -7914.44 |
| short | 410 | -6825.63 |

Net P&L if costs were different (first-order re-pricing of the same trades):

- no_slippage: -11191.77
- benchmark: -14740.07
- slippage_plus5: -18288.37
- fees_x1_5: -20228.19
- fees_x2: -25716.31
- adverse: -27324.79

Parameters chosen per test window (each on its own training data):

- 2022-12-09 to 2023-01-20: `vwap_s10_t15`
- 2023-01-20 to 2023-03-03: `nov_s15_t30`
- 2023-03-03 to 2023-04-14: `vwap_s10_t15`
- 2023-04-14 to 2023-05-26: `vwap_s10_t15`
- 2023-05-26 to 2023-07-07: `nov_s10_t15`
- 2023-07-07 to 2023-08-18: `nov_s15_t20`
- 2023-08-18 to 2023-09-29: `vwap_s10_t15`
- 2023-09-29 to 2023-11-10: `nov_s15_t20`
- 2023-11-10 to 2023-12-22: `vwap_s10_t15`
- 2023-12-22 to 2024-02-02: `vwap_s15_t30`
- 2024-02-02 to 2024-03-15: `vwap_s10_t15`
- 2024-03-15 to 2024-04-26: `nov_s10_t15`
- 2024-04-26 to 2024-06-07: `vwap_s10_t15`
- 2024-06-07 to 2024-07-19: `nov_s10_t15`
- 2024-07-19 to 2024-08-30: `vwap_s10_t15`
- 2024-08-30 to 2024-10-11: `vwap_s10_t15`
- 2024-10-11 to 2024-11-22: `vwap_s15_t30`
- 2024-11-22 to 2025-01-03: `nov_s10_t15`
- 2025-01-03 to 2025-02-14: `nov_s10_t15`
- 2025-02-14 to 2025-03-28: `vwap_s10_t15`
- 2025-03-28 to 2025-05-09: `nov_s10_t15`
- 2025-05-09 to 2025-06-20: `nov_s15_t30`
- 2025-06-20 to 2025-08-01: `nov_s10_t15`
- 2025-08-01 to 2025-09-12: `vwap_s10_t15`

## gap_go_v1: FAILED

Out-of-sample: 459 trades, net -7699.45 (gross -1572.94, charges 6126.51), win rate 32.5%, profit factor 0.501, compounded return -14.36%.

| check | actual | required | |
|---|---|---|---|
| net profit is positive | -7699.45 | > 0 | FAIL |
| enough trades to mean something | 459 | >= 150 | ok |
| profit factor | 0.501 | >= 1.2 | FAIL |
| profitable in most out-of-sample windows | 2 of 24 | >= 60% of windows | FAIL |
| no window's drawdown is too deep | 3.2% | <= 10% | ok |
| profitable across instruments, not a few | 3 of 26 | >= 50% of instruments | FAIL |
| still profitable without its best window | -8394.57 | > 0 | FAIL |
| still profitable if charges were twice as high | -13825.96 | > 0 | FAIL |

Classification: **REJECTED**

| gate | outcome | evidence |
|---|---|---|
| profit after costs is real | fail | net -7699.45, 95% interval -10937.86 to -4554.24, P(net > 0) 0.000 |
| profit survives adverse costs | unknown | net -7699.45 as run, -14779.64 under adverse costs |
| profitable in most walk-forward windows | fail | 2 of 24 windows |
| enough trades | pass | 459 trades, 150 needed to validate |
| enough history | pass | 820 trading days, 500 needed |
| drawdown within the risk budget | pass | worst window 3.2%, budget 10.0% |
| profit is not concentrated | unknown | no profit to attribute |
| survives parameter changes | fail | 14% of neighbouring parameter sets profit, 50% needed |
| beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 672 trials, 0.95 needed |
| beats the always-long baseline | pass | strategy net -7699.45, baseline -16219.40 |
| evidence spans enough distinct regimes | pass | 1 regime(s); diversity not required |

By direction (out-of-sample, 459 trades total):

| side | trades | net P&L |
|---|---|---|
| long | 283 | -4941.97 |
| short | 176 | -2757.48 |

Net P&L if costs were different (first-order re-pricing of the same trades):

- no_slippage: -5690.98
- benchmark: -7699.45
- slippage_plus5: -9707.92
- fees_x1_5: -10762.70
- fees_x2: -13825.96
- adverse: -14779.64

Parameters chosen per test window (each on its own training data):

- 2022-12-09 to 2023-01-20: `g100_t25`
- 2023-01-20 to 2023-03-03: `g150_t25`
- 2023-03-03 to 2023-04-14: `g150_t15`
- 2023-04-14 to 2023-05-26: `g150_t15`
- 2023-05-26 to 2023-07-07: `g150_t15`
- 2023-07-07 to 2023-08-18: `g50_t15`
- 2023-08-18 to 2023-09-29: `g150_t25`
- 2023-09-29 to 2023-11-10: `g150_t25`
- 2023-11-10 to 2023-12-22: `g50_t25`
- 2023-12-22 to 2024-02-02: `g150_t15`
- 2024-02-02 to 2024-03-15: `g150_t15`
- 2024-03-15 to 2024-04-26: `g150_t15`
- 2024-04-26 to 2024-06-07: `g100_t25`
- 2024-06-07 to 2024-07-19: `g50_t25`
- 2024-07-19 to 2024-08-30: `g150_t15`
- 2024-08-30 to 2024-10-11: `g100_t15`
- 2024-10-11 to 2024-11-22: `g100_t25`
- 2024-11-22 to 2025-01-03: `g100_t25`
- 2025-01-03 to 2025-02-14: `g50_t15`
- 2025-02-14 to 2025-03-28: `g100_t15`
- 2025-03-28 to 2025-05-09: `g150_t25`
- 2025-05-09 to 2025-06-20: `g50_t15`
- 2025-06-20 to 2025-08-01: `g100_t25`
- 2025-08-01 to 2025-09-12: `g50_t25`

## gap_fade_v1: FAILED

Out-of-sample: 293 trades, net -3938.42 (gross -25.29, charges 3913.13), win rate 33.1%, profit factor 0.390, compounded return -7.62%.

| check | actual | required | |
|---|---|---|---|
| net profit is positive | -3938.42 | > 0 | FAIL |
| enough trades to mean something | 293 | >= 150 | ok |
| profit factor | 0.390 | >= 1.2 | FAIL |
| profitable in most out-of-sample windows | 2 of 24 | >= 60% of windows | FAIL |
| no window's drawdown is too deep | 1.7% | <= 10% | ok |
| profitable across instruments, not a few | 3 of 27 | >= 50% of instruments | FAIL |
| still profitable without its best window | -4604.47 | > 0 | FAIL |
| still profitable if charges were twice as high | -7851.55 | > 0 | FAIL |

Classification: **REJECTED**

| gate | outcome | evidence |
|---|---|---|
| profit after costs is real | fail | net -3938.42, 95% interval -5404.26 to -2464.26, P(net > 0) 0.000 |
| profit survives adverse costs | unknown | net -3938.42 as run, -8472.28 under adverse costs |
| profitable in most walk-forward windows | fail | 2 of 24 windows |
| enough trades | pass | 293 trades, 150 needed to validate |
| enough history | pass | 820 trading days, 500 needed |
| drawdown within the risk budget | pass | worst window 1.7%, budget 10.0% |
| profit is not concentrated | unknown | no profit to attribute |
| survives parameter changes | fail | 4% of neighbouring parameter sets profit, 50% needed |
| beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 840 trials, 0.95 needed |
| beats the always-long baseline | pass | strategy net -3938.42, baseline -16219.40 |
| evidence spans enough distinct regimes | pass | 5 regime(s); diversity not required |

By direction (out-of-sample, 293 trades total):

| side | trades | net P&L |
|---|---|---|
| long | 132 | -1532.33 |
| short | 161 | -2406.09 |

Net P&L if costs were different (first-order re-pricing of the same trades):

- no_slippage: -2649.77
- benchmark: -3938.42
- slippage_plus5: -5227.07
- fees_x1_5: -5894.98
- fees_x2: -7851.55
- adverse: -8472.28

Parameters chosen per test window (each on its own training data):

- 2022-12-09 to 2023-01-20: `g150_f10`
- 2023-01-20 to 2023-03-03: `g150_f10`
- 2023-03-03 to 2023-04-14: `g150_f10`
- 2023-04-14 to 2023-05-26: `g150_f10`
- 2023-05-26 to 2023-07-07: `g150_f10`
- 2023-07-07 to 2023-08-18: `g150_f10`
- 2023-08-18 to 2023-09-29: `g150_f5`
- 2023-09-29 to 2023-11-10: `g150_f10`
- 2023-11-10 to 2023-12-22: `g150_f10`
- 2023-12-22 to 2024-02-02: `g150_f10`
- 2024-02-02 to 2024-03-15: `g150_f10`
- 2024-03-15 to 2024-04-26: `g150_f10`
- 2024-04-26 to 2024-06-07: `g150_f10`
- 2024-06-07 to 2024-07-19: `g150_f10`
- 2024-07-19 to 2024-08-30: `g150_f10`
- 2024-08-30 to 2024-10-11: `g150_f5`
- 2024-10-11 to 2024-11-22: `g50_f10`
- 2024-11-22 to 2025-01-03: `g150_f5`
- 2025-01-03 to 2025-02-14: `g150_f5`
- 2025-02-14 to 2025-03-28: `g150_f10`
- 2025-03-28 to 2025-05-09: `g150_f10`
- 2025-05-09 to 2025-06-20: `g100_f10`
- 2025-06-20 to 2025-08-01: `g150_f5`
- 2025-08-01 to 2025-09-12: `g50_f10`

