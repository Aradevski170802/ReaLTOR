"""Montgomery County PA Tax Claim Bureau upset sale list parser.

The list is a Word table exported to PDF: legal notice pages, then one table spanning many pages with
columns Municipality | Sale Number | Parcel | BOA Owner Name | BOA: Location | Approx. Sale Price.
Cells wrap onto extra lines. Table extraction is used first and cross-checked against the number of
sale numbers present in the page text; pages that don't reconcile fall back to a positional parser.
"""

from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from app.common.parcels import normalize_parcel
from app.common.parsing import clean_ws, parse_money
from app.ingestion.ocr import OcrEngine, get_ocr_engine
from app.ingestion.pdf_layout import Line, classify_page, group_lines, join_words, page_words
from app.ingestion.types import ExtractedField, ParsedRecord, ParseResult

SIGNATURE = re.compile(r"Montgomery\s+County\s+Tax\s+Claim\s+Bureau", re.I)
SALE_RE = re.compile(r"^U\d{2}-\d{4}$")
SALE_ANY_RE = re.compile(r"\bU\d{2}-\d{4}\b")
PRICE_RE = re.compile(r"^\$[\d,]+\.\d{2}$")
SALE_AS_OF_RE = re.compile(r"Upset\s+Sale\s+as\s+of\s+(\d{1,2}/\d{1,2}/\d{4})", re.I)
SALE_DATE_RE = re.compile(r"public\s+sale.*?\bon\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", re.S)

HEADER_MAP = {
    "municipality": "municipality",
    "sale number": "sale_number",
    "parcel": "parcel",
    "boa owner name": "owner_name",
    "owner name": "owner_name",
    "boa: location": "property_address",
    "location": "property_address",
    "approx. sale price": "amount_due",
}
DEFAULT_ORDER = ["municipality", "sale_number", "parcel", "owner_name", "property_address", "amount_due"]
# Column left edges observed on the 2026 list (points); refined from the header row when present.
DEFAULT_BOUNDS = {"sale_number": 150.0, "parcel": 222.0, "owner_name": 316.0, "property_address": 545.0, "amount_due": 700.0}


def _cell(value: str | None) -> str | None:
    return clean_ws(value.replace("\n", " ")) if value else None


class MontcoUpsetListParser:
    key = "montco_tcb_upset_list_v1"
    county = "montco"

    def __init__(self, ocr: OcrEngine | None = None):
        self._ocr = ocr
        self._order = list(DEFAULT_ORDER)
        self._bounds = dict(DEFAULT_BOUNDS)

    @staticmethod
    def matches(text: str) -> bool:
        return bool(SIGNATURE.search(text)) and bool(SALE_ANY_RE.search(text) or "Upset" in text)

    def parse(self, path: str | Path) -> ParseResult:
        ocr = self._ocr if self._ocr is not None else get_ocr_engine()
        result = ParseResult(parser_key=self.key, records=[])
        seen: set[str] = set()
        with pdfplumber.open(str(path)) as pdf:
            for index, page in enumerate(pdf.pages):
                info = classify_page(page, index + 1)
                result.pages.append(info)
                text = page.extract_text() or ""
                if index < 4 and (m := SALE_AS_OF_RE.search(text)):
                    result.metadata.setdefault("list_as_of", m.group(1))
                if index < 4 and (m := SALE_DATE_RE.search(text)):
                    result.metadata.setdefault("sale_date", m.group(1))

                if info.kind == "scanned":
                    words = page_words(path, page, info, ocr, result.warnings)
                    rows = self._positional(group_lines(words, info.number), info.number)
                    method = "ocr-positional"
                else:
                    expected = len(SALE_ANY_RE.findall(text))
                    if expected == 0:
                        info.kind = "notice" if len(text.strip()) > 40 else "empty"
                        page.close()
                        continue
                    tables = page.find_tables()
                    info.table_count = len(tables)
                    rows = self._from_tables([t.extract() for t in tables], info.number)
                    method = "table"
                    if len(rows) != expected:
                        fallback = self._positional(group_lines(page_words(path, page, info, ocr, result.warnings), info.number), info.number)
                        if len(fallback) >= len(rows):
                            result.warnings.append(
                                f"Page {info.number}: table extraction found {len(rows)} of {expected} rows; used positional parser"
                            )
                            rows, method = fallback, "positional"

                for fields, raw, reasons in rows:
                    sale_no = fields["sale_number"].value or ""
                    if sale_no in seen:
                        reasons.append(f"Duplicate sale number {sale_no} (repeated header or continuation page)")
                    seen.add(sale_no)
                    if method != "table":
                        reasons.append(f"Parsed with {method} fallback; verify columns")
                        for f in fields.values():
                            f.confidence = min(f.confidence, 0.78)
                    result.records.append(
                        ParsedRecord(
                            row_index=len(result.records),
                            page_number=info.number,
                            raw_text=raw,
                            fields=fields,
                            review_reasons=reasons,
                        )
                    )
                    info.records += 1
        return result

    # ------------------------------------------------------------------ table path
    def _from_tables(self, tables: list[list[list[str | None]]], page_number: int):
        rows = []
        for table in tables:
            for raw_row in table:
                cells = [_cell(c) for c in raw_row]
                lowered = [(c or "").lower() for c in cells]
                if "sale number" in lowered:
                    order = [HEADER_MAP.get(c) for c in lowered]
                    if all(order) and len(order) == len(DEFAULT_ORDER):
                        self._order = order  # type: ignore[assignment]
                    continue
                if len(cells) != len(self._order):
                    continue
                values = dict(zip(self._order, cells, strict=True))
                if not SALE_RE.match(values.get("sale_number") or ""):
                    continue
                raw_text = " | ".join((c or "").replace("\n", " ") for c in raw_row)
                rows.append(self._build(values, raw_text, 0.98))
        return rows

    # ------------------------------------------------------------------ positional path
    def _positional(self, lines: list[Line], page_number: int):
        header = next((ln for ln in lines if "Sale" in ln.text and "Parcel" in ln.text and "Municipality" in ln.text), None)
        if header:
            xs: dict[str, float] = {}
            for w in header.words:
                xs.setdefault(w.text, w.x0)  # "Sale" appears in both "Sale Number" and "Approx. Sale Price"
            boa = [w.x0 for w in header.words if w.text.startswith("BOA")]
            self._bounds.update(
                {
                    "sale_number": xs.get("Sale", self._bounds["sale_number"]) - 4,
                    "parcel": xs.get("Parcel", self._bounds["parcel"]) - 4,
                    "owner_name": (boa[0] if boa else self._bounds["owner_name"]) - 4,
                    "property_address": (boa[1] if len(boa) > 1 else self._bounds["property_address"]) - 4,
                    "amount_due": xs.get("Approx.", self._bounds["amount_due"]) - 4,
                }
            )
        order = ["municipality", "sale_number", "parcel", "owner_name", "property_address", "amount_due"]
        edges = [self._bounds[k] for k in order[1:]]

        def column_of(x0: float) -> str:
            for i, edge in enumerate(edges):
                if x0 < edge:
                    return order[i]
            return order[-1]

        grouped: list[tuple[dict[str, list[str]], list[str]]] = []
        for line in lines:
            if line is header:
                continue
            if any(SALE_RE.match(w.text) for w in line.words):
                cols: dict[str, list[str]] = {k: [] for k in order}
                grouped.append((cols, [line.text]))
            elif grouped:
                cols = grouped[-1][0]
                grouped[-1][1].append(line.text)
            else:
                continue
            for w in line.words:
                key = "amount_due" if PRICE_RE.match(w.text) else column_of(w.x0)
                cols[key].append(w.text)

        rows = []
        for cols, raw in grouped:
            values = {k: clean_ws(" ".join(v)) for k, v in cols.items()}
            rows.append(self._build(values, " / ".join(raw), 0.9))
        return rows

    # ------------------------------------------------------------------ record
    @staticmethod
    def _build(values: dict[str, str | None], raw_text: str, base: float):
        reasons: list[str] = []
        parcel = normalize_parcel("montco", values.get("parcel"))
        if not parcel.valid:
            reasons.append(f"Parcel could not be validated: {'; '.join(parcel.notes)}")
        elif parcel.repaired:
            reasons.append(f"Parcel repaired: {'; '.join(parcel.notes)}")
        price_raw = values.get("amount_due")
        price = parse_money(price_raw)
        if price is None:
            reasons.append("Approx. Sale Price missing or unreadable")
        for key in ("owner_name", "property_address", "municipality"):
            if not values.get(key):
                reasons.append(f"{key.replace('_', ' ').title()} missing")

        def fld(key: str, conf: float | None = None) -> ExtractedField:
            v = values.get(key)
            return ExtractedField(v, v, base if (conf is None and v) else (conf if conf is not None else 0.3))

        fields = {
            "municipality": fld("municipality"),
            "sale_number": fld("sale_number"),
            "parcel": ExtractedField(values.get("parcel"), parcel.normalized, min(base, parcel.confidence)),
            "owner_name": fld("owner_name"),
            "property_address": fld("property_address"),
            "amount_due": ExtractedField(price_raw, None if price is None else f"{price:.2f}", base if price is not None else 0.3),
        }
        return fields, raw_text, reasons


def _unused(words):  # pragma: no cover - keeps join_words import meaningful for type checkers
    return join_words(words)
