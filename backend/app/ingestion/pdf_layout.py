"""Word/line geometry helpers and page classification shared by PDF parsers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.ingestion.ocr import OcrEngine, Word
from app.ingestion.types import PageInfo


@dataclass
class Line:
    page: int
    top: float
    words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def min_conf(self) -> float:
        return min((w.conf for w in self.words), default=1.0)


def classify_page(page, number: int) -> PageInfo:
    chars = len(page.chars)
    images = page.images
    page_area = float(page.width * page.height) or 1.0
    image_cover = sum(abs((img["x1"] - img["x0"]) * (img["bottom"] - img["top"])) for img in images) / page_area
    if chars >= 20:
        kind = "mixed" if image_cover > 0.5 else "text"
    elif images:
        kind = "scanned"
    else:
        kind = "empty"
    return PageInfo(number=number, kind=kind, char_count=chars, image_count=len(images))


def pdf_words(page) -> list[Word]:
    return [
        Word(w["text"], float(w["x0"]), float(w["x1"]), float(w["top"]), float(w["bottom"]))
        for w in page.extract_words(keep_blank_chars=False, use_text_flow=False, x_tolerance=1.5, y_tolerance=2)
    ]


def page_words(pdf_path: str | Path, page, info: PageInfo, ocr: OcrEngine | None, warnings: list[str]) -> list[Word]:
    """Text-layer words, or OCR words for image-only pages when an engine is available."""
    if info.kind in ("text", "mixed"):
        return pdf_words(page)
    if info.kind == "scanned":
        if ocr is None:
            note = f"Page {info.number} is image-only and no OCR engine is configured; rows on this page need manual entry"
            info.notes.append(note)
            warnings.append(note)
            return []
        words = ocr.words_for_page(pdf_path, info.number - 1)
        info.ocr_applied = True
        info.ocr_engine = ocr.name
        return words
    return []


def group_lines(words: list[Word], page: int, tolerance: float = 2.2) -> list[Line]:
    lines: list[Line] = []
    for word in sorted(words, key=lambda w: (w.top, w.x0)):
        if lines and abs(word.top - lines[-1].top) <= tolerance:
            lines[-1].words.append(word)
        else:
            lines.append(Line(page=page, top=word.top, words=[word]))
    for line in lines:
        line.words.sort(key=lambda w: w.x0)
    return lines


def join_words(words: list[Word]) -> str:
    return " ".join(w.text for w in words).strip()
