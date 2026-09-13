"""Property snapshot read model.

Bulk-loads every current record for a set of properties and resolves each grid/export/rule field into a
Cell carrying the value plus its provenance (source, URL, retrieval time, raw value, quality, notes).
The grid, the XLSX export and the rules engine all read through this module so they never disagree.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.common.parsing import parse_date, parse_float
from app.connectors.registry import all_sources
from app.enums import DataQuality, EntryMethod, LookupStatus, MortgageStatus, TaxStatusCategory
from app.models import (
    AssessmentRecord,
    CaptureTask,
    CivilLienCase,
    FieldProvenance,
    MortgageRecord,
    Property,
    PropertyImage,
    RecorderDocument,
    RuleEvaluation,
    SaleListTaxLine,
    SourceEvidence,
    SourceLookup,
    TaxClaimRecord,
    TaxClaimYear,
    UserOverride,
    ValuationRecord,
    ValuationSelection,
)
from app.rules.classification import classify_delco
from app.services.properties import active_overrides
from app.valuation.selector import PROVIDER_LABELS

SALE_LIST = "sale_list"
ASSESSMENT_PRIORITY = {
    "montco": ["montco.assessment_portal", "montco.assessment_gis"],
    "delco": ["delco.assessment_portal", "delco.parcels_gis"],
}
TAX_SOURCE = {"montco": "montco.tax_claim", "delco": "delco.assessment_portal"}
RECORDER_SOURCES = {"montco.recorder", "delco.recorder_publicsearch", "delco.recorder_countyweb"}
CIVIL_SOURCES = {"montco.civil", "delco.civil"}
ESTIMATED = {("delco.parcels_gis", "acres")}
SEARCH_DONE = {LookupStatus.SUCCESS, LookupStatus.NO_MATCH}

FIELD_KINDS: dict[str, str] = {
    **{k: "money" for k in (
        "listed_amount", "listed_assessment", "assessed_value", "last_sale_price", "tax_2023_total", "tax_2024_total",
        "tax_2024_balance", "tax_2025_total", "tax_2025_balance", "delinquent_total", "tax_2024_due", "tax_2025_due",
        "val_homes_com", "val_zillow", "val_realtor", "val_redfin", "val_trulia", "val_attom", "val_rentcast", "val_sandbox",
        "selected_valuation", "lowest_mortgage_amount")},
    **{k: "int" for k in (
        "year_built", "total_rooms", "bedrooms", "full_baths", "half_baths", "total_living_units", "commercial_units",
        "commercial_year_built", "source_page", "mortgage_count", "unsatisfied_mortgages", "satisfied_mortgages",
        "review_mortgages", "open_lien_cases")},
    **{k: "number" for k in ("living_area_sqft", "base_area_sqft", "gross_building_area", "commercial_living_area", "acres",
                             "extraction_confidence")},
    **{k: "date" for k in ("sale_date", "last_sale_date")},
    **{k: "datetime" for k in ("tax_checked_at", "last_enriched_at")},
}

_SOURCE_NAMES: dict[str, str] | None = None


def source_name(key: str | None) -> str | None:
    global _SOURCE_NAMES
    if key is None:
        return None
    if _SOURCE_NAMES is None:
        _SOURCE_NAMES = {s.descriptor.key: s.descriptor.name for s in all_sources()}
        _SOURCE_NAMES.update({SALE_LIST: "County sale list (PDF/CSV import)", "user_override": "User override",
                              "decision_engine": "Screening rules", "valuation_selection": "Selected market valuation"})
    if key.startswith("manual:"):
        return f"Manual observation ({key.split(':', 1)[1]})"
    if key.startswith("valuation:"):
        return PROVIDER_LABELS.get(key.split(":", 1)[1], key)
    return _SOURCE_NAMES.get(key, key)


def coerce(kind: str, value: Any) -> Any:
    if value is None or value == "":
        return None
    if kind in ("money", "number"):
        return parse_float(str(value).replace("$", "")) if not isinstance(value, int | float) else float(value)
    if kind == "int":
        f = parse_float(str(value)) if not isinstance(value, int | float) else value
        return None if f is None else int(f)
    if kind == "date":
        return value if isinstance(value, date) else parse_date(str(value))
    return value


@dataclass
class Cell:
    value: Any = None
    source_key: str | None = None
    source_name: str | None = None
    url: str | None = None
    retrieved_at: datetime | None = None
    quality: str = DataQuality.UNKNOWN
    raw: str | None = None
    notes: str | None = None
    evidence_id: int | None = None
    confidence: float | None = None

    def as_dict(self) -> dict[str, Any]:
        value = self.value
        if isinstance(value, datetime | date):
            value = value.isoformat()
        return {
            "value": value, "source_key": self.source_key, "source_name": self.source_name, "url": self.url,
            "retrieved_at": self.retrieved_at.isoformat() if self.retrieved_at else None, "quality": self.quality,
            "raw": self.raw, "notes": self.notes, "evidence_id": self.evidence_id, "confidence": self.confidence,
        }


class PropertySnapshot:
    def __init__(self, prop: Property, config: dict, now: datetime | None = None):
        self.prop = prop
        self.config = config
        self.now = now or datetime.now(UTC)
        self.assessments: dict[str, AssessmentRecord] = {}
        self.tax_claims: dict[str, TaxClaimRecord] = {}
        self.tax_lines: list[SaleListTaxLine] = []
        self.valuations: list[ValuationRecord] = []
        self.selection: ValuationSelection | None = None
        self.mortgages: list[MortgageRecord] = []
        self.documents: dict[int, RecorderDocument] = {}
        self.liens: list[CivilLienCase] = []
        self.lookups: dict[str, SourceLookup] = {}
        self.provenance: dict[tuple[str, str], FieldProvenance] = {}
        self.images: list[PropertyImage] = []
        self.open_tasks: list[CaptureTask] = []
        self.evidence: dict[int, SourceEvidence] = {}
        self.rule_results: list[RuleEvaluation] = []
        self.overrides: dict[str, UserOverride] = active_overrides(prop)
        self._cache: dict[str, Cell] = {}

    # ---------------------------------------------------------------- helpers
    def _quality(self, source_key: str, field: str, entry_method: str | None, retrieved_at: datetime | None) -> str:
        if entry_method == EntryMethod.MANUAL:
            return DataQuality.MANUAL
        if entry_method == EntryMethod.FIXTURE:
            return DataQuality.ESTIMATED  # demo fixture data is never presented as verified
        if (source_key, field) in ESTIMATED:
            return DataQuality.ESTIMATED
        if source_key == TAX_SOURCE.get(self.prop.county) and field.startswith(("tax_", "delinquent")) and retrieved_at:
            if self.now - retrieved_at > timedelta(hours=self.config["tax"]["stale_after_hours"]):
                return DataQuality.STALE
        return DataQuality.VERIFIED

    def _cell(self, field: str, value: Any, source_key: str, retrieved_at: datetime | None, evidence_id: int | None,
              entry_method: str | None = None, quality: str | None = None, notes: str | None = None) -> Cell:
        prov = self.provenance.get((field, source_key))
        ev = self.evidence.get(evidence_id) if evidence_id else None
        note = notes or (prov.notes if prov else None)
        if entry_method == EntryMethod.FIXTURE:
            note = "DEMO FIXTURE (not live county data)" + (f"; {note}" if note else "")
        return Cell(
            value=value, source_key=source_key, source_name=source_name(source_key), url=ev.url if ev else None,
            retrieved_at=retrieved_at, quality=quality or self._quality(source_key, field, entry_method, retrieved_at),
            raw=prov.raw_value if prov else None, notes=note, evidence_id=evidence_id,
            confidence=prov.confidence if prov else None,
        )

    def _missing(self, field: str, source_keys: list[str]) -> Cell:
        notes = []
        for key in source_keys:
            lk = self.lookups.get(key)
            if lk:
                notes.append(f"{source_name(key)}: {lk.status}{' — ' + lk.message if lk.message else ''}")
            else:
                notes.append(f"{source_name(key)}: not run")
        return Cell(quality=DataQuality.UNKNOWN, notes="; ".join(notes) or None)

    def _sale_list(self, field: str, value: Any) -> Cell:
        return Cell(value=value, source_key=SALE_LIST, source_name=source_name(SALE_LIST), retrieved_at=self.prop.created_at,
                    quality=DataQuality.VERIFIED if value is not None else DataQuality.UNKNOWN,
                    notes=f"PDF page {self.prop.source_page}" if self.prop.source_page else None,
                    confidence=self.prop.extraction_confidence)

    def assessment(self, attr: str, field: str | None = None) -> Cell:
        field = field or attr
        priority = ASSESSMENT_PRIORITY[self.prop.county]
        for key in priority:
            rec = self.assessments.get(key)
            if rec is not None and getattr(rec, attr) not in (None, ""):
                return self._cell(field, getattr(rec, attr), key, rec.retrieved_at, rec.evidence_id, rec.entry_method)
        return self._missing(field, priority)

    @property
    def tax_claim(self) -> TaxClaimRecord | None:
        return self.tax_claims.get(TAX_SOURCE[self.prop.county])

    def tax_year(self, year: int, kind: str) -> TaxClaimYear | None:
        rec = self.tax_claim
        if rec is None:
            return None
        return next((y for y in rec.years if y.year == year and y.kind == kind), None)

    def _tax_cell(self, field: str, year: int, kind: str, attr: str) -> Cell:
        rec = self.tax_claim
        if rec is None:
            return self._missing(field, [TAX_SOURCE[self.prop.county]])
        y = self.tax_year(year, kind)
        if y is None:
            return Cell(quality=DataQuality.VERIFIED, source_key=rec.source_key, source_name=source_name(rec.source_key),
                        retrieved_at=rec.retrieved_at, notes=f"No {year} {kind} year on the record")
        return self._cell(field, getattr(y, attr), rec.source_key, rec.retrieved_at, rec.evidence_id, rec.entry_method)

    @property
    def recorder_searched(self) -> bool:
        return any(self.lookups.get(k) and self.lookups[k].status in SEARCH_DONE for k in RECORDER_SOURCES)

    @property
    def lien_searched(self) -> bool:
        return any(self.lookups.get(k) and self.lookups[k].status in SEARCH_DONE for k in CIVIL_SOURCES)

    def mortgages_since(self) -> list[MortgageRecord]:
        since = date(self.config["mortgage"]["since_year"], 1, 1)
        return [m for m in self.mortgages if m.recorded_date is None or m.recorded_date >= since]

    def valuation_record(self, provider_key: str) -> ValuationRecord | None:
        candidates = [v for v in self.valuations if v.provider_key == provider_key and v.is_current]
        return max(candidates, key=lambda v: v.response_at) if candidates else None

    def _valuation_cell(self, field: str, provider_keys: list[str]) -> Cell:
        records = [r for r in (self.valuation_record(k) for k in provider_keys) if r is not None]
        with_value = [r for r in records if r.point is not None]
        if not with_value:
            if records:
                r = records[0]
                return Cell(source_key=f"valuation:{r.provider_key}", source_name=r.provider_name, quality=DataQuality.UNKNOWN,
                            retrieved_at=r.response_at, notes=f"{r.coverage_status}: {r.notes or ''}".strip())
            return Cell(quality=DataQuality.UNKNOWN, notes="Not configured / not entered")
        r = max(with_value, key=lambda v: v.response_at)
        quality = DataQuality.MANUAL if r.entry_method == EntryMethod.MANUAL else DataQuality.ESTIMATED
        notes = "; ".join(x for x in (
            "SANDBOX (not real data)" if r.is_sandbox else None,
            f"{r.estimate_type}", f"range ${r.low:,.0f}–${r.high:,.0f}" if r.low and r.high else None,
            f"confidence {r.confidence:.2f}" if r.confidence is not None else None,
            f"match {r.match_score:.2f}" if r.match_score is not None else None, r.notes) if x)
        ev = self.evidence.get(r.evidence_id) if r.evidence_id else None
        return Cell(value=r.point, source_key=f"valuation:{r.provider_key}", source_name=r.provider_name,
                    url=r.source_url or (ev.url if ev else None), retrieved_at=r.response_at, quality=quality,
                    raw=None, notes=notes, evidence_id=r.evidence_id, confidence=r.confidence)

    # ---------------------------------------------------------------- resolution
    def cell(self, key: str) -> Cell:
        if key in self._cache:
            return self._cache[key]
        base = self._resolve(key)
        ov = self.overrides.get(key)
        if ov is not None:
            kind = FIELD_KINDS.get(key, "text")
            cell = Cell(value=coerce(kind, ov.override_value), source_key="user_override",
                        source_name=f"User override by {ov.created_by or 'unknown'}", retrieved_at=ov.created_at,
                        quality=DataQuality.MANUAL, raw=None if base.value is None else str(base.value),
                        notes=f"Reason: {ov.reason}. Original value from {base.source_name or 'n/a'}: {base.value}")
        else:
            cell = base
        self._cache[key] = cell
        return cell

    def value(self, key: str) -> Any:
        return self.cell(key).value

    def _resolve(self, key: str) -> Cell:  # noqa: C901 - explicit field table
        p = self.prop
        county = p.county
        simple_sale_list = {
            "municipality": p.municipality, "sale_number": p.sale_number, "parcel": p.parcel_normalized,
            "parcel_original": p.parcel_original, "owner_name": p.owner_name, "location": p.property_address,
            "mailing_address": p.mailing_address, "listed_amount": p.listed_amount, "listed_assessment": p.listed_assessment,
            "listed_description": p.listed_description, "listed_lot_size": p.listed_lot_size, "sale_date": p.sale_date,
            "source_page": p.source_page, "extraction_confidence": p.extraction_confidence,
        }
        if key in simple_sale_list:
            return self._sale_list(key, simple_sale_list[key])
        if key == "notes":
            return Cell(value=p.notes, source_key="user_notes", source_name="Notes", quality=DataQuality.MANUAL if p.notes else DataQuality.UNKNOWN)

        if key == "full_location":
            street = self.assessment("site_address").value or p.property_address
            zip_code = None
            gis = self.assessments.get("montco.assessment_gis")
            if gis and gis.raw_codes:
                zip_code = (gis.raw_codes.get("LOC_ZIP") or "")[:5] or None
            city = (p.municipality or "").title() if county == "montco" else (p.municipality or "").title()
            value = ", ".join(x for x in (street, city, "PA" + (f" {zip_code}" if zip_code else "")) if x)
            return Cell(value=value or None, source_key="montco.assessment_gis" if zip_code else SALE_LIST,
                        source_name=source_name("montco.assessment_gis" if zip_code else SALE_LIST),
                        quality=DataQuality.VERIFIED if zip_code else DataQuality.ESTIMATED,
                        notes=None if zip_code else "City from municipality name; ZIP not available from an official source")

        assessment_attrs = {
            "land_use_description", "property_type", "property_class", "school_district", "assessed_value", "building_style",
            "year_built", "exterior_wall", "living_area_sqft", "base_area_sqft", "total_rooms", "bedrooms", "full_baths",
            "half_baths", "last_sale_date", "last_sale_price", "gross_building_area", "total_living_units",
            "structure_description", "improvement_name", "commercial_living_area", "commercial_units",
            "commercial_year_built", "legal_description", "site_address", "acres",
        }
        if key in assessment_attrs:
            cell = self.assessment(key)
            if key == "assessed_value" and cell.value is None and county == "delco" and p.listed_assessment is not None:
                c = self._sale_list(key, p.listed_assessment)
                c.notes = "CURRENT ASSESSMENT printed on the sale list (assessment page not captured yet)"
                return c
            if key == "acres" and cell.value is None:
                gis = self.assessments.get("delco.parcels_gis")
                if gis and gis.acres is not None:
                    return self._cell("acres", gis.acres, "delco.parcels_gis", gis.retrieved_at, gis.evidence_id,
                                      quality=DataQuality.ESTIMATED, notes="GIS-calculated acreage from parcel geometry")
            return cell
        if key == "lot_size":
            cell = self.assessment("lot_size_text", "lot_size")
            if cell.value is None and p.listed_lot_size:
                return self._sale_list(key, p.listed_lot_size)
            return cell
        if key == "category":
            cell = self.assessment("category")
            if cell.value is None and county == "delco" and p.listed_description:
                c = classify_delco(None, None, p.listed_description)
                return Cell(value=c.category, source_key=SALE_LIST, source_name=source_name(SALE_LIST),
                            quality=DataQuality.ESTIMATED, notes=c.basis, confidence=c.confidence)
            if cell.value is not None:
                rec = self.assessments.get(cell.source_key or "")
                basis = (rec.raw_codes or {}).get("classification_basis") if rec else None
                cell.notes = basis or cell.notes
            return cell

        # --------------------------------------------------------- tax
        if key == "tax_2023_total":
            return self._tax_cell(key, 2023, "claim", "total")
        if key == "tax_2024_total":
            return self._tax_cell(key, 2024, "claim", "total")
        if key == "tax_2024_balance":
            return self._tax_cell(key, 2024, "claim", "balance")
        if key == "tax_2025_total":
            return self._tax_cell(key, 2025, "claim", "total")
        if key == "tax_2025_balance":
            return self._tax_cell(key, 2025, "claim", "balance")
        if key == "tax_2024_due":
            return self._tax_cell(key, 2024, "delinquent", "balance")
        if key == "tax_2025_due":
            return self._tax_cell(key, 2025, "delinquent", "balance")
        if key in ("delinquent_total", "tax_sale_status", "tax_checked_at"):
            rec = self.tax_claim
            if rec is None:
                return self._missing(key, [TAX_SOURCE[county]])
            value = {"delinquent_total": rec.delinquent_total, "tax_sale_status": rec.tax_sale_status,
                     "tax_checked_at": rec.retrieved_at}[key]
            if key == "tax_sale_status" and not value:
                value = "Status missing"
            return self._cell(key, value, rec.source_key, rec.retrieved_at, rec.evidence_id, rec.entry_method,
                              quality=self._quality(rec.source_key, "tax_", rec.entry_method, rec.retrieved_at))

        # --------------------------------------------------------- valuation
        manual_sites = {"val_homes_com": ["manual:homes_com"], "val_zillow": ["zillow_bridge", "manual:zillow"],
                        "val_realtor": ["manual:realtor"], "val_redfin": ["manual:redfin"], "val_trulia": ["manual:trulia"],
                        "val_attom": ["attom"], "val_rentcast": ["rentcast"], "val_sandbox": ["sandbox"]}
        if key in manual_sites:
            return self._valuation_cell(key, manual_sites[key])
        if key in ("selected_valuation", "selected_valuation_method", "valuation_detail"):
            sel = self.selection
            if sel is None:
                return Cell(quality=DataQuality.UNKNOWN, notes="Valuation not evaluated yet")
            label = PROVIDER_LABELS.get(sel.provider_key or "", source_name(f"manual:{sel.provider_key}") if sel.provider_key else None)
            value = {"selected_valuation": sel.value,
                     "selected_valuation_method": None if sel.method == "unknown" else f"{sel.method} — {label}",
                     "valuation_detail": sel.explanation}[key]
            return Cell(value=value, source_key="valuation_selection", source_name=source_name("valuation_selection"),
                        retrieved_at=sel.computed_at, quality=DataQuality.ESTIMATED if sel.value is not None else DataQuality.UNKNOWN,
                        notes=sel.explanation)
        if key == "market_value_flag":
            v = self.value("selected_valuation")
            threshold = self.config["valuation"]["interest_threshold"]
            short = f"${threshold / 1000:,.0f}k"
            text = "No valuation retrieved" if v is None else (f"under {short} (${v:,.0f})" if v < threshold else f"above {short} (${v:,.0f})")
            return Cell(value=text, source_key="valuation_selection", source_name=source_name("valuation_selection"),
                        quality=DataQuality.ESTIMATED if v is not None else DataQuality.UNKNOWN)
        if key == "property_photo":
            v = self.value("selected_valuation")
            threshold = self.config["valuation"]["interest_threshold"]
            primary = next((img for img in self.images if img.is_primary), None)
            if v is None or v < threshold:
                return Cell(value=None, quality=DataQuality.UNKNOWN, notes="Photos are shown only for valuations at or above the threshold")
            if primary is None:
                return Cell(value="No image available", quality=DataQuality.UNKNOWN,
                            notes="No permitted image source configured or uploaded")
            return Cell(value=f"/api/images/{primary.id}/content", source_key=f"image:{primary.source_kind}",
                        source_name=primary.attribution or primary.source_kind, retrieved_at=primary.created_at,
                        quality=DataQuality.MANUAL if primary.source_kind == "user_upload" else DataQuality.VERIFIED,
                        notes=primary.license_note)

        # --------------------------------------------------------- mortgages & liens
        if key in ("mortgage_summary", "mortgage_count", "unsatisfied_mortgages", "satisfied_mortgages", "review_mortgages"):
            if not self.recorder_searched:
                return Cell(value="Recorder not searched" if key == "mortgage_summary" else None, quality=DataQuality.UNKNOWN,
                            notes="Recorder of Deeds documents have not been captured yet")
            ms = self.mortgages_since()
            statuses = [m.effective_status for m in ms]
            sat = statuses.count(MortgageStatus.SATISFIED)
            unsat = statuses.count(MortgageStatus.NO_SATISFACTION)
            review = len(statuses) - sat - unsat
            if key == "mortgage_summary":
                if not ms:
                    text = "NO MTG"
                elif unsat == 0 and review == 0:
                    text = f"{len(ms)} MTG(s)- OK(SATs rcrd)"
                else:
                    parts = [f"{sat} SAT"]
                    if unsat:
                        parts.append(f"{unsat} NO SAT")
                    if review:
                        parts.append(f"{review} REVIEW")
                    text = f"{len(ms)} MTG(s)- " + ", ".join(parts)
                value: Any = text
            else:
                value = {"mortgage_count": len(ms), "unsatisfied_mortgages": unsat, "satisfied_mortgages": sat,
                         "review_mortgages": review}[key]
            recorder_lookup = max((self.lookups[k] for k in RECORDER_SOURCES if k in self.lookups),
                                  key=lambda lk: lk.finished_at or lk.started_at)
            notes = f"Mortgages recorded since {self.config['mortgage']['since_year']}; statuses from transparent satisfaction matching"
            fixture = any(m.document is not None and m.document.entry_method == EntryMethod.FIXTURE for m in ms)
            if fixture:
                notes = "DEMO FIXTURE (not live county data); " + notes
            return Cell(value=value, source_key=recorder_lookup.source_key, source_name=source_name(recorder_lookup.source_key),
                        retrieved_at=recorder_lookup.finished_at, quality=DataQuality.ESTIMATED if fixture else DataQuality.VERIFIED,
                        evidence_id=recorder_lookup.evidence_id, notes=notes)
        if key in ("lien_summary", "open_lien_cases"):
            if not self.lien_searched:
                return Cell(value="Not searched" if key == "lien_summary" else None, quality=DataQuality.UNKNOWN)
            relevant = [c for c in self.liens if c.is_relevant]
            value = len(relevant) if key == "open_lien_cases" else (f"{len(relevant)} lien case(s)" if relevant else "No lien cases")
            lk = max((self.lookups[k] for k in CIVIL_SOURCES if k in self.lookups), key=lambda lk: lk.finished_at or lk.started_at)
            fixture = any(c.entry_method == EntryMethod.FIXTURE for c in relevant)
            return Cell(value=value, source_key=lk.source_key, source_name=source_name(lk.source_key), retrieved_at=lk.finished_at,
                        quality=DataQuality.ESTIMATED if fixture else DataQuality.VERIFIED, evidence_id=lk.evidence_id,
                        notes="DEMO FIXTURE (not live county data)" if fixture else None)

        # --------------------------------------------------------- decision & status
        if key in ("decision_status", "decision_summary"):
            value = p.decision_status if key == "decision_status" else p.decision_summary
            return Cell(value=value, source_key="decision_engine", source_name=source_name("decision_engine"), retrieved_at=p.decision_at,
                        quality=DataQuality.ESTIMATED if value else DataQuality.UNKNOWN)
        if key == "last_enriched_at":
            return Cell(value=p.last_enriched_at, quality=DataQuality.VERIFIED if p.last_enriched_at else DataQuality.UNKNOWN)
        if key == "pending_actions":
            tasks = sorted({source_name(t.source_key) or t.source_key for t in self.open_tasks})
            return Cell(value="; ".join(tasks) or None, quality=DataQuality.UNKNOWN if tasks else DataQuality.VERIFIED)
        if key == "source_status":
            parts = [f"{k.split('.', 1)[1]}={lk.status}" for k, lk in sorted(self.lookups.items())]
            return Cell(value="; ".join(parts) or "not enriched", quality=DataQuality.VERIFIED)
        raise KeyError(key)

    # ---------------------------------------------------------------- styling
    def styles(self) -> dict[str, Any]:
        cfg = self.config
        cells: dict[str, str] = {}
        row = None
        v = self.value("selected_valuation")
        if v is not None and v < cfg["valuation"]["interest_threshold"]:
            for key in ("selected_valuation", "market_value_flag"):
                cells[key] = "orange"
        for key in ("val_homes_com", "val_zillow", "val_realtor", "val_redfin", "val_trulia", "val_attom", "val_rentcast", "val_sandbox"):
            val = self.value(key)
            if val is not None and val < cfg["valuation"]["interest_threshold"]:
                cells[key] = "orange"
        if self.prop.county == "montco":
            bal = self.value("tax_2024_balance")
            if bal is not None and abs(bal) < 0.005 and cfg["tax"]["montco_zero_2024_balance_resolved"] \
                    and "tax_status_interpretation" not in self.overrides:
                row = "red"
        else:
            rec = self.tax_claim
            cells["tax_sale_status"] = "green" if rec is not None and rec.status_category == TaxStatusCategory.LISTED else "red"
            av = self.value("assessed_value")
            if av is not None and av < cfg["assessment"]["highlight_below"]:
                cells["assessed_value"] = "orange"
        if self.recorder_searched:
            unsat = self.value("unsatisfied_mortgages") or 0
            review = self.value("review_mortgages") or 0
            count = self.value("mortgage_count") or 0
            if unsat:
                cells["mortgage_summary"] = "red"
            elif review:
                cells["mortgage_summary"] = "yellow"
            elif count:
                cells["mortgage_summary"] = "green"
        decision = self.prop.decision_status
        if decision:
            cells["decision_status"] = {"Interested": "green", "Not interested": "orange", "Review required": "yellow",
                                        "Insufficient data": "grey", "Removed/resolved": "red"}.get(decision, "")
        return {"row": row, "cells": cells}


def load_snapshots(session: Session, property_ids: list[int], config: dict) -> dict[int, PropertySnapshot]:
    if not property_ids:
        return {}
    props = session.scalars(
        select(Property).where(Property.id.in_(property_ids)).options(
            selectinload(Property.overrides), selectinload(Property.identifiers), selectinload(Property.tax_lines)
        )
    ).all()
    snaps = {p.id: PropertySnapshot(p, config) for p in props}

    def chunks(ids: list[int], size: int = 800):
        for i in range(0, len(ids), size):
            yield ids[i : i + size]

    evidence_ids: set[int] = set()
    for ids in chunks(property_ids):
        for rec in session.scalars(select(AssessmentRecord).where(AssessmentRecord.property_id.in_(ids), AssessmentRecord.is_current.is_(True))):
            snaps[rec.property_id].assessments[rec.source_key] = rec
            evidence_ids.add(rec.evidence_id or 0)
        for rec in session.scalars(select(TaxClaimRecord).where(TaxClaimRecord.property_id.in_(ids), TaxClaimRecord.is_current.is_(True))
                                   .options(selectinload(TaxClaimRecord.years))):
            snaps[rec.property_id].tax_claims[rec.source_key] = rec
            evidence_ids.add(rec.evidence_id or 0)
        for rec in session.scalars(select(ValuationRecord).where(ValuationRecord.property_id.in_(ids), ValuationRecord.is_current.is_(True))):
            snaps[rec.property_id].valuations.append(rec)
        for rec in session.scalars(select(ValuationSelection).where(ValuationSelection.property_id.in_(ids), ValuationSelection.is_current.is_(True))):
            snaps[rec.property_id].selection = rec
        for rec in session.scalars(select(MortgageRecord).where(MortgageRecord.property_id.in_(ids)).options(
                selectinload(MortgageRecord.document), selectinload(MortgageRecord.matches))):
            snaps[rec.property_id].mortgages.append(rec)
        for rec in session.scalars(select(CivilLienCase).where(CivilLienCase.property_id.in_(ids))):
            snaps[rec.property_id].liens.append(rec)
        for rec in session.scalars(select(SourceLookup).where(SourceLookup.property_id.in_(ids), SourceLookup.is_current.is_(True))):
            snaps[rec.property_id].lookups[rec.source_key] = rec
        for rec in session.scalars(select(FieldProvenance).where(FieldProvenance.property_id.in_(ids), FieldProvenance.is_current.is_(True))):
            snaps[rec.property_id].provenance[(rec.field, rec.source_key)] = rec
        for rec in session.scalars(select(PropertyImage).where(PropertyImage.property_id.in_(ids))):
            snaps[rec.property_id].images.append(rec)
        for rec in session.scalars(select(CaptureTask).where(CaptureTask.property_id.in_(ids), CaptureTask.status == "open")):
            snaps[rec.property_id].open_tasks.append(rec)
    evidence_ids.discard(0)
    if evidence_ids:
        for ids in chunks(sorted(evidence_ids)):
            evs = session.scalars(select(SourceEvidence).where(SourceEvidence.id.in_(ids))).all()
            by_prop: dict[int, list[SourceEvidence]] = defaultdict(list)
            for ev in evs:
                if ev.property_id:
                    by_prop[ev.property_id].append(ev)
            for pid, items in by_prop.items():
                if pid in snaps:
                    snaps[pid].evidence.update({e.id: e for e in items})
    for snap in snaps.values():
        snap.tax_lines = list(snap.prop.tax_lines)
    return snaps
