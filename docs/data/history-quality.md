# History quality: 5m bars, 2016-10-03..2026-09-18

29 instruments, 2453 market days (a day at least half of the instruments traded). Prices are unadjusted broker prices.

## Checks

| check | what it finds | errors | warnings |
|---|---|---|---|
| duplicate_timestamps | two bars at one instant | 0 | 0 |
| timestamp_alignment | a bar off the grid or outside the session | 0 | 0 |
| missing_market_days | a market day with no bars, inside the instrument's own span | 0 | 3 |
| intraday_gaps | a market day with fewer bars than the session holds | 0 | 1228 |
| overnight_discontinuity | an open far from the previous close: split, bonus or bad data | 0 | 7 |

## Instruments

| instrument | bars | first day | last day | findings |
|---|---|---|---|---|
| NSE:25 | 183719 | 2016-10-03 | 2026-09-18 | 43 |
| NSE:236 | 183812 | 2016-10-03 | 2026-09-18 | 40 |
| NSE:5900 | 183662 | 2016-10-03 | 2026-09-18 | 41 |
| NSE:16675 | 183883 | 2016-10-03 | 2026-09-18 | 44 |
| NSE:317 | 183737 | 2016-10-03 | 2026-09-18 | 40 |
| NSE:10604 | 183736 | 2016-10-03 | 2026-09-18 | 41 |
| NSE:20374 | 183736 | 2016-10-03 | 2026-09-18 | 41 |
| NSE:7229 | 183721 | 2016-10-03 | 2026-09-18 | 40 |
| NSE:1333 | 183722 | 2016-10-03 | 2026-09-18 | 40 |
| NSE:1394 | 183786 | 2016-10-03 | 2026-09-18 | 41 |
| NSE:4963 | 183722 | 2016-10-03 | 2026-09-18 | 40 |
| NSE:1594 | 183796 | 2016-10-03 | 2026-09-18 | 40 |
| NSE:1660 | 183637 | 2016-10-03 | 2026-09-18 | 42 |
| NSE:11723 | 183797 | 2016-10-03 | 2026-09-18 | 39 |
| NSE:1922 | 183736 | 2016-10-03 | 2026-09-18 | 41 |
| NSE:11483 | 183738 | 2016-10-03 | 2026-09-18 | 40 |
| NSE:2031 | 183736 | 2016-10-03 | 2026-09-18 | 41 |
| NSE:10999 | 183737 | 2016-10-03 | 2026-09-18 | 40 |
| NSE:17963 | 183721 | 2016-10-03 | 2026-09-18 | 95 |
| NSE:11630 | 183663 | 2016-10-03 | 2026-09-18 | 41 |
| NSE:2475 | 183888 | 2016-10-03 | 2026-09-18 | 41 |
| NSE:14977 | 183814 | 2016-10-03 | 2026-09-18 | 40 |
| NSE:3045 | 183737 | 2016-10-03 | 2026-09-18 | 40 |
| NSE:3351 | 183736 | 2016-10-03 | 2026-09-18 | 41 |
| NSE:3499 | 183887 | 2016-10-03 | 2026-09-18 | 43 |
| NSE:13538 | 183736 | 2016-10-03 | 2026-09-18 | 41 |
| NSE:3506 | 183735 | 2016-10-03 | 2026-09-18 | 41 |
| NSE:11532 | 183737 | 2016-10-03 | 2026-09-18 | 40 |
| NSE:3787 | 183736 | 2016-10-03 | 2026-09-18 | 41 |

## missing_market_days (3)

a market day with no bars, inside the instrument's own span.

- NSE:5900 2020-11-23: no bars on a market day
- NSE:1660 2020-11-23: no bars on a market day
- NSE:11630 2020-11-23: no bars on a market day

## intraday_gaps (1228)

a market day with fewer bars than the session holds.

- NSE:25 2016-11-24: 74 of 75 bars (1 missing)
- NSE:25 2017-07-10: 34 of 75 bars (41 missing)
- NSE:25 2018-04-05: 67 of 75 bars (8 missing)
- NSE:25 2018-09-06: 67 of 75 bars (8 missing)
- NSE:25 2020-03-13: 64 of 75 bars (11 missing)
- NSE:25 2020-03-23: 65 of 75 bars (10 missing)
- NSE:25 2021-01-19: 74 of 75 bars (1 missing)
- NSE:25 2021-02-24: 30 of 75 bars (45 missing)
- NSE:25 2025-10-21: 12 of 75 bars (63 missing)
- NSE:25 2026-08-03: 73 of 75 bars (2 missing)
- NSE:25 2026-08-04: 73 of 75 bars (2 missing)
- NSE:25 2026-08-05: 73 of 75 bars (2 missing)
- NSE:25 2026-08-06: 73 of 75 bars (2 missing)
- NSE:25 2026-08-07: 73 of 75 bars (2 missing)
- NSE:25 2026-08-10: 73 of 75 bars (2 missing)
- NSE:25 2026-08-11: 73 of 75 bars (2 missing)
- NSE:25 2026-08-12: 73 of 75 bars (2 missing)
- NSE:25 2026-08-13: 73 of 75 bars (2 missing)
- NSE:25 2026-08-14: 73 of 75 bars (2 missing)
- NSE:25 2026-08-17: 73 of 75 bars (2 missing)
- NSE:25 2026-08-18: 73 of 75 bars (2 missing)
- NSE:25 2026-08-19: 73 of 75 bars (2 missing)
- NSE:25 2026-08-20: 73 of 75 bars (2 missing)
- NSE:25 2026-08-21: 73 of 75 bars (2 missing)
- NSE:25 2026-08-24: 73 of 75 bars (2 missing)
- ... and 1203 more (all in the JSON record)

## overnight_discontinuity (7)

an open far from the previous close: split, bonus or bad data.

- NSE:16675 2020-09-08: opened at 0.1006 x the previous close (6263.650000 -> 629.900000)
- NSE:17963 2021-12-31: opened at 0.0998 x the previous close (9703.300000 -> 968.750000)
- NSE:2475 2020-03-23: opened at 0.8383 x the previous close (72.350000 -> 60.650000)
- NSE:14977 2021-09-07: opened at 0.7490 x the previous close (174.350000 -> 130.580000)
- NSE:3499 2020-07-28: opened at 0.1008 x the previous close (352.850000 -> 35.560000)
- NSE:3499 2022-07-26: opened at 10.1176 x the previous close (96.070000 -> 972.000000)
- NSE:3499 2022-07-28: opened at 0.1023 x the previous close (959.400000 -> 98.100000)

## What this does not establish

- **Survivorship.** The universe is the instruments listed today. Names that were delisted, merged
  or dropped from the index over these years are absent, so a backtest on this set flatters the
  past. The broker offers no point-in-time constituents; an instrument that starts late (see the
  table) was listed late, which the as-of instrument master handles for the universe a run uses.
- **Adjustments.** Prices are as traded. Each `overnight_discontinuity` above is either a
  corporate action (split, bonus) or a bad bar, and needs a person's decision before a strategy
  trusts the series across it.
