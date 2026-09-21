# Market history: how far back it goes, where it lives, what it costs

EM-132. Everything here was measured on 2026-09-21 unless it says otherwise.

## 1. What the broker holds (read-only probe)

`emporos history probe-depth --symbol SBIN-EQ` asks Angel One's `getCandleData` for ever longer
spans and ever older weeks and records what comes back (it writes nothing). Result for SBIN-EQ,
as of Friday 2026-09-18:

| interval | longest request served whole | first request cut short | oldest bar | requests used |
|---|---|---|---|---|
| 1m | 30 days | 45 days | 2016-10-03 | 23 |
| 5m | 100 days | 120 days | 2016-10-03 | 27 |
| 15m | 200 days | 365 days | 2016-10-03 | 30 |
| 1h | 400 days | 730 days | 2016-10-03 | 32 |
| 1d | 730 days | (none up to 730) | 2006-09-18 or older | 36 |

- **Ten years of intraday history exist** (from 2016-10-03), at every interval. The daily series
  is at least twenty years (the probe stops looking at twenty).
- An over-long request is **silently truncated** to its most recent days: no error, just fewer
  bars. The "cut short" column is where that starts; the safe chunk is the "served whole" column.
  A finding is a bracket, not an exact figure (a cut of a day or two hides inside a request that
  opens on a weekend or holiday); we fetch 1m in chunks of 28 days, below the 30-day bracket.
- Our database held one year (2025-09-22 onward). The other nine years were simply never fetched.

### 1m or 5m?

We store **5m**, derived from 1m by the same `derive` the live pipeline uses, so a backtest bar is
built exactly like a live one. Fetching the broker's own 5m interval would be 3.5x fewer requests
(100 days per request against 28), but its bars are built by the broker (and the last minutes of
the session are missing from broker 1m history, as recorded earlier), so mixing the two across
the years would put a seam in the series. 1m is not stored: ten years of it is about 27 million
bars, and nothing tests a 1m strategy yet. Fetching it later is the same command with a 1m target.

## 2. Where it lives

**Not in Mongo.** Before this change the `candles` collection held 569,946 documents, 566,530 of
them 5m bars: 108 MB of data, 31 MB compressed on disk, **60 MB of indexes** (two, one unique
compound), for something a backtest only ever reads sequentially. Ten years of 5m for the same 29
symbols would have been about 5.4 million documents.

Now:

| tier | holds | where | why |
|---|---|---|---|
| hot | 1m for 3 days, 5m/15m/1h for 14 days | Mongo `candles` | what the live pipeline needs: restart recovery, strategy warm-up (300 bars of 5m is under 5 sessions) |
| cold | everything older | Parquet, one file per instrument and month | columnar, ~48 KB per instrument-month at 5m, read sequentially by backtests |
| daily | all of it | Mongo | 29 x 250 x 20 years = 145k tiny documents at most; kept hot as before (never rolled) |

The cold tier is **S3 when `S3_BUCKET` is set, otherwise a local directory** (`COLD_ARCHIVE_DIR`,
default `~/.local/share/emporos/cold`). Both implement the same `ObjectStore` interface and hold
the same files (`candles/{timeframe}/{instrument}/{YYYY-MM}.parquet`), so moving from one to the
other is a copy of a directory. `CandleRepository` unions the tiers, so callers cannot tell which
served a bar; `emporos history rollup` moves aged bars from Mongo to the cold tier, verifying by
read-back before it deletes.

**The coverage ledger** for deep history is not in Mongo either: one small JSON object per
instrument-timeframe-month beside the bars (`coverage/5m/{instrument}/{YYYY-MM}.json`), because
a document per instrument-day is 70,000 documents for a record only a resumable backfill reads.
(The 1m backfill keeps using the Mongo ledger, which holds a few dozen documents.)

**`ticks` (the time-series collection)** was created empty by the schema and never written:
plan.md §6 lists it as an "optional raw tick archive, debugging only", and nothing was ever wired
to it. It is removed from the schema. If raw ticks are ever wanted, they belong in the cold tier
as Parquet, not in Mongo.

## 3. Cost

| item | size | cost |
|---|---|---|
| Mongo, before | 570k candle documents, 31 MB + 60 MB indexes | (the shared Atlas tier) |
| Mongo, after | 20,367 candle documents (measured) | |
| cold, 29 symbols x 10 years, 5m | 181 MB measured (section 5) | local disk: none |
| the same on S3 (Standard, Mumbai, about USD 0.025/GB-month) | the same | about USD 0.005 a month |
| the same, 1m instead of 5m | about 5x | still a few cents a month on S3 |

The backtest cache (`~/.cache/emporos/candles`) is a derived copy of closed months; with a local
cold tier it duplicates files that already exist, which is harmless but pointless, and it is safe
to delete.

## 4. How it was filled, and how to repeat it

```bash
# read-only: how far back does the broker go?
pipenv run emporos history probe-depth --symbol SBIN-EQ

# fetch (resumable: a chunk is recorded only after its bars are stored; re-run to continue)
pipenv run emporos history fetch-bars -s SBIN-EQ -s INFY-EQ ... -t 5m --from 2016-10-03 --to 2025-09-19

# move what has aged out of Mongo into the cold tier (verify, then delete)
pipenv run emporos history rollup

# audit what is stored
pipenv run emporos history check config/strategies/orb_v1.yaml -t 5m --from 2016-10-03 --to 2026-09-18
```

Rate limits are respected by the transport (bounded backoff on the broker's "access rate" denial).
Bars the broker sends that are not valid candles (a high below a low, or a negative volume) are
skipped and listed in the command's output, instead of failing the 28-day request they arrived in.

**After a backfill, clear the backtest cache** (`emporos backtest cache clear`): a month already
cached from before the backfill does not contain the bars the backfill added to it.

## 5. What was stored, and what the checks found

Measured 2026-09-21, after the backfill and the rollup.

### Stored

| | |
|---|---|
| range | 2016-10-03 to 2026-09-18, 29 symbols, 5m bars: 5,328,803 bars over 2,453 market days |
| cold tier | 181 MB in 3,514 Parquet files (168 MB of it 5m; about 47 KB per instrument-month), on local disk |
| the same on S3 | about USD 0.005 a month at USD 0.025/GB-month: a rounding error |
| Mongo `candles` | **20,367 documents, down from 569,946** (all 5m; the last 14 days for 29 symbols). No 1m, 15m, 1h or daily bars are held |
| backfill | 9 years (2016-10-03 to 2025-09-19) in 28-day chunks of 1m, derived to 5m; the last re-run reported 0 chunks failed |

The first backfill pass used a strict adapter and failed about 22 chunks on malformed broker bars.
After the adapter was changed to skip a bad bar and list it, the same command (resumable: the
ledger skips finished chunks) fetched the remaining 32 chunks, 45,600 bars.

**54 bars were skipped**, all 1m, on two days: 51 on Saturday 2024-03-02 (the special trading
session) and 3 on 2023-03-03. A read-only probe of NSE:3787 on 2024-03-02 shows why: the broker
reports **negative volumes** for some minutes (-364,224 at 11:15 IST, -144 at 11:19) and a large
positive one right after (+364,472), so the volume on those two days is not trustworthy even where a
bar was kept. Prices on those bars are flat and plausible. Nothing tests volume-based signals on
these days; a strategy that uses volume should exclude them.

### What the checks found (`emporos history check`, full detail in `history-quality.md`/`.json`)

0 errors and 1,238 warnings. No duplicate bar, no bar off the 5-minute grid or outside the session.

- **Which symbols start late: none.** All 29 have bars from 2016-10-03, so there is no listing-date
  effect to handle. (Survivorship is a separate problem, below.)
- **Gaps: 1,228 instrument-days with fewer bars than the session holds (75).**
  - 986 of them are the **latest 34 sessions** (2026-08-03 onward), each missing 2 bars at the end of
    the session: the broker's recent history omits the last minutes (known, EM-99 I2). Sessions
    older than those 34 are complete, which suggests the broker fills a day in some weeks later; that
    is inferred, not measured, and re-fetching those days in a few weeks would show it. It is not
    damage to the older years.
  - **Five market-wide days** hit all 29 instruments with a large hole: 2017-07-10, 2020-03-13,
    2020-03-23, 2021-02-24 and 2025-10-21. 2025-10-21 has 12 bars, which fits the one-hour Diwali
    Muhurat session; 2020-03-13, 2020-03-23 and 2021-02-24 coincide with the March 2020 circuit
    breakers and the February 2021 NSE outage. The 2017-07-10 cause was not established. Every
    instrument shows the same hole on the same day, which points at the market and not at the
    fetching, but that was not checked against exchange notices.
  - The other ~90 are isolated short gaps (1 to 9 missing bars), mostly minutes with no trade in a
    thin stock: NESTLEIND-EQ alone has 61 such days in 2016 to 2018.
- **Missing market days: 3**, all on 2020-11-23: AXISBANK-EQ, ITC-EQ and NTPC-EQ have no bars that
  day. Cause not established.
- **Overnight discontinuities: 7** (an open far from the previous close). They are not repaired:
  prices are the broker's unadjusted prices, and a person has to decide each one.

  | instrument | day | open vs previous close | reading |
  |---|---|---|---|
  | BAJAJFINSV-EQ | 2020-09-08 | 0.1006x (6263.65 to 629.90) | consistent with a 10-for-1 change in share count |
  | NESTLEIND-EQ | 2021-12-31 | 0.0998x (9703.30 to 968.75) | consistent with a 10-for-1 split |
  | TATASTEEL-EQ | 2020-07-28 | 0.1008x (352.85 to 35.56) | 10x drop: a split, or a scaling error in the series |
  | TATASTEEL-EQ | 2022-07-26 | 10.12x (96.07 to 972.00) | a 10x jump up two days before the next drop: not a price move, so the series is scaled inconsistently around 2022-07-28 |
  | TATASTEEL-EQ | 2022-07-28 | 0.1023x (959.40 to 98.10) | consistent with a 10-for-1 split |
  | ONGC-EQ | 2020-03-23 | 0.838x | the same day as a market-wide hole above, so probably a real move |
  | POWERGRID-EQ | 2021-09-07 | 0.749x | a corporate action or bad data; not resolved |

  **A backtest across BAJAJFINSV, NESTLEIND or TATASTEEL over these dates would see a fake 90%
  overnight crash**, and Tata Steel's series is scaled inconsistently for a few days in July 2022.
  Those symbols should be adjusted, or cut at the date, before a strategy is judged on the years
  before them. Nothing in this repository adjusts prices yet.

### What this costs to run

`history check` over the full ten years holds the bars in memory: its peak was about 5.3 GB and it
took 10 minutes. Do not run it alongside other heavy work on a small machine.

### What is still not established

- **Survivorship** (see the report's caveat): the universe is today's, so the past is flattered.
- **Adjustments**: prices are unadjusted; the splits above are the only ones the check can see, and
  it only sees a split as large as its threshold.
- **Volume** on 2024-03-02 and 2023-03-03 (negative broker volumes).
- **The 34 latest sessions** miss their last two bars until the broker completes them.
