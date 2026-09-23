"""A `SessionParity` as a JSON-safe document, and back (EM-185).

A daily report keeps its session's rows so weekly and cumulative reports aggregate without running
the shadow backtest again (and without re-reading Atlas). Money and decimals are exact strings,
times ISO-8601, durations whole microseconds: nothing passes through a float.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal

from emporos.backtest.portfolio import TradeDirection
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.signals import SignalKind
from emporos.parity.ledger import SessionParity, SignalParity, TradeFacts, TradePair
from emporos.parity.models import (
    LatencyProfile,
    ParityStatus,
    ReferenceKind,
    SideOutcome,
    SignalOrigin,
    SignalPoint,
    Slippage,
)

SCHEMA_VERSION = 1
Doc = dict[str, object]


def _money(value: Money | None) -> str | None:
    return None if value is None else str(value.amount)


def _to_money(value: object) -> Money | None:
    return None if value is None else Money.of(str(value))


def _span(value: timedelta | None) -> int | None:
    return None if value is None else value // timedelta(microseconds=1)


def _to_span(value: object) -> timedelta | None:
    return None if value is None else timedelta(microseconds=int(str(value)))


class SessionParityCodec:
    def to_document(self, session: SessionParity) -> Doc:
        return {
            "schema_version": SCHEMA_VERSION,
            "strategy": session.strategy,
            "run_id": session.run_id,
            "session_date": session.session_date.isoformat(),
            "behaviour_hash": session.behaviour_hash,
            "starting_cash": str(session.starting_cash),
            "signals": [self._signal(s) for s in session.signals],
            "trades": [self._pair(t) for t in session.trades],
        }

    def from_document(self, document: Mapping[str, object]) -> SessionParity:
        if document.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"parity session schema {document.get('schema_version')!r} unknown")
        signals = document["signals"]
        trades = document["trades"]
        assert isinstance(signals, list) and isinstance(trades, list)
        return SessionParity(
            str(document["strategy"]), str(document["run_id"]),
            date.fromisoformat(str(document["session_date"])),
            str(document["behaviour_hash"]), Decimal(str(document["starting_cash"])),
            tuple(self._to_signal(s) for s in signals), tuple(self._to_pair(t) for t in trades),
        )  # fmt: skip

    # --- signals ----------------------------------------------------------------------------
    def _signal(self, row: SignalParity) -> Doc:
        return {
            "instrument_id": row.instrument_id,
            "status": row.status.value,
            "reason": row.reason,
            "paper": None if row.paper is None else self._point(row.paper),
            "backtest": None if row.backtest is None else self._point(row.backtest),
            "paper_outcome": None
            if row.paper_outcome is None
            else self._outcome(row.paper_outcome),
            "backtest_outcome": None
            if row.backtest_outcome is None
            else self._outcome(row.backtest_outcome),
        }

    def _to_signal(self, raw: object) -> SignalParity:
        d = _as_doc(raw)
        return SignalParity(
            str(d["instrument_id"]), ParityStatus(str(d["status"])), str(d["reason"]),
            None if d["paper"] is None else self._to_point(d["paper"]),
            None if d["backtest"] is None else self._to_point(d["backtest"]),
            None if d["paper_outcome"] is None else self._to_outcome(d["paper_outcome"]),
            None if d["backtest_outcome"] is None else self._to_outcome(d["backtest_outcome"]),
        )  # fmt: skip

    @staticmethod
    def _point(p: SignalPoint) -> Doc:
        return {
            "origin": p.origin.value, "ref": p.ref, "sequence": p.sequence,
            "instrument_id": p.instrument_id, "kind": p.kind.value, "side": p.side.value,
            "ts": p.ts.isoformat(), "price": _money(p.price), "quantity": p.quantity,
            "system": p.system,
        }  # fmt: skip

    @staticmethod
    def _to_point(raw: object) -> SignalPoint:
        d = _as_doc(raw)
        price = _to_money(d["price"])
        assert price is not None
        return SignalPoint(
            SignalOrigin(str(d["origin"])), str(d["ref"]), int(str(d["sequence"])),
            str(d["instrument_id"]), SignalKind(str(d["kind"])), OrderSide(str(d["side"])),
            datetime.fromisoformat(str(d["ts"])), price, int(str(d["quantity"])),
            bool(d["system"]),
        )  # fmt: skip

    @staticmethod
    def _outcome(o: SideOutcome) -> Doc:
        latency = o.latency
        slippage = o.slippage
        return {
            "ordered_quantity": o.ordered_quantity,
            "filled_quantity": o.filled_quantity,
            "average_fill_price": _money(o.average_fill_price),
            "charges": _money(o.charges),
            "rejected_by_risk": o.rejected_by_risk,
            "latency": None
            if latency is None
            else {
                "decision": _span(latency.decision), "placement": _span(latency.placement),
                "fill": _span(latency.fill), "reprices": latency.reprices,
            },
            "slippage": None
            if slippage is None
            else {
                "bps": str(slippage.bps), "reference": _money(slippage.reference),
                "kind": slippage.kind.value,
            },
        }  # fmt: skip

    @staticmethod
    def _to_outcome(raw: object) -> SideOutcome:
        d = _as_doc(raw)
        charges = _to_money(d["charges"])
        assert charges is not None
        latency = d["latency"]
        slippage = d["slippage"]
        lat = None if latency is None else _as_doc(latency)
        slip = None if slippage is None else _as_doc(slippage)
        reference = None if slip is None else _to_money(slip["reference"])
        return SideOutcome(
            int(str(d["ordered_quantity"])), int(str(d["filled_quantity"])),
            _to_money(d["average_fill_price"]), charges, bool(d["rejected_by_risk"]),
            None
            if lat is None
            else LatencyProfile(
                _to_span(lat["decision"]), _to_span(lat["placement"]), _to_span(lat["fill"]),
                int(str(lat["reprices"])),
            ),
            None
            if slip is None or reference is None
            else Slippage(Decimal(str(slip["bps"])), reference, ReferenceKind(str(slip["kind"]))),
        )  # fmt: skip

    # --- trades -----------------------------------------------------------------------------
    def _pair(self, pair: TradePair) -> Doc:
        return {
            "instrument_id": pair.instrument_id,
            "direction": pair.direction.value,
            "paper": None if pair.paper is None else self._facts(pair.paper),
            "backtest": None if pair.backtest is None else self._facts(pair.backtest),
        }

    def _to_pair(self, raw: object) -> TradePair:
        d = _as_doc(raw)
        return TradePair(
            str(d["instrument_id"]), TradeDirection(str(d["direction"])),
            None if d["paper"] is None else self._to_facts(d["paper"]),
            None if d["backtest"] is None else self._to_facts(d["backtest"]),
        )  # fmt: skip

    @staticmethod
    def _facts(f: TradeFacts) -> Doc:
        return {
            "opened_at": f.opened_at.isoformat(), "closed_at": f.closed_at.isoformat(),
            "quantity": f.quantity, "entry_notional": str(f.entry_notional),
            "gross_pnl": str(f.gross_pnl), "fees": str(f.fees),
        }  # fmt: skip

    @staticmethod
    def _to_facts(raw: object) -> TradeFacts:
        d = _as_doc(raw)
        return TradeFacts(
            datetime.fromisoformat(str(d["opened_at"])),
            datetime.fromisoformat(str(d["closed_at"])),
            int(str(d["quantity"])), Decimal(str(d["entry_notional"])),
            Decimal(str(d["gross_pnl"])), Decimal(str(d["fees"])),
        )  # fmt: skip


def _as_doc(raw: object) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise ValueError("a parity document is malformed: expected an object")
    return raw
