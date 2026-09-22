# Strategy curation

Selection criteria (fixed before any result was seen):

- at least 150 out-of-sample trades, profit factor >= 1.2, net profit > 0
- profitable in >= 60% of out-of-sample windows and >= 50% of instruments
- worst window drawdown <= 10%
- still profitable without its best window, and with charges doubled

## gap_go_v1: FAILED

Out-of-sample: 151 trades, net -2791.94 (gross -781.40, charges 2010.54), win rate 29.8%, profit factor 0.402, compounded return -5.47%.

| check | actual | required | |
|---|---|---|---|
| net profit is positive | -2791.94 | > 0 | FAIL |
| enough trades to mean something | 151 | >= 150 | ok |
| profit factor | 0.402 | >= 1.2 | FAIL |
| profitable in most out-of-sample windows | 0 of 5 | >= 60% of windows | FAIL |
| no window's drawdown is too deep | 1.5% | <= 10% | ok |
| profitable across instruments, not a few | 6 of 26 | >= 50% of instruments | FAIL |
| still profitable without its best window | -2645.73 | > 0 | FAIL |
| still profitable if charges were twice as high | -4802.48 | > 0 | FAIL |

Classification: **REJECTED**

| gate | outcome | evidence |
|---|---|---|
| profit after costs is real | fail | net -2791.94, 95% interval -4063.39 to -1541.42, P(net > 0) 0.000 |
| profit survives adverse costs | unknown | net -2791.94 as run, -5091.90 under adverse costs |
| profitable in most walk-forward windows | fail | 0 of 5 windows |
| enough trades | pass | 151 trades, 150 needed to validate |
| enough history | unknown | 260 trading days, 500 needed |
| drawdown within the risk budget | pass | worst window 1.5%, budget 10.0% |
| profit is not concentrated | unknown | no profit to attribute |
| survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 353 trials, 0.95 needed |
| beats the always-long baseline | pass | strategy net -2791.94, baseline -3062.66 |

Net P&L if costs were different (first-order re-pricing of the same trades):

- no_slippage: -2144.60
- benchmark: -2791.94
- slippage_plus5: -3439.28
- fees_x1_5: -3797.21
- fees_x2: -4802.48
- adverse: -5091.90

Parameters chosen per test window (each on its own training data):

- 2026-01-30 to 2026-03-13: `g50_t25`
- 2026-03-13 to 2026-04-24: `g100_t15`
- 2026-04-24 to 2026-06-05: `g100_t25`
- 2026-06-05 to 2026-07-17: `g150_t15`
- 2026-07-17 to 2026-08-28: `g50_t25`

