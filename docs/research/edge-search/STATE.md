# Edge search — resume point (EM-191)

Plan of record: [`EDGE_SEARCH_PLAN.md`](../../../EDGE_SEARCH_PLAN.md). Map:
[`search-map.yaml`](search-map.yaml) (validated in CI by `emporos.research.search_map`).

| Field | Value |
|---|---|
| Iteration | 6 (2026-09-24) |
| Last completed | F4 size-aware evaluation: declared position value everywhere (EM-198); F3B signal-level parity (EM-196); F3 core (EM-195); F2B (EM-194); F2 (EM-193); F1 (EM-192) |
| Next | **VAULT** seal (§4.3): needs a ticket, `vault.yaml`, and a `VaultGate` in `emporos.backtest.integrity` style with a look-ahead-style test. Then lanes open, except those waiting on D-frontiers; D7 quote recording and D1 in the background |
| Global N | **14,028** on 2026-09-24 (`emporos backtest trials program`): 717 strategy trials, 23 registry rows, 13,288 documented study trials (feature 11,020, cross-sectional 252, lead-lag 2,016) from `historical-trials.yaml`. Still a **lower bound**: only runs a report states are counted |
| Vault opens used | 0 of 3 (vault not yet sealed) |
| Cells | 42 TODO, 0 terminal; no lane may run before VAULT is DONE |
| Blocked on operator | none. D8 done (EM-197): the operator reconciled the fee schedule on 2026-09-24 |
| Paper (S6) running | no |
| Last commit | see `git log -1` (EM-194) |

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
