"""CSV / XLSX sale-list import (alternative to PDF extraction)."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from pathlib import Path

import openpyxl

from app.common.parcels import normalize_parcel
from app.common.parsing import clean_ws, parse_date, parse_money
from app.ingestion.fields import header_score, map_headers
from app.ingestion.types import ExtractedField, ParsedRecord, ParseResult

PARSER_KEY = "tabular_import_v1"


def _stringify(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return clean_ws(value)


def read_rows(path: str | Path, sheet: str | None = None) -> list[list[str | None]]:
    path = Path(path)
    if path.suffix.lower() == ".csv":
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        return [[clean_ws(c) for c in row] for row in csv.reader(io.StringIO(text), dialect)]
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    return [[_stringify(c) for c in row] for row in ws.iter_rows(values_only=True)]


def parse_tabular(path: str | Path, county: str, sheet: str | None = None,
                  mapping_override: dict[int, str] | None = None) -> ParseResult:
    rows = read_rows(path, sheet)
    result = ParseResult(parser_key=PARSER_KEY, records=[])
    header_idx = next((i for i, r in enumerate(rows[:25]) if header_score(r) >= 2), None)
    if header_idx is None and mapping_override is None:
        result.warnings.append("No header row with recognizable column names found in the first 25 rows")
        return result
    headers = rows[header_idx] if header_idx is not None else []
    mapping = mapping_override or map_headers(headers)
    result.metadata["column_mapping"] = {str(k): v for k, v in mapping.items()}
    result.metadata["headers"] = headers
    if "parcel" not in mapping.values():
        result.warnings.append("No parcel/folio column was mapped; map it on the review screen")

    start = (header_idx + 1) if header_idx is not None else 0
    for offset, row in enumerate(rows[start:]):
        if not any(row):
            continue
        fields: dict[str, ExtractedField] = {}
        reasons: list[str] = []
        for idx, key in mapping.items():
            if idx >= len(row):
                continue
            raw = row[idx]
            value = raw
            conf = 0.95 if raw else 0.5
            if key == "parcel" and raw:
                parts = normalize_parcel(county, raw)
                value, conf = parts.normalized, parts.confidence
                if not parts.valid:
                    reasons.append(f"Parcel could not be validated: {'; '.join(parts.notes)}")
            elif key in ("amount_due", "assessment") and raw:
                money = parse_money(raw)
                value = None if money is None else f"{money:.2f}"
                conf = 0.95 if money is not None else 0.3
            elif key == "sale_date" and raw:
                d = parse_date(raw)
                value = d.isoformat() if d else None
                conf = 0.95 if d else 0.3
            fields[key] = ExtractedField(raw, value, conf)
        if not fields.get("parcel") or not fields["parcel"].value:
            reasons.append("Parcel/folio missing")
        result.records.append(
            ParsedRecord(
                row_index=len(result.records),
                page_number=None,
                raw_text=f"row {start + offset + 1}: " + " | ".join(c or "" for c in row),
                fields=fields,
                review_reasons=reasons,
            )
        )
    return result
