# Strategy curation

Selection criteria (fixed before any result was seen):

- at least 150 out-of-sample trades, profit factor >= 1.2, net profit > 0
- profitable in >= 60% of out-of-sample windows and >= 50% of instruments
- worst window drawdown <= 10%
- still profitable without its best window, and with charges doubled

## relative_strength_v1: FAILED

Out-of-sample: 176 trades, net -2869.34 (gross -509.71, charges 2359.63), win rate 10.2%, profit factor 0.184, compounded return -5.61%.

| check | actual | required | |
|---|---|---|---|
| net profit is positive | -2869.34 | > 0 | FAIL |
| enough trades to mean something | 176 | >= 150 | ok |
| profit factor | 0.184 | >= 1.2 | FAIL |
| profitable in most out-of-sample windows | 0 of 5 | >= 60% of windows | FAIL |
| no window's drawdown is too deep | 1.3% | <= 10% | ok |
| profitable across instruments, not a few | 2 of 22 | >= 50% of instruments | FAIL |
| still profitable without its best window | -2346.46 | > 0 | FAIL |
| still profitable if charges were twice as high | -5228.97 | > 0 | FAIL |

Classification: **REJECTED**

| gate | outcome | evidence |
|---|---|---|
| profit after costs is real | fail | net -2869.34, 95% interval -3491.50 to -2175.09, P(net > 0) 0.000 |
| profit survives adverse costs | unknown | net -2869.34 as run, -5649.51 under adverse costs |
| profitable in most walk-forward windows | fail | 0 of 5 windows |
| enough trades | pass | 176 trades, 150 needed to validate |
| enough history | unknown | 260 trading days, 500 needed |
| drawdown within the risk budget | pass | worst window 1.3%, budget 10.0% |
| profit is not concentrated | unknown | no profit to attribute |
| survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 493 trials, 0.95 needed |
| beats the always-long baseline | pass | strategy net -2869.34, baseline -3062.66 |

Net P&L if costs were different (first-order re-pricing of the same trades):

- no_slippage: -2069.16
- benchmark: -2869.34
- slippage_plus5: -3669.52
- fees_x1_5: -4049.16
- fees_x2: -5228.97
- adverse: -5649.51

Parameters chosen per test window (each on its own training data):

- 2026-01-30 to 2026-03-13: `fast_t20`
- 2026-03-13 to 2026-04-24: `fast_t20`
- 2026-04-24 to 2026-06-05: `fast_t20`
- 2026-06-05 to 2026-07-17: `fast_t20`
- 2026-07-17 to 2026-08-28: `fast_t20`

