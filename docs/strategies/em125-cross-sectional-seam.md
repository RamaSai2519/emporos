# EM-125: the cross-sectional seam in `relative_strength_v1`

Every other built-in strategy decides using only the instrument its current bar belongs to. Ranking
needs the opposite: at a rebalance, a verdict about instrument A that depends on instrument B's
price too. This note is the design decision EM-125 asked to be written down, made before the
strategy was backtested.

## What the seam is

`relative_strength_v1` ranks its whole configured universe by intraday momentum and trades the
extremes. On every rebalance it must read one bar (or `lookback` bars) for *every* instrument in
the universe from a single strategy instance — a cross-sectional read, not the usual
per-instrument decision.

## The seam did not need a new context method

`ctx.history.bars(instrument_id, timeframe, limit)` (`src/emporos/strategies/history.py`) was
already general over ANY instrument: it has always taken the instrument id as a parameter, and
`ClosedBarHistory` keys its series by `(instrument_id, timeframe)` and gates each bar on
`closes_at <= now` against the replay clock. `ResolvedStrategyConfig.universe` / `instrument_ids`
(`src/emporos/strategies/config.py`) already lets one strategy own many instruments — `momentum_v1`
proved this pattern since EM-69 (one indicator track per instrument). So a cross-sectional read is
just calling `ctx.history.bars` once per id in `config.instrument_ids`, from the same strategy
instance the runner already built. Nothing in `StrategyContext`, `BarHistory` or the run loop
changed for this strategy.

## The timing rule that keeps a cross-sectional read safe

A single-instrument strategy can act on its own latest bar. A cross-sectional ranking may not: the
first symbol to arrive must not be ranked against stale siblings. Two rules hold this together:

1. **Same-close barrier.** A rebalance waits until every configured instrument has supplied its
   closed bar for the interval (`_universe_is_current_through`). This is a *bounded* wait, not an
   unbounded one: `ctx.history.bars` never reveals an open or future bar, and the live candle
   aggregator emits a flat, zero-volume bar (OHLC = previous close) for a silent instrument
   (`src/emporos/marketdata/aggregator.py`), so an illiquid name cannot stall the ranking forever.
2. **Once per interval.** `_last_rebalanced_at` ensures exactly one rebalance per `closes_at`, even
   though `on_market_data` is called once per instrument per bar.

This makes the rank deterministic across the merge-sorted backtest feed (bars interleaved in close
order by `ClosedBarFeed`) and independently-derived live/paper bars.

## What the seam deliberately does not do

- It does not add a method to `StrategyContext` or `BarHistory`; the interface any other strategy
  sees is unchanged, so nothing about layering or the strategy protocol was extended.
- It does not make the strategy itself responsible for the timing of arrivals beyond the barrier:
  gaps and halts surface as flat aggregator bars rather than missing rows, so the same code path is
  exercised in backtest and live.
- It does not re-rank on every bar of every instrument: ranking happens only on the configured
  `rebalance_every_bars` cadence of the instrument whose bar triggered the rebalance.

## Honest limits

The barrier waits for the last instrument's bar, so a rebalance is keyed to the slowest name in the
universe. The universe is a fixed static list of large NSE symbols (the same 29 the other
curations use), so in practice the slowest bar is still milliseconds-fresh; this is documented, not
benchmarked, and the effect on fills is covered by the exchange's auction pattern already used in
the other backtests.