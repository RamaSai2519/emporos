"""The ledgers in words: counts, onset timing and the crossing baseline (EM-243).

Counts and timing are the Move Ledger's report (plan §2). The continuation lines are the no-cause
baseline of the Crossing Ledger (§0a): the same statistic over ALL crossings of a kind, which every
cause class is later measured against. No P&L, no cost, no trade is computed here."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import date

import numpy as np

from emporos.research.atlas.crossings import FORWARD, CrossingBlock
from emporos.research.atlas.events import EventClass, MoveEvent, OnsetKind
from emporos.research.atlas.stats import clustered_mean_t

__all__ = ["crossing_lines", "move_lines", "names_csv"]

TOP_NAMES = 15
BUCKET_MINUTES = 30
FIRST_MINUTE = 9 * 60 + 15


def move_lines(events: Sequence[MoveEvent]) -> list[str]:
    lines = [f"Move Ledger: {len(events):,} events", ""]
    lines += [*_by_class(events), "", *_by_month(events), "", *_top_names(events), ""]
    return [*lines, *_onsets(events)]


def _by_class(events: Sequence[MoveEvent]) -> list[str]:
    counts = Counter(e.event_class for e in events)
    lines = ["Events by class:"]
    lines += [f"  {c.value:<12} {counts.get(c, 0):>8,}" for c in EventClass]
    jumps = [e.z for e in events if e.event_class is EventClass.STOCK_JUMP]
    for level in (4.0, 5.0):
        lines.append(f"  (stock_jump with z >= {level:g}: {sum(1 for z in jumps if z >= level):,})")
    return lines


def _by_month(events: Sequence[MoveEvent]) -> list[str]:
    table: dict[str, Counter[EventClass]] = defaultdict(Counter)
    for e in events:
        table[e.day.strftime("%Y-%m")][e.event_class] += 1
    head = "  month    " + "".join(f"{c.value:>13}" for c in EventClass)
    lines = ["Events per month and class:", head]
    for month in sorted(table):
        lines.append(f"  {month}  " + "".join(f"{table[month].get(c, 0):>13,}" for c in EventClass))
    return lines


def _top_names(events: Sequence[MoveEvent]) -> list[str]:
    stock = Counter(
        e.name for e in events if e.event_class in (EventClass.STOCK_DAILY, EventClass.STOCK_JUMP)
    )
    lines = [f"Stock events, the {TOP_NAMES} busiest names (the full list is the names CSV):"]
    lines += [f"  {name:<14} {n:>6,}" for name, n in stock.most_common(TOP_NAMES)]
    return lines


def names_csv(events: Sequence[MoveEvent]) -> str:
    table: dict[str, Counter[str]] = defaultdict(Counter)
    for e in events:
        table[e.name][e.event_class.value] += 1
    classes = [c.value for c in EventClass]
    rows = ["name," + ",".join(classes)]
    for name in sorted(table):
        rows.append(name + "," + ",".join(str(table[name].get(c, 0)) for c in classes))
    return "\n".join(rows) + "\n"


def _onsets(events: Sequence[MoveEvent]) -> list[str]:
    lines = ["Onset (the bar at which a quarter of the day's residual was in, or the open):"]
    for cls in (EventClass.STOCK_DAILY, EventClass.SECTOR, EventClass.MARKET, EventClass.PLACEBO):
        chosen = [e for e in events if e.event_class is cls]
        kinds = Counter(e.onset_kind for e in chosen)
        total = max(len(chosen), 1)
        lines.append(
            f"  {cls.value:<12} n={len(chosen):>7,}  "
            + "  ".join(f"{k.value} {kinds.get(k, 0) / total:5.1%}" for k in OnsetKind)
        )
        lines += _time_buckets(chosen)
    return lines


def _time_buckets(events: Sequence[MoveEvent]) -> list[str]:
    placed = [e for e in events if e.onset_at is not None and e.onset_kind is OnsetKind.INTRADAY]
    if not placed:
        return []
    buckets: Counter[int] = Counter()
    for e in placed:
        assert e.onset_at is not None
        minute = e.onset_at.hour * 60 + e.onset_at.minute
        buckets[(minute - FIRST_MINUTE) // BUCKET_MINUTES * BUCKET_MINUTES + FIRST_MINUTE] += 1
    total = len(placed)
    cells = "  ".join(
        f"{m // 60:02d}:{m % 60:02d} {buckets[m] / total:4.0%}" for m in sorted(buckets)
    )
    gaps = [
        (e.peak_at - e.onset_at).total_seconds() / 60
        for e in placed
        if e.peak_at is not None and e.onset_at is not None
    ]
    median = float(np.median(gaps)) if gaps else float("nan")
    return [
        f"      intraday onsets by half hour: {cells}",
        f"      median minutes onset to peak {median:.0f}",
    ]


# --- crossings ---------------------------------------------------------------------------------
def crossing_lines(block: CrossingBlock) -> list[str]:
    cols = block.columns
    days: list[date] = [d for d in cols["day"] if isinstance(d, date)]
    kinds = sorted({(str(k), float(kk)) for k, kk in zip(cols["kind"], cols["k"], strict=True)})  # type: ignore[arg-type]
    lines = [
        f"Crossing Ledger: {len(block):,} crossings. Every figure is over ALL crossings of its kind"
        " (the no-cause baseline), in the crossing's direction, from the entry reference after the"
        " crossing bar, in percent",
        "NOTE: seen before any hypothesis: 2017-2023 unconditioned crossing drift. These signed"
        " figures were requested as the no-cause baseline; the back-test years stay honest for"
        " with-the-move hypotheses only, and no further signed statistic is drawn on 2017-2023"
        " unless a declared hypothesis runs there.",
        "",
        *_crossing_months(cols, days, kinds),
    ]
    for kind, k in kinds:
        rows = [
            i
            for i, (a, b) in enumerate(zip(cols["kind"], cols["k"], strict=True))
            if a == kind and b == k
        ]
        lines += _kind(block, rows, days, kind, k)
    return lines


def _crossing_months(
    cols: dict[str, list[object]], days: list[date], kinds: list[tuple[str, float]]
) -> list[str]:
    counts: Counter[tuple[str, tuple[str, float]]] = Counter()
    for day, kind, k in zip(days, cols["kind"], cols["k"], strict=True):
        counts[(f"{day.year}-{day.month:02d}", (str(kind), float(k)))] += 1  # type: ignore[arg-type]
    labels = [f"{kind}@{k}" if k else kind for kind, k in kinds]
    width = max(len(label) for label in labels) + 2
    lines = [
        "Crossings per month and kind:",
        "  month   " + "".join(f"{x:>{width}}" for x in labels),
    ]
    for month in sorted({m for m, _ in counts}):
        cells = "".join(f"{counts[(month, key)]:>{width},}" for key in kinds)
        lines.append(f"  {month}  {cells}")
    return [*lines, ""]


def _kind(
    block: CrossingBlock, rows: list[int], days: list[date], kind: str, k: float
) -> list[str]:
    cols = block.columns
    day = [days[i] for i in rows]
    at_open = float(np.mean([bool(cols["at_open"][i]) for i in rows]))
    slip = np.array([float(cols["slip_pct"][i]) for i in rows])  # type: ignore[arg-type]
    title = f"{kind} (k={k})" if k else kind
    lines = [
        f"{title}: n={len(rows):,}, {at_open:.1%} already there at the open, "
        f"mean slip before the entry reference {np.nanmean(slip):+.3f}%"
    ]
    for label in FORWARD:
        for basis in ("resid", "raw"):
            values = np.array([float(cols[f"{basis}_{label}_pct"][i]) for i in rows])  # type: ignore[arg-type]
            found = clustered_mean_t(values, day)
            if found.n:
                lines.append(f"    {basis:<5} to {label:<5} {found.line()}")
    lines += _years(cols, rows, days)
    return [*lines, ""]


def _years(cols: dict[str, list[object]], rows: list[int], days: list[date]) -> list[str]:
    by_year: dict[int, list[float]] = defaultdict(list)
    for i in rows:
        value = float(cols["resid_1515_pct"][i])  # type: ignore[arg-type]
        if not np.isnan(value):
            by_year[days[i].year].append(value)
    if not by_year:
        return []
    cells = "  ".join(f"{y}: {np.mean(v):+.3f}%" for y, v in sorted(by_year.items()))
    return [f"    residual to 15:15 by year: {cells}"]
