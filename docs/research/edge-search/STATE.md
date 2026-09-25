# Edge search — resume point (EM-191)

Plan of record: [`EDGE_SEARCH_PLAN.md`](../../../EDGE_SEARCH_PLAN.md). Map:
[`search-map.yaml`](search-map.yaml) (validated in CI by `emporos.research.search_map`).

> **Steering (2026-09-25, head session): the program is now run under [`PROFIT_PLAN.md`](../../../PROFIT_PLAN.md) (EM-219).**
> The intraday search (this file, Track C) continues as a loop in Agent 2's session, which reports every cell to the
> head (emporos-bd) and declares children only on the head's go. Swing (Track A) and index options (Track B) are new
> tracks with their own ledger. L17 (review-2, EM-216) is SCREEN_REJECT: the daily rank lifts gross to +0.21..0.34% on
> D1 where a threshold gets about 0%, but it stays under the adverse break-even and is concentrated in one name.
> Priority for Track C: PROFIT_PLAN §6.

> **Steering (2026-09-24, operator session): read [`review-1.md`](review-1.md) before choosing the next cell.**
> Its §4 priority order supersedes the "Next" row below: D1 (wide and mid-cap universe, run in the background) and D5
> (event calendar) come ahead of further mega-cap cells. D7 quote recording starts now. Mega-cap cells only where the
> conditioning set selects large days by construction, hold to 15:15 by default, 2-6 arms per cell. Operator decisions
> were pending on EM-206 and EM-207; both are now approved and committed (see the Blocked row).

| Field | Value |
|---|---|
| Capital and size | **Rs 1,00,000 capital, Rs 50,000 max position** (EM-207, 325f8b8): risk.yaml deployed 1,00,000, daily loss 2,000; benchmark.yaml capital 1,00,000. New declarations default to Rs 50,000, so the §3.5 bar is about 0.99%. Cells declared earlier keep their declared size; rejected cells are not re-run at the new size |
| DSR at S4/S5 | EM-206 (529c8e2): `deflated_sharpe_spread`, null hypothesis spread 1/sqrt(T-1), with the full program-wide N. At N about 14k a DSR of 0.95 needs z of about 5.6, an annualised Sharpe of about 2.0 over 2,000 walk-forward days: S4 is passable, so a candidate that clears S3 goes on to S4 and is not parked |
| Iteration | 20 (2026-09-25) |
| Last completed | **L5-index-trend-day-leader: SCREEN_REJECT** (EM-227, 4 arms on 148 D1 names, feasible, gross -0.10..+0.085%: index-trend continuation is closed with no child); **D1 research universe (EM-214) and L2-earnings-gap-fade-after-first-hour: SCREEN_REJECT** (EM-213, 4 arms on 148 names, feasible, gross about zero: the 29-name near-miss was small-sample); **L2-earnings-gap-fade and L2-earnings-gap-continuation: both SCREEN_REJECT** (EM-213, 8 arms, all feasible; fade after 30 minutes gross +0.14..0.19% under the 0.23% break-even, continuation gross negative); D5 results events done (EM-209): 8,573 filings, 8,070 first-public events for 250 names, gaps documented**; L4-india-vix-regime: SCREEN_REJECT** (EM-210, 6 arms, feasible at Rs 50,000 but t under 0.5); D5 collector built and running (EM-209, operator-authorised); **D1 started (EM-208): lists committed, `history fetch-universe` built, background fetch running**; L4-nr7-inside-day-breakout: SCREEN_REJECT** (EM-205, 6 arms, all INFEASIBLE, gross under 0.06%); L3-first-hour-reversal-large-gap: SCREEN_REJECT** (EM-204, 4 arms, feasible but t under 1); L3-first-hour-shock-reversal: SCREEN_REJECT** (EM-203, 6 arms, all INFEASIBLE); L3-orb-high-rvol-wide-range: SCREEN_REJECT** (EM-202, 12 arms, all INFEASIBLE); L3-raw-gap-hold-to-close: SCREEN_REJECT (EM-201, 16 arms); D2 index and VIX series fetched, verified and cached (EM-200); VAULT seal: `vault.yaml`, `VaultGate`, reads refused (EM-199); F4 size-aware evaluation: declared position value everywhere (EM-198); F3B signal-level parity (EM-196); F3 core (EM-195); F2B (EM-194); F2 (EM-193); F1 (EM-192) |
| Next | **The gap-fade family is closed on Discovery** (29 names and D1). Per review-2 (EM-215) the operator session builds L17 (daily top shock fade, EM-216) on the D1 seam. Loop candidates after that: L2-earnings-pead-first-hour (reaction-day gate exists; continuation after a results gap, D1 universe), D5b (EM-212), mega-cap large-day cells (expect weak), and D3 sector taxonomy (the Industry column of the D1 lists is the first source). **A D1 screen takes about 38 minutes for 4 arms**: budget one cell per iteration and run it in the background |
| Global N | **14,098** on 2026-09-25 (14,094 plus the 4 arms of L5-index-trend-day-leader; Track A/B screens are counted in `docs/research/profit/screens.jsonl`) (`emporos backtest trials program`): 717 strategy trials, 23 registry rows, 13,288 documented study trials (feature 11,020, cross-sectional 252, lead-lag 2,016) from `historical-trials.yaml`. Still a **lower bound**: only runs a report states are counted |
| Vault opens used | 0 of 3. Sealed 2026-09-24: 2026-03-19..2026-09-18, every instrument (time-only until D1), seal hash `b4b21b9e8c03` |
| Cells | 36 TODO, 0 BLOCKED, 11 terminal (all SCREEN_REJECT); D5b (EM-212) gates 4 L2 cells; foundations F1-F4, VAULT and D8 are DONE, so cells without a D-dependency may run |
| Blocked on operator | none. EM-209 was ruled on by the operator (NSE announcements endpoint authorised, 2026-09-24); EM-206 and EM-207 approved |
| Paper (S6) running | no |
| Last commit | see `git log -1` (EM-194) |

## Iteration 20 (2026-09-25): L5-index-trend-day-leader (EM-227)
Track C run by Agent 2 under PROFIT_PLAN §6. Design approved by the head with one change (the rank key
is an arm, so the move-ranked arm tests L17's "extension reverts" finding directly). **Declared first**
(0761c6a): NIFTY 50 first-hour move (09:15 open to the 10:10 close) at least 0.4 or 0.5%; candidates are
names whose own first-hour move has the index's sign and exceeds the index's, with at least 10 of the
previous 20 first hours as a volume baseline; rank key volume (first-hour volume over the name's
baseline) or move; top 1 per session from signals before fills; entry at the 10:10 close, hold to
15:15, Rs 50,000, D1, Discovery. Prior stated: under 5% (the index's own later move is a 0.42-0.58%
median absolute on those days). Machinery (2f87c04): `scans/index_trend_leader.py`, the recipe in
`ranked_screen_commands`, 34 tests (one caught the move arm skipping the baseline the declaration gives
both arms).

**Result: every arm SCREEN_REJECT, all feasible.** Median |move| 1.09-1.44%. Gross +0.034%
(0.4%/volume), +0.070% (0.4%/move), -0.104% (0.5%/volume), +0.085% (0.5%/move); net -0.15..-0.34%, t
-0.8..-2.6, 331-507 trades. The declaration's falsification (no volume-ranked arm at gross >= 0.25%)
holds, so **the index-trend continuation lane is closed, no child**. Side note, within noise: the
move-ranked arms did better than the volume-ranked ones, the reverse of what the participation
mechanism predicted, and nowhere near the 0.495% adverse break-even.

## Iteration 19 (2026-09-25): D1 research universe (EM-214) and L2-earnings-gap-fade-after-first-hour (EM-213)
**D1 universe seam** (0f51655). `research.d1_universe` and `config/universe/d1/universe.yaml` (a committed,
hash-checked manifest; `emporos research d1-universe` writes it, once). Rules, fixed in code before the
profile was looked at: (1) **holdout**: 30% of the 221 D1 names outside the audited 29 (66 names) are set
aside by a hash of `seed:id` (seed `emporos-d1-vault-2026-09-25`) and their bars were never read by the
builder (a test proves it; `universe.yaml` lists them); (2) **liquidity**: at least 500 Discovery sessions
and a median daily traded value of Rs 10 crore, from Discovery bars only (36 names excluded: 18 too thin,
18 too short, 8 of them with no Discovery bars); (3) **corporate actions**: an open 15% or more from the
previous close is quarantined (58 instrument-days; a 1:10 bonus is a 9% step and is NOT caught). Result:
148 names screen (the audited 29 plus 119). `CellScreenRun` now takes a `ScreenUniverse` (the audit and
`D1Universe` both fit); `research screen <slug> --universe d1` reads the local cold tier through
`ColdArchiveFiles` (a read-only cache layer, `write` and `clear` refuse) with `FileCandleReader(memoize=False)`:
the memoizing reader holds every name's bars and took 32% of RAM on the first attempt. Building the
manifest takes about 40 minutes. **Known weakness:** the holdout is a UNIVERSE-level exclusion (no research
path reaches those names), not a reader-level seal like the vault's time range: `vault.yaml` is the
operator's file and still says "time-only". S5 must name the 66 held-out names as its instrument set and
the operator should decide whether to move them into `vault.yaml` (a new seal hash).

**Cell.** Declared first (b744b6c): the parent's rules unchanged, entry after 30 minutes (bar 6) or one
hour (bar 12), gaps 1.0 and 2.0%, Rs 50,000, Discovery, 4 arms. **Result: every arm SCREEN_REJECT, all
feasible** (median |move| 1.17-1.36%): 1,072-1,804 trades, gross -0.02..+0.02%, net -0.21..-0.25%, t
-2.7..-3.7. The parent's gross of +0.14..0.19% on 29 names does not survive six times the trades: it was
the small sample. 241 reaction sessions in 2022 against about 380 in a normal year (the ledger's hole);
1,447 results published in session were skipped. No child: gross is zero and no mechanism points the other
way (the continuation was negative on 29 names and there is no gross to mirror). **The results-day gap fade
is closed** on Discovery, on the 29 names and on the wider universe.

## Iteration 18 (2026-09-25): L2 earnings-gap fade and continuation (EM-213)
Atlassian tools loaded again (the session had lost them), so this iteration is tracked: EM-213.
**Machinery** (b746833): `scans/event_days.py` decides the session that REACTS to a results filing
from its exchange timestamp and the name's own sessions only (before 09:15: same session; after
15:30 or a non-session day: the next session; in-session: skipped and counted), and
`EventReactionScan` runs an inner scan per instrument gated to that instrument's reaction days
(the older `DayGate` was date-only). `config/universe/d1/tokens.csv` maps the 250 symbols to Angel
One tokens (public scrip master, 2026-09-25) so events keyed by symbol join candles keyed by
`NSE:<token>`. Recipe `EarningsGapCell`; the screen prints reaction sessions used per year and
the events skipped. 18 tests. The scan is the raw-gap rules unchanged.

**Declared first** (faa7796): fade and continuation, gap 1.0/2.0% x entry bar 1 (09:15 close) or 6
(first 30 minutes), hold to 15:15, Rs 50,000, Discovery, the 29 names. 4 arms each, 8 counted looks.

**Result: both rejected, all arms feasible** (median |move| 1.15-1.49%, so results days are large
by construction). Fade: entering at the first bar is worth nothing (gross -0.03..+0.02%), entering
after 30 minutes gross +0.14% (1%) and +0.19% (2%), net -0.04..-0.09%, t under 1; the 2% arm is the
nearest miss (break-even at Rs 50,000 is 0.23%) with only 206 trades. Continuation: gross negative
in every arm (-0.05..-0.20%), worse after waiting, t down to -3.8: it is the mirror of the fade.
Coverage: 14-79 reaction sessions a year (2022 has 49, the ledger's known hole), 297 results were
published in session and skipped, 0 reaction sessions missing from the bars.
**Child** (§7.4, gross positive net negative and too few trades: widen the universe before loosening
anything): L2-earnings-gap-fade-after-first-hour on D1, waiting for the first hour as EM-203 found
waiting pays. It follows two independent observations (waiting lifts the fade; results days are
large), not a grid point. Continuation: no child.

## Iteration 17 (2026-09-24): D5 results events done (EM-209)
The first collection used one subject and left holes: the exchange tags results as "Financial Result
Updates" (the whole record before mid-2022, sparse after) and, since, mostly as "Outcome of Board
Meeting". The collector now takes both (board outcomes kept only when their text is about results),
and the ledger records the subject. Result: 8,573 filings, 8,070 first-public events, median 37 per
name (about 40 quarters exist), 2016-10..2026-08. **Known gaps** (in `events/README.md`): 2022 has 541
events against about 860 in a normal year, 5% of gaps between a name's events exceed 150 days, and
ABBOTINDIA and MCX have none. A missing event is a missed trade, not a leaked one, but lanes must
report events per year used. Only results are collected; ex-dates, bulk deals, rebalances and the F&O
ban list are D5b (EM-212). The 4 L2/L14 cells that need only results are TODO; the 4 that need D5b
require it.

## Iteration 16 (2026-09-24): L4-india-vix-regime (EM-210), and D5 collector authorised
**D5.** The operator, in this session, ruled that NSE's corporate-announcements endpoint may be used.
Built (66d8704): `research.results_filings` (the exchange's IST timestamp on every filing, first-public
per results season, append-only resumable ledger in `docs/research/edge-search/events/`) and
`research.nse_announcements` (one request per name for the whole window, 3 s apart, honest user
agent, no retry, a 401/403/429 stops the run). `emporos research collect-results --to 2026-09-18` is
running for the 250 D1 names (about 14 minutes; 32 filings per name back to October 2016). Validation
and unblocking the 8 cells is next.

**Cell.** Declared first (25564df), Rs 50,000: the first-hour gap reversal (the nearest miss) only on
days when the INDIA VIX 09:15 open is at or above the 0.6 or 0.8 quantile of its own previous 252
opens; 6 arms. Machinery: `scans/regime_gate.py` (`TrailingPercentileDays`, `DayGatedRules`, a
decorator any scan can take) and `scans/vix_shock_reversal.py`; the recipe loads the VIX series
through a new `prepare` step. 19 tests.

**Result: feasible, rejected.** The gate does select large days (median |move| 1.02-1.52%, above the
0.99% bar), so every arm passes S1, but gross is 0.16-0.28%, not better than the ungated 0.19-0.35%;
net -0.07..+0.05%, t under 0.5. The stricter 0.8 gate is worse than 0.6. So a large day is necessary
but not sufficient: the fade has no extra edge there. No child.

## Iteration 15 (2026-09-24): D5 blocked on the operator (EM-209)
Probed the screener MCP for the earnings calendar: `get_company_announcements` returned nothing for
RELIANCE, `get_quarterly_results` gives only the last 8 quarter-end figures (no announcement date, no
time), `get_document_list` gives annual reports. A quarter-end is not when a result became public, and
choosing the reaction day from price or volume would select it by its outcome, so neither is used.
The plan allows scraping only within a site's terms, and whether NSE's or BSE's announcement endpoints
qualify is the operator's call. Filed EM-209 with the four options; D5 and its 8 cells (7 L2 cells,
L14-results-season) are `BLOCKED(EM-209)`. Other lanes continue (§7.6). Noted that the operator session
approved EM-206 and EM-207 while this loop ran.

## Iteration 14 (2026-09-24): D1 wider universe started (EM-208)
Lists: `config/universe/d1/nifty100.csv` and `niftymidcap150.csv`, one plain GET each from NSE Indices'
published constituent files on 2026-09-24, with `SOURCE.yaml` recording the URLs and the caveat: they
are the CURRENT constituents (survivorship bias when projected back to 2016; every D1 cell must use
the EM-177 as-of rules or declare it). The Industry column is a first D3 source. 250 names, no overlap.
`emporos.research.universe_lists` reads them (10 tests); `emporos history fetch-universe` resolves
them against the instrument master (unknown ones are reported and skipped), then fetches in batches of
5 through the same resumable fetcher as `fetch-bars`. Bars older than the hot retention go straight to
the cold Parquet tier, so Atlas holds only the last two weeks: no broad Atlas load. Started with
`--from 2016-10-03 --to 2026-09-18 --batch-size 5`; batch 1 took about 6 minutes for 533 chunks, 0
failed, so the run is about 5-6 hours. Re-running the same command resumes (chunks already recorded are
skipped). Liquidity is decided AFTER the fetch from Discovery-period bars, not before, because a
liquidity pre-filter needs daily traded value which is not on hand.

## Iteration 13 (2026-09-24): L4-nr7-inside-day-breakout (EM-205)
Declared and committed first (dae511a): the previous session was NR4, NR7 or inside; the first bar
closing 5 or 25 bps beyond its high or low is the entry; hold to 15:15. 6 arms, Discovery only. Scan
`scans/range_compression.py` (17 tests): sessions are built from the 5m bars, one with fewer than 70
bars is a hole and breaks the run.

**Result: nothing there.** Every arm INFEASIBLE, median |move| 0.65-0.70%, gross 0.02-0.05% over
5,800-12,900 trades (net -0.28..-0.31%, t -14..-25). Compression in yesterday's range does not
predict a bigger move today on these names, and the 25 bps buffer is no different from 5. No child:
gross is barely positive and no mechanism points the other way. This cell was run before the
steering note arrived (it was already declared); the next iterations follow the note.

## Iteration 12 (2026-09-24): L3-first-hour-reversal-large-gap (EM-204)
Child of the shock-reversal cell, declared and committed first (02c4c46): the same scan at gaps of 2.5
and 3% with retrace 0.25 or 0.5, 4 arms, Discovery only. The recipe class now takes its slug, so one
scan serves both declarations.

**Result: feasible, still rejected.** Median |move| 1.39-1.70% clears the S1 bar for the first time
in this program. At retrace 0.25 gross is 0.43% (2.5%, 504 trades) and 0.39% (3%, 301 trades): above
the 0.324% benchmark cost, below the 0.636% adverse break-even. Net +0.10% (t 0.77) and +0.06% (t
0.33). A 0.5 retrace floor halves gross and turns net negative. The trade count falls to the 300
floor as the gap grows, so the size-bucket lever is spent: what is left is a wider universe (D1),
which the declaration named as the only permitted fix. The declaration said no further threshold is
tried; the fade-after-a-gap family is closed on Discovery. Advisory scan.

## Iteration 11 (2026-09-24): L3-first-hour-shock-reversal (EM-203)
Declared and committed first (1583988): a raw gap of at least 1/1.5/2%, then at the close of the 10:10
bar (end of the first hour) the retrace (open minus close over open minus previous close) must be at
least 0.25 or 0.5; if so, trade the retrace direction from that close to the 15:15 square-off, no stop.
6 arms, Rs 25,000, Discovery only. Scan `scans/shock_reversal.py`, 17 tests, recipe in `CELLS`.

**Result: every arm INFEASIBLE** (median |move| 0.85-1.23%, bar about 1.27%). But this is the nearest
miss so far: waiting for the retrace lifts the fade's gross from under 0.1% (EM-201's blind 09:15 entry)
to 0.17-0.35%, and gross rises with gap size (0.188, 0.257, 0.351% at 1/1.5/2% with retrace 0.25).
The 2% gap at retrace 0.25 is the only net-positive arm (+0.023%, t 0.24, 809 trades: noise). A looser
retrace floor did better than a stricter one. Child per §7.4 (gross positive, net negative: restrict to
the days that can pay): L3-first-hour-reversal-large-gap, gaps of 2.5% and up. Not a fit-to-the-grid
child: it follows the monotone trend in gap size that three thresholds showed, and its trade count is
the risk (the 2% arm has 809, so 3% may have only a few hundred).

## Iteration 10 (2026-09-24): L3-orb-high-rvol-wide-range (EM-202)
Declared and committed first (`config/experiments/l3-orb-high-rvol-wide-range.yaml`, 863e627): opening
range = first 6 bars; breakout = first close 5 bps beyond it; taken only if the range's volume is at
least `rvol_min` x the mean of the previous 20 sessions' (10 needed) and the range is at least
`or_width_min_bps` wide; no stop or target; exit at the 15:15 square-off or a 12:30 time stop. 12 arms
(rvol 1.5/2/3 x width 50/100 x exit close/1230), Rs 25,000, Discovery only. Scan `scans/orb_rvol.py`
(rules pinned in 17 tests in `test_orb_rvol.py`), recipe added to `screen_commands.CELLS`.

**Result: every arm INFEASIBLE** (median |move| 0.38-0.80%, bar about 1.27%), so also failing S2 on
its face: net -0.13..-0.30%, net t -2.7..-22.6. The one structure worth a child: hold-to-close gross
rises monotonically with the volume multiple (0.068, 0.103, 0.185% at width 50; 0.073, 0.113, 0.200% at
100), still under the 0.324% cost, and the 12:30 time stop roughly halves it (the move accrues late,
so holding longer, not shorter). The width floor adds little. Child per §7.4 (gross positive, net
negative: restrict to a stricter filter): L3-orb-extreme-rvol, declared next time. The trade count at
3x is about 1,400, so 4x-7x still has hundreds. The scan is advisory (no engine strategy to prove parity
against). The screen takes about 9 minutes for 12 arms.

## Iteration 9 (2026-09-24): L3-raw-gap-hold-to-close (EM-201) and D2 closed
**The cell (first lane run).** Declared and committed first (`config/experiments/l3-raw-gap-hold-to-close.yaml`,
d30993a): 16 arms (gap threshold 1/1.5/2/3%, continuation or fade, entry at bar 1 or 3), one trade per
name per day held to the 15:15 square-off, Rs 25,000, Discovery only (2016-10-03..2024-12-31), the 29
names with ten years of bars, the 7 audit-quarantined corporate-action days excluded. Machinery
(committed before the run, 64a8990): S1 feasibility is the evaluator's first check (median absolute
move >= 2x mean adverse round-trip cost, the plan's §3.5), `ScreenVerdict` (INFEASIBLE / SCREEN_REJECT
/ PASS), the raw-gap scan, `CellScreenRun` (instrument-outer so memory stays bounded) and
`emporos research screen <slug>`; every arm is one line in `screens.jsonl`.

**Result: every arm fails.** Continuation is gross-negative in 7 of 8 arms (down to -0.42%). Fade is
gross-positive in 6 of 8 but small (+0.07..0.10% at 1-2% gaps; it does not grow with gap size and is
-0.05% at 3%), against a benchmark cost of 0.324% and an adverse 0.636%: net -0.23..-0.26% for the
1-2% fades, net t between -2.3 and -16.9 across all arms. Six arms are INFEASIBLE (median |move| below
the ~1.27% bar: the four 1% arms and the two 1.5% bar-3 arms); the other ten are SCREEN_REJECT. **The scan is advisory** (no engine strategy
to prove parity against), so this is a triage rejection; its rules are pinned by 21 tests. Lesson in
the map. Child per §7.4 (gross positive, net negative: restrict to days that can pay): the cell
L4-gap-fade-expected-range. Continuation has no mechanism worth a child (its opposite is the fade arms).

**D2 is verified and closed.** 14 of 15 series carry the full 2,450 sessions (2016-10-03..2026-09-10 in
cold, the last days in the hot tier), 75 bars a day, volume 0; INDIA VIX has 2 fewer days. **Nifty Pvt
Bank starts 2023-06-16** (the broker's history for that index begins there), so a cell needing it before
then cannot have it. 51 malformed bars were skipped and reported by the fetch, 0 chunks failed. The
cache now holds all 15 (2.63M bars). Only 29 equities have ten years of 5m bars: **RELIANCE and TCS have
one year** (why the committed fixture is a year), so they are outside the Discovery universe.

## Iteration 8 (2026-09-24): D2 index and INDIA VIX series (EM-200)
Indices are not instruments: the master drops `AMXIDX` rows on purpose. `emporos.domain.reference_series`
adds `ReferenceSeries` (token, symbol, name, kind) as a separate, non-tradable thing; its candles
are stored under `NSE:<token>` through the ordinary `CandleRepository`, and only a private
`fetch_handle()` gives the bar fetcher an `Instrument` to ask the broker with. A test proves every
declared index row is rejected by `CashSegmentFilter`, so none can reach anything that sizes or
orders. `config/reference_series.yaml` declares 15 series (NIFTY 50, BANK, FIN SERVICE, INDIA VIX,
and 11 sector indices), checked against a recorded copy of the scrip-master rows
(`tests/fixtures/reference_series_master_rows.json`) and against the live master before every
fetch: a moved or renamed token stops the run before any request. New: `emporos history
fetch-reference` and `emporos backtest cache warm-reference`. Probe results (pre-vault months): 75
five-minute bars a day (09:15..15:25), volume always 0 (indices carry none), sensible levels, and the
broker serves back to at least 2016-10. Volume-based features cannot use these series.

The full fetch finished at about 18:44 (see Iteration 9 for what it produced).

## Iteration 7 (2026-09-24): the vault seal (EM-199)
`emporos.backtest.vault`: `VaultSeal` (days, instruments, at most 3 opens), `UnsealRecord` (open
number, the seal it was made against, the candidate hash, why, the range and instruments it admits),
`VaultGate` and `VaultedCandleReader`. The reader refuses any read that touches the sealed days,
before the source is asked, unless a committed unseal record for the named candidate admits the
sealed part of the read (warm-up bars from before the vault need no admitting). The gate will not
build with a fourth record, a gap or repeat in the numbering, a record against another seal hash, or
one reaching outside the vault: the budget cannot be exceeded by adding a file. State is
`docs/research/edge-search/vault.yaml` plus `vault-opens/open-<n>.yaml`; an open counts only if git
tracks it with no pending change, and a missing seal fails closed. The shipped seal is pinned in a
test, hash included. Wired into `open_backtest_runtime` (so backtest, curate, Jev and parity read
through it) and into the curation worker processes (`CurationRecipe.vault`). `emporos research vault
status` prints the seal and the opens left. Curate's `--to` past 2026-03-18 is now refused: the
confirmation split ends there.

The S5 command that names a candidate (`open_backtest_runtime` has no candidate argument yet) is part
of the S5 stage, to build when the first candidate reaches it. Vault opens are never made by a
command: an open is a committed record.

## Iteration 6 (2026-09-24): F4 size-aware evaluation (EM-198)
`emporos.domain.sizing`: `DeclaredSize` (rupees per position, and whether it was declared or the risk
limit's default) and `SizeResolver`. Precedence: the declaration's `position_value`, else
`--position-value`, else `config/risk.yaml` `max_position_value` (25,000). A command-line value that
contradicts the declaration is refused. `ExperimentDeclaration.position_value` is optional and enters
the experiment id only when stated, so older declarations keep their ids and older reports re-render
byte for byte (the report golden is untouched). `backtest curate` now judges at the resolved size
(the old default was the benchmark's 10% share, Rs 5,000): `BenchmarkScaler` takes it for the
strategy and the risk gate alike, the recorded verdict's note names it, and every published report
carries `supporting.sizing` plus a note, flagging a size above the risk limit as needing an operator
decision (§8). The feature, lead-lag and cross-sectional engines take a required `size` (the
feature engine used a fixed share count; cross-sectional split a capital across legs, so its cost
moved with the tail size). Cross-sectional legs each trade at the size and a book that does not fit
`capital` is refused, not shrunk. `test_size_is_required.py` pins that `size` has no default.

## Iteration 5 (2026-09-24): F3B signal parity (EM-196)
`emporos.research.scans`: `ScanRules` (a strategy's entry/exit rules as a state machine over bars) and
`IntradayScan` (the order path they share, mirroring the backtest: a signal at a bar's close is a
marketable limit that first trades on the next bar, only strictly through the limit and only if
10% of the bar's volume covers it; 15:15 square-off; the broker's forced close). Scans for
orb_v1, vwap_reversion_v1 and rsi_pullback_v1 reuse the strategies' own indicators, so only the
decisions are re-implemented. `test_scan_parity.py` runs the real `BacktestJob` and each scan over
the committed year of RELIANCE/TCS 5m bars: **the trade sets are identical** (323 / 833 / 1,109
round trips) and screener net expectancy is within 1.4-1.7% of the engine's (bar: 10%), benchmark
and adverse scenarios alike. A negative control (orb with shorting off) fails the same comparison.
`PARITY_PROVEN` lists the proven scans and the parity test iterates it; `ScanScreener` makes a
screen non-advisory only for a scan in it, so a new hypothesis's scan is advisory until it has its
own parity case. Speed: about 0.3-0.7 s per strategy-year for two names, Decimal not numpy.

## Iteration 4 (2026-09-24): F3 core (EM-195)
`emporos.research` gained the screener: `ScreenTrade`/`DeclaredValueSizer` (whole shares at the
declared size), `ScreenCostScenario`/`ScreenCostModel` (the benchmark.yaml scenarios, statutory
charges from `TransactionCostModel`, per-side slippage), `ScreenEvaluator` with the plan's fixed
S2 `ScreenBar` (pinned by a test), and `Screener`, which appends every screen to the append-only
`screens.jsonl` before returning; `ProgramTrialCount` counts that file, so N grows with each screen.
The same screen twice is one look. A test checks evaluator parity with `backtest curate`'s own
re-pricing (expectancy within 10%). **Split (§7):** signal-level parity needs vectorised
re-implementations of three strategies, so it is F3B (EM-196). Until it passes, `Screener` is
`advisory` and no cell verdict may rest on a screen. Not vectorised with numpy: it is not a
dependency, and the pure-Decimal path is exact; revisit if a screen is too slow.

## Iteration 3 (2026-09-24): F2B
The Mongo feature, cross-sectional and lead-lag ledgers are empty because EM-178..EM-181 ran with
in-memory ledgers. Rather than insert synthetic trials with invented metrics into shared Atlas, the
documented proof runs are a committed manifest (`historical-trials.yaml`, append-only, each row
tied to the report line that states its trial count, guarded by a test). `HistoricalGridCounter`
counts each family as one more source in `ProgramTrialCount`; the factory refuses to build N if the
manifest is missing. None of the 23 registry rows are these families, so nothing is double counted.
A future re-run of a study is new looks and lands in its own ledger, counted separately.
Effect: N 740 -> 14,028, so the DSR hurdle at S4/S5 is now priced at the program's real breadth.

## Known weaknesses carried forward
- The seal covers the analysis seams (backtest, curate, Jev, parity and the curation workers). Data
  plumbing that produces no result still reads bars: `cache warm` and prefetching, `quality_commands`,
  and history tooling. They are not a route to a verdict, but the seal is a structural guard and not
  a proof against a determined caller: the operator's git history is the audit.
- Sizing gaps left for their lanes: `backtest/jev_sweep.py` still judges Jev arms at the benchmark
  share (L16 must thread `DeclaredSize`); `BenchmarkScaler` keeps `max_capital_deployed` at the
  benchmark capital, so L1 sizes above Rs 50,000 also need a leverage-aware capital before they run;
  the lead-lag engine still books an unaffordable or unpriced observation at gross (net == gross),
  which flatters it, so a lead-lag cell must declare a size its names can afford.
- Scan parity is proven on two liquid names, one year, no risk gate, one instrument at a time. Not
  modelled: cross-instrument risk limits (3 open positions, capital deployed, daily loss), partial
  fills on thin volume (the scan skips an order a bar cannot take whole), re-signals while an order
  rests. A screen is triage; a verdict comes from `curate`. Widen the parity case once D1 lands.
- Program-wide N is still a lower bound: it counts only grids a report states. Screener evaluations
  (F3) will add to it. N is summed across sources that may overlap (report rows and ledger trials of
  one strategy run), which can only raise it.
- `backtest/jev_sweep.py` still prices Jev arms at the strategy ledger's count (pinned in
  `test_program_trials.py`). L16 must move it onto `ProgramTrialCount` before a Jev result feeds S4/S5.
- Until D1 lands, the vault is time-only over the 29 current names (§4.3). Forward paper results
  get extra weight.
- Fee schedule is `verified: true` from 2026-09-24 (EM-197; reconciled by the operator, line-level diff not seen by the agent). Reports published before then still carry `verified: false` and are immutable. The IPFT levy is still not modelled, so costs are marginally understated.
