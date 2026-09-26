"""The modelled option premium against the RECORDED quotes (EM-248, declaration
`s1-size-target-trail`: "when recorded option quotes exist, the model's error against them is
measured and reported; a model error larger than the benchmark slippage voids the options arms'
verdict").

For each recorded quote (the EC2 recorder's `quotes-options` parts) the model prices the same
contract at the quote's moment from the quote's own recorded spot, with the previous session's
settle, and the difference to the quote's price (the bid-ask mid when both sides are quoted, else
the last price) is the error. The verdict is VOID when the mean absolute error is larger than the
mean benchmark slippage a fill would be charged on those premiums (one tick plus 0.5% a side).
Counts and errors only: nothing about a strategy's P&L."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq

from emporos.core.clock import IST
from emporos.options.slippage import BENCHMARK, SlippageScenario
from emporos.research.iv.black76 import Right
from emporos.research.s1.option_contracts import Contract, ExpiryCalendar, LotSizes
from emporos.research.s1.option_model import PremiumModel, PriorSession

__all__ = [
    "ModelError", "ModelErrorMeter", "RecordedQuote", "load_recorded_quotes", "pooled",
    "quote_price",
]  # fmt: skip

TICK = 0.05
_COLUMNS = ["underlying", "expiry", "strike", "right", "spot", "exchange_ts", "ltp", "bid", "ask"]


@dataclass(frozen=True)
class RecordedQuote:
    contract: Contract
    day: date
    minute: int  # minutes after midnight IST
    spot: float  # the underlying at the quote
    price: float  # the mid, or the last price


@dataclass(frozen=True)
class ModelError:
    n: int
    mean_signed: float  # model minus recorded, rupees per unit
    mean_abs: float
    mean_abs_fraction: float  # of the recorded price
    mean_slippage: float  # the benchmark slippage of one fill on the same premiums
    unpriced: int  # quotes the model could not price (no prior settle)

    @property
    def voids_verdict(self) -> bool:
        return self.mean_abs > self.mean_slippage

    def lines(self) -> list[str]:
        verdict = "VOID: the error exceeds the benchmark slippage" if self.voids_verdict else "ok"
        return [
            f"Model error against {self.n:,} recorded quotes ({self.unpriced:,} not priced):",
            f"  mean signed {self.mean_signed:+.3f}  mean abs {self.mean_abs:.3f}"
            f"  ({self.mean_abs_fraction:.1%} of the price)",
            f"  benchmark slippage of one fill on the same premiums {self.mean_slippage:.3f}",
            f"  options verdict: {verdict}",
        ]


def quote_price(bid: float | None, ask: float | None, ltp: float | None) -> float | None:
    """The mid when both sides are quoted (and not crossed), else the last price."""
    if bid and ask and 0 < bid <= ask:
        return (bid + ask) / 2
    return ltp if ltp and ltp > 0 else None


class ModelErrorMeter:
    def __init__(self, prior: PriorSession, slippage: SlippageScenario = BENCHMARK) -> None:
        self._prior, self._slippage = prior, slippage

    def measure(self, quotes: Iterable[RecordedQuote]) -> ModelError | None:
        signed: list[float] = []
        fractions: list[float] = []
        slips: list[float] = []
        unpriced = 0
        for q in quotes:
            inputs = self._prior.inputs(q.contract, q.day)
            if inputs is None:
                unpriced += 1
                continue
            modelled = PremiumModel(q.contract, inputs).premium(q.spot, q.day, q.minute)
            signed.append(modelled - q.price)
            fractions.append(abs(modelled - q.price) / q.price)
            slips.append(
                self._slippage.ticks * TICK + float(self._slippage.premium_fraction) * q.price
            )
        if not signed:
            return None
        n = len(signed)
        return ModelError(
            n, sum(signed) / n, sum(abs(e) for e in signed) / n, sum(fractions) / n,
            sum(slips) / n, unpriced,
        )  # fmt: skip


def load_recorded_quotes(
    root: Path, day: date, calendar: ExpiryCalendar, lots: LotSizes
) -> Iterator[RecordedQuote]:
    """One IST day of the recorder's parts, a file at a time and only the columns needed. Rows whose
    expiry is not in the calendar or with no price are left out."""
    for part in sorted((root / f"date={day.isoformat()}").glob("*.parquet")):
        for row in pq.read_table(part, columns=_COLUMNS).to_pylist():
            lot = lots.lot(row["underlying"], day)
            listed = {e.expiry: e.kind for e in calendar.listed(row["underlying"], day)}
            price = quote_price(
                *(None if row[k] is None else float(row[k]) for k in ("bid", "ask", "ltp"))
            )
            kind = listed.get(row["expiry"])
            if lot is None or kind is None or price is None or row["spot"] is None:
                continue
            when = row["exchange_ts"].astimezone(IST)
            yield RecordedQuote(
                Contract(
                    row["underlying"], row["expiry"], kind, float(row["strike"]),
                    Right(row["right"]), lot,
                ),
                day, when.hour * 60 + when.minute, float(row["spot"]), price,
            )  # fmt: skip


def pooled(errors: Sequence[ModelError]) -> ModelError | None:
    """Several days' errors as one, weighted by the number of quotes."""
    total = sum(e.n for e in errors)
    if not total:
        return None
    w = [e.n / total for e in errors]
    return ModelError(
        total, sum(a * e.mean_signed for a, e in zip(w, errors, strict=True)),
        sum(a * e.mean_abs for a, e in zip(w, errors, strict=True)),
        sum(a * e.mean_abs_fraction for a, e in zip(w, errors, strict=True)),
        sum(a * e.mean_slippage for a, e in zip(w, errors, strict=True)),
        sum(e.unpriced for e in errors),
    )  # fmt: skip
