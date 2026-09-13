"""Shared user-assisted recorder-of-deeds adapter behaviour (Montco countyweb, Delco publicsearch/countyweb)."""

from __future__ import annotations

from app.connectors.base import SourceAdapter, SourceOutcome
from app.connectors.capture_tables import RECORDER_SYNONYMS, extract_tables, rows_from_tables, to_recorder_documents
from app.enums import LookupStatus

RECORDER_IMPORT_COLUMNS = (
    "Instrument | Type | Recorded Date | Grantor | Grantee | Amount | Book | Page | Assoc Instruments | URL "
    "(any subset; Type and either Instrument or Book+Page are required)"
)


class RecorderCaptureMixin(SourceAdapter):
    def parse_capture(self, prop, content: str) -> SourceOutcome:
        rows = rows_from_tables(extract_tables(content), RECORDER_SYNONYMS, {"doc_type_raw"})
        docs = to_recorder_documents(rows)
        if not docs:
            return SourceOutcome(
                LookupStatus.PARSE_FAILURE,
                "No recorder document rows recognised. Expected a results table with columns like " + RECORDER_IMPORT_COLUMNS,
            )
        return SourceOutcome(LookupStatus.SUCCESS, f"{len(docs)} recorder document(s) captured", payload=docs)
