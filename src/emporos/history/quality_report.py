"""A `QualityReport` as the two documents people and tools read: Markdown and JSON."""

from __future__ import annotations

from typing import Any

from emporos.history.quality import Finding, QualityReport, Severity

_CHECKS = (
    ("duplicate_timestamps", "two bars at one instant"),
    ("timestamp_alignment", "a bar off the grid or outside the session"),
    ("missing_market_days", "a market day with no bars, inside the instrument's own span"),
    ("intraday_gaps", "a market day with fewer bars than the session holds"),
    ("overnight_discontinuity", "an open far from the previous close: split, bonus or bad data"),
)
_SHOWN = 25  # findings of one kind listed in full; the JSON has them all


class QualityMarkdown:
    def render(self, report: QualityReport, notes: str = "") -> str:
        lines = [
            f"# History quality: {report.timeframe.value} bars, {report.first}..{report.last}",
            "",
            f"{len(report.spans)} instruments, {report.market_days} market days (a day at least "
            "half of the instruments traded). Prices are unadjusted broker prices.",
            "",
            "## Checks",
            "",
            "| check | what it finds | errors | warnings |",
            "|---|---|---|---|",
        ]
        for name, meaning in _CHECKS:
            errors = report.count(name, Severity.ERROR)
            warnings = report.count(name, Severity.WARNING)
            lines.append(f"| {name} | {meaning} | {errors} | {warnings} |")
        lines += [
            "",
            "## Instruments",
            "",
            "| instrument | bars | first day | last day | findings |",
        ]
        lines.append("|---|---|---|---|---|")
        for span in report.spans:
            count = sum(1 for f in report.findings if f.instrument_id == span.instrument_id)
            lines.append(
                f"| {span.instrument_id} | {span.bars} | {span.first_day or '-'} | "
                f"{span.last_day or '-'} | {count} |"
            )
        for name, meaning in _CHECKS:
            found = [f for f in report.findings if f.check == name]
            if found:
                lines += ["", f"## {name} ({len(found)})", "", f"{meaning}.", ""]
                lines += [self._line(f) for f in found[:_SHOWN]]
                if len(found) > _SHOWN:
                    lines.append(f"- ... and {len(found) - _SHOWN} more (all in the JSON record)")
        if notes:
            lines += ["", notes.rstrip()]
        return "\n".join(lines) + "\n"

    @staticmethod
    def _line(finding: Finding) -> str:
        return f"- {finding.instrument_id} {finding.day or ''}: {finding.detail}"


class QualityJson:
    def of(self, report: QualityReport) -> dict[str, Any]:
        return {
            "timeframe": report.timeframe.value,
            "first": report.first.isoformat(),
            "last": report.last.isoformat(),
            "market_days": report.market_days,
            "errors": report.errors,
            "spans": [
                {
                    "instrument": s.instrument_id,
                    "bars": s.bars,
                    "first_day": s.first_day.isoformat() if s.first_day else None,
                    "last_day": s.last_day.isoformat() if s.last_day else None,
                }
                for s in report.spans
            ],
            "findings": [
                {
                    "check": f.check,
                    "instrument": f.instrument_id,
                    "day": f.day.isoformat() if f.day else None,
                    "severity": f.severity.value,
                    "detail": f.detail,
                }
                for f in report.findings
            ],
        }
