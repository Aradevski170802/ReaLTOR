"""Background job handlers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import RETRYABLE_STATUSES, LookupStatus
from app.jobs.queue import RetryableJobError, enqueue, set_progress
from app.models import ImportedSaleList, Job, Project, Property

log = logging.getLogger(__name__)
Handler = Callable[[Session, Job], dict[str, Any]]
HANDLERS: dict[str, Handler] = {}


def handler(kind: str):
    def wrap(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn
    return wrap


@handler("parse_import")
def parse_import(session: Session, job: Job) -> dict[str, Any]:
    from app.services.imports import run_parse

    sale_list = session.get(ImportedSaleList, job.payload["import_id"])
    if sale_list is None:
        raise ValueError("Import not found")
    result = run_parse(session, sale_list, job.created_by or "worker", job.payload.get("parser_key"),
                       confirm_overwrite=bool(job.payload.get("confirm_overwrite")))
    return {"records": len(result.records), "needs_review": result.stats["needs_review"], "warnings": result.warnings[:20]}


def _target_properties(session: Session, project_id: int, selection: Any) -> list[int]:
    query = select(Property.id).where(Property.project_id == project_id, Property.excluded.is_(False))
    if selection == "pilot":
        query = query.where(Property.is_pilot.is_(True))
    elif isinstance(selection, list):
        query = query.where(Property.id.in_([int(x) for x in selection]))
    return list(session.scalars(query.order_by(Property.sequence, Property.id)))


@handler("enrich_batch")
def enrich_batch(session: Session, job: Job) -> dict[str, Any]:
    ids = _target_properties(session, job.project_id, job.payload.get("selection", "all"))
    if not ids:
        raise ValueError("No properties selected (mark pilot properties first, or choose 'all')")
    for pid in ids:
        enqueue(session, "enrich_property", {"property_id": pid, "sources": job.payload.get("sources"), "force": job.payload.get("force", False)},
                project_id=job.project_id, property_id=pid, parent_id=job.id,
                idempotency_key=f"enrich:{pid}:{','.join(sorted(job.payload.get('sources') or []))}", created_by=job.created_by)
    set_progress(session, job.id, 0, len(ids))
    return {"queued": len(ids), "selection": job.payload.get("selection", "all")}


@handler("enrich_property")
def enrich_property_job(session: Session, job: Job) -> dict[str, Any]:
    from app.services.enrichment import enrich_property

    prop = session.get(Property, job.payload["property_id"])
    if prop is None:
        raise ValueError("Property not found")
    summary = enrich_property(session, prop, actor=job.created_by or "worker", source_keys=job.payload.get("sources"),
                              force=bool(job.payload.get("force")) and job.attempts <= 1, job_id=job.id)
    retryable = [k for k, status in summary.items() if status in RETRYABLE_STATUSES]
    if retryable and job.attempts < job.max_attempts:
        session.commit()  # keep successful sources; the retry skips them via the cache TTL
        raise RetryableJobError(f"Retryable source failures: {', '.join(retryable)}")
    return {"sources": summary}


@handler("run_valuations")
def run_valuations_job(session: Session, job: Job) -> dict[str, Any]:
    from app.services.rules_service import active_config, evaluate_properties
    from app.services.valuation_service import run_valuations

    prop = session.get(Property, job.payload["property_id"])
    records = run_valuations(session, prop, active_config(session), actor=job.created_by or "worker", force=bool(job.payload.get("force")),
                             provider_keys=job.payload.get("providers"))
    evaluate_properties(session, [prop.id], job.created_by or "worker")
    return {"providers": [r.provider_key for r in records]}


@handler("refresh_tax_status")
def refresh_tax_status_job(session: Session, job: Job) -> dict[str, Any]:
    from app.services.refresh import refresh_tax_status

    project = session.get(Project, job.project_id)
    run = refresh_tax_status(session, project, job.payload.get("trigger", "manual"), actor=job.created_by or "scheduler",
                             job_id=job.id, property_ids=job.payload.get("property_ids"))
    return {"refresh_run_id": run.id, "summary": run.summary}


@handler("recompute_rules")
def recompute_rules_job(session: Session, job: Job) -> dict[str, Any]:
    from app.services.rules_service import evaluate_properties, project_property_ids

    projects = [job.project_id] if job.project_id else list(session.scalars(select(Project.id).where(Project.archived.is_(False))))
    total = 0
    for pid in projects:
        ids = project_property_ids(session, pid)
        for start in range(0, len(ids), 200):
            evaluate_properties(session, ids[start : start + 200], job.created_by or "worker")
            session.commit()
        total += len(ids)
    return {"evaluated": total}


@handler("export_workbook")
def export_workbook_job(session: Session, job: Job) -> dict[str, Any]:
    from app.export.xlsx_exporter import export_project

    project = session.get(Project, job.project_id)
    record = export_project(session, project, actor=job.created_by or "worker", job_id=job.id,
                            property_ids=job.payload.get("property_ids"))
    return {"export_id": record.id, "filename": record.filename, "rows": record.row_count}


__all__ = ["HANDLERS", "LookupStatus"]
