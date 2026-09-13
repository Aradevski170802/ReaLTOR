"""Append-only, hash-chained audit log helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import AuditLog
from app.security.secrets import redact_text


def _digest(prev_hash: str | None, payload: dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(((prev_hash or "") + body).encode()).hexdigest()


def _payload(entry: AuditLog) -> dict[str, Any]:
    return {
        "occurred_at": entry.occurred_at.isoformat() if entry.occurred_at else None,
        "actor": entry.actor,
        "action": entry.action,
        "entity_type": entry.entity_type,
        "entity_id": entry.entity_id,
        "project_id": entry.project_id,
        "details": entry.details,
    }


def record_audit(
    session: Session,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: object | None = None,
    project_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    safe_details = json.loads(redact_text(json.dumps(details or {}, default=str)))
    session.flush()
    prev_hash = session.scalar(select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1))
    entry = AuditLog(
        occurred_at=utcnow().replace(microsecond=0),
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        project_id=project_id,
        details=safe_details,
        prev_hash=prev_hash,
    )
    entry.hash = _digest(prev_hash, _payload(entry))
    session.add(entry)
    session.flush()
    return entry


def verify_chain(session: Session) -> tuple[bool, int | None]:
    prev: str | None = None
    for entry in session.scalars(select(AuditLog).order_by(AuditLog.id)):
        if entry.prev_hash != prev or entry.hash != _digest(prev, _payload(entry)):
            return False, entry.id
        prev = entry.hash
    return True, None
