"""Run valuation providers, record manual observations, derive the selected market valuation."""

from __future__ import annotations

from datetime import date, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.config import get_settings
from app.db import utcnow
from app.enums import CoverageStatus, EntryMethod, EstimateType
from app.models import Property, ProviderConfig, ValuationRecord, ValuationSelection
from app.security.secrets import get_secret
from app.services.evidence import store_evidence
from app.services.properties import supersede
from app.services.snapshot import load_snapshots
from app.valuation.base import ValuationSubject
from app.valuation.providers import MANUAL_SITES, PROVIDERS
from app.valuation.selector import EstimateView, select_valuation


def provider_config(session: Session, key: str) -> ProviderConfig:
    cfg = session.get(ProviderConfig, key)
    if cfg is None:
        descriptor = PROVIDERS[key].descriptor
        # Keyless, non-sandbox providers (the local estimate) are on by default; keyed providers start disabled.
        default_enabled = not descriptor.secret_names and not descriptor.is_sandbox
        cfg = ProviderConfig(provider_key=key, enabled=default_enabled, settings=dict(descriptor.default_settings))
        session.add(cfg)
        session.flush()
    return cfg


def provider_secrets(session: Session, key: str) -> dict[str, str]:
    out = {}
    for name in PROVIDERS[key].descriptor.secret_names:
        value = get_secret(session, name)
        if value:
            out[name] = value
    return out


def active_providers(session: Session, is_demo: bool) -> list[str]:
    keys = []
    for key, provider in PROVIDERS.items():
        d = provider.descriptor
        if d.is_sandbox:
            if is_demo or get_settings().demo_mode:
                keys.append(key)
            continue
        cfg = session.get(ProviderConfig, key)
        if not d.secret_names:
            # Keyless providers (the local assessment/sale estimate) run for every property unless explicitly disabled.
            if cfg is None or cfg.enabled:
                keys.append(key)
            continue
        # A keyed provider runs once its key is configured, unless explicitly disabled. Verified integrations
        # (ATTOM, RentCast) auto-activate when a key is present; unverified ones (Zillow Bridge) need an explicit enable.
        if provider.missing_secrets(provider_secrets(session, key)):
            continue
        if (cfg and cfg.enabled) or (cfg is None and d.verified_integration):
            keys.append(key)
    return keys


def build_subject(session: Session, prop: Property, config: dict) -> ValuationSubject:
    snap = load_snapshots(session, [prop.id], config)[prop.id]
    zip_code = None
    gis = snap.assessments.get("montco.assessment_gis")
    if gis and gis.raw_codes and gis.raw_codes.get("LOC_ZIP"):
        zip_code = gis.raw_codes["LOC_ZIP"][:5]
    baths = snap.value("full_baths")
    half = snap.value("half_baths")
    from app.common.parsing import parse_date

    last_sale = snap.value("last_sale_date")
    return ValuationSubject(
        property_id=prop.id, county=prop.county, parcel=prop.parcel_normalized,
        street=snap.value("site_address") or prop.property_address,
        city=(prop.municipality or "").title() or None, zip_code=zip_code, category=snap.value("category"),
        bedrooms=snap.value("bedrooms"), bathrooms=(baths or 0) + 0.5 * (half or 0) if baths else None,
        square_feet=snap.value("living_area_sqft") or snap.value("base_area_sqft"),
        assessed_value=snap.value("assessed_value"),
        last_sale_price=snap.value("last_sale_price"),
        last_sale_date=last_sale if not isinstance(last_sale, str) else parse_date(last_sale),
    )


def run_valuations(session: Session, prop: Property, config: dict, actor: str = "system", force: bool = False,
                   provider_keys: list[str] | None = None) -> list[ValuationRecord]:
    is_demo = prop.project.is_demo
    keys = provider_keys or active_providers(session, is_demo)
    records = []
    subject = None
    for key in keys:
        provider = PROVIDERS[key]
        if provider.descriptor.is_sandbox and not (is_demo or get_settings().demo_mode):
            continue
        existing = session.scalar(select(ValuationRecord).where(ValuationRecord.property_id == prop.id, ValuationRecord.provider_key == key,
                                                                ValuationRecord.is_current.is_(True)))
        if existing and not force and existing.coverage_status == CoverageStatus.MATCHED and utcnow() - existing.response_at < timedelta(days=7):
            continue
        subject = subject or build_subject(session, prop, config)
        cfg = session.get(ProviderConfig, key)
        try:
            result = provider.estimate(subject, provider_secrets(session, key), (cfg.settings if cfg else None) or {})
        except httpx.HTTPError as exc:
            from app.valuation.base import ValuationResult

            result = ValuationResult(CoverageStatus.ERROR, notes=f"{type(exc).__name__} contacting {provider.descriptor.name}")
        evidence = None
        if result.evidence is not None:
            evidence = store_evidence(session, result.evidence, source_key=f"valuation.{key}", source_name=provider.descriptor.name,
                                      access_method="official_api", property_id=prop.id, project_id=prop.project_id,
                                      parser_version="valuation/1")
        supersede(session, ValuationRecord, prop.id, provider_key=key)
        record = ValuationRecord(
            property_id=prop.id, provider_key=key, provider_name=provider.descriptor.name, product=provider.descriptor.product,
            coverage_status=result.coverage_status, provider_property_id=result.provider_property_id,
            estimate_type=result.estimate_type, low=result.low, high=result.high, point=result.point, currency=result.currency,
            estimate_date=result.estimate_date, source_url=result.source_url, response_at=utcnow(),
            evidence_id=evidence.id if evidence else None, confidence=result.confidence, confidence_label=result.confidence_label,
            match_score=result.match_score, entry_method=EntryMethod.FIXTURE if provider.descriptor.is_sandbox else EntryMethod.AUTOMATED,
            is_sandbox=provider.descriptor.is_sandbox,
            notes="; ".join(x for x in (result.notes, f"matched address: {result.matched_address}" if result.matched_address else None) if x) or None,
        )
        session.add(record)
        records.append(record)
        record_audit(session, actor, "valuation.run", "property", prop.id, prop.project_id,
                     {"provider": key, "coverage": result.coverage_status, "point": result.point})
    session.flush()
    recompute_selection(session, prop, config)
    return records


def add_manual_valuation(session: Session, prop: Property, site: str, value: float, observed_on: date | None, url: str | None,
                         note: str | None, actor: str, config: dict) -> ValuationRecord:
    if site not in MANUAL_SITES:
        raise ValueError(f"Unknown site '{site}'. Use one of {sorted(MANUAL_SITES)}")
    if value <= 0:
        raise ValueError("Value must be positive")
    key = f"manual:{site}"
    supersede(session, ValuationRecord, prop.id, provider_key=key)
    record = ValuationRecord(
        property_id=prop.id, provider_key=key, provider_name=f"{MANUAL_SITES[site]} — entered by {actor}",
        product="Manual observation", coverage_status=CoverageStatus.MATCHED, estimate_type=EstimateType.MANUAL_OBSERVATION,
        point=value, estimate_date=observed_on or date.today(), source_url=url, response_at=utcnow(),
        entry_method=EntryMethod.MANUAL, notes=note,
    )
    session.add(record)
    session.flush()
    record_audit(session, actor, "valuation.manual", "property", prop.id, prop.project_id,
                 {"site": site, "value": value, "observed_on": str(observed_on) if observed_on else None, "url": url, "note": note})
    recompute_selection(session, prop, config)
    return record


def recompute_selection(session: Session, prop: Property, config: dict) -> ValuationSelection:
    records = session.scalars(select(ValuationRecord).where(ValuationRecord.property_id == prop.id, ValuationRecord.is_current.is_(True))).all()
    views = [EstimateView(r.id, r.provider_key.split(":", 1)[-1] if r.provider_key.startswith("manual:") else r.provider_key,
                          r.provider_name, r.coverage_status, r.estimate_type, r.point, r.low, r.high, r.confidence, r.match_score,
                          r.estimate_date, r.is_sandbox, r.entry_method) for r in records]
    is_demo = prop.project.is_demo or get_settings().demo_mode
    selection = select_valuation(views, config["valuation_selection"], is_demo, active_providers(session, is_demo))
    supersede(session, ValuationSelection, prop.id)
    row = ValuationSelection(property_id=prop.id, valuation_id=selection.valuation_id, value=selection.value, method=selection.method,
                             provider_key=selection.provider_key, estimate_type=selection.estimate_type,
                             explanation=selection.explanation, computed_at=utcnow())
    session.add(row)
    session.flush()
    return row
