"""Track L: an LLM-led event trader behind a code-enforced risk engine (EM-240, PROFIT_PLAN §12).

The models choose WHAT to trade and in which direction; code decides HOW MUCH, enforces every limit
and can always refuse. Nothing here reaches a broker: orders belong to the worker, and this package
only produces decisions, sized by the risk engine, and replays them against bars."""
