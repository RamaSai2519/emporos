# Strategy curation

Selection criteria (fixed before any result was seen):

- at least 150 out-of-sample trades, profit factor >= 1.2, net profit > 0
- profitable in >= 60% of out-of-sample windows and >= 50% of instruments
- worst window drawdown <= 10%
- still profitable without its best window, and with charges doubled

## liquidity_thrust_v1: FAILED

Out-of-sample: 1134 trades, net -18574.98 (gross -3455.09, charges 15119.89), win rate 24.1%, profit factor 0.320, compounded return -31.18%.

| check | actual | required | |
|---|---|---|---|
| net profit is positive | -18574.98 | > 0 | FAIL |
| enough trades to mean something | 1134 | >= 150 | ok |
| profit factor | 0.320 | >= 1.2 | FAIL |
| profitable in most out-of-sample windows | 0 of 32 | >= 60% of windows | FAIL |
| no window's drawdown is too deep | 1.7% | <= 10% | ok |
| profitable across instruments, not a few | 0 of 27 | >= 50% of instruments | FAIL |
| still profitable without its best window | -18240.95 | > 0 | FAIL |
| still profitable if charges were twice as high | -33694.87 | > 0 | FAIL |

Classification: **REJECTED**

| gate | outcome | evidence |
|---|---|---|
| profit after costs is real | fail | net -18574.98, 95% interval -20686.15 to -16440.76, P(net > 0) 0.000 |
| profit survives adverse costs | unknown | net -18574.98 as run, -35976.90 under adverse costs |
| profitable in most walk-forward windows | fail | 0 of 32 windows |
| enough trades | pass | 1134 trades, 150 needed to validate |
| enough history | pass | 1080 trading days, 500 needed |
| drawdown within the risk budget | pass | worst window 1.7%, budget 10.0% |
| profit is not concentrated | unknown | no profit to attribute |
| survives parameter changes | fail | 0% of neighbouring parameter sets profit, 50% needed |
| beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.000 over 717 trials, 0.95 needed |
| beats the always-long baseline | pass | strategy net -18574.98, baseline -21066.37 |
| evidence spans enough distinct regimes | pass | 5 regime(s); diversity not required |

By direction (out-of-sample, 1134 trades total):

| side | trades | net P&L |
|---|---|---|
| long | 660 | -10484.21 |
| short | 474 | -8090.77 |

Net P&L if costs were different (first-order re-pricing of the same trades):

- no_slippage: -13653.99
- benchmark: -18574.98
- slippage_plus5: -23495.97
- fees_x1_5: -26134.92
- fees_x2: -33694.87
- adverse: -35976.90

Parameters chosen per test window (each on its own training data):

- 2022-12-09 to 2023-01-20: `surge25_t5`
- 2023-01-20 to 2023-03-03: `surge25_t5`
- 2023-03-03 to 2023-04-14: `surge40_t3`
- 2023-04-14 to 2023-05-26: `surge25_t4`
- 2023-05-26 to 2023-07-07: `surge25_t3`
- 2023-07-07 to 2023-08-18: `surge25_t5`
- 2023-08-18 to 2023-09-29: `surge40_t5`
- 2023-09-29 to 2023-11-10: `surge40_t5`
- 2023-11-10 to 2023-12-22: `surge40_t4`
- 2023-12-22 to 2024-02-02: `surge25_t5`
- 2024-02-02 to 2024-03-15: `surge40_t5`
- 2024-03-15 to 2024-04-26: `surge25_t5`
- 2024-04-26 to 2024-06-07: `surge40_t5`
- 2024-06-07 to 2024-07-19: `surge25_t4`
- 2024-07-19 to 2024-08-30: `surge25_t5`
- 2024-08-30 to 2024-10-11: `surge40_t3`
- 2024-10-11 to 2024-11-22: `surge25_t4`
- 2024-11-22 to 2025-01-03: `surge40_t3`
- 2025-01-03 to 2025-02-14: `surge25_t4`
- 2025-02-14 to 2025-03-28: `surge40_t5`
- 2025-03-28 to 2025-05-09: `surge25_t5`
- 2025-05-09 to 2025-06-20: `surge25_t5`
- 2025-06-20 to 2025-08-01: `surge25_t3`
- 2025-08-01 to 2025-09-12: `surge25_t3`
- 2025-09-12 to 2025-10-24: `surge25_t3`
- 2025-10-24 to 2025-12-05: `surge25_t4`
- 2025-12-05 to 2026-01-16: `surge40_t5`
- 2026-01-16 to 2026-02-27: `surge40_t3`
- 2026-02-27 to 2026-04-10: `surge25_t5`
- 2026-04-10 to 2026-05-22: `surge25_t5`
- 2026-05-22 to 2026-07-03: `surge40_t5`
- 2026-07-03 to 2026-08-14: `surge40_t5`

