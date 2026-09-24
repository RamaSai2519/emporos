# EXP-20260924-orb-breakout-d089023d

**INCONCLUSIVE** | strategy | orb-breakout | declared 2026-09-24T03:00:00+00:00

## Declaration (made before the run)

- Hypothesis: Opening-range breakouts continue on high-volume days.
- Economic rationale: Order-flow imbalance at the open persists for the first hour.
- Falsified by: Net expectancy after costs is not positive out of sample.

Parameter grid:

| parameter | values |
|---|---|
| range_bars | 3, 6 |
| target_r | 1.5, 3.0 |

Feature versions:

| feature | version |
|---|---|
| opening_range | 1 |

## Versions

| what | value |
|---|---|
| behaviour hash (base config) | sha256:abababababababababababababababababababababababababababababababab |
| dataset universe hash | sha256:0101010101010101010101010101010101010101010101010101010101010101 |
| dataset calendar | sha256:0202020202020202020202020202020202020202020202020202020202020202 |
| dataset quarantine hash | sha256:0303030303030303030303030303030303030303030303030303030303030303 |
| dataset span | 5m 2025-09-22 to 2026-08-07 |
| fee schedule | angelone-2026-09-20 |
| slippage (bps per side) | 5 |
| benchmark hash | sha256:0404040404040404040404040404040404040404040404040404040404040404 |
| code revision | 1748aab |
| candidate hash r3_t15 | sha256:cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd |

## Periods

| period | days |
|---|---|
| train | 2025-09-22 to 2026-06-26 |
| validation (walk-forward out-of-sample) | 2026-01-30 to 2026-08-07 |
| holdout (reserved) | 2026-08-08 to 2026-09-18 |

## Headline metrics

| metric | value |
|---|---|
| gross P&L | 5210.40 |
| net P&L | 3120.75 |
| expectancy per trade | 8.67 |
| profit factor | 1.421 |
| max drawdown (worst window) | 4.1% |
| Sharpe (annualised) | 1.372 |
| Deflated Sharpe | 0.931 |
| PBO | 0.220 |
| trades | 360 |
| win rate | 51.1% |

## Cost breakdown

| component | amount |
|---|---|
| brokerage | 900.00 |
| statutory charges | 700.00 |
| spread | 310.40 |
| slippage | 479.25 |
| total | 2389.65 |
| per trade (bps of notional) | 6.638 |
| per trade (INR) | 6.64 |

## By regime

| regime | trades | net P&L | win rate |
|---|---|---|---|
| range | 150 | 720.65 | 45.3% |
| trend_up | 210 | 2400.10 | 55.0% |

## By walk-forward window

| window | first | last | net P&L |
|---|---|---|---|
| 0 | 2026-01-30 | 2026-03-12 | 1200.50 |
| 1 | 2026-03-13 | 2026-04-23 | -310.25 |

## Reasons

| code | rule | outcome | evidence |
|---|---|---|---|
| net_pnl_not_real | profit after costs is real | pass | net 3120.75, P(net > 0) 0.981 |
| dsr_below_threshold | beats the luck of the search (Deflated Sharpe) | unknown | DSR 0.931 over 35 trials, 0.95 needed |

## Notes

- the final holdout was reserved and is not evaluated by curation
