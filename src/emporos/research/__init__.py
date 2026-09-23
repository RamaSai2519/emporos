"""Feature research and alpha discovery (EM-178): a leakage-safe framework for evaluating whether
a computed feature carries forward-looking edge, before any of it becomes a strategy.

An orchestration layer, like `emporos.opportunity` and `emporos.jev`: it composes `domain`,
`core`, `persistence` (through `CandleRepository`), `backtest` (universe, provenance, the trial
ledger pattern) and `strategies` (the live regime classifier), but nothing below it ever imports
it back (see `pyproject.toml`'s import-linter contracts).
"""

from __future__ import annotations
