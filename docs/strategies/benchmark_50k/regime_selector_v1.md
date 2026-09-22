# Strategy curation

Selection criteria (fixed before any result was seen):

- at least 150 out-of-sample trades, profit factor >= 1.2, net profit > 0
- profitable in >= 60% of out-of-sample windows and >= 50% of instruments
- worst window drawdown <= 10%
- still profitable without its best window, and with charges doubled

## regime_selector_v1: FAILED

Out-of-sample: 175 trades, net -2879.72 (gross -548.36, charges 2331.36), win rate 22.3%, profit factor 0.123, compounded return -5.63%.

| check | actual | required | |
|---|---|---|---|
| net profit is positive | -2879.72 | > 0 | FAIL |
| enough trades to mean something | 175 | >= 150 | ok |
| profit factor | 0.123 | >= 1.2 | FAIL |
| profitable in most out-of-sample windows | 0 of 5 | >= 60% of windows | FAIL |
| no window's drawdown is too deep | 1.3% | <= 10% | ok |
| profitable across instruments, not a few | 0 of 27 | >= 50% of instruments | FAIL |
| still profitable without its best window | -2435.52 | > 0 | FAIL |
| still profitable if charges were twice as high | -5211.08 | > 0 | FAIL |

Classification: **REJECTED**

| gate | outcome | evidence |
|---|---|---|
| profit after costs is real | fail | net -2879.72, 95% interval -3380.53 to -2394.90, P(net > 0) 0.000 |
| profit survives adverse costs | unknown | net -2879.72 as run, -5555.58 under adverse costs |
| profitable in most walk-forward windows | fail | 0 of 5 windows |
| enough trades | pass | 175 trades, 150 needed to validate |
| enough history | unknown | 260 trading days, 500 needed |
| drawdown within the risk budget | pass | worst window 1.3%, budget 10.0% |
| profit is not concentrated | unknown | no profit to attribute |
| survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 458 trials, 0.95 needed |
| beats the always-long baseline | pass | strategy net -2879.72, baseline -3062.66 |

Net P&L if costs were different (first-order re-pricing of the same trades):

- no_slippage: -2124.63
- benchmark: -2879.72
- slippage_plus5: -3634.81
- fees_x1_5: -4045.40
- fees_x2: -5211.08
- adverse: -5555.58

Parameters chosen per test window (each on its own training data):

- 2026-01-30 to 2026-03-13: `wide_trend`
- 2026-03-13 to 2026-04-24: `wide_trend`
- 2026-04-24 to 2026-06-05: `base`
- 2026-06-05 to 2026-07-17: `wide_reversion`
- 2026-07-17 to 2026-08-28: `wide_trend`

