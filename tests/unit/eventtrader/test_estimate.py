"""The pre-call size estimate: real request sizes, assumed answers."""

from __future__ import annotations

from decimal import Decimal

from emporos.eventtrader.estimate import estimate_run
from emporos.eventtrader.llm.http_clients import GATEWAY_MODEL
from emporos.eventtrader.llm.pricing import ModelPrice, PriceTable
from tests.unit.eventtrader.fakes import item

PRICES = PriceTable({GATEWAY_MODEL: ModelPrice(Decimal("0.15"), Decimal("0.60"))}, Decimal(88))


def test_triage_is_asked_of_every_event_and_the_later_stages_only_of_those_that_pass() -> None:
    items = [item(event_id=f"E{i}") for i in range(100)]

    low, high = estimate_run(items, 250, (0.1, 0.3), PRICES, GATEWAY_MODEL)

    by = {s.name: s for s in low.stages}
    assert by["triage"].calls == 102  # every event, plus the 2% asked twice
    assert (
        by["panel (3 personas)"].calls == 30
    )  # 10 passers x 3 personas (the 2% retries round away)
    assert by["posture (v4, v5)"].calls == 255
    judge = "judge with panel (v1, v2, v4, v5)"
    assert {s.name: s.calls for s in high.stages}[judge] == 3 * by[judge].calls


def test_a_higher_pass_rate_costs_more_and_the_batch_price_is_half() -> None:
    items = [item(event_id=f"E{i}") for i in range(50)]

    low, high = estimate_run(items, 10, (0.1, 0.3), PRICES, GATEWAY_MODEL)

    assert high.usd > low.usd > 0
    assert low.usd_batch == low.usd / 2
    assert low.calls == sum(s.calls for s in low.stages)


def test_longer_text_costs_more_tokens_in_every_stage() -> None:
    short = [item(event_id="E1", text="x" * 100)]
    long = [item(event_id="E1", text="x" * 3000)]

    a = estimate_run(short, 0, (0.5,), PRICES, GATEWAY_MODEL)[0]
    b = estimate_run(long, 0, (0.5,), PRICES, GATEWAY_MODEL)[0]

    assert all(
        lb.tokens_in > sa.tokens_in for sa, lb in zip(a.stages[:4], b.stages[:4], strict=True)
    )
