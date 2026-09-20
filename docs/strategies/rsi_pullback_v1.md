# Strategy curation

Selection criteria (fixed before any result was seen):

- at least 150 out-of-sample trades, profit factor >= 1.2, net profit > 0
- profitable in >= 60% of out-of-sample windows and >= 50% of instruments
- worst window drawdown <= 10%
- still profitable without its best window, and with charges doubled

## rsi_pullback_v1: FAILED

Out-of-sample: 51 trades, net -3484.56 (gross -700.30, charges 2784.26), win rate 7.8%, profit factor 0.098, compounded return -3.44%.

| check | actual | required | |
|---|---|---|---|
| net profit is positive | -3484.56 | > 0 | FAIL |
| enough trades to mean something | 51 | >= 150 | FAIL |
| profit factor | 0.098 | >= 1.2 | FAIL |
| profitable in most out-of-sample windows | 0 of 5 | >= 60% of windows | FAIL |
| no window's drawdown is too deep | 1.1% | <= 10% | ok |
| profitable across instruments, not a few | 2 of 22 | >= 50% of instruments | FAIL |
| still profitable without its best window | -2958.59 | > 0 | FAIL |
| still profitable if charges were twice as high | -6268.82 | > 0 | FAIL |

Parameters chosen per test window (each on its own training data):

- 2026-01-30 to 2026-03-13: `e15_x55`
- 2026-03-13 to 2026-04-24: `e05_x70`
- 2026-04-24 to 2026-06-05: `e05_x55`
- 2026-06-05 to 2026-07-17: `e15_x70`
- 2026-07-17 to 2026-08-28: `e05_x55`

