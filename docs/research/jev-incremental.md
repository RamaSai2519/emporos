# Jev as an incremental signal, not an alpha dependency (EM-187)

Jev is the optional LLM decision-provider (EM-152). The question this work answers is narrow and
falsifiable: **given a portfolio that already works, does asking Jev to confirm or re-rank its
entries add anything measurable, after Jev's own cost, without a deeper drawdown?** It is never
asked to *be* the edge.

## The expected outcome today: "Jev is not eligible to lift anything"

No strategy currently has a VALIDATED baseline: every verdict in `docs/strategies/` is REJECTED.
The graduation rule is that a strategy is not validated merely because Jev improves a weak or
negative baseline, so any run today ends **INCONCLUSIVE (`jev_baseline_not_validated`)**, or
REJECTED if the Jev arm is itself rejected. What this ticket ships is therefore the harness and its
guardrails, not a result.

**No real run has been made, on purpose.** The record run needs `VERCEL_GATEWAY_KEY` and spends
money, and with no validated baseline it could only confirm the sentence above. The journal is
empty, so a `replay` run would fail with "not in the journal" (the sweep refuses to report a run in
which the model was never asked, rather than measure the outage). No `ExperimentReport` is
published and no number appears in this document; nothing was estimated or invented. When a
baseline validates, run the record pass once (below), commit nothing until the holdout has been
looked at exactly once, and publish.

## What is guaranteed by construction

| Concern | Mechanism |
| --- | --- |
| Identical arms | `JevOnOffBacktestExperiment.from_factory` builds both arms from one `EngineFactory` whose `build(jev_filter)` takes nothing else; an `ExperimentFingerprint` (spec, data provenance hashes, fee schedule, holdout) is verified against every arm's result and a mismatch raises. |
| Look-ahead through the model's memory | `KnowledgeCutoffGuard`: every window, and every single request (`CutoffEnforcingJevProvider`), must be strictly after the model's declared cutoff plus a margin (90 days by default). The cutoff is **never assumed**: it is a required, committed field of the experiment declaration together with the source it was verified against, and a missing value is a refusal. |
| Look-ahead through the request | `JevRequest.as_of` is required and never sent to the model (`payload()` omits it); every other field is derived from the candidate at that instant. `SymbolAnonymiser` replaces the symbol with a salted pseudonym (`INSTR_<hash>`) and strips identifying keys; a defence-in-depth check refuses a request in which the real name still appears. |
| Reproducibility and cost | Record once, replay forever: `RecordMissesJevProvider` answers from the `jev_decisions` journal and only asks the model for questions it has never seen; `ReplayJevProvider` never touches a network. The journal key is `(request_hash, prompt_hash, model)`, so a changed prompt or model is a new question. One recording serves RANKING and CONFIRMATION and every threshold. |
| Hard spend cap | `--max-requests` (default 500) is enforced by `CallBudgetJevProvider` around the live client only; the run stops with an error when it is reached. Record mode prints its worst-case token and rupee ceiling first and asks for confirmation. |
| Provenance | `JevPrompt` (version + SHA-256 of the text); every `JevDecision` records `prompt_version`, `prompt_hash`, `request_hash`, model and tokens. The report records model, cutoff, source, prompt, provider mode, anonymisation and the fingerprint. |
| Multiple testing | Every (mode, threshold) arm is appended to the trial ledger (`experiment="jev-<slug>"`, `candidate="jev:<mode>:<threshold>:<prompt version>"`) **before** any arm is analysed, so each arm's Deflated Sharpe counts the whole search. `STRATEGY_SELECTION` is the same ask as `RANKING`, so it is one arm. Trial ids are deterministic, so replaying an experiment adds no trials. |
| No rescue of a weak baseline | `JevIncrementalPolicy` (`backtest/robustness/jev_gate.py`): if the baseline is not VALIDATED the Jev arm is at most INCONCLUSIVE. If it is, the arm needs its own full verdict VALIDATED, a positive expectancy gain **net of Jev's cost**, a paired day-level bootstrap interval entirely above zero, and no deeper maximum drawdown. |

## What is measured

Per arm, and as treatment minus baseline: net P&L, trade count, expectancy per trade, annualised
Sharpe, Deflated Sharpe, maximum drawdown, turnover and charges. Jev's cost in rupees is
`tokens / 1000 x inr_per_1k_tokens` (a declared rate; an unpriced Jev is refused, never free). The
*net-of-Jev* gain subtracts that cost; for the paired bootstrap it is spread evenly over the
treatment's days. The paired interval resamples **days** (the arms share their days) with the seeded
resampler of the trade-level Monte Carlo. Per-regime deltas are reported so Jev cannot "help" only
by trading one regime.

## Running it

```
pipenv run emporos db migrate                        # creates jev_decisions and its unique index
# 1. once, when a baseline validates: record the model's answers (spends money, capped)
pipenv run emporos backtest jev-compare --declaration config/experiments/jev_incremental_v1.yaml \
    --from 2024-03-01 --to 2026-08-31 --provider record --max-requests 500
# 2. any number of times, free: replay reproduces the same report
pipenv run emporos backtest jev-compare --declaration config/experiments/jev_incremental_v1.yaml \
    --from 2024-03-01 --to 2026-08-31 --provider replay
```

The declaration (`config/experiments/jev_incremental_v1.yaml`) fixes, before any run, the model
(`openai/gpt-4o-mini`), its knowledge cutoff (`2023-10-01`) with the source it was read from (the
provider's model page, checked 2026-09-24), the token price (a deliberately high round-up; check it
against the gateway invoice), the prompt version and hash, and the modes and thresholds the run may
try. `--mode` and `--threshold` may only *select* from that grid; anything else is a new experiment.
The plan (`config/jev/jev_incremental_v1.plan.yaml`) names the portfolio and reserves a holdout at
the end of the range that the experiment never reads.

The cutoff plus margin means nothing before 2023-12-31 may be used; the leakage-safe span is
whatever the stored history offers after that. If it is too short for the verdict's
`min_history_days`, the honest outcome is INCONCLUSIVE; the guard is not relaxed to get more.

## Known limits, stated plainly

* The Jev arm is not judged by the full walk-forward verdict policy: a single fill-level run has no
  windows to walk. It is handed to the policy as *unjudged*, so **no Jev report can currently be
  ACCEPTED**. That is the safe direction; closing it needs the treatment run through
  `RobustnessAssessor`, which is future work.
* Anonymisation removes what Jev could recall about a named stock, and with it sector knowledge.
  Price levels remain in the request (entry, stop, target) and can hint at an instrument; the
  cutoff guard, not the pseudonym, is what excludes memorised history.
* `JevConfig.inr_per_1k_tokens` is a single blended rate; input and output tokens are not priced
  separately.
* A run with a reserved holdout still selects the headline arm by outcome (largest net-of-Jev gain
  per trade); the holdout is what must confirm it, once.
* EM-189: a Jev-enabled config must not be promotable unless the latest `jev_incremental`
  experiment for its behaviour hash is ACCEPTED (`JevDependencyAllowed`). EM-189 has not landed, so
  that requirement is left as a note on the EM-189 ticket, not code.
