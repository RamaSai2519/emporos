"""Attachment text, extracted locally (PROFIT_PLAN §12.2, EM-239).

A filing's attachment is a PDF the exchange hosts. Its text is extracted on this machine with
poppler's `pdftotext` (`PdftotextExtractor`); no OCR is attempted, so a PDF that is only page
images yields (almost) no text and is marked `image_only`, never silently empty. Only the text, its
length in pages and a hash of the file are kept: the PDF itself is not, so a run does not fill the
disk."""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

__all__ = ["ExtractedText", "PdfExtractionError", "PdfTextExtractor", "PdftotextExtractor"]

MIN_TEXT_CHARS = 40  # fewer letters and digits than this in a PDF is a scan, not a document


class PdfExtractionError(RuntimeError):
    """The file is not a PDF the extractor can read."""


@dataclass(frozen=True)
class ExtractedText:
    text: str
    image_only: bool
    sha256: str
    size: int


class PdfTextExtractor(Protocol):
    def extract(self, pdf: bytes) -> ExtractedText: ...


class PdftotextExtractor:
    def __init__(self, binary: str = "pdftotext", timeout_seconds: float = 60.0) -> None:
        self._binary = binary
        self._timeout = timeout_seconds

    def extract(self, pdf: bytes) -> ExtractedText:
        if not pdf.startswith(b"%PDF"):
            raise PdfExtractionError("not a PDF (no %PDF header)")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "in.pdf"
            source.write_bytes(pdf)
            try:
                done = subprocess.run(
                    [self._binary, "-layout", "-enc", "UTF-8", str(source), "-"],
                    capture_output=True, timeout=self._timeout, check=False,
                )  # fmt: skip
            except (OSError, subprocess.TimeoutExpired) as error:
                raise PdfExtractionError(str(error)) from error
        if done.returncode != 0:
            raise PdfExtractionError(done.stderr.decode("utf-8", "replace").strip()[:200])
        text = "\n".join(
            line.rstrip()
            for line in done.stdout.decode("utf-8", "replace").replace("\f", "\n").splitlines()
        ).strip()
        alnum = sum(c.isalnum() for c in text)
        return ExtractedText(
            text, alnum < MIN_TEXT_CHARS, hashlib.sha256(pdf).hexdigest(), len(pdf)
        )
