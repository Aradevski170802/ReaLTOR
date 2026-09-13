"""Daily tax-sale status refresh with preserved history."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.connectors.base import SourceOutcome
from app.connectors.registry import get_source
from app.db import utcnow
from app.enums import EntryMethod, LookupStatus
from app.models import Project, Property, RefreshRun, TaxStatusHistory
from app.services.enrichment import ensure_capture_task, persist_outcome
from app.services.policies import get_policy
from app.services.properties import property_ref
from app.services.rules_service import evaluate_properties

REFRESH_SOURCE = {"montco": "montco.tax_claim", "delco": "delco.assessment_portal"}


def refresh_tax_status(session: Session, project: Project, trigger: str, actor: str = "scheduler", job_id: int | None = None,
                       property_ids: list[int] | None = None) -> RefreshRun:
    adapter = get_source(REFRESH_SOURCE[project.county])
    policy = get_policy(session, adapter.descriptor.key)
    run = RefreshRun(job_id=job_id, project_id=project.id, kind="tax_status", trigger=trigger, started_at=utcnow())
    session.add(run)
    session.flush()
    query = select(Property).where(Property.project_id == project.id, Property.excluded.is_(False))
    if property_ids:
        query = query.where(Property.id.in_(property_ids))
    props = session.scalars(query.order_by(Property.sequence, Property.id)).all()
    touched: list[int] = []
    for prop in props:
        run.total += 1
        automated_ok = adapter.descriptor.automated and policy.enabled and (
            not adapter.descriptor.requires_terms_ack or policy.terms_acknowledged_at is not None)
        if automated_ok:
            previous_changed = session.scalar(select(TaxStatusHistory.id).where(TaxStatusHistory.refresh_run_id == run.id,
                                                                                 TaxStatusHistory.property_id == prop.id))
            outcome = adapter.lookup(property_ref(prop))
            persist_outcome(session, prop, adapter, outcome, entry_method=EntryMethod.AUTOMATED, actor=actor, job_id=job_id,
                            refresh_run_id=run.id)
            if outcome.status == LookupStatus.SUCCESS:
                run.updated += 1
                latest = session.scalar(select(TaxStatusHistory).where(TaxStatusHistory.refresh_run_id == run.id,
                                                                       TaxStatusHistory.property_id == prop.id)
                                        .order_by(TaxStatusHistory.id.desc()))
                if latest is not None and latest.changed and previous_changed is None:
                    run.changed += 1
            else:
                run.failed += 1
        else:
            reason = "Automated refresh not permitted for this source; re-capture task created"
            if adapter.descriptor.requires_terms_ack and policy.terms_acknowledged_at is None:
                reason = "Terms of use not acknowledged; re-capture task created"
            ensure_capture_task(session, prop, adapter, "daily_refresh")
            session.add(TaxStatusHistory(property_id=prop.id, source_key=adapter.descriptor.key, lookup_status=LookupStatus.USER_ACTION,
                                         refresh_run_id=run.id, status_text=reason))
            run.user_action_needed += 1
            if adapter.descriptor.key == "montco.tax_claim":
                persist_outcome(session, prop, adapter, SourceOutcome(LookupStatus.TERMS_NOT_ACKNOWLEDGED, reason),
                                entry_method=EntryMethod.AUTOMATED, actor=actor, job_id=job_id, refresh_run_id=run.id)
        touched.append(prop.id)
        session.flush()
    evaluate_properties(session, touched, actor)
    run.finished_at = utcnow()
    run.summary = (f"{run.total} checked: {run.updated} updated ({run.changed} changed), {run.failed} failed, "
                   f"{run.user_action_needed} need user capture")
    record_audit(session, actor, "refresh.tax_status", "project", project.id, project.id,
                 {"run_id": run.id, "trigger": trigger, "summary": run.summary, "source": adapter.descriptor.key})
    session.flush()
    return run
