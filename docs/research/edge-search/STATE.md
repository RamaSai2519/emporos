# Edge search — resume point (EM-191)

Plan of record: [`EDGE_SEARCH_PLAN.md`](../../../EDGE_SEARCH_PLAN.md). Map:
[`search-map.yaml`](search-map.yaml) (validated in CI by `emporos.research.search_map`).

| Field | Value |
|---|---|
| Iteration | 2 (2026-09-24) |
| Last completed | F2 — program-wide trial counter (EM-193); F1 map/validator (EM-192) |
| Next | **F2B** — backfill EM-178..EM-181 research grids into their ledgers (EM-194) |
| Then | F3 screener, F4 size-aware evaluation, vault seal, D7 quote recording (start ASAP), D1 (background), D2 |
| Global N | **740** on 2026-09-24 (`emporos backtest trials program`): 717 strategy trials, 23 registry rows, 0 feature / cross-sectional / lead-lag. A **lower bound** until F2B |
| Vault opens used | 0 of 3 (vault not yet sealed) |
| Cells | 42 TODO, 0 terminal; no lane may run before F2B, F3, F4 and VAULT are DONE |
| Blocked on operator | D8 fee reconciliation → BLOCKED(EM-190) |
| Paper (S6) running | no |
| Last commit | see `git log --grep EM-192` |

## Blocked on the operator (2026-09-24, end of iteration 2)
The Atlassian MCP needs re-authentication (`/mcp`). The loop is stopped until it is back. Pending
Jira actions to apply first on resume:
- EM-193 → Done (F2 committed in edb0dd8).
- Comment on EM-191: F2 done; N = 740 is a lower bound; EM-194 (F2B) filed; jev_sweep pinned for L16.
- Then start EM-194 (F2B): move to In Progress.

## Known weaknesses carried forward
- Program-wide N undercounts until F2B: the EM-178..EM-181 study grids never reached their Mongo
  ledgers, so each counts as one registry row. N is summed across overlapping sources (report rows
  and ledger trials of the same run), which can only raise it.
- `backtest/jev_sweep.py` still prices Jev arms at the strategy ledger's count (pinned in
  `test_program_trials.py`). L16 must move it onto `ProgramTrialCount` before a Jev result feeds S4/S5.
- Until D1 lands, the vault is time-only over the 29 current names (§4.3). Forward paper results
  get extra weight.
- Fee schedule not reconciled against a contract note: every report carries `verified: false`.
