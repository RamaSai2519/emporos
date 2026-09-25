"""The fixed list of stock-option underlyings to record, refreshed monthly (EM-246).

`config/universe/quotes/option-underlyings.yaml`: each name's trading symbol and the id whose quote
is its spot. The index rules are code (NIFTY and BANKNIFTY, the nearest two expiries, five strikes
either side); the stock rule is the nearest expiry and two strikes either side."""

from __future__ import annotations

from pathlib import Path

import yaml

from emporos.quotes.strikes import UnderlyingRule

__all__ = ["DEFAULT_UNDERLYINGS", "INDEX_RULES", "index_and_stock_rules"]

DEFAULT_UNDERLYINGS = Path("config/universe/quotes/option-underlyings.yaml")
INDEX_RULES = (
    UnderlyingRule("NIFTY", "NSE:99926000", expiries=2, each_side=5),
    UnderlyingRule("BANKNIFTY", "NSE:99926009", expiries=2, each_side=5),
)


def index_and_stock_rules(path: Path = DEFAULT_UNDERLYINGS) -> tuple[UnderlyingRule, ...]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    stocks = tuple(
        UnderlyingRule(str(row["symbol"]), str(row["spot_id"]), expiries=1, each_side=2)
        for row in document["underlyings"]
    )
    return (*INDEX_RULES, *stocks)
