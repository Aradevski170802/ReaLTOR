from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import Actor, analyst, get_or_404, viewer
from app.api.schemas import (
    CaptureIn,
    ManualValuationIn,
    MatchReviewIn,
    MortgageStatusIn,
    OverrideIn,
    OverrideRevokeIn,
    PilotIn,
    PropertyPatch,
    RerunIn,
)
from app.audit import record_audit
from app.config import get_settings
from app.connectors.registry import get_connector
from app.db import get_session, utcnow
from app.enums import COUNTY_LABELS
from app.export.workbook_spec import main_columns
from app.jobs.queue import enqueue
from app.models import (
    AssessmentRecord,
    CaptureTask,
    MortgageRecord,
    Project,
    Property,
    PropertyImage,
    RecorderDocument,
    RuleEvaluation,
    SourceEvidence,
    TaxClaimRecord,
    TaxStatusHistory,
    UserOverride,
    ValuationRecord,
)
from app.security.secrets import get_secret
from app.services.capture_files import import_recorder_workbook, submit_capture_file
from app.services.enrichment import jsonable, submit_capture
from app.services.evidence import evidence_file
from app.services.recorder_service import review_match, set_mortgage_status
from app.services.rules_service import active_config, evaluate_properties
from app.services.snapshot import FIELD_KINDS, load_snapshots
from app.services.valuation_service import add_manual_valuation
from app.valuation.images import (
    STREET_VIEW_ATTRIBUTION,
    STREET_VIEW_LICENSE,
    STREET_VIEW_SECRET,
    fetch_street_view,
    street_view_available,
)
from app.valuation.providers import MANUAL_SITES

router = APIRouter(prefix="/api", tags=["properties"])
OVERRIDABLE = set(FIELD_KINDS) | {
    "owner_name", "municipality", "location", "land_use_description", "property_type", "category", "school_district",
    "building_style", "exterior_wall", "structure_description", "improvement_name", "decision_status", "tax_status_interpretation",
    "lot_size", "site_address", "tax_sale_status", "mortgage_summary", "full_location",
}
DECISIONS = {"Interested", "Not interested", "Review required", "Insufficient data", "Removed/resolved"}


def _json(value):
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


@router.get("/projects/{project_id}/grid")
def grid(project_id: int, session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    project = get_or_404(session, Project, project_id)
    config = active_config(session)
    columns = main_columns(project.county, config, project.is_demo)
    ids = list(session.scalars(select(Property.id).where(Property.project_id == project_id)))
    snaps = load_snapshots(session, ids, config)
    rows = []
    for snap in sorted(snaps.values(), key=lambda s: ((s.prop.municipality or "").upper(), s.prop.sequence or 0, s.prop.id)):
        values, quality, sources = {}, {}, {}
        for col in columns:
            if col.key == "tax_status_override":
                ov = snap.overrides.get("tax_status_interpretation")
                values[col.key] = f"Overridden: {ov.reason}" if ov else None
                quality[col.key] = "manual" if ov else "unknown"
                continue
            cell = snap.cell(col.key)
            values[col.key] = _json(cell.value)
            quality[col.key] = cell.quality
            sources[col.key] = cell.source_name
        rows.append({"id": snap.prop.id, "values": values, "quality": quality, "sources": sources, "styles": snap.styles(),
                     "is_pilot": snap.prop.is_pilot, "excluded": snap.prop.excluded, "needs_review": snap.prop.needs_review,
                     "open_tasks": len(snap.open_tasks)})
    return {"project": {"id": project.id, "name": project.name, "county": project.county, "county_label": COUNTY_LABELS[project.county],
                        "is_demo": project.is_demo},
            "columns": [c.as_dict() for c in columns], "rows": rows,
            "thresholds": {"valuation": config["valuation"]["interest_threshold"], "assessment": config["assessment"]["highlight_below"]}}


@router.post("/projects/{project_id}/pilot")
def set_pilot(project_id: int, body: PilotIn, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    get_or_404(session, Project, project_id)
    session.execute(update(Property).where(Property.project_id == project_id).values(is_pilot=False))
    if body.property_ids:
        ids = body.property_ids
    else:
        count = body.count or 8
        ids = list(session.scalars(select(Property.id).where(Property.project_id == project_id, Property.excluded.is_(False))
                                   .order_by(Property.sequence, Property.id).limit(count)))
    if not 1 <= len(ids) <= 50:
        raise HTTPException(400, "Select between 1 and 50 pilot properties (7–8 recommended)")
    session.execute(update(Property).where(Property.project_id == project_id, Property.id.in_(ids)).values(is_pilot=True))
    record_audit(session, actor.name, "project.pilot_selected", "project", project_id, project_id, {"property_ids": ids})
    session.commit()
    return {"pilot": ids}


@router.get("/properties/{property_id}")
def property_detail(property_id: int, session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    prop = get_or_404(session, Property, property_id)
    config = active_config(session)
    snap = load_snapshots(session, [prop.id], config)[prop.id]
    connector = get_connector(prop.county)
    keys = sorted({c.key for c in main_columns(prop.county, config, True)} - {"tax_status_override"} | {
        "site_address", "legal_description", "valuation_detail", "mortgage_count", "satisfied_mortgages", "open_lien_cases",
        "commercial_year_built", "property_class", "total_rooms", "tax_2023_total", "tax_2025_balance", "tax_2024_due", "tax_2025_due"})
    cells = {k: snap.cell(k).as_dict() for k in keys}
    docs = session.scalars(select(RecorderDocument).where(RecorderDocument.property_id == prop.id)
                           .order_by(RecorderDocument.recorded_date.desc().nullslast())).all()
    doc_by_id = {d.id: d for d in docs}
    return {
        "property": {
            "id": prop.id, "project_id": prop.project_id, "county": prop.county, "county_label": COUNTY_LABELS[prop.county],
            "parcel_label": connector.parcel_label, "parcel": prop.parcel_normalized, "parcel_original": prop.parcel_original,
            "sale_number": prop.sale_number, "owner_name": prop.owner_name, "property_address": prop.property_address,
            "municipality": prop.municipality, "mailing_address": prop.mailing_address, "sale_date": _json(prop.sale_date),
            "listed_amount": prop.listed_amount, "listed_assessment": prop.listed_assessment, "listed_description": prop.listed_description,
            "listed_lot_size": prop.listed_lot_size, "source_page": prop.source_page, "import_id": prop.import_id,
            "extraction_confidence": prop.extraction_confidence, "needs_review": prop.needs_review, "is_pilot": prop.is_pilot,
            "excluded": prop.excluded, "notes": prop.notes, "decision_status": prop.decision_status,
            "decision_summary": prop.decision_summary, "decision_at": _json(prop.decision_at), "last_enriched_at": _json(prop.last_enriched_at),
        },
        "identifiers": {pi.kind: pi.value for pi in prop.identifiers},
        "sale_list_tax_lines": [{"year": t.tax_year, "cty": t.cty_assessment, "sch": t.sch_assessment, "twn": t.twn_assessment,
                                 "amount": t.amount, "raw": t.raw_line} for t in prop.tax_lines],
        "cells": cells,
        "styles": snap.styles(),
        "assessments": [jsonable({c.name: getattr(a, c.name) for c in AssessmentRecord.__table__.columns}) | {
            "sales": [{"sale_date": _json(s.sale_date), "sale_price": s.sale_price, "grantor": s.grantor, "grantee": s.grantee} for s in a.sales]}
            for a in session.scalars(select(AssessmentRecord).where(AssessmentRecord.property_id == prop.id).order_by(AssessmentRecord.retrieved_at.desc())).all()],
        "tax_claims": [jsonable({c.name: getattr(t, c.name) for c in TaxClaimRecord.__table__.columns}) | {
            "years": [{"year": y.year, "kind": y.kind, "face": y.face, "penalty": y.penalty, "interest": y.interest, "total": y.total,
                       "paid": y.paid, "balance": y.balance} for y in t.years]}
            for t in session.scalars(select(TaxClaimRecord).where(TaxClaimRecord.property_id == prop.id).order_by(TaxClaimRecord.retrieved_at.desc()).limit(10)).all()],
        "tax_history": [jsonable({c.name: getattr(h, c.name) for c in TaxStatusHistory.__table__.columns})
                        for h in session.scalars(select(TaxStatusHistory).where(TaxStatusHistory.property_id == prop.id).order_by(TaxStatusHistory.checked_at.desc()).limit(60))],
        "valuations": [jsonable({c.name: getattr(v, c.name) for c in ValuationRecord.__table__.columns})
                       for v in session.scalars(select(ValuationRecord).where(ValuationRecord.property_id == prop.id).order_by(ValuationRecord.response_at.desc()).limit(40))],
        "selection": None if snap.selection is None else jsonable({"value": snap.selection.value, "method": snap.selection.method,
                                                                    "provider_key": snap.selection.provider_key, "explanation": snap.selection.explanation,
                                                                    "computed_at": snap.selection.computed_at}),
        "manual_sites": MANUAL_SITES,
        "documents": [jsonable({c.name: getattr(d, c.name) for c in RecorderDocument.__table__.columns}) for d in docs],
        "mortgages": [jsonable({
            "id": m.id, "document_id": m.document_id, "instrument": doc_by_id[m.document_id].instrument_number if m.document_id in doc_by_id else None,
            "recorded_date": m.recorded_date, "original_amount": m.original_amount, "lender": m.lender, "borrower": m.borrower,
            "status": m.status, "user_status": m.user_status, "effective_status": m.effective_status, "status_reason": m.status_reason,
            "reviewed_by": m.reviewed_by, "review_note": m.review_note,
            "matches": [{"satisfaction_document_id": x.satisfaction_document_id,
                         "instrument": doc_by_id[x.satisfaction_document_id].instrument_number if x.satisfaction_document_id in doc_by_id else None,
                         "recorded_date": doc_by_id[x.satisfaction_document_id].recorded_date if x.satisfaction_document_id in doc_by_id else None,
                         "score": x.score, "method": x.method, "reasons": x.reasons, "decision": x.decision, "decided_by": x.decided_by}
                        for x in m.matches]}) for m in snap.mortgages],
        "liens": [jsonable({"id": c.id, "source_key": c.source_key, "case_number": c.case_number, "status": c.status,
                            "classification": c.classification, "case_type": c.case_type, "parties": c.parties, "filing_date": c.filing_date,
                            "amount": c.amount, "source_url": c.source_url, "relevance_reason": c.relevance_reason, "search_term": c.search_term,
                            "retrieved_at": c.retrieved_at}) for c in snap.liens],
        "lookups": {k: jsonable({"status": lk.status, "message": lk.message, "finished_at": lk.finished_at, "evidence_id": lk.evidence_id})
                    for k, lk in snap.lookups.items()},
        "tasks": [jsonable({"id": t.id, "source_key": t.source_key, "status": t.status, "url": t.url, "search_hint": t.search_hint,
                            "instructions": t.instructions, "reason": t.reason, "last_error": t.last_error, "created_at": t.created_at})
                  for t in session.scalars(select(CaptureTask).where(CaptureTask.property_id == prop.id).order_by(CaptureTask.created_at.desc()).limit(40))],
        "sources": [a.descriptor.as_dict() | {"supports_capture": a.supports_capture} for a in connector.sources()],
        "rules": [jsonable({"rule_key": r.rule_key, "outcome": r.outcome, "message": r.message, "inputs": r.inputs, "evaluated_at": r.evaluated_at})
                  for r in session.scalars(select(RuleEvaluation).where(RuleEvaluation.property_id == prop.id, RuleEvaluation.is_current.is_(True)))],
        "overrides": [jsonable({"id": o.id, "field": o.field, "original_value": o.original_value, "override_value": o.override_value,
                                "reason": o.reason, "created_by": o.created_by, "created_at": o.created_at, "active": o.active})
                      for o in sorted(prop.overrides, key=lambda o: o.created_at, reverse=True)],
        "images": [jsonable({"id": i.id, "source_kind": i.source_kind, "attribution": i.attribution, "license_note": i.license_note,
                             "is_primary": i.is_primary, "created_at": i.created_at, "url": f"/api/images/{i.id}/content"}) for i in snap.images],
        "disclaimer": "Informational research only — not legal, title, appraisal, tax, investment or lien-clearance advice.",
    }


@router.patch("/properties/{property_id}")
def patch_property(property_id: int, body: PropertyPatch, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    prop = get_or_404(session, Property, property_id)
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(prop, key, value)
    record_audit(session, actor.name, "property.update", "property", prop.id, prop.project_id, changes)
    session.commit()
    return {"ok": True}


@router.post("/properties/{property_id}/overrides", status_code=201)
def add_override(property_id: int, body: OverrideIn, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    prop = get_or_404(session, Property, property_id)
    if body.field not in OVERRIDABLE:
        raise HTTPException(400, f"Field '{body.field}' cannot be overridden")
    if body.field == "decision_status" and body.value not in DECISIONS:
        raise HTTPException(400, f"Decision must be one of {sorted(DECISIONS)}")
    config = active_config(session)
    snap = load_snapshots(session, [prop.id], config)[prop.id]
    original = None
    if body.field not in ("tax_status_interpretation",):
        try:
            original = snap._resolve(body.field).value
        except KeyError:
            original = getattr(prop, body.field, None)
    session.execute(update(UserOverride).where(UserOverride.property_id == prop.id, UserOverride.field == body.field, UserOverride.active.is_(True))
                    .values(active=False, superseded_at=utcnow()))
    ov = UserOverride(property_id=prop.id, field=body.field, original_value=None if original is None else str(_json(original)),
                      override_value=body.value, reason=body.reason, created_by=actor.name)
    session.add(ov)
    session.flush()
    record_audit(session, actor.name, "property.override", "property", prop.id, prop.project_id,
                 {"field": body.field, "original": ov.original_value, "value": body.value, "reason": body.reason})
    evaluate_properties(session, [prop.id], actor.name)
    session.commit()
    return {"id": ov.id}


@router.post("/overrides/{override_id}/revoke")
def revoke_override(override_id: int, body: OverrideRevokeIn, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    ov = get_or_404(session, UserOverride, override_id, "Override")
    ov.active = False
    ov.superseded_at = utcnow()
    prop = session.get(Property, ov.property_id)
    record_audit(session, actor.name, "property.override_revoked", "property", ov.property_id, prop.project_id,
                 {"field": ov.field, "reason": body.reason})
    evaluate_properties(session, [ov.property_id], actor.name)
    session.commit()
    return {"ok": True}


@router.post("/properties/{property_id}/rerun")
def rerun(property_id: int, body: RerunIn, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    prop = get_or_404(session, Property, property_id)
    job = enqueue(session, "enrich_property", {"property_id": prop.id, "sources": body.sources, "force": body.force},
                  project_id=prop.project_id, property_id=prop.id, created_by=actor.name,
                  idempotency_key=f"rerun:{prop.id}:{','.join(sorted(body.sources or []))}")
    session.commit()
    return {"job_id": job.id}


@router.post("/properties/{property_id}/capture")
def capture(property_id: int, body: CaptureIn, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    prop = get_or_404(session, Property, property_id)
    try:
        outcome, preview = submit_capture(session, prop, body.source_key, body.content, actor.name, no_results=body.no_results,
                                          preview=body.preview, url=body.url)
    except (KeyError, ValueError) as exc:
        session.rollback()
        raise HTTPException(400, str(exc)) from exc
    session.commit()
    return preview | {"saved": not body.preview and outcome.status in ("success", "no_match")}


@router.post("/properties/{property_id}/capture/file")
async def capture_file(property_id: int, source_key: str = Form(...), preview: bool = Form(False), file: UploadFile = File(...),
                       session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    prop = get_or_404(session, Property, property_id)
    data = await file.read()
    try:
        outcome, result = submit_capture_file(session, prop, source_key, file.filename or "capture.html", data, actor.name, preview=preview)
    except (KeyError, ValueError) as exc:
        session.rollback()
        raise HTTPException(400, str(exc)) from exc
    session.commit()
    return result | {"saved": not preview and outcome.status in ("success", "no_match")}


@router.post("/projects/{project_id}/recorder-import")
async def recorder_bulk_import(project_id: int, source_key: str = Form(...), file: UploadFile = File(...),
                               session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    project = get_or_404(session, Project, project_id)
    try:
        result = import_recorder_workbook(session, project, source_key, file.filename or "documents.xlsx", await file.read(), actor.name)
    except (KeyError, ValueError) as exc:
        session.rollback()
        raise HTTPException(400, str(exc)) from exc
    session.commit()
    return result


@router.get("/projects/{project_id}/tasks")
def list_tasks(project_id: int, status: str = "open", source_key: str | None = None, session: Session = Depends(get_session),
               _: Actor = Depends(viewer)):
    query = select(CaptureTask, Property).join(Property, Property.id == CaptureTask.property_id).where(CaptureTask.project_id == project_id)
    if status != "all":
        query = query.where(CaptureTask.status == status)
    if source_key:
        query = query.where(CaptureTask.source_key == source_key)
    rows = session.execute(query.order_by(CaptureTask.source_key, Property.sequence, Property.id)).all()
    return [jsonable({"id": t.id, "property_id": p.id, "parcel": p.parcel_normalized, "owner_name": p.owner_name,
                      "location": p.property_address, "is_pilot": p.is_pilot, "source_key": t.source_key, "status": t.status, "url": t.url,
                      "search_hint": t.search_hint, "instructions": t.instructions, "reason": t.reason, "last_error": t.last_error,
                      "created_at": t.created_at}) for t, p in rows]


@router.post("/tasks/{task_id}/skip")
def skip_task(task_id: int, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    task = get_or_404(session, CaptureTask, task_id, "Task")
    task.status = "skipped"
    task.completed_by = actor.name
    task.completed_at = utcnow()
    record_audit(session, actor.name, "capture.skipped", "capture_task", task.id, task.project_id, {"source": task.source_key})
    session.commit()
    return {"ok": True}


@router.post("/properties/{property_id}/valuations/manual", status_code=201)
def manual_valuation(property_id: int, body: ManualValuationIn, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    prop = get_or_404(session, Property, property_id)
    try:
        record = add_manual_valuation(session, prop, body.site, body.value, body.observed_on, body.url, body.note, actor.name,
                                      active_config(session))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    evaluate_properties(session, [prop.id], actor.name)
    session.commit()
    return {"id": record.id}


@router.post("/properties/{property_id}/valuations/run")
def run_valuation(property_id: int, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    prop = get_or_404(session, Property, property_id)
    job = enqueue(session, "run_valuations", {"property_id": prop.id, "force": True}, project_id=prop.project_id, property_id=prop.id,
                  created_by=actor.name, idempotency_key=f"valuation:{prop.id}")
    session.commit()
    return {"job_id": job.id}


@router.post("/mortgages/{mortgage_id}/matches")
def mortgage_match(mortgage_id: int, body: MatchReviewIn, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    mortgage = get_or_404(session, MortgageRecord, mortgage_id, "Mortgage")
    try:
        review_match(session, mortgage, body.satisfaction_document_id, body.confirm, actor.name, body.note,
                     active_config(session)["mortgage"]["since_year"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    evaluate_properties(session, [mortgage.property_id], actor.name)
    session.commit()
    return {"status": mortgage.effective_status}


@router.post("/mortgages/{mortgage_id}/status")
def mortgage_status(mortgage_id: int, body: MortgageStatusIn, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    mortgage = get_or_404(session, MortgageRecord, mortgage_id, "Mortgage")
    try:
        set_mortgage_status(session, mortgage, body.status, actor.name, body.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    evaluate_properties(session, [mortgage.property_id], actor.name)
    session.commit()
    return {"status": mortgage.effective_status}


@router.get("/evidence/{evidence_id}")
def evidence_meta(evidence_id: int, session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    ev = get_or_404(session, SourceEvidence, evidence_id, "Evidence")
    return jsonable({c.name: getattr(ev, c.name) for c in SourceEvidence.__table__.columns}) | {"download": f"/api/evidence/{ev.id}/content"}


@router.get("/evidence/{evidence_id}/content")
def evidence_content(evidence_id: int, session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    ev = get_or_404(session, SourceEvidence, evidence_id, "Evidence")
    path = evidence_file(ev)
    if path is None:
        raise HTTPException(404, "Snapshot file missing")
    media = "text/plain; charset=utf-8" if path.suffix in (".html", ".txt") else (ev.content_type or "application/octet-stream")
    # HTML snapshots are served as plain text so captured third-party markup never executes in the app origin.
    return FileResponse(path, media_type=media, filename=path.name, headers={"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"})


@router.post("/properties/{property_id}/images", status_code=201)
async def upload_image(property_id: int, file: UploadFile = File(...), attribution: str = Form(""), license_note: str = Form(""),
                       session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    prop = get_or_404(session, Property, property_id)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(400, "Upload a JPG, PNG or WebP image")
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(400, "Image exceeds 15 MB")
    folder = get_settings().images_dir / str(prop.id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{utcnow():%Y%m%d%H%M%S}{suffix}"
    path.write_bytes(data)
    session.execute(update(PropertyImage).where(PropertyImage.property_id == prop.id).values(is_primary=False))
    image = PropertyImage(property_id=prop.id, source_kind="user_upload", stored_path=str(path), attribution=attribution or f"Uploaded by {actor.name}",
                          license_note=license_note or "User-supplied image; the uploader is responsible for having rights to use it",
                          created_by=actor.name, is_primary=True)
    session.add(image)
    session.flush()
    record_audit(session, actor.name, "image.upload", "property", prop.id, prop.project_id, {"image_id": image.id})
    session.commit()
    return {"id": image.id}


@router.post("/properties/{property_id}/images/street-view", status_code=201)
def street_view(property_id: int, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    prop = get_or_404(session, Property, property_id)
    key = get_secret(session, STREET_VIEW_SECRET)
    if not key:
        raise HTTPException(400, "Google Street View Static API key is not configured (Settings → Images)")
    snap = load_snapshots(session, [prop.id], active_config(session))[prop.id]
    address = snap.value("full_location") or f"{prop.property_address}, {prop.municipality}, PA"
    ok, message = street_view_available(address, key)
    if not ok:
        raise HTTPException(404, message)
    session.execute(update(PropertyImage).where(PropertyImage.property_id == prop.id).values(is_primary=False))
    image = PropertyImage(property_id=prop.id, source_kind="street_view", url=address, attribution=STREET_VIEW_ATTRIBUTION,
                          license_note=STREET_VIEW_LICENSE, provider_key="google_street_view", created_by=actor.name, is_primary=True)
    session.add(image)
    session.flush()
    record_audit(session, actor.name, "image.street_view", "property", prop.id, prop.project_id, {"image_id": image.id})
    session.commit()
    return {"id": image.id}


@router.get("/images/{image_id}/content")
def image_content(image_id: int, session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    image = get_or_404(session, PropertyImage, image_id, "Image")
    if image.source_kind == "street_view":
        key = get_secret(session, STREET_VIEW_SECRET)
        if not key:
            raise HTTPException(404, "Street View key no longer configured")
        content, media = fetch_street_view(image.url or "", key)
        return Response(content, media_type=media, headers={"Cache-Control": "private, max-age=600", "X-Attribution": STREET_VIEW_ATTRIBUTION})
    if not image.stored_path or not Path(image.stored_path).exists():
        raise HTTPException(404, "Image file missing")
    return FileResponse(image.stored_path)
