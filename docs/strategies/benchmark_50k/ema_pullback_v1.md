# Strategy curation

Selection criteria (fixed before any result was seen):

- at least 150 out-of-sample trades, profit factor >= 1.2, net profit > 0
- profitable in >= 60% of out-of-sample windows and >= 50% of instruments
- worst window drawdown <= 10%
- still profitable without its best window, and with charges doubled

## ema_pullback_v1: FAILED

Out-of-sample: 155 trades, net -3312.55 (gross -1247.61, charges 2064.94), win rate 12.3%, profit factor 0.115, compounded return -6.45%.

| check | actual | required | |
|---|---|---|---|
| net profit is positive | -3312.55 | > 0 | FAIL |
| enough trades to mean something | 155 | >= 150 | ok |
| profit factor | 0.115 | >= 1.2 | FAIL |
| profitable in most out-of-sample windows | 0 of 5 | >= 60% of windows | FAIL |
| no window's drawdown is too deep | 1.6% | <= 10% | ok |
| profitable across instruments, not a few | 2 of 26 | >= 50% of instruments | FAIL |
| still profitable without its best window | -2711.62 | > 0 | FAIL |
| still profitable if charges were twice as high | -5377.49 | > 0 | FAIL |

Classification: **REJECTED**

| gate | outcome | evidence |
|---|---|---|
| profit after costs is real | fail | net -3312.55, 95% interval -3794.03 to -2787.38, P(net > 0) 0.000 |
| profit survives adverse costs | unknown | net -3312.55 as run, -5682.47 under adverse costs |
| profitable in most walk-forward windows | fail | 0 of 5 windows |
| enough trades | pass | 155 trades, 150 needed to validate |
| enough history | unknown | 260 trading days, 500 needed |
| drawdown within the risk budget | pass | worst window 1.6%, budget 10.0% |
| profit is not concentrated | unknown | no profit to attribute |
| survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 318 trials, 0.95 needed |
| beats the always-long baseline | fail | strategy net -3312.55, baseline -3062.66 |

Net P&L if costs were different (first-order re-pricing of the same trades):

- no_slippage: -2643.82
- benchmark: -3312.55
- slippage_plus5: -3981.28
- fees_x1_5: -4345.02
- fees_x2: -5377.49
- adverse: -5682.47

Parameters chosen per test window (each on its own training data):

- 2026-01-30 to 2026-03-13: `vwap_s10_t15`
- 2026-03-13 to 2026-04-24: `vwap_s15_t30`
- 2026-04-24 to 2026-06-05: `nov_s10_t15`
- 2026-06-05 to 2026-07-17: `vwap_s10_t15`
- 2026-07-17 to 2026-08-28: `vwap_s15_t20`

