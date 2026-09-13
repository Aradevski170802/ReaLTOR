from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExtractedField:
    raw: str | None
    value: str | None
    confidence: float = 1.0


@dataclass
class PageInfo:
    number: int
    kind: str  # text | scanned | mixed | empty | notice
    char_count: int = 0
    image_count: int = 0
    table_count: int = 0
    ocr_applied: bool = False
    ocr_engine: str | None = None
    records: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "kind": self.kind,
            "char_count": self.char_count,
            "image_count": self.image_count,
            "table_count": self.table_count,
            "ocr_applied": self.ocr_applied,
            "ocr_engine": self.ocr_engine,
            "records": self.records,
            "notes": self.notes,
        }


@dataclass
class ParsedRecord:
    row_index: int
    page_number: int | None
    raw_text: str
    fields: dict[str, ExtractedField]
    tax_lines: list[dict[str, Any]] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)

    ESSENTIAL = ("parcel", "owner_name", "property_address")

    @property
    def confidence(self) -> float:
        values = [self.fields[f].confidence for f in self.ESSENTIAL if f in self.fields]
        if not values:
            return 0.0
        return round(min(values), 3)

    @property
    def needs_review(self) -> bool:
        return bool(self.review_reasons) or self.confidence < 0.8

    def value(self, name: str) -> str | None:
        f = self.fields.get(name)
        return f.value if f else None


@dataclass
class ParseResult:
    parser_key: str
    records: list[ParsedRecord]
    pages: list[PageInfo] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "parser": self.parser_key,
            "records": len(self.records),
            "needs_review": sum(1 for r in self.records if r.needs_review),
            "pages": len(self.pages),
            "pages_by_kind": _count(p.kind for p in self.pages),
            "ocr_pages": sum(1 for p in self.pages if p.ocr_applied),
            "warnings": self.warnings,
            **self.metadata,
        }


def _count(items) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        out[item] = out.get(item, 0) + 1
    return out
