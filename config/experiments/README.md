# Experiment declarations (EM-188)

One YAML file per research experiment, named `<slug>.yaml`, **committed before the run**. It states
what is claimed and what would prove it wrong, so a result can be judged against a claim that was
fixed first. The run publishes a report under `docs/strategies/experiments/` whose id is derived
from this file's content: `EXP-<YYYYMMDD declared>-<slug>-<8 hex of the content hash>`.

```yaml
family: strategy            # strategy | feature | cross_sectional | lead_lag | jev_incremental
slug: orb-v1-ten-year       # must equal the file name; lowercase words joined by - or _
hypothesis: what is expected to be true
economic_rationale: why it should be true (who is on the other side of the trade, and why they lose)
falsification: the result that rejects it
parameter_grid:             # every parameter the run may vary, with every value it may take
  range_bars: [3, 6]        # integers or QUOTED strings; a bare float (1.5) is refused
  target_r: ["1.5", "3.0"]
feature_versions:           # the version of each feature definition the run reads
  opening_range: 1
declared_at: 2026-09-24T09:00:00+05:30   # ISO-8601 with a UTC offset
```

Unknown keys are refused. Validate a file and see its id (no run needed):

    pipenv run emporos research experiments declare config/experiments/<slug>.yaml

Run a curation against it (one declaration describes one experiment, so pick one strategy):

    pipenv run emporos backtest curate --only <strategy> --from <day> --to <day> \
        --declaration config/experiments/<slug>.yaml

The command refuses a declaration that is not committed (`--allow-uncommitted-declaration`
forfeits that proof), publishes `<id>.json` and `<id>.md`, and rebuilds `INDEX.md`. Published
reports are immutable: re-running the same declaration to a different result is refused, and new
evidence needs a new declaration. The plan must reserve a holdout (`holdout_days` in
`config/curation/plan.yaml`); without one the report cannot be ACCEPTED.
