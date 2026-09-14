"""Operator source policies: enable/disable sources and acknowledge terms of use."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.audit import record_audit
from app.connectors.registry import all_sources, get_source
from app.db import utcnow
from app.models import SourcePolicy


def get_policy(session: Session, source_key: str) -> SourcePolicy:
    policy = session.get(SourcePolicy, source_key)
    if policy is None:
        policy = SourcePolicy(source_key=source_key, enabled=True)
        session.add(policy)
        session.flush()
    return policy


def acknowledge_terms(session: Session, source_key: str, actor: str, note: str | None) -> SourcePolicy:
    adapter = get_source(source_key)
    policy = get_policy(session, source_key)
    policy.terms_acknowledged_by = actor
    policy.terms_acknowledged_at = utcnow()
    policy.notes = note
    record_audit(session, actor, "source.terms_acknowledged", "source_policy", source_key, None,
                 {"terms_notes": adapter.descriptor.terms_notes, "note": note})
    return policy


def revoke_terms(session: Session, source_key: str, actor: str) -> SourcePolicy:
    policy = get_policy(session, source_key)
    policy.terms_acknowledged_by = None
    policy.terms_acknowledged_at = None
    record_audit(session, actor, "source.terms_revoked", "source_policy", source_key)
    return policy


def acknowledge_automated_terms(session: Session, actor: str) -> list[str]:
    """Acknowledge terms for every source that can run automatically once terms are accepted. Idempotent."""
    acknowledged: list[str] = []
    for adapter in all_sources():
        d = adapter.descriptor
        if not d.requires_terms_ack:
            continue
        policy = get_policy(session, d.key)
        if policy.terms_acknowledged_at is None:
            acknowledge_terms(session, d.key, actor, "Acknowledged automatically at start-up (USI_AUTO_ACCEPT_TERMS)")
            acknowledged.append(d.key)
    return acknowledged


def set_enabled(session: Session, source_key: str, enabled: bool, actor: str) -> SourcePolicy:
    get_source(source_key)
    policy = get_policy(session, source_key)
    policy.enabled = enabled
    record_audit(session, actor, "source.enabled" if enabled else "source.disabled", "source_policy", source_key)
    return policy
