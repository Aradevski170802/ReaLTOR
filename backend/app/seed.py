"""Offline demo data built from anonymized fixtures. Never contacts external services.

Creates one demo project per county from the anonymized PDF fixture pages, marks 8 pilot properties and, when
requested, attaches fixture-backed source records (recorded official GIS responses with owners anonymized, a captured
Tax Claim page with the owner anonymized, and synthetic portal/recorder/civil captures). Every fixture record carries
entry_method="fixture" and is displayed as an estimate labelled DEMO FIXTURE; valuations come from the SANDBOX provider.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR, get_settings
from app.connectors.base import EvidenceDraft, SourceOutcome
from app.connectors.delco.gis import QUERY as DELCO_GIS_QUERY
from app.connectors.montco.gis import QUERY as MONTCO_GIS_QUERY
from app.connectors.registry import get_connector
from app.db import session_scope
from app.enums import EntryMethod, LookupStatus
from app.models import Project, Property
from app.services.enrichment import ensure_capture_task, persist_outcome
from app.services.imports import commit_import, run_parse, save_upload
from app.services.properties import property_ref
from app.services.rules_service import active_ruleset, evaluate_properties
from app.services.valuation_service import run_valuations

FIXTURES = BACKEND_DIR / "tests" / "fixtures"
DEMO_NAMES = {
    "montco": "DEMO — Montgomery County PA (anonymized fixture)",
    "delco": "DEMO — Delaware County PA (anonymized fixture)",
}
PDFS = {"montco": "montco_upset_list_subset.pdf", "delco": "delco_sales_report_subset.pdf"}


def _evidence(content: bytes, content_type: str, url: str | None, note: str) -> list[EvidenceDraft]:
    return [EvidenceDraft(url=url, content=content, content_type=content_type, entry_method=EntryMethod.FIXTURE,
                          request_summary="demo seed", notes=note)]


def _persist(session: Session, prop: Property, source_key: str, outcome: SourceOutcome) -> None:
    adapter = get_connector(prop.county).source(source_key)
    persist_outcome(session, prop, adapter, outcome, entry_method=EntryMethod.FIXTURE, actor="seed")


def fill_delco_template(values: dict) -> str:
    template = (FIXTURES / "capture" / "delco_datalet_template.html").read_text(encoding="utf-8")
    for key, value in values.items():
        template = template.replace("{" + key + "}", str(value))
    return template


def delco_portal_values(prop: Property, index: int, gis_acres: float | None) -> dict:
    ground = (prop.listed_description or "").upper().startswith(("GRD", "VAC"))
    styles = ["10 - TWIN", "06 - CONVENTIONAL", "08 - CAPE", "12 - ROW INTERIOR", "06 - CONVENTIONAL"]
    base_area = [1148, 2172, 1531, 820, 1744][index % 5]
    if ground:
        detail_section = "Land"
        detail_rows = f'  <tr><td class="DataletSideHeading">Acres</td><td class="DataletData">{gis_acres or 0.12:.4f}</td></tr>'
        ptype = "06 - Ground"
    else:
        detail_section = "Residential"
        detail_rows = "\n".join(
            f'  <tr><td class="DataletSideHeading">{label}</td><td class="DataletData">{value}</td></tr>'
            for label, value in (("Style", styles[index % 5]), ("Base Area", f"{base_area:,}"), ("Year Built", 1918 + index * 7),
                                 ("Bed Rooms", 2 + index % 3), ("Full Baths", 1 + index % 2), ("Half Baths", index % 2),
                                 ("Acres", f"{gis_acres or 0.1:.4f}"))
        )
        ptype = "01 - Taxable Residential"
    status = {5: "Paid - Removed from Sale", 6: ""}.get(index, "Listed for Upset Sale")
    due = prop.listed_amount or 0
    return {
        "pin": prop.parcel_search_key, "site_location": prop.property_address or "", "legal": f"{prop.listed_description or ''} {prop.listed_lot_size or ''}".strip(),
        "school": "SAMPLE SCHOOL DISTRICT", "ptype": ptype, "assessment": f"${(prop.listed_assessment or 0):,.0f}",
        "detail_section": detail_section, "detail_rows": detail_rows, "sale_date": "09-23-2016", "sale_price": f"${120000 + index * 23500:,}",
        "status": status, "face_2024": f"${due * 0.8:,.2f}", "due_2024": f"${due:,.2f}", "face_2025": f"${due * 0.35:,.2f}",
        "due_2025": f"${due * 0.4:,.2f}", "total_due": f"${due * 1.4:,.2f}", "face_2026": f"${due * 0.3:,.2f}", "balance_2026": f"${due * 0.3:,.2f}",
    }


def _seed_montco(session: Session, pilot: list[Property]) -> None:
    connector = get_connector("montco")
    resolved = (FIXTURES / "http" / "montco_taxclaim_detail.html").read_text(encoding="utf-8")
    open2024 = (FIXTURES / "http" / "montco_taxclaim_detail_open2024.html").read_text(encoding="utf-8")
    recorder = (FIXTURES / "capture" / "recorder_results_synthetic.html").read_text(encoding="utf-8")
    civil = (FIXTURES / "capture" / "montco_civil_results_synthetic.html").read_text(encoding="utf-8")
    gis_adapter = connector.source("montco.assessment_gis")
    tax_adapter = connector.source("montco.tax_claim")
    for i, prop in enumerate(pilot):
        digits = prop.parcel_search_key
        gis_file = FIXTURES / "http" / f"montco_gis_{digits}.json"
        if gis_file.exists():
            raw = gis_file.read_bytes()
            outcome = gis_adapter.parse_response(json.loads(raw), digits)
            outcome.evidence = _evidence(raw, "application/json", MONTCO_GIS_QUERY, "Recorded official GIS response; owner anonymized")
            _persist(session, prop, "montco.assessment_gis", outcome)
        html = resolved if i % 4 == 1 else open2024
        payload = tax_adapter.parse_detail(html)
        _persist(session, prop, "montco.tax_claim", SourceOutcome(
            LookupStatus.SUCCESS, payload.tax_sale_status or "", payload=payload,
            evidence=_evidence(html.encode(), "text/html", "https://webapp02.montcopa.org/taxclaim/ParcelInfo.aspx",
                               "Captured page for a different parcel, owner anonymized; amounts illustrative only")))
        recorder_adapter = connector.source("montco.recorder")
        civil_adapter = connector.source("montco.civil")
        if i < 4:
            outcome = recorder_adapter.parse_capture(property_ref(prop), recorder)
            outcome.evidence = _evidence(recorder.encode(), "text/html", None, "Synthetic recorder results")
            _persist(session, prop, "montco.recorder", outcome)
        else:
            ensure_capture_task(session, prop, recorder_adapter, "enrichment")
        if i < 3:
            outcome = civil_adapter.parse_capture(property_ref(prop), civil)
            outcome.evidence = _evidence(civil.encode(), "text/html", None, "Synthetic civil case results")
            _persist(session, prop, "montco.civil", outcome)
        else:
            ensure_capture_task(session, prop, civil_adapter, "enrichment")


def _seed_delco(session: Session, pilot: list[Property]) -> None:
    connector = get_connector("delco")
    recorder = (FIXTURES / "capture" / "recorder_results_synthetic.html").read_text(encoding="utf-8")
    civil = (FIXTURES / "capture" / "delco_civil_results_synthetic.txt").read_text(encoding="utf-8")
    gis_adapter = connector.source("delco.parcels_gis")
    portal = connector.source("delco.assessment_portal")
    for i, prop in enumerate(pilot):
        parid = int(prop.parcel_search_key)
        gis_file = FIXTURES / "http" / f"delco_gis_{parid}.json"
        acres = None
        if gis_file.exists():
            raw = gis_file.read_bytes()
            outcome = gis_adapter.parse_response(json.loads(raw), parid)
            outcome.evidence = _evidence(raw, "application/json", DELCO_GIS_QUERY, "Recorded official GIS response")
            acres = outcome.payload.acres if outcome.payload else None
            _persist(session, prop, "delco.parcels_gis", outcome)
        html = fill_delco_template(delco_portal_values(prop, i, acres))
        outcome = portal.parse_capture(property_ref(prop), html)
        outcome.evidence = _evidence(html.encode(), "text/html", None, "Synthetic iasWorld-style assessment page")
        _persist(session, prop, "delco.assessment_portal", outcome)
        recorder_adapter = connector.source("delco.recorder_countyweb")
        civil_adapter = connector.source("delco.civil")
        if i < 3:
            outcome = recorder_adapter.parse_capture(property_ref(prop), recorder)
            outcome.evidence = _evidence(recorder.encode(), "text/html", None, "Synthetic recorder results")
            _persist(session, prop, "delco.recorder_countyweb", outcome)
        else:
            ensure_capture_task(session, prop, recorder_adapter, "enrichment")
        if i < 2:
            outcome = civil_adapter.parse_capture(property_ref(prop), civil)
            outcome.evidence = _evidence(civil.encode(), "text/plain", None, "Synthetic civil party search results")
            _persist(session, prop, "delco.civil", outcome)
        else:
            ensure_capture_task(session, prop, civil_adapter, "enrichment")


def seed_demo(with_enrichment: bool = True) -> dict:
    get_settings().ensure_dirs()
    results: dict[str, dict] = {}
    with session_scope() as session:
        config = active_ruleset(session).config
        for county in ("montco", "delco"):
            existing = session.scalar(select(Project).where(Project.name == DEMO_NAMES[county]))
            if existing is not None:
                results[county] = {"project_id": existing.id, "created": False}
                continue
            project = Project(name=DEMO_NAMES[county], county=county, is_demo=True, created_by="seed",
                              description="Anonymized pilot built from fixture pages of the county sale list. Source records are "
                                          "DEMO FIXTURES and valuations are SANDBOX values — not real data.")
            session.add(project)
            session.flush()
            pdf: Path = FIXTURES / "pdf" / PDFS[county]
            sale_list = save_upload(session, project, pdf.name, pdf.read_bytes(), "seed")
            run_parse(session, sale_list, "seed")
            commit_import(session, sale_list, "seed")
            props = session.scalars(select(Property).where(Property.project_id == project.id).order_by(Property.sequence, Property.id)).all()
            pilot = list(props[:8])
            for prop in pilot:
                prop.is_pilot = True
            session.flush()
            if with_enrichment:
                (_seed_montco if county == "montco" else _seed_delco)(session, pilot)
                for prop in pilot:
                    run_valuations(session, prop, config, actor="seed")
            evaluate_properties(session, [p.id for p in props], "seed")
            results[county] = {"project_id": project.id, "properties": len(props), "pilot": [p.id for p in pilot], "created": True}
    return results
