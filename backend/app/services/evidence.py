"""Immutable, content-addressed storage of raw source snapshots."""

from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.connectors.base import EvidenceDraft
from app.db import utcnow
from app.models import SourceEvidence
from app.security.secrets import redact_text

_EXT = (
    ("json", "json"), ("html", "html"), ("xml", "xml"), ("pdf", "pdf"), ("csv", "csv"),
    ("spreadsheet", "xlsx"), ("jpeg", "jpg"), ("png", "png"), ("text", "txt"),
)


def _extension(content_type: str | None, content: bytes) -> str:
    ct = (content_type or "").lower()
    for needle, ext in _EXT:
        if needle in ct:
            return ext
    head = content[:200].lstrip().lower()
    if head.startswith((b"{", b"[")):
        return "json"
    if head.startswith(b"<"):
        return "html"
    return "bin"


def store_evidence(
    session: Session,
    draft: EvidenceDraft,
    *,
    source_key: str,
    source_name: str,
    access_method: str,
    property_id: int | None = None,
    project_id: int | None = None,
    parser_version: str | None = None,
    captured_by: str | None = None,
) -> SourceEvidence:
    content = draft.content or b""
    digest = hashlib.sha256(content).hexdigest()
    ext = _extension(draft.content_type, content)
    rel = f"{digest[:2]}/{digest}.{ext}"
    path = get_settings().evidence_dir / rel
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    evidence = SourceEvidence(
        project_id=project_id,
        property_id=property_id,
        source_key=source_key,
        source_name=source_name,
        access_method=access_method,
        url=redact_text(draft.url) if draft.url else None,
        request_summary=redact_text(draft.request_summary) if draft.request_summary else None,
        retrieved_at=utcnow(),
        http_status=draft.http_status,
        content_type=draft.content_type,
        sha256=digest,
        snapshot_path=rel,
        snapshot_bytes=len(content),
        entry_method=draft.entry_method,
        captured_by=captured_by,
        parser_version=parser_version,
        notes=draft.notes,
    )
    session.add(evidence)
    session.flush()
    return evidence


def evidence_file(evidence: SourceEvidence) -> Path | None:
    if not evidence.snapshot_path:
        return None
    path = get_settings().evidence_dir / evidence.snapshot_path
    return path if path.exists() else None
