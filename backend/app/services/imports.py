"""Sale-list upload, parsing, review corrections and commit to properties."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.common.parcels import normalize_parcel
from app.common.parsing import parse_date, parse_float
from app.config import get_settings
from app.connectors.registry import get_connector
from app.db import utcnow
from app.enums import ImportStatus
from app.ingestion.parsers import parse_pdf
from app.ingestion.tabular_import import parse_tabular
from app.ingestion.types import ParseResult
from app.models import ExtractedRow, ExtractedValue, ImportedSaleList, Project, Property, SaleListTaxLine
from app.services.properties import set_identifier

ALLOWED = {".pdf": "pdf", ".csv": "csv", ".xlsx": "xlsx", ".xlsm": "xlsx"}
PROPERTY_FIELD_MAP = {
    "sale_number": "sale_number", "owner_name": "owner_name", "property_address": "property_address",
    "municipality": "municipality", "mailing_address": "mailing_address", "description": "listed_description",
    "lot_size": "listed_lot_size",
}


class OverwriteConflict(Exception):
    def __init__(self, message: str, conflicts: list[dict]):
        super().__init__(message)
        self.conflicts = conflicts


@dataclass
class CommitResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)


def save_upload(session: Session, project: Project, filename: str, data: bytes, actor: str) -> ImportedSaleList:
    settings = get_settings()
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED:
        raise ValueError(f"Unsupported file type '{suffix}'. Upload a PDF, CSV or XLSX sale list.")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ValueError(f"File exceeds {settings.max_upload_mb} MB")
    if ALLOWED[suffix] == "pdf" and not data.startswith(b"%PDF"):
        raise ValueError("File does not look like a PDF")
    digest = hashlib.sha256(data).hexdigest()
    folder = settings.uploads_dir / str(project.id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{digest}{suffix}"
    path.write_bytes(data)
    sale_list = ImportedSaleList(project_id=project.id, filename=Path(filename).name, sha256=digest, stored_path=str(path),
                                 kind=ALLOWED[suffix], status=ImportStatus.UPLOADED, created_by=actor)
    session.add(sale_list)
    session.flush()
    record_audit(session, actor, "import.upload", "imported_sale_list", sale_list.id, project.id,
                 {"filename": sale_list.filename, "sha256": digest, "bytes": len(data)})
    return sale_list


def corrected_fields(sale_list: ImportedSaleList) -> list[dict]:
    return [
        {"row_index": row.row_index, "field": v.field, "value": v.value, "corrected_by": v.corrected_by}
        for row in sale_list.rows for v in row.values if v.corrected
    ]


def run_parse(session: Session, sale_list: ImportedSaleList, actor: str, parser_key: str | None = None,
              confirm_overwrite: bool = False) -> ParseResult:
    corrections = corrected_fields(sale_list)
    if corrections and not confirm_overwrite:
        raise OverwriteConflict(f"{len(corrections)} user-corrected value(s) would be discarded by re-parsing", corrections)
    county = sale_list.project.county
    sale_list.status = ImportStatus.PARSING
    session.flush()
    try:
        if sale_list.kind == "pdf":
            result = parse_pdf(sale_list.stored_path, county, parser_key)
        else:
            mapping = {int(k): v for k, v in (sale_list.column_mapping or {}).items()} or None
            result = parse_tabular(sale_list.stored_path, county, mapping_override=mapping)
    except Exception as exc:
        sale_list.status = ImportStatus.FAILED
        sale_list.error = f"{type(exc).__name__}: {exc}"
        record_audit(session, actor, "import.parse_failed", "imported_sale_list", sale_list.id, sale_list.project_id, {"error": sale_list.error})
        raise
    store_parse_result(session, sale_list, result)
    record_audit(session, actor, "import.parsed", "imported_sale_list", sale_list.id, sale_list.project_id,
                 {"parser": result.parser_key, "records": len(result.records), "needs_review": result.stats["needs_review"],
                  "discarded_corrections": len(corrections)})
    return result


def store_parse_result(session: Session, sale_list: ImportedSaleList, result: ParseResult) -> None:
    for row in list(sale_list.rows):
        session.delete(row)
    session.flush()
    session.expire(sale_list, ["rows"])
    for record in result.records:
        row = ExtractedRow(import_id=sale_list.id, row_index=record.row_index, page_number=record.page_number,
                           raw_text=record.raw_text, confidence=record.confidence, needs_review=record.needs_review,
                           review_reasons=record.review_reasons or None)
        for name, extracted in record.fields.items():
            row.values.append(ExtractedValue(field=name, raw_value=extracted.raw, value=extracted.value,
                                             confidence=round(max(0.0, extracted.confidence), 3)))
        if record.tax_lines:
            row.values.append(ExtractedValue(field="tax_lines", raw_value=None, value=json.dumps(record.tax_lines), confidence=1.0))
        sale_list.rows.append(row)
    sale_list.parser_key = result.parser_key
    sale_list.page_count = len(result.pages) or None
    sale_list.detection = {"pages": [p.as_dict() for p in result.pages]}
    sale_list.stats = result.stats
    sale_list.status = ImportStatus.REVIEW
    sale_list.error = None
    if "column_mapping" in result.metadata and result.metadata["column_mapping"]:
        sale_list.column_mapping = {str(k): v for k, v in result.metadata["column_mapping"].items()}
    session.flush()


def correct_value(session: Session, row: ExtractedRow, field_name: str, value: str | None, actor: str) -> ExtractedValue:
    county = row.sale_list.project.county
    item = next((v for v in row.values if v.field == field_name), None)
    old = item.value if item else None
    if field_name == "parcel" and value:
        parts = normalize_parcel(county, value)
        value = parts.normalized
    if item is None:
        item = ExtractedValue(field=field_name, raw_value=None, value=value, confidence=1.0)
        row.values.append(item)
    item.value = value
    item.corrected = True
    item.corrected_by = actor
    item.corrected_at = utcnow()
    item.confidence = 1.0
    row.confidence = min((v.confidence for v in row.values if v.field in ("parcel", "owner_name", "property_address")), default=1.0)
    session.flush()
    record_audit(session, actor, "import.correct_value", "extracted_row", row.id, row.sale_list.project_id,
                 {"field": field_name, "old": old, "new": value})
    return item


def resolve_review(session: Session, row: ExtractedRow, actor: str) -> None:
    row.needs_review = False
    record_audit(session, actor, "import.row_reviewed", "extracted_row", row.id, row.sale_list.project_id, {"reasons": row.review_reasons})


def _row_values(row: ExtractedRow) -> dict[str, ExtractedValue]:
    return {v.field: v for v in row.values}


def commit_import(session: Session, sale_list: ImportedSaleList, actor: str, confirm_overwrite: bool = False,
                  row_ids: list[int] | None = None) -> CommitResult:
    project = sale_list.project
    connector = get_connector(project.county)
    result = CommitResult()
    default_sale_date = parse_date((sale_list.stats or {}).get("sale_date"))
    existing = {p.parcel_normalized: p for p in session.scalars(select(Property).where(Property.project_id == project.id))}
    planned: list[tuple[ExtractedRow, dict[str, ExtractedValue], object]] = []

    for row in sale_list.rows:
        if row.excluded or (row_ids is not None and row.id not in row_ids):
            continue
        values = _row_values(row)
        parcel_raw = values.get("parcel")
        parts = normalize_parcel(project.county, parcel_raw.value if parcel_raw else None)
        if not parts.valid:
            result.skipped.append({"row_id": row.id, "row_index": row.row_index, "reason": f"Invalid {connector.parcel_label}: {parts.original!r}"})
            continue
        prop = existing.get(parts.normalized)
        if prop is not None:
            overridden = {ov.field for ov in prop.overrides if ov.active}
            touched = sorted(set(_changes(prop, values, default_sale_date)) & overridden)
            if touched and not confirm_overwrite:
                result.conflicts.append({"row_id": row.id, "parcel": parts.normalized, "fields": touched,
                                         "message": "Property has user overrides for these fields"})
                continue
        planned.append((row, values, parts))

    if result.conflicts and not confirm_overwrite:
        return result

    for row, values, parts in planned:
        prop = existing.get(parts.normalized)
        created = prop is None
        if created:
            prop = Property(project_id=project.id, county=project.county, parcel_original=(values["parcel"].raw_value or parts.original),
                            parcel_normalized=parts.normalized, parcel_search_key=parts.digits)
            session.add(prop)
            existing[parts.normalized] = prop
        changes = _changes(prop, values, default_sale_date) if not created else None
        _apply(prop, values, default_sale_date)
        prop.import_id = sale_list.id
        prop.extracted_row_id = row.id
        prop.source_page = row.page_number
        prop.extraction_confidence = row.confidence
        prop.needs_review = row.needs_review
        seq = values.get("sale_number")
        prop.sequence = int(seq.value) if seq and seq.value and seq.value.isdigit() else row.row_index + 1
        session.flush()
        for kind, value in connector.identifiers(parts).items():
            set_identifier(session, prop, kind, value, "sale_list")
        tax = values.get("tax_lines")
        if tax and tax.value:
            for line in list(prop.tax_lines):
                session.delete(line)
            for item in json.loads(tax.value):
                prop.tax_lines.append(SaleListTaxLine(**item))
        if created:
            result.created += 1
        elif changes:
            result.updated += 1
        else:
            result.unchanged += 1

    sale_list.status = ImportStatus.COMMITTED
    sale_list.committed_at = utcnow()
    session.flush()
    record_audit(session, actor, "import.commit", "imported_sale_list", sale_list.id, project.id,
                 {"created": result.created, "updated": result.updated, "unchanged": result.unchanged,
                  "skipped": len(result.skipped), "confirm_overwrite": confirm_overwrite})
    return result


def _target_values(values: dict[str, ExtractedValue], default_sale_date: date | None) -> dict[str, object]:
    out: dict[str, object] = {}
    for src, dst in PROPERTY_FIELD_MAP.items():
        if src in values:
            out[dst] = values[src].value
    if "sale_date" in values:
        out["sale_date"] = parse_date(values["sale_date"].value)
    elif default_sale_date:
        out["sale_date"] = default_sale_date
    if "amount_due" in values:
        out["listed_amount"] = parse_float(values["amount_due"].value)
    if "assessment" in values:
        out["listed_assessment"] = parse_float(values["assessment"].value)
    return out


def _changes(prop: Property, values: dict[str, ExtractedValue], default_sale_date: date | None) -> list[str]:
    return [k for k, v in _target_values(values, default_sale_date).items() if getattr(prop, k) != v]


def _apply(prop: Property, values: dict[str, ExtractedValue], default_sale_date: date | None) -> None:
    for key, value in _target_values(values, default_sale_date).items():
        setattr(prop, key, value)
