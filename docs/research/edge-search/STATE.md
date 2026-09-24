# Edge search — resume point (EM-191)

Plan of record: [`EDGE_SEARCH_PLAN.md`](../../../EDGE_SEARCH_PLAN.md). Map:
[`search-map.yaml`](search-map.yaml) (validated in CI by `emporos.research.search_map`).

| Field | Value |
|---|---|
| Iteration | 3 (2026-09-24) |
| Last completed | F2B: documented EM-178..EM-181 grids counted into N (EM-194); F2 (EM-193); F1 (EM-192) |
| Next | **F3** fast screener with the curate-parity test (needs a Jira subtask first). Then F4, vault seal; D7 quote recording and D1 in the background |
| Global N | **14,028** on 2026-09-24 (`emporos backtest trials program`): 717 strategy trials, 23 registry rows, 13,288 documented study trials (feature 11,020, cross-sectional 252, lead-lag 2,016) from `historical-trials.yaml`. Still a **lower bound**: only runs a report states are counted |
| Vault opens used | 0 of 3 (vault not yet sealed) |
| Cells | 42 TODO, 0 terminal; no lane may run before F3, F4 and VAULT are DONE |
| Blocked on operator | D8 fee reconciliation -> BLOCKED(EM-190) |
| Paper (S6) running | no |
| Last commit | see `git log -1` (EM-194) |

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
- Program-wide N is still a lower bound: it counts only grids a report states. Screener evaluations
  (F3) will add to it. N is summed across sources that may overlap (report rows and ledger trials of
  one strategy run), which can only raise it.
- `backtest/jev_sweep.py` still prices Jev arms at the strategy ledger's count (pinned in
  `test_program_trials.py`). L16 must move it onto `ProgramTrialCount` before a Jev result feeds S4/S5.
- Until D1 lands, the vault is time-only over the 29 current names (§4.3). Forward paper results
  get extra weight.
- Fee schedule not reconciled against a contract note: every report carries `verified: false`.
