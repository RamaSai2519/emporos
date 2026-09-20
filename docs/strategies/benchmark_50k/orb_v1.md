# Strategy curation

Selection criteria (fixed before any result was seen):

- at least 150 out-of-sample trades, profit factor >= 1.2, net profit > 0
- profitable in >= 60% of out-of-sample windows and >= 50% of instruments
- worst window drawdown <= 10%
- still profitable without its best window, and with charges doubled

## orb_v1: FAILED

Out-of-sample: 167 trades, net -3136.27 (gross -915.68, charges 2220.59), win rate 27.5%, profit factor 0.166, compounded return -6.12%.

| check | actual | required | |
|---|---|---|---|
| net profit is positive | -3136.27 | > 0 | FAIL |
| enough trades to mean something | 167 | >= 150 | ok |
| profit factor | 0.166 | >= 1.2 | FAIL |
| profitable in most out-of-sample windows | 0 of 5 | >= 60% of windows | FAIL |
| no window's drawdown is too deep | 1.4% | <= 10% | ok |
| profitable across instruments, not a few | 1 of 27 | >= 50% of instruments | FAIL |
| still profitable without its best window | -2569.71 | > 0 | FAIL |
| still profitable if charges were twice as high | -5356.86 | > 0 | FAIL |

Classification: **REJECTED**

| gate | outcome | evidence |
|---|---|---|
| profit after costs is real | fail | net -3136.27, 95% interval -3719.24 to -2528.62, P(net > 0) 0.000 |
| profit survives adverse costs | unknown | net -3136.27 as run, -5662.52 under adverse costs |
| profitable in most walk-forward windows | fail | 0 of 5 windows |
| enough trades | pass | 167 trades, 150 needed to validate |
| enough history | unknown | 260 trading days, 500 needed |
| drawdown within the risk budget | pass | worst window 1.4%, budget 10.0% |
| profit is not concentrated | unknown | no profit to attribute |
| survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 143 trials, 0.95 needed |
| beats the always-long baseline | fail | strategy net -3136.27, baseline -3062.66 |

Net P&L if costs were different (first-order re-pricing of the same trades):

- no_slippage: -2428.29
- benchmark: -3136.27
- slippage_plus5: -3844.25
- fees_x1_5: -4246.56
- fees_x2: -5356.86
- adverse: -5662.52

Parameters chosen per test window (each on its own training data):

- 2026-01-30 to 2026-03-13: `r3_t30_s10`
- 2026-03-13 to 2026-04-24: `r6_t15_s10`
- 2026-04-24 to 2026-06-05: `r3_t15_s10`
- 2026-06-05 to 2026-07-17: `r3_t20_s15`
- 2026-07-17 to 2026-08-28: `r3_t20_s15`

