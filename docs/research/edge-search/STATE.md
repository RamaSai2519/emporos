# Edge search — resume point (EM-191)

Plan of record: [`EDGE_SEARCH_PLAN.md`](../../../EDGE_SEARCH_PLAN.md). Map:
[`search-map.yaml`](search-map.yaml) (validated in CI by `emporos.research.search_map`).

| Field | Value |
|---|---|
| Iteration | 1 (2026-09-24) |
| Last completed | F1 — search map, STATE.md, typed loader/validator (EM-192) |
| Next | **F2** — global trial counter (program-wide N into the S4/S5 DSR) |
| Then | F3 screener, F4 size-aware evaluation, vault seal, D7 quote recording (start ASAP), D1 (background), D2 |
| Global N | not yet counted — F2 builds the program-wide query (EM-114..EM-187 trials included) |
| Vault opens used | 0 of 3 (vault not yet sealed) |
| Cells | 43 TODO, 0 terminal; no lane may run before F2, F3, F4 and VAULT are DONE |
| Blocked on operator | D8 fee reconciliation → BLOCKED(EM-190) |
| Paper (S6) running | no |
| Last commit | see `git log --grep EM-192` |

## Known weaknesses carried forward
- Until D1 lands, the vault is time-only over the 29 current names (§4.3). Forward paper results
  get extra weight.
- Fee schedule not reconciled against a contract note: every report carries `verified: false`.
