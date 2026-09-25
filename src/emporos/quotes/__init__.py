"""Forward L1 quote recording (EDGE_SEARCH_PLAN D7, EM-217).

Read-only by construction: this package sees a `QuoteSource` (one method, `get_quote`) and a
`QuoteSink`, and imports no order, execution, risk, strategy or concrete-broker code. It records
the best bid and ask, their sizes and the last price for a fixed set of instruments during market
hours into Parquet files, so the microstructure lanes (L13, the quote-conditioned part of L9) and
the cost model have real spreads to work with in a few months."""
