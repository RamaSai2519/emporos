"""EM-239: attachment text, extracted locally; image-only PDFs are marked, never silently empty."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from emporos.research.filings.attachments import (
    AttachmentCollector,
    AttachmentHalted,
    AttachmentText,
    AttachmentTextStore,
)
from emporos.research.filings.pdf_text import (
    ExtractedText,
    PdfExtractionError,
    PdftotextExtractor,
)
from emporos.research.filings.polite import PoliteGet, SourceRefused


def pdf(text: str) -> bytes:
    """A one-page PDF (a hand-written body: poppler rebuilds the missing cross-reference table)."""
    stream = f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET" if text else ""
    return (
        "%PDF-1.4\n"
        "1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R"
        "/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
        f"4 0 obj<</Length {len(stream)}>>stream\n{stream}\nendstream endobj\n"
        "5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        "trailer<</Root 1 0 R/Size 6>>\n%%EOF\n"
    ).encode()


needs_poppler = pytest.mark.skipif(
    shutil.which("pdftotext") is None, reason="poppler not installed"
)


class TestPdftotext:
    @needs_poppler
    def test_it_extracts_the_text_of_a_pdf(self) -> None:
        got = PdftotextExtractor().extract(
            pdf("Outcome of Board Meeting: results for the quarter ended 30 June 2024")
        )

        assert "Outcome of Board Meeting" in got.text
        assert not got.image_only
        assert len(got.sha256) == 64

    @needs_poppler
    def test_a_pdf_with_no_text_is_marked_image_only(self) -> None:
        got = PdftotextExtractor().extract(pdf(""))

        assert got.image_only
        assert got.text == ""

    def test_something_that_is_not_a_pdf_is_refused(self) -> None:
        with pytest.raises(PdfExtractionError, match="%PDF"):
            PdftotextExtractor().extract(b"<html>Access Denied</html>")

    def test_a_missing_binary_is_an_extraction_error(self) -> None:
        with pytest.raises(PdfExtractionError):
            PdftotextExtractor("no-such-binary-xyz").extract(pdf("x"))


class FakeExtractor:
    def extract(self, body: bytes) -> ExtractedText:
        if body == b"%PDF scan":
            return ExtractedText("", True, "h", len(body))
        if body == b"%PDF bad":
            raise PdfExtractionError("damaged")
        if not body.startswith(b"%PDF"):
            raise PdfExtractionError("not a PDF (no %PDF header)")
        return ExtractedText(body.decode(), False, "h", len(body))


class NoSleep:
    async def sleep(self, seconds: float) -> None:
        return None


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 25, 14, 0, tzinfo=UTC)


def collector(tmp_path: Path, bodies: dict[str, int | bytes]) -> AttachmentCollector:
    def handler(request: httpx.Request) -> httpx.Response:
        answer = bodies[str(request.url)]
        return (
            httpx.Response(answer)
            if isinstance(answer, int)
            else httpx.Response(200, content=answer)
        )

    getter = PoliteGet(httpx.AsyncClient(transport=httpx.MockTransport(handler)), NoSleep())
    return AttachmentCollector(getter, FakeExtractor(), AttachmentTextStore(tmp_path), FixedClock())


class TestCollector:
    async def test_each_outcome_is_recorded_with_its_status(self, tmp_path: Path) -> None:
        bodies: dict[str, int | bytes] = {
            "https://a/ok.pdf": b"%PDF hello", "https://a/scan.pdf": b"%PDF scan",
            "https://a/x.xml": b"<xml/>", "https://a/gone.pdf": 404,
            "https://a/bad.pdf": b"%PDF bad",
        }  # fmt: skip

        report = await collector(tmp_path, bodies).run(list(bodies), None, lambda _: None)

        assert report.by_status == {
            "ok": 1, "image_only": 1, "not_pdf": 1, "not_found": 1, "error": 1
        }  # fmt: skip
        stored = {a.url: a for a in AttachmentTextStore(tmp_path).all()}
        assert stored["https://a/ok.pdf"].text == "%PDF hello"
        assert stored["https://a/scan.pdf"].status == "image_only"

    async def test_a_url_already_held_is_not_asked_again_and_a_limit_stops_the_run(
        self, tmp_path: Path
    ) -> None:
        bodies: dict[str, int | bytes] = {f"https://a/{i}.pdf": b"%PDF t" for i in range(4)}
        first = await collector(tmp_path, bodies).run(list(bodies), 2, lambda _: None)
        second = await collector(tmp_path, bodies).run(list(bodies), None, lambda _: None)

        assert (first.fetched, second.skipped, second.fetched) == (2, 2, 2)

    async def test_a_refusal_stops_the_run(self, tmp_path: Path) -> None:
        with pytest.raises(SourceRefused):
            await collector(tmp_path, {"https://a/1.pdf": 403}).run(
                ["https://a/1.pdf"], None, lambda _: None
            )

    async def test_three_transport_failures_in_a_row_halt_it(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        getter = PoliteGet(httpx.AsyncClient(transport=httpx.MockTransport(handler)), NoSleep())
        c = AttachmentCollector(
            getter, FakeExtractor(), AttachmentTextStore(tmp_path), FixedClock()
        )

        with pytest.raises(AttachmentHalted):
            await c.run([f"https://a/{i}" for i in range(5)], None, lambda _: None)


def test_a_status_outside_the_known_set_is_refused() -> None:
    with pytest.raises(ValueError, match="status is one of"):
        AttachmentText("u", datetime(2026, 1, 1, tzinfo=UTC), "weird", "", 0, "")
