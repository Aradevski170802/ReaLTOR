from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Actor, analyst, get_or_404, viewer
from app.api.schemas import ProjectCreate, ProjectOut, ProjectPatch
from app.audit import record_audit
from app.connectors.registry import get_connector
from app.db import get_session
from app.enums import COUNTY_LABELS
from app.models import CaptureTask, ImportedSaleList, Project, Property, RefreshRun, SourceLookup

router = APIRouter(prefix="/api", tags=["projects"])


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(include_archived: bool = False, session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    query = select(Project).order_by(Project.created_at.desc())
    if not include_archived:
        query = query.where(Project.archived.is_(False))
    return session.scalars(query).all()


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectCreate, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    get_connector(body.county)
    project = Project(name=body.name, county=body.county, description=body.description, sale_date=body.sale_date,
                      is_demo=body.is_demo, created_by=actor.name)
    session.add(project)
    session.flush()
    record_audit(session, actor.name, "project.create", "project", project.id, project.id,
                 {"name": body.name, "county": COUNTY_LABELS[body.county], "is_demo": body.is_demo})
    session.commit()
    return project


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    return get_or_404(session, Project, project_id)


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def patch_project(project_id: int, body: ProjectPatch, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    project = get_or_404(session, Project, project_id)
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(project, key, value)
    record_audit(session, actor.name, "project.update", "project", project.id, project.id, changes)
    session.commit()
    return project


@router.get("/projects/{project_id}/summary")
def project_summary(project_id: int, session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    project = get_or_404(session, Project, project_id)
    decisions = dict(session.execute(select(Property.decision_status, func.count()).where(Property.project_id == project_id)
                                     .group_by(Property.decision_status)).all())
    total = session.scalar(select(func.count()).select_from(Property).where(Property.project_id == project_id)) or 0
    pilot = session.scalar(select(func.count()).select_from(Property).where(Property.project_id == project_id, Property.is_pilot.is_(True))) or 0
    tasks = dict(session.execute(select(CaptureTask.source_key, func.count()).where(CaptureTask.project_id == project_id,
                                                                                    CaptureTask.status == "open")
                                 .group_by(CaptureTask.source_key)).all())
    lookups = session.execute(
        select(SourceLookup.source_key, SourceLookup.status, func.count())
        .join(Property, Property.id == SourceLookup.property_id)
        .where(Property.project_id == project_id, SourceLookup.is_current.is_(True))
        .group_by(SourceLookup.source_key, SourceLookup.status)
    ).all()
    health: dict[str, dict[str, int]] = {}
    for key, status, count in lookups:
        health.setdefault(key, {})[status] = count
    last_run = session.scalar(select(RefreshRun).where(RefreshRun.project_id == project_id).order_by(RefreshRun.started_at.desc()))
    imports = session.scalars(select(ImportedSaleList).where(ImportedSaleList.project_id == project_id)
                              .order_by(ImportedSaleList.created_at.desc())).all()
    return {
        "project_id": project.id, "county": project.county, "county_label": COUNTY_LABELS[project.county],
        "properties": total, "pilot": pilot, "decisions": {str(k or "Not evaluated"): v for k, v in decisions.items()},
        "open_tasks": tasks, "source_health": health,
        "imports": [{"id": i.id, "filename": i.filename, "status": i.status, "records": (i.stats or {}).get("records")} for i in imports],
        "last_refresh": None if last_run is None else {"id": last_run.id, "started_at": last_run.started_at,
                                                       "finished_at": last_run.finished_at, "summary": last_run.summary},
    }
