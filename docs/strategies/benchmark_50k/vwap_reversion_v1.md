# Strategy curation

Selection criteria (fixed before any result was seen):

- at least 150 out-of-sample trades, profit factor >= 1.2, net profit > 0
- profitable in >= 60% of out-of-sample windows and >= 50% of instruments
- worst window drawdown <= 10%
- still profitable without its best window, and with charges doubled

## vwap_reversion_v1: FAILED

Out-of-sample: 174 trades, net -3107.08 (gross -787.65, charges 2319.43), win rate 19.0%, profit factor 0.127, compounded return -6.06%.

| check | actual | required | |
|---|---|---|---|
| net profit is positive | -3107.08 | > 0 | FAIL |
| enough trades to mean something | 174 | >= 150 | ok |
| profit factor | 0.127 | >= 1.2 | FAIL |
| profitable in most out-of-sample windows | 0 of 5 | >= 60% of windows | FAIL |
| no window's drawdown is too deep | 1.4% | <= 10% | ok |
| profitable across instruments, not a few | 5 of 27 | >= 50% of instruments | FAIL |
| still profitable without its best window | -2563.45 | > 0 | FAIL |
| still profitable if charges were twice as high | -5426.51 | > 0 | FAIL |

Classification: **REJECTED**

| gate | outcome | evidence |
|---|---|---|
| profit after costs is real | fail | net -3107.08, 95% interval -3704.17 to -2529.45, P(net > 0) 0.000 |
| profit survives adverse costs | unknown | net -3107.08 as run, -5774.49 under adverse costs |
| profitable in most walk-forward windows | fail | 0 of 5 windows |
| enough trades | pass | 174 trades, 150 needed to validate |
| enough history | unknown | 260 trading days, 500 needed |
| drawdown within the risk budget | pass | worst window 1.4%, budget 10.0% |
| profit is not concentrated | unknown | no profit to attribute |
| survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 178 trials, 0.95 needed |
| beats the always-long baseline | fail | strategy net -3107.08, baseline -3062.66 |

Net P&L if costs were different (first-order re-pricing of the same trades):

- no_slippage: -2353.23
- benchmark: -3107.08
- slippage_plus5: -3860.93
- fees_x1_5: -4266.80
- fees_x2: -5426.51
- adverse: -5774.49

Parameters chosen per test window (each on its own training data):

- 2026-01-30 to 2026-03-13: `z25_x30_s15`
- 2026-03-13 to 2026-04-24: `z20_x25_s25`
- 2026-04-24 to 2026-06-05: `z20_x30_s15`
- 2026-06-05 to 2026-07-17: `z25_x30_s15`
- 2026-07-17 to 2026-08-28: `z25_x30_s15`

