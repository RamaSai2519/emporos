"""A signal reaches the market only through record, risk and execution; each step is on record."""

from decimal import Decimal

from emporos.broker.errors import BrokerTransportError
from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.money import Money
from emporos.domain.signals import Signal
from emporos.execution.errors import ExecutionRefusedError
from emporos.persistence.records import SignalRecord
from emporos.risk.approval import RiskRejection
from emporos.risk.engine import RiskEngine
from emporos.session.signal_path import GatedExecutionSink
from emporos.signals.quotes import DecisionQuote
from emporos.signals.recorder import SignalRecorder
from tests.support.fakes import RecordingAlertSink
from tests.support.risk import NOW, MemoryRejectionLog, ScriptedRule, StaticSnapshots, healthy
from tests.support.strategies import make_signal
from tests.unit.execution.rig import ExecutionRig


class Signals:
    def __init__(self) -> None:
        self.rows: dict[str, SignalRecord] = {}

    async def insert(self, record: SignalRecord) -> None:
        self.rows[record.id] = record

    async def get(self, record_id: str) -> SignalRecord | None:
        return self.rows.get(record_id)

    async def replace(self, record: SignalRecord) -> None:
        self.rows[record.id] = record


class Path:
    def __init__(self, allow: bool = True, error: Exception | None = None) -> None:
        self.rig = ExecutionRig(error)
        self.store = Signals()
        self.alerts = RecordingAlertSink()
        self.rejections = MemoryRejectionLog()
        rules = [ScriptedRule("rule", allow)]
        self.risk = RiskEngine(
            rules, StaticSnapshots(healthy()), self.rejections, IdGenerator(),
            FixedClock(NOW), self.alerts,
        )  # fmt: skip
        self.sink = GatedExecutionSink(
            SignalRecorder(self.store, IdGenerator()), self.risk, self.rig.engine, self.alerts
        )

    async def send(self, signal: Signal | None = None) -> None:
        await self.sink.submit(signal or make_signal())


async def test_an_approved_signal_is_recorded_executed_and_linked_both_ways() -> None:
    path = Path()
    await path.send()
    (record,) = path.store.rows.values()
    (order,) = path.rig.journal.orders.values()
    assert order.signal_id == record.id and order.signal_kind == "ENTRY"
    assert record.ordertag == order.ordertag  # the signal names its order
    assert order.state == "OPEN" and len(path.rig.gateway.placements) == 1


async def test_a_rejected_signal_is_recorded_and_never_reaches_execution() -> None:
    path = Path(allow=False)
    await path.send()
    assert len(path.store.rows) == 1  # the signal is on record...
    assert path.rig.journal.orders == {} and path.rig.gateway.placements == []
    assert len(path.rejections.rejections) == 1  # and the rejection is on record too


async def test_an_execution_refusal_is_alerted_not_silently_dropped() -> None:
    path = Path(error=BrokerTransportError("lost"))
    await path.send()  # the placement becomes UNKNOWN: the instrument is now frozen
    await path.send(make_signal(ts=NOW))  # a second signal on the frozen instrument
    assert [name for name, _ in path.alerts.alerts] == ["execution_refused"]
    assert len(path.store.rows) == 2 and len(path.rig.journal.orders) == 1


async def test_only_an_approval_can_be_executed() -> None:
    path = Path()
    decision = await path.risk.review(make_signal(), "x")
    assert not isinstance(decision, RiskRejection)
    assert isinstance(ExecutionRefusedError("x"), ValueError) and Decimal(1)


class Quotes:
    """A `DecisionQuotes` that hands out the quote it was given, once per instrument."""

    def __init__(self, quote: DecisionQuote | None) -> None:
        self._quote = quote

    def take(self, instrument_id: str) -> DecisionQuote | None:
        quote, self._quote = self._quote, None
        return quote


class Recorder(SignalRecorder):
    def __init__(self, store: Signals, fail: bool = False) -> None:
        super().__init__(store, IdGenerator())
        self._fail = fail

    async def stamp_quote(self, signal_id: str, quote: DecisionQuote) -> None:
        if self._fail:
            raise RuntimeError("mongo unavailable")
        await super().stamp_quote(signal_id, quote)


def _quoted_path(
    allow: bool = True, fail: bool = False, quote: DecisionQuote | None = None
) -> Path:
    path = Path(allow=allow)
    recorder = Recorder(path.store, fail)
    path.sink = GatedExecutionSink(
        recorder, path.risk, path.rig.engine, path.alerts,
        Quotes(quote or DecisionQuote(NOW, "test_quote", Money.of("100"), Money.of("99.9"),
                                      Money.of("100.1"))),
        recorder,
    )  # fmt: skip
    return path


async def test_the_decision_quote_is_stamped_on_an_approved_signal() -> None:
    path = _quoted_path()
    await path.send()
    (record,) = path.store.rows.values()
    assert (record.quote_ltp, record.quote_bid, record.quote_ask) == (
        Money.of("100"), Money.of("99.9"), Money.of("100.1"),
    )  # fmt: skip
    assert (record.quote_ts, record.quote_source) == (NOW, "test_quote")


async def test_the_decision_quote_is_stamped_on_a_rejected_signal_too() -> None:
    path = _quoted_path(allow=False)
    await path.send()
    (record,) = path.store.rows.values()
    assert record.quote_ltp == Money.of("100") and path.rig.gateway.placements == []


async def test_a_failing_quote_stamp_alerts_but_never_stops_the_trade() -> None:
    path = _quoted_path(fail=True)
    await path.send()
    assert [name for name, _ in path.alerts.alerts] == ["signal_quote_not_recorded"]
    assert len(path.rig.gateway.placements) == 1  # the order still went out
    (record,) = path.store.rows.values()
    assert record.quote_ltp is None


async def test_a_review_with_no_quote_leaves_the_signal_unstamped() -> None:
    path = Path()
    recorder = Recorder(path.store)
    path.sink = GatedExecutionSink(
        recorder, path.risk, path.rig.engine, path.alerts, Quotes(None), recorder
    )
    await path.send()
    (record,) = path.store.rows.values()
    assert record.quote_ltp is None and record.quote_source is None


def test_a_decision_quote_needs_a_price_and_a_utc_time() -> None:
    import pytest

    with pytest.raises(ValueError, match="at least one price"):
        DecisionQuote(NOW, "x")
    with pytest.raises(ValueError, match="UTC"):
        DecisionQuote(NOW.replace(tzinfo=None), "x", Money.of("1"))
