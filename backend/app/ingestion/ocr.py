"""Pluggable OCR for image-only PDF pages.

The default engine shells out to the Tesseract CLI (TSV output) so there is no hard dependency.
When no engine is available, image-only pages are reported for manual review; nothing is invented.
"""

from __future__ import annotations

import csv
import io
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.config import get_settings


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    conf: float = 1.0


class OcrEngine(Protocol):
    name: str

    def available(self) -> bool: ...

    def words_for_page(self, pdf_path: str | Path, page_index: int) -> list[Word]: ...


class TesseractEngine:
    name = "tesseract"

    def __init__(self, cmd: str | None = None, dpi: int = 300):
        self.cmd = cmd or shutil.which("tesseract")
        self.dpi = dpi

    def available(self) -> bool:
        return bool(self.cmd) and Path(self.cmd).exists() if self.cmd and ("/" in self.cmd or "\\" in self.cmd) else bool(self.cmd)

    def words_for_page(self, pdf_path: str | Path, page_index: int) -> list[Word]:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(pdf_path))
        try:
            page = doc[page_index]
            image = page.render(scale=self.dpi / 72).to_pil()
        finally:
            doc.close()
        with tempfile.TemporaryDirectory() as tmp:
            img_path = Path(tmp) / "page.png"
            image.save(img_path)
            proc = subprocess.run(
                [self.cmd, str(img_path), "stdout", "--psm", "6", "tsv"],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        if proc.returncode != 0:
            raise RuntimeError(f"tesseract failed: {proc.stderr.strip()[:300]}")
        return parse_tesseract_tsv(proc.stdout, scale=72 / self.dpi)


def parse_tesseract_tsv(tsv: str, scale: float) -> list[Word]:
    words: list[Word] = []
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    for row in reader:
        text = (row.get("text") or "").strip()
        if not text or row.get("level") != "5":
            continue
        try:
            left, top = float(row["left"]), float(row["top"])
            width, height = float(row["width"]), float(row["height"])
            conf = max(0.0, float(row.get("conf") or 0)) / 100.0
        except (KeyError, ValueError):
            continue
        words.append(Word(text, left * scale, (left + width) * scale, top * scale, (top + height) * scale, conf))
    return words


def get_ocr_engine() -> OcrEngine | None:
    engine = TesseractEngine(get_settings().tesseract_cmd)
    return engine if engine.available() else None
