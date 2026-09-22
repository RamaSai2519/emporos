# Strategy curation

Selection criteria (fixed before any result was seen):

- at least 150 out-of-sample trades, profit factor >= 1.2, net profit > 0
- profitable in >= 60% of out-of-sample windows and >= 50% of instruments
- worst window drawdown <= 10%
- still profitable without its best window, and with charges doubled

## vwap_trend_v1: FAILED

Out-of-sample: 161 trades, net -3155.88 (gross -1017.92, charges 2137.96), win rate 14.9%, profit factor 0.177, compounded return -6.15%.

| check | actual | required | |
|---|---|---|---|
| net profit is positive | -3155.88 | > 0 | FAIL |
| enough trades to mean something | 161 | >= 150 | ok |
| profit factor | 0.177 | >= 1.2 | FAIL |
| profitable in most out-of-sample windows | 0 of 5 | >= 60% of windows | FAIL |
| no window's drawdown is too deep | 1.5% | <= 10% | ok |
| profitable across instruments, not a few | 2 of 26 | >= 50% of instruments | FAIL |
| still profitable without its best window | -2563.22 | > 0 | FAIL |
| still profitable if charges were twice as high | -5293.84 | > 0 | FAIL |

Classification: **REJECTED**

| gate | outcome | evidence |
|---|---|---|
| profit after costs is real | fail | net -3155.88, 95% interval -3803.73 to -2478.69, P(net > 0) 0.000 |
| profit survives adverse costs | unknown | net -3155.88 as run, -5573.47 under adverse costs |
| profitable in most walk-forward windows | fail | 0 of 5 windows |
| enough trades | pass | 161 trades, 150 needed to validate |
| enough history | unknown | 260 trading days, 500 needed |
| drawdown within the risk budget | pass | worst window 1.5%, budget 10.0% |
| profit is not concentrated | unknown | no profit to attribute |
| survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 248 trials, 0.95 needed |
| beats the always-long baseline | fail | strategy net -3155.88, baseline -3062.66 |

Net P&L if costs were different (first-order re-pricing of the same trades):

- no_slippage: -2481.58
- benchmark: -3155.88
- slippage_plus5: -3830.18
- fees_x1_5: -4224.86
- fees_x2: -5293.84
- adverse: -5573.47

Parameters chosen per test window (each on its own training data):

- 2026-01-30 to 2026-03-13: `cont_s15_t30`
- 2026-03-13 to 2026-04-24: `cont_s15_t30`
- 2026-04-24 to 2026-06-05: `cont_s15_t30`
- 2026-06-05 to 2026-07-17: `pull_s10_t15`
- 2026-07-17 to 2026-08-28: `pull_s15_t30`

