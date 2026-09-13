"""Source catalog & policies, valuation providers, encrypted secrets, workbook templates, metadata."""

from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import __version__
from app.api.deps import Actor, admin, viewer
from app.api.schemas import ProviderConfigIn, ProviderTestIn, SecretIn, SourcePolicyIn
from app.audit import record_audit
from app.config import get_settings
from app.connectors.registry import all_sources, connectors, get_source
from app.db import get_session
from app.enums import COUNTY_LABELS, DataQuality, Decision, LookupStatus, MortgageStatus
from app.export.template_inspector import inspect_workbook
from app.models import Property, SourceLookup, SourcePolicy, WorkbookTemplate
from app.security.secrets import delete_secret, secret_status, set_secret
from app.services.enrichment import jsonable
from app.services.policies import acknowledge_terms, get_policy, revoke_terms, set_enabled
from app.services.valuation_service import provider_config, provider_secrets
from app.valuation.base import ValuationSubject
from app.valuation.images import STREET_VIEW_DESCRIPTOR, STREET_VIEW_SECRET
from app.valuation.providers import MANUAL_SITES, PROVIDERS

router = APIRouter(prefix="/api", tags=["settings"])
ALLOWED_SECRETS = {name for p in PROVIDERS.values() for name in p.descriptor.secret_names} | {STREET_VIEW_SECRET}
DISCLAIMER = ("Informational research only — not legal, title, appraisal, tax, investment or lien-clearance advice. "
              "Verify all information with official records and qualified professionals.")


@router.get("/meta")
def meta(_: Actor = Depends(viewer)):
    settings = get_settings()
    return {
        "version": __version__, "auth_mode": settings.auth_mode, "demo_mode": settings.demo_mode, "disclaimer": DISCLAIMER,
        "counties": [c.describe() | {"label": COUNTY_LABELS[c.county]} for c in connectors().values()],
        "decisions": [d.value for d in Decision], "mortgage_statuses": [m.value for m in MortgageStatus],
        "lookup_statuses": [s.value for s in LookupStatus], "quality": [q.value for q in DataQuality], "manual_sites": MANUAL_SITES,
    }


@router.get("/health")
def health():
    return {"status": "ok", "version": __version__}


@router.get("/sources")
def list_sources(county: str | None = None, session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    out = []
    for adapter in all_sources():
        d = adapter.descriptor
        if county and d.county != county:
            continue
        policy = session.get(SourcePolicy, d.key)
        out.append(d.as_dict() | {
            "supports_capture": adapter.supports_capture,
            "policy": {"enabled": policy.enabled if policy else True,
                       "terms_acknowledged_by": policy.terms_acknowledged_by if policy else None,
                       "terms_acknowledged_at": policy.terms_acknowledged_at.isoformat() if policy and policy.terms_acknowledged_at else None,
                       "notes": policy.notes if policy else None},
        })
    return out


@router.get("/sources/health")
def source_health(project_id: int | None = None, session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    query = select(SourceLookup.source_key, SourceLookup.status, func.count(), func.max(SourceLookup.finished_at)).where(SourceLookup.is_current.is_(True))
    if project_id:
        query = query.join(Property, Property.id == SourceLookup.property_id).where(Property.project_id == project_id)
    health: dict[str, dict] = {}
    for key, status, count, last in session.execute(query.group_by(SourceLookup.source_key, SourceLookup.status)):
        entry = health.setdefault(key, {"counts": {}, "last_checked": None})
        entry["counts"][status] = count
        if last and (entry["last_checked"] is None or last.isoformat() > entry["last_checked"]):
            entry["last_checked"] = last.isoformat()
    return health


@router.patch("/sources/{source_key}")
def update_source(source_key: str, body: SourcePolicyIn, session: Session = Depends(get_session), actor: Actor = Depends(admin)):
    try:
        get_source(source_key)
    except (KeyError, ValueError) as exc:
        raise HTTPException(404, f"Unknown source {source_key}") from exc
    if body.enabled is not None:
        set_enabled(session, source_key, body.enabled, actor.name)
    if body.acknowledge_terms is True:
        acknowledge_terms(session, source_key, actor.name, body.note)
    elif body.acknowledge_terms is False:
        revoke_terms(session, source_key, actor.name)
    policy = get_policy(session, source_key)
    session.commit()
    return jsonable({"source_key": policy.source_key, "enabled": policy.enabled, "terms_acknowledged_by": policy.terms_acknowledged_by,
                     "terms_acknowledged_at": policy.terms_acknowledged_at})


@router.get("/providers")
def list_providers(session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    out = []
    for key, provider in PROVIDERS.items():
        cfg = provider_config(session, key)
        out.append(provider.descriptor.as_dict() | {
            "enabled": cfg.enabled or (provider.descriptor.is_sandbox and get_settings().demo_mode),
            "settings": cfg.settings,
            "secrets": [secret_status(session, name) for name in provider.descriptor.secret_names],
            "status": "sandbox" if provider.descriptor.is_sandbox else (
                "ready" if cfg.enabled and not provider.missing_secrets(provider_secrets(session, key))
                else "not_configured" if provider.missing_secrets(provider_secrets(session, key)) else "disabled"),
        })
    session.commit()
    images = STREET_VIEW_DESCRIPTOR | {"secrets": [secret_status(session, STREET_VIEW_SECRET)]}
    return {"valuation": out, "images": [images]}


@router.put("/providers/{provider_key}")
def update_provider(provider_key: str, body: ProviderConfigIn, session: Session = Depends(get_session), actor: Actor = Depends(admin)):
    if provider_key not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    provider = PROVIDERS[provider_key]
    if provider.descriptor.is_sandbox:
        raise HTTPException(400, "The sandbox provider is controlled by demo mode / demo projects")
    cfg = provider_config(session, provider_key)
    if body.enabled is True and provider.missing_secrets(provider_secrets(session, provider_key)):
        raise HTTPException(400, "Save the API key before enabling this provider")
    if body.enabled is not None:
        cfg.enabled = body.enabled
    if body.settings is not None:
        cfg.settings = (cfg.settings or {}) | body.settings
    cfg.updated_by = actor.name
    record_audit(session, actor.name, "provider.update", "provider_config", provider_key, None,
                 {"enabled": cfg.enabled, "settings": cfg.settings})
    session.commit()
    return {"provider_key": provider_key, "enabled": cfg.enabled, "settings": cfg.settings}


@router.post("/providers/{provider_key}/test")
def test_provider(provider_key: str, body: ProviderTestIn, session: Session = Depends(get_session), actor: Actor = Depends(admin)):
    if provider_key not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    provider = PROVIDERS[provider_key]
    subject = ValuationSubject(property_id=0, county="test", parcel="test", street=body.street, city=body.city, zip_code=body.zip_code,
                               category=body.category)
    try:
        result = provider.estimate(subject, provider_secrets(session, provider_key), provider_config(session, provider_key).settings or {})
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"{type(exc).__name__} contacting provider") from exc
    record_audit(session, actor.name, "provider.test", "provider_config", provider_key, None,
                 {"coverage": result.coverage_status, "point": result.point})
    session.commit()
    data = jsonable({k: v for k, v in result.__dict__.items() if k not in ("evidence",)})
    return data


@router.put("/secrets/{name}")
def put_secret(name: str, body: SecretIn, session: Session = Depends(get_session), actor: Actor = Depends(admin)):
    if name not in ALLOWED_SECRETS:
        raise HTTPException(400, f"Unknown secret name. Allowed: {sorted(ALLOWED_SECRETS)}")
    set_secret(session, name, body.value.strip(), actor.name)
    record_audit(session, actor.name, "secret.set", "secret", name, None, {"name": name})
    session.commit()
    return secret_status(session, name)


@router.delete("/secrets/{name}")
def remove_secret(name: str, session: Session = Depends(get_session), actor: Actor = Depends(admin)):
    if not delete_secret(session, name):
        raise HTTPException(404, "Secret not configured")
    for key, provider in PROVIDERS.items():
        if name in provider.descriptor.secret_names:
            provider_config(session, key).enabled = False
    record_audit(session, actor.name, "secret.delete", "secret", name, None, {"name": name})
    session.commit()
    return {"ok": True}


@router.get("/templates")
def list_templates(session: Session = Depends(get_session), _: Actor = Depends(viewer)):
    return [jsonable({"id": t.id, "name": t.name, "county": t.county, "filename": t.filename, "created_at": t.created_at,
                      "sheets": [{"title": s["title"], "columns": len(s["columns"])} for s in t.spec.get("sheets", [])]})
            for t in session.scalars(select(WorkbookTemplate).order_by(WorkbookTemplate.id.desc()))]


@router.post("/templates", status_code=201)
async def upload_template(county: str, file: UploadFile = File(...), session: Session = Depends(get_session), actor: Actor = Depends(admin)):
    if county not in connectors():
        raise HTTPException(400, "Unknown county")
    data = await file.read()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "template.xlsx"
        path.write_bytes(data)
        try:
            spec = inspect_workbook(path)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"Could not read workbook: {exc}") from exc
    for sheet in spec["sheets"]:
        sheet.pop("text_rows", None)
    template = WorkbookTemplate(name=Path(file.filename or "template.xlsx").stem, county=county, filename=file.filename, spec=spec,
                                created_by=actor.name)
    session.add(template)
    session.flush()
    record_audit(session, actor.name, "template.upload", "workbook_template", template.id, None,
                 {"county": county, "sheets": [s["title"] for s in spec["sheets"]]})
    session.commit()
    return {"id": template.id, "sheets": [{"title": s["title"], "columns": [c["header"] for c in s["columns"]]} for s in spec["sheets"]]}
