# Strategy curation

Selection criteria (fixed before any result was seen):

- at least 150 out-of-sample trades, profit factor >= 1.2, net profit > 0
- profitable in >= 60% of out-of-sample windows and >= 50% of instruments
- worst window drawdown <= 10%
- still profitable without its best window, and with charges doubled

## rsi_pullback_v1: FAILED

Out-of-sample: 171 trades, net -3138.68 (gross -864.94, charges 2273.74), win rate 3.5%, profit factor 0.045, compounded return -6.12%.

| check | actual | required | |
|---|---|---|---|
| net profit is positive | -3138.68 | > 0 | FAIL |
| enough trades to mean something | 171 | >= 150 | ok |
| profit factor | 0.045 | >= 1.2 | FAIL |
| profitable in most out-of-sample windows | 0 of 5 | >= 60% of windows | FAIL |
| no window's drawdown is too deep | 1.4% | <= 10% | ok |
| profitable across instruments, not a few | 1 of 27 | >= 50% of instruments | FAIL |
| still profitable without its best window | -2577.06 | > 0 | FAIL |
| still profitable if charges were twice as high | -5412.42 | > 0 | FAIL |

Classification: **REJECTED**

| gate | outcome | evidence |
|---|---|---|
| profit after costs is real | fail | net -3138.68, 95% interval -3583.50 to -2708.15, P(net > 0) 0.000 |
| profit survives adverse costs | unknown | net -3138.68 as run, -5726.26 under adverse costs |
| profitable in most walk-forward windows | fail | 0 of 5 windows |
| enough trades | pass | 171 trades, 150 needed to validate |
| enough history | unknown | 260 trading days, 500 needed |
| drawdown within the risk budget | pass | worst window 1.4%, budget 10.0% |
| profit is not concentrated | unknown | no profit to attribute |
| survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 213 trials, 0.95 needed |
| beats the always-long baseline | fail | strategy net -3138.68, baseline -3062.66 |

Net P&L if costs were different (first-order re-pricing of the same trades):

- no_slippage: -2413.33
- benchmark: -3138.68
- slippage_plus5: -3864.03
- fees_x1_5: -4275.55
- fees_x2: -5412.42
- adverse: -5726.26

Parameters chosen per test window (each on its own training data):

- 2026-01-30 to 2026-03-13: `e10_x55`
- 2026-03-13 to 2026-04-24: `e15_x55`
- 2026-04-24 to 2026-06-05: `e10_x70`
- 2026-06-05 to 2026-07-17: `e15_x55`
- 2026-07-17 to 2026-08-28: `e15_x55`

