"""File-based captures and bulk recorder imports (saved HTML pages, CSV/XLSX exports)."""

from __future__ import annotations

import re
import tempfile
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.common.parcels import normalize_parcel
from app.connectors.base import EvidenceDraft, SourceOutcome
from app.connectors.capture_tables import (
    CIVIL_SYNONYMS,
    RECORDER_SYNONYMS,
    rows_from_file,
    to_lien_cases,
    to_recorder_documents,
)
from app.connectors.registry import get_source
from app.enums import EntryMethod, LookupStatus
from app.models import Project, Property
from app.services.enrichment import persist_outcome, submit_capture
from app.services.rules_service import evaluate_properties

TEXT_EXT = {".html", ".htm", ".txt", ".xhtml"}
TABLE_EXT = {".csv", ".xlsx", ".xlsm"}
CONTENT_TYPES = {".csv": "text/csv", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12"}


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _rows(filename: str, data: bytes, synonyms: dict, required: set[str]) -> list[dict[str, str]]:
    suffix = Path(filename).suffix.lower()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"upload{suffix}"
        path.write_bytes(data)
        return rows_from_file(path, synonyms, required)


def submit_capture_file(session: Session, prop: Property, source_key: str, filename: str, data: bytes, actor: str,
                        preview: bool = False):
    suffix = Path(filename).suffix.lower()
    adapter = get_source(source_key)
    if suffix in TEXT_EXT:
        return submit_capture(session, prop, source_key, _decode(data), actor, preview=preview)
    if suffix not in TABLE_EXT or adapter.descriptor.category not in ("recorder", "civil"):
        raise ValueError("Upload a saved .html/.txt page, or a CSV/XLSX export for recorder and civil sources")
    if adapter.descriptor.category == "recorder":
        payload = to_recorder_documents(_rows(filename, data, RECORDER_SYNONYMS, {"doc_type_raw"}))
        outcome = SourceOutcome(LookupStatus.SUCCESS if payload else LookupStatus.PARSE_FAILURE,
                                f"{len(payload)} recorder document(s) imported from {filename}", payload=payload)
    else:
        cases = to_lien_cases(_rows(filename, data, CIVIL_SYNONYMS, {"case_number"}))
        relevant = [(c, why) for c in cases for keep, why in [adapter.relevance(c)] if keep]
        outcome = SourceOutcome(LookupStatus.SUCCESS if relevant else (LookupStatus.NO_MATCH if cases else LookupStatus.PARSE_FAILURE),
                                f"{len(cases)} case(s) imported; {len(relevant)} relevant", payload=relevant)
    from app.services.enrichment import jsonable

    preview_data = {"status": outcome.status, "message": outcome.message, "payload": jsonable(outcome.payload)}
    if preview or outcome.status == LookupStatus.PARSE_FAILURE:
        return outcome, preview_data
    outcome.evidence = [EvidenceDraft(url=None, content=data, content_type=CONTENT_TYPES[suffix], entry_method=EntryMethod.IMPORT,
                                      request_summary=f"File {filename} imported by {actor}")]
    persist_outcome(session, prop, adapter, outcome, entry_method=EntryMethod.IMPORT, actor=actor)
    evaluate_properties(session, [prop.id], actor)
    return outcome, preview_data


def import_recorder_workbook(session: Session, project: Project, source_key: str, filename: str, data: bytes, actor: str) -> dict:
    """Bulk import a document index covering many parcels (e.g. a 'Mortgage Info' sheet with a Parcel column)."""
    adapter = get_source(source_key)
    if adapter.descriptor.county != project.county or adapter.descriptor.category != "recorder":
        raise ValueError("Choose a recorder source for this project's county")
    rows = _rows(filename, data, RECORDER_SYNONYMS, {"doc_type_raw", "parcel"})
    if not rows:
        raise ValueError("No rows found. The file needs a Parcel/Folio column and a Type column.")
    by_parcel: dict[str, list[dict[str, str]]] = defaultdict(list)
    unmatched: set[str] = set()
    for row in rows:
        parts = normalize_parcel(project.county, row.get("parcel"))
        if parts.valid:
            by_parcel[parts.normalized].append(row)
        else:
            unmatched.add(row.get("parcel") or "")
    props = {p.parcel_normalized: p for p in session.scalars(select(Property).where(Property.project_id == project.id))}
    imported = 0
    touched = []
    for parcel, parcel_rows in by_parcel.items():
        prop = props.get(parcel)
        if prop is None:
            unmatched.add(parcel)
            continue
        docs = to_recorder_documents(parcel_rows)
        body = "\n".join("\t".join(f"{k}={v}" for k, v in r.items()) for r in parcel_rows).encode()
        outcome = SourceOutcome(LookupStatus.SUCCESS, f"{len(docs)} document(s) from bulk file {filename}", payload=docs,
                                evidence=[EvidenceDraft(url=None, content=body, content_type="text/plain", entry_method=EntryMethod.IMPORT,
                                                        request_summary=f"Rows for {parcel} from {filename}")])
        persist_outcome(session, prop, adapter, outcome, entry_method=EntryMethod.IMPORT, actor=actor)
        imported += len(docs)
        touched.append(prop.id)
    evaluate_properties(session, touched, actor)
    result = {"properties": len(touched), "documents": imported,
              "unmatched_parcels": sorted(x for x in unmatched if x and re.search(r"\d", x))[:200]}
    record_audit(session, actor, "recorder.bulk_import", "project", project.id, project.id, {"file": filename, **result})
    return result
