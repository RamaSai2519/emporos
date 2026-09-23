# Known data limitations (EM-177)

What `docs/data/history.md`, `history-quality.md` and the strategy reports in `docs/strategies/`
each say piecemeal, in one place. Every limitation below is either mechanically enforced (a
backtest cannot silently violate it) or explicitly surfaced in a run's `assumptions`/`provenance`
— never a limitation a run can hit without saying so.

## Survivorship: the universe is reconstructed as-of, but not before recorded history

`AsOfInstruments.as_of(moment)` (`src/emporos/backtest/universe.py`) resolves the tradable set from
the instrument master's own versioned eras — a name later delisted, merged or renamed still
resolves correctly for a moment inside its era. The gap: **the master only began recording history
when it was first synced**, so for a moment before an instrument's earliest recorded era, nothing
says what it looked like. The default refuses (fails loudly); `assume_earliest_before_history=True`
(`--assume-current-universe` on the CLI) opts in to that instrument's earliest known definition, and
every run that relied on it says so — in `BacktestSpec.assumptions` (a sentence naming the
instruments) and in `ResearchProvenance.assumed_instrument_ids` (the exact ids). EM-99 H8.

## Prices are unadjusted; known corporate-action artifacts are quarantined, not adjusted

`history/quality.py` stores and audits raw broker prices. `OvernightDiscontinuity` is what an
unadjusted split, bonus or bad print looks like: an open far from the previous close. As of the
last full audit (`docs/data/history-quality.md`, 29 instruments, 2016-10-03..2026-09-18) there are
7 such findings, across NSE:16675, NSE:17963, NSE:2475, NSE:14977 and NSE:3499 (three of them).

**These are quarantined, not adjusted.** `CorporateActionQuarantine`
(`src/emporos/history/quarantine.py`) tracks every quarantined instrument/day; `ResearchIntegrityGate`
(`src/emporos/backtest/integrity.py`) refuses a backtest whose instrument/date window crosses one,
unless the caller passes an explicit, recorded `allow_quarantined_instruments=True` — the same
opt-in shape as `assume_current_universe`. `emporos history quarantine <strategy> --from ... --to
...` proposes `DETECTED` entries from a fresh audit's findings (never duplicating what is already
quarantined) and, with `--write`, persists them to `corporate_action_quarantine`. A `DETECTED` entry
means a check flagged it and nobody has reviewed it yet; a person reviewing one (confirming it is a
genuine split/bonus, or a bad print worth a different fix) re-records it `CURATED`. **No adjustment
pipeline exists yet** — quarantine prevents a strategy from silently trading across the artifact; it
does not yet produce a split/dividend-adjusted series. That is future work, not scoped to EM-177.

## The trading calendar is inferred from the data, not a holiday table

`StoredTradingCalendar` (`src/emporos/history/calendar.py`) has no hand-curated holiday list.
`derive_trading_days` infers a holiday from whether a reference instrument's own daily bars exist
for that weekday. This is deliberately calendar-free (it cannot disagree with the data it judges),
but it means a data gap in the reference instrument's daily bars looks identical to a real holiday.
`StoredTradingCalendar.content_hash()` is recorded in every run's `ResearchProvenance.calendar_version`,
so two runs that disagree about which days were trading days are distinguishable after the fact.

## The broker silently omits some minutes, even for liquid names

`history/quality.py`'s `IntradayGaps` check and `history/gaps.py`'s "confirmed-absent" minute
tracking exist because the broker's history API does not return every minute of every session, for
reasons outside Emporos's control. The most recent ~34 sessions (as of EM-132) have no bars for
15:15-15:29 IST; an exit relying on that window uses the forced square-off instead. EM-99 I2.

## Fee schedules are dated when they were read, not when they took effect

`config/fees/*.yaml` is dated by when the schedule was fetched; `--assume-earliest-fees` lets a run
price days before the oldest schedule with it anyway, and the run's `costs.all_verified` field (and
`assumptions`) say so. Charges are frequently the majority of a strategy's loss, so this assumption
is never silent. EM-99 G4.

## What EM-177 versions per run, and what it does not

Every `BacktestResult.spec.provenance` (`src/emporos/backtest/provenance.py`) is a content hash of:
the resolved universe (this run's own instrument ids only, not the whole instrument master), the
trading-calendar snapshot it was checked against, and the corporate-action quarantine state in
force — plus the dataset's timeframe and date range. Two runs with identical provenance hashes
provably saw identical data; two runs that disagree can be told apart without re-running either one.

**Not yet versioned**: the raw candle content itself (a silent upstream correction to a stored bar
would not change any of these hashes — only `history/quality.py`'s per-repository audit would catch
it), and the fee-schedule library's content (schedules are named/dated, not hashed).
