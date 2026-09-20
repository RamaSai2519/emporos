# Strategy curation

Selection criteria (fixed before any result was seen):

- at least 150 out-of-sample trades, profit factor >= 1.2, net profit > 0
- profitable in >= 60% of out-of-sample windows and >= 50% of instruments
- worst window drawdown <= 10%
- still profitable without its best window, and with charges doubled

## vwap_reversion_v1: FAILED

Out-of-sample: 56 trades, net -4241.34 (gross -1152.29, charges 3089.05), win rate 33.9%, profit factor 0.269, compounded return -4.17%.

| check | actual | required | |
|---|---|---|---|
| net profit is positive | -4241.34 | > 0 | FAIL |
| enough trades to mean something | 56 | >= 150 | FAIL |
| profit factor | 0.269 | >= 1.2 | FAIL |
| profitable in most out-of-sample windows | 0 of 5 | >= 60% of windows | FAIL |
| no window's drawdown is too deep | 1.5% | <= 10% | ok |
| profitable across instruments, not a few | 9 of 27 | >= 50% of instruments | FAIL |
| still profitable without its best window | -3681.87 | > 0 | FAIL |
| still profitable if charges were twice as high | -7330.39 | > 0 | FAIL |

Parameters chosen per test window (each on its own training data):

- 2026-01-30 to 2026-03-13: `z20_x30_s15`
- 2026-03-13 to 2026-04-24: `z20_x30_s15`
- 2026-04-24 to 2026-06-05: `z20_x30_s15`
- 2026-06-05 to 2026-07-17: `z25_x25_s25`
- 2026-07-17 to 2026-08-28: `z30_x25_s15`

