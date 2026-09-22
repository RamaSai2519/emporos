# Strategy curation

Selection criteria (fixed before any result was seen):

- at least 150 out-of-sample trades, profit factor >= 1.2, net profit > 0
- profitable in >= 60% of out-of-sample windows and >= 50% of instruments
- worst window drawdown <= 10%
- still profitable without its best window, and with charges doubled

## donchian_v1: FAILED

Out-of-sample: 173 trades, net -3033.26 (gross -730.93, charges 2302.33), win rate 21.4%, profit factor 0.188, compounded return -5.92%.

| check | actual | required | |
|---|---|---|---|
| net profit is positive | -3033.26 | > 0 | FAIL |
| enough trades to mean something | 173 | >= 150 | ok |
| profit factor | 0.188 | >= 1.2 | FAIL |
| profitable in most out-of-sample windows | 0 of 5 | >= 60% of windows | FAIL |
| no window's drawdown is too deep | 1.4% | <= 10% | ok |
| profitable across instruments, not a few | 3 of 27 | >= 50% of instruments | FAIL |
| still profitable without its best window | -2531.55 | > 0 | FAIL |
| still profitable if charges were twice as high | -5335.59 | > 0 | FAIL |

Classification: **REJECTED**

| gate | outcome | evidence |
|---|---|---|
| profit after costs is real | fail | net -3033.26, 95% interval -3674.20 to -2383.43, P(net > 0) 0.000 |
| profit survives adverse costs | unknown | net -3033.26 as run, -5662.81 under adverse costs |
| profitable in most walk-forward windows | fail | 0 of 5 windows |
| enough trades | pass | 173 trades, 150 needed to validate |
| enough history | unknown | 260 trading days, 500 needed |
| drawdown within the risk budget | pass | worst window 1.4%, budget 10.0% |
| profit is not concentrated | unknown | no profit to attribute |
| survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 283 trials, 0.95 needed |
| beats the always-long baseline | pass | strategy net -3033.26, baseline -3062.66 |

Net P&L if costs were different (first-order re-pricing of the same trades):

- no_slippage: -2294.07
- benchmark: -3033.26
- slippage_plus5: -3772.45
- fees_x1_5: -4184.42
- fees_x2: -5335.59
- adverse: -5662.81

Parameters chosen per test window (each on its own training data):

- 2026-01-30 to 2026-03-13: `l40_tr3`
- 2026-03-13 to 2026-04-24: `l20_tr3`
- 2026-04-24 to 2026-06-05: `l20_tr2`
- 2026-06-05 to 2026-07-17: `l20_tr3`
- 2026-07-17 to 2026-08-28: `l20_tr3`

