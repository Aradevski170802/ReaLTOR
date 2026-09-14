from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Actor, analyst, get_or_404, viewer
from app.api.schemas import CommitIn, CorrectionIn, ImportOut, ParseIn, RowPatch
from app.audit import record_audit
from app.db import get_session
from app.ingestion.fields import FIELDS
from app.ingestion.parsers import PDF_PARSERS
from app.jobs.queue import enqueue
from app.models import ExtractedRow, ImportedSaleList, Project
from app.services.imports import OverwriteConflict, commit_import, correct_value, resolve_review, run_parse, save_upload

router = APIRouter(prefix="/api", tags=["imports"])


def _row_out(row: ExtractedRow) -> dict:
    return {
        "id": row.id, "row_index": row.row_index, "page_number": row.page_number, "raw_text": row.raw_text,
        "confidence": row.confidence, "needs_review": row.needs_review, "review_reasons": row.review_reasons or [],
        "excluded": row.excluded,
        "values": {v.field: {"raw": v.raw_value, "value": v.value, "confidence": v.confidence, "corrected": v.corrected,
                             "corrected_by": v.corrected_by} for v in row.values if v.field != "tax_lines"},
        "tax_lines": next((v.value for v in row.values if v.field == "tax_lines"), None),
    }


@router.post("/projects/{project_id}/imports", status_code=201)
async def upload_sale_list(project_id: int, file: UploadFile = File(...), parse_now: bool = Query(False),
                           autopilot: bool = Query(False), session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    project = get_or_404(session, Project, project_id)
    data = await file.read()
    try:
        sale_list = save_upload(session, project, file.filename or "upload.pdf", data, actor.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    job_id = None
    if autopilot:
        # Parse now, then hand off to the autopilot job which commits and enriches everything.
        run_parse(session, sale_list, actor.name)
        job_id = enqueue(session, "autopilot", {}, project_id=project.id, created_by=actor.name,
                         idempotency_key=f"autopilot:{project.id}", max_attempts=1).id
    elif parse_now:
        run_parse(session, sale_list, actor.name)
    else:
        job_id = enqueue(session, "parse_import", {"import_id": sale_list.id}, project_id=project.id,
                         idempotency_key=f"parse:{sale_list.id}", created_by=actor.name, max_attempts=1).id
    session.commit()
    return {"import": ImportOut.model_validate(sale_list).model_dump(), "job_id": job_id, "autopilot": autopilot}


@router.get("/projects/{project_id}/imports", response_model=list[ImportOut])
def list_imports(project_id: int, session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    return session.scalars(select(ImportedSaleList).where(ImportedSaleList.project_id == project_id)
                           .order_by(ImportedSaleList.created_at.desc())).all()


@router.get("/imports/{import_id}", response_model=ImportOut)
def get_import(import_id: int, session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    return get_or_404(session, ImportedSaleList, import_id, "Import")


@router.get("/imports/{import_id}/fields")
def import_fields(import_id: int, session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    sale_list = get_or_404(session, ImportedSaleList, import_id, "Import")
    county = sale_list.project.county
    return {"fields": [{"key": f.key, "label": f.label_for(county), "essential": f.essential, "kind": f.kind} for f in FIELDS],
            "parsers": list(PDF_PARSERS)}


@router.get("/imports/{import_id}/rows")
def list_rows(import_id: int, needs_review: bool | None = None, page: int = Query(1, ge=1), page_size: int = Query(200, ge=1, le=2000),
              session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    get_or_404(session, ImportedSaleList, import_id, "Import")
    query = select(ExtractedRow).where(ExtractedRow.import_id == import_id)
    if needs_review is not None:
        query = query.where(ExtractedRow.needs_review.is_(needs_review))
    rows = session.scalars(query.order_by(ExtractedRow.row_index).offset((page - 1) * page_size).limit(page_size)).all()
    total = len(session.scalars(select(ExtractedRow.id).where(ExtractedRow.import_id == import_id)).all())
    return {"total": total, "page": page, "rows": [_row_out(r) for r in rows]}


@router.post("/imports/{import_id}/parse")
def reparse(import_id: int, body: ParseIn, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    sale_list = get_or_404(session, ImportedSaleList, import_id, "Import")
    if body.column_mapping is not None:
        sale_list.column_mapping = body.column_mapping
    try:
        result = run_parse(session, sale_list, actor.name, body.parser_key, confirm_overwrite=body.confirm_overwrite)
    except OverwriteConflict as exc:
        session.rollback()
        raise HTTPException(409, {"message": str(exc), "conflicts": exc.conflicts}) from exc
    except Exception as exc:
        session.commit()
        raise HTTPException(422, f"Parsing failed: {exc}") from exc
    session.commit()
    return {"records": len(result.records), "stats": result.stats}


@router.patch("/rows/{row_id}")
def patch_row(row_id: int, body: RowPatch, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    row = get_or_404(session, ExtractedRow, row_id, "Row")
    if body.excluded is not None:
        row.excluded = body.excluded
        record_audit(session, actor.name, "import.row_excluded" if body.excluded else "import.row_included", "extracted_row", row.id,
                     row.sale_list.project_id)
    if body.reviewed:
        resolve_review(session, row, actor.name)
    session.commit()
    return _row_out(row)


@router.post("/rows/{row_id}/corrections")
def correct_row(row_id: int, body: CorrectionIn, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    row = get_or_404(session, ExtractedRow, row_id, "Row")
    correct_value(session, row, body.field, body.value, actor.name)
    session.commit()
    return _row_out(row)


@router.post("/imports/{import_id}/commit")
def commit(import_id: int, body: CommitIn, session: Session = Depends(get_session), actor: Actor = Depends(analyst)):
    sale_list = get_or_404(session, ImportedSaleList, import_id, "Import")
    result = commit_import(session, sale_list, actor.name, confirm_overwrite=body.confirm_overwrite, row_ids=body.row_ids)
    if result.conflicts and not body.confirm_overwrite:
        session.rollback()
        raise HTTPException(409, {"message": "Some properties have user overrides that the import would change", "conflicts": result.conflicts})
    session.commit()
    return result.__dict__


@router.get("/imports/{import_id}/file")
def download_original(import_id: int, session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    sale_list = get_or_404(session, ImportedSaleList, import_id, "Import")
    return FileResponse(sale_list.stored_path, filename=sale_list.filename)


@router.get("/imports/{import_id}/pages/{page_number}")
def page_image(import_id: int, page_number: int, scale: float = Query(1.4, ge=0.5, le=3),
               session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    sale_list = get_or_404(session, ImportedSaleList, import_id, "Import")
    if sale_list.kind != "pdf":
        raise HTTPException(400, "Page images are only available for PDF imports")
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(sale_list.stored_path)
    try:
        if not 1 <= page_number <= len(doc):
            raise HTTPException(404, "Page out of range")
        image = doc[page_number - 1].render(scale=scale).to_pil()
    finally:
        doc.close()
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})
