"""Fallback parser for unrecognized tabular PDFs.

Extracts every table, detects the header row via synonyms, maps columns to canonical fields and keeps
unmapped columns as extra fields. Every record is flagged for header/row-mapping review.
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber

from app.common.parsing import clean_ws
from app.ingestion.fields import header_score, map_headers
from app.ingestion.ocr import get_ocr_engine
from app.ingestion.pdf_layout import classify_page
from app.ingestion.types import ExtractedField, ParsedRecord, ParseResult


class GenericTablePdfParser:
    key = "generic_table_pdf_v1"
    county = None

    @staticmethod
    def matches(text: str) -> bool:
        return True

    def parse(self, path: str | Path) -> ParseResult:
        result = ParseResult(parser_key=self.key, records=[])
        mapping: dict[int, str] | None = None
        ocr_available = get_ocr_engine() is not None
        with pdfplumber.open(str(path)) as pdf:
            for index, page in enumerate(pdf.pages):
                info = classify_page(page, index + 1)
                result.pages.append(info)
                if info.kind == "scanned":
                    info.notes.append(
                        "Image-only page: generic OCR table reconstruction is not supported; enter or import these rows manually"
                        if ocr_available
                        else "Image-only page and no OCR engine configured; enter or import these rows manually"
                    )
                    result.warnings.append(f"Page {info.number}: image-only page skipped")
                    page.close()
                    continue
                tables = page.extract_tables()
                page.close()  # tables are materialized; release the page's object cache
                info.table_count = len(tables)
                for table in tables:
                    for raw_row in table:
                        cells = [clean_ws(c) for c in raw_row]
                        if not any(cells):
                            continue
                        if header_score(cells) >= 2:
                            mapping = map_headers(cells)
                            continue
                        if mapping is None:
                            continue
                        fields = {}
                        for idx, cell in enumerate(cells):
                            key = mapping.get(idx)
                            if key:
                                fields[key] = ExtractedField(cell, cell, 0.75)
                        result.records.append(
                            ParsedRecord(
                                row_index=len(result.records),
                                page_number=info.number,
                                raw_text=" | ".join(c or "" for c in cells),
                                fields=fields,
                                review_reasons=["Generic table parser: confirm the header mapping and each value"],
                            )
                        )
                        info.records += 1
        if not result.records:
            result.warnings.append("No table with recognizable headers was found; use CSV/XLSX import or manual entry")
        result.metadata["column_mapping"] = mapping
        return result
