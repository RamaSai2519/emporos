# Strategy curation

Selection criteria (fixed before any result was seen):

- at least 150 out-of-sample trades, profit factor >= 1.2, net profit > 0
- profitable in >= 60% of out-of-sample windows and >= 50% of instruments
- worst window drawdown <= 10%
- still profitable without its best window, and with charges doubled

## gap_fade_v1: FAILED

Out-of-sample: 147 trades, net -2077.25 (gross -123.36, charges 1953.89), win rate 34.7%, profit factor 0.344, compounded return -4.09%.

| check | actual | required | |
|---|---|---|---|
| net profit is positive | -2077.25 | > 0 | FAIL |
| enough trades to mean something | 147 | >= 150 | FAIL |
| profit factor | 0.344 | >= 1.2 | FAIL |
| profitable in most out-of-sample windows | 0 of 5 | >= 60% of windows | FAIL |
| no window's drawdown is too deep | 1.8% | <= 10% | ok |
| profitable across instruments, not a few | 6 of 27 | >= 50% of instruments | FAIL |
| still profitable without its best window | -1881.45 | > 0 | FAIL |
| still profitable if charges were twice as high | -4031.14 | > 0 | FAIL |

Classification: **REJECTED**

| gate | outcome | evidence |
|---|---|---|
| profit after costs is real | fail | net -2077.25, 95% interval -2981.39 to -1194.00, P(net > 0) 0.000 |
| profit survives adverse costs | unknown | net -2077.25 as run, -4295.91 under adverse costs |
| profitable in most walk-forward windows | fail | 0 of 5 windows |
| enough trades | unknown | 147 trades, 150 needed to validate |
| enough history | unknown | 260 trading days, 500 needed |
| drawdown within the risk budget | pass | worst window 1.8%, budget 10.0% |
| profit is not concentrated | unknown | no profit to attribute |
| survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 388 trials, 0.95 needed |
| beats the always-long baseline | pass | strategy net -2077.25, baseline -3062.66 |

Net P&L if costs were different (first-order re-pricing of the same trades):

- no_slippage: -1456.39
- benchmark: -2077.25
- slippage_plus5: -2698.11
- fees_x1_5: -3054.20
- fees_x2: -4031.14
- adverse: -4295.91

Parameters chosen per test window (each on its own training data):

- 2026-01-30 to 2026-03-13: `g150_f10`
- 2026-03-13 to 2026-04-24: `g150_f10`
- 2026-04-24 to 2026-06-05: `g100_f10`
- 2026-06-05 to 2026-07-17: `g150_f10`
- 2026-07-17 to 2026-08-28: `g100_f10`

