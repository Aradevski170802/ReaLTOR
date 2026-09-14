"""Enrichment batches, jobs, refresh, rules, exports, audit and saved views."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Actor, admin, analyst, get_or_404, viewer
from app.api.schemas import EnrichIn, ExportIn, RefreshIn, RulesUpdateIn, SavedViewIn
from app.audit import record_audit, verify_chain
from app.connectors.registry import get_connector
from app.db import get_session
from app.enums import JobStatus
from app.jobs.queue import cancel, children_progress, enqueue
from app.models import AuditLog, ExportRecord, Job, Project, RefreshRun, RuleSet, SavedView
from app.rules.defaults import DEFAULT_RULES
from app.services.enrichment import jsonable
from app.services.rules_service import active_ruleset, update_ruleset

router = APIRouter(prefix="/api", tags=["operations"])


def _job_out(session: Session, job: Job) -> dict:
    data = jsonable({c.name: getattr(job, c.name) for c in Job.__table__.columns})
    if job.kind == "enrich_batch":
        data["children"] = children_progress(session, job.id)
    return data


@router.post("/projects/{project_id}/enrich")
def enrich(project_id: int, body: EnrichIn, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    project = get_or_404(session, Project, project_id)
    if body.sources:
        valid = {s.descriptor.key for s in get_connector(project.county).sources()}
        unknown = set(body.sources) - valid
        if unknown:
            raise HTTPException(400, f"Unknown sources for this county: {sorted(unknown)}")
    job = enqueue(session, "enrich_batch", {"selection": body.selection, "sources": body.sources, "force": body.force},
                  project_id=project.id, created_by=actor.name, max_attempts=1)
    record_audit(session, actor.name, "enrichment.batch_requested", "project", project.id, project.id,
                 {"selection": body.selection if isinstance(body.selection, str) else f"{len(body.selection)} properties",
                  "sources": body.sources, "force": body.force, "job_id": job.id})
    session.commit()
    return {"job_id": job.id}


@router.post("/projects/{project_id}/autopilot")
def autopilot(project_id: int, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    project = get_or_404(session, Project, project_id)
    job = enqueue(session, "autopilot", {}, project_id=project.id, created_by=actor.name,
                  idempotency_key=f"autopilot:{project.id}", max_attempts=1)
    record_audit(session, actor.name, "project.autopilot", "project", project.id, project.id, {"job_id": job.id})
    session.commit()
    return {"job_id": job.id}


@router.get("/jobs")
def list_jobs(project_id: int | None = None, status: str | None = None, kind: str | None = None, limit: int = 100,
              include_children: bool = False, session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    query = select(Job).order_by(Job.id.desc()).limit(min(limit, 500))
    if project_id:
        query = query.where(Job.project_id == project_id)
    if status:
        query = query.where(Job.status == status)
    if kind:
        query = query.where(Job.kind == kind)
    if not include_children:
        query = query.where(Job.parent_id.is_(None))
    return [_job_out(session, j) for j in session.scalars(query)]


@router.get("/jobs/{job_id}")
def get_job(job_id: int, session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    return _job_out(session, get_or_404(session, Job, job_id, "Job"))


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    job = get_or_404(session, Job, job_id, "Job")
    count = cancel(session, job)
    record_audit(session, actor.name, "job.cancel", "job", job.id, job.project_id, {"cancelled": count})
    session.commit()
    return {"cancelled": count}


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    job = get_or_404(session, Job, job_id, "Job")
    if job.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
        raise HTTPException(400, "Only failed or cancelled jobs can be retried")
    new = enqueue(session, job.kind, job.payload, project_id=job.project_id, property_id=job.property_id, created_by=actor.name,
                  parent_id=job.parent_id)
    session.commit()
    return {"job_id": new.id}


@router.post("/projects/{project_id}/refresh-tax-status")
def refresh(project_id: int, body: RefreshIn, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    project = get_or_404(session, Project, project_id)
    job = enqueue(session, "refresh_tax_status", {"trigger": "manual", "property_ids": body.property_ids}, project_id=project.id,
                  created_by=actor.name, idempotency_key=f"manual_refresh:{project.id}", max_attempts=2)
    session.commit()
    return {"job_id": job.id}


@router.get("/projects/{project_id}/refresh-runs")
def refresh_runs(project_id: int, session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    runs = session.scalars(select(RefreshRun).where(RefreshRun.project_id == project_id).order_by(RefreshRun.started_at.desc()).limit(100))
    return [jsonable({c.name: getattr(r, c.name) for c in RefreshRun.__table__.columns}) for r in runs]


@router.get("/rules")
def get_rules(session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    active = active_ruleset(session)
    session.commit()
    history = session.scalars(select(RuleSet).order_by(RuleSet.version.desc()).limit(50))
    return {"active": jsonable({"id": active.id, "version": active.version, "name": active.name, "config": active.config,
                                "change_reason": active.change_reason, "created_by": active.created_by, "created_at": active.created_at}),
            "defaults": DEFAULT_RULES,
            "history": [jsonable({"id": r.id, "version": r.version, "change_reason": r.change_reason, "created_by": r.created_by,
                                  "created_at": r.created_at, "is_active": r.is_active}) for r in history]}


@router.put("/rules")
def put_rules(body: RulesUpdateIn, session: Session = Depends(get_session), actor: Actor = Depends(admin)):
    try:
        rs = update_ruleset(session, body.patch, actor.name, body.reason)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    job = enqueue(session, "recompute_rules", {}, created_by=actor.name, idempotency_key="recompute_rules:all", max_attempts=2)
    session.commit()
    return {"version": rs.version, "recompute_job_id": job.id}


@router.post("/projects/{project_id}/exports")
def create_export(project_id: int, body: ExportIn, sync: bool = False, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    project = get_or_404(session, Project, project_id)
    if sync:
        from app.export.xlsx_exporter import export_project

        record = export_project(session, project, actor.name, property_ids=body.property_ids)
        session.commit()
        return {"export_id": record.id, "download": f"/api/exports/{record.id}/download"}
    job = enqueue(session, "export_workbook", {"property_ids": body.property_ids}, project_id=project.id, created_by=actor.name, max_attempts=2)
    session.commit()
    return {"job_id": job.id}


@router.get("/projects/{project_id}/exports")
def list_exports(project_id: int, session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    rows = session.scalars(select(ExportRecord).where(ExportRecord.project_id == project_id).order_by(ExportRecord.created_at.desc()).limit(50))
    return [jsonable({"id": r.id, "filename": r.filename, "rows": r.row_count, "sha256": r.sha256, "created_by": r.created_by,
                      "created_at": r.created_at, "download": f"/api/exports/{r.id}/download"}) for r in rows]


@router.get("/exports/{export_id}/download")
def download_export(export_id: int, session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    record = get_or_404(session, ExportRecord, export_id, "Export")
    return FileResponse(record.file_path, filename=record.filename,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/audit")
def audit_log(project_id: int | None = None, entity_type: str | None = None, entity_id: str | None = None, limit: int = 200,
              session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    query = select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 2000))
    if project_id:
        query = query.where(AuditLog.project_id == project_id)
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if entity_id:
        query = query.where(AuditLog.entity_id == entity_id)
    return [jsonable({c.name: getattr(a, c.name) for c in AuditLog.__table__.columns}) for a in session.scalars(query)]


@router.get("/audit/verify")
def audit_verify(session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    ok, bad = verify_chain(session)
    return {"intact": ok, "first_invalid_id": bad}


@router.get("/views")
def list_views(project_id: int | None = None, session: Session = Depends(get_session), actor: Actor = Depends(viewer)):
    query = select(SavedView).order_by(SavedView.name)
    if project_id:
        query = query.where((SavedView.project_id == project_id) | SavedView.project_id.is_(None))
    return [jsonable({"id": v.id, "name": v.name, "project_id": v.project_id, "owner": v.owner, "state": v.state, "updated_at": v.updated_at})
            for v in session.scalars(query)]


@router.post("/views", status_code=201)
def save_view(body: SavedViewIn, session: Session = Depends(get_session), actor: Actor = Depends(viewer)):
    view = session.scalar(select(SavedView).where(SavedView.name == body.name, SavedView.project_id == body.project_id,
                                                  SavedView.owner == actor.name))
    if view is None:
        view = SavedView(name=body.name, project_id=body.project_id, owner=actor.name, state=body.state)
        session.add(view)
    else:
        view.state = body.state
    session.commit()
    return {"id": view.id}


@router.delete("/views/{view_id}")
def delete_view(view_id: int, session: Session = Depends(get_session), actor: Actor = Depends(viewer)):
    view = get_or_404(session, SavedView, view_id, "View")
    if view.owner != actor.name and actor.role != "admin":
        raise HTTPException(403, "Only the owner can delete this view")
    session.delete(view)
    session.commit()
    return {"ok": True}
