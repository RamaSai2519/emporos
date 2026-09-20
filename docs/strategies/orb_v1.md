# Strategy curation

Selection criteria (fixed before any result was seen):

- at least 150 out-of-sample trades, profit factor >= 1.2, net profit > 0
- profitable in >= 60% of out-of-sample windows and >= 50% of instruments
- worst window drawdown <= 10%
- still profitable without its best window, and with charges doubled

## orb_v1: FAILED

Out-of-sample: 40 trades, net -3769.43 (gross -1595.50, charges 2173.93), win rate 32.5%, profit factor 0.217, compounded return -3.71%.

| check | actual | required | |
|---|---|---|---|
| net profit is positive | -3769.43 | > 0 | FAIL |
| enough trades to mean something | 40 | >= 150 | FAIL |
| profit factor | 0.217 | >= 1.2 | FAIL |
| profitable in most out-of-sample windows | 0 of 5 | >= 60% of windows | FAIL |
| no window's drawdown is too deep | 1.2% | <= 10% | ok |
| profitable across instruments, not a few | 4 of 19 | >= 50% of instruments | FAIL |
| still profitable without its best window | -3197.02 | > 0 | FAIL |
| still profitable if charges were twice as high | -5943.36 | > 0 | FAIL |

Parameters chosen per test window (each on its own training data):

- 2026-01-30 to 2026-03-13: `r3_t15_s10`
- 2026-03-13 to 2026-04-24: `r3_t15_s10`
- 2026-04-24 to 2026-06-05: `r3_t15_s10`
- 2026-06-05 to 2026-07-17: `r3_t20_s15`
- 2026-07-17 to 2026-08-28: `r6_t30_s10`

