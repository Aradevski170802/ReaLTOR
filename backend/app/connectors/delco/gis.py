"""Delaware County PA official ArcGIS REST: Parcels_Public_Access."""

from __future__ import annotations

import json

from app.common.parsing import clean_ws, parse_float, parse_int
from app.connectors.base import (
    PropertyRef,
    RetryPolicy,
    SearchDef,
    SourceAdapter,
    SourceDescriptor,
    SourceError,
    SourceOutcome,
)
from app.connectors.http import ComplianceHttpClient
from app.connectors.payloads import ParcelGisPayload
from app.enums import AccessMethod, LookupStatus

LAYER = "https://gis.delcopa.gov/arcgis/rest/services/Parcels/Parcels_Public_Access/FeatureServer/0"
QUERY = f"{LAYER}/query"
FIELDS = ["PARID", "PIN", "MAPID", "MUNICIPALI", "CALCULATED", "TAXYR", "ADRCAT", "CLICKTHROU", "LEGAL2", "LEGAL3"]

DESCRIPTOR = SourceDescriptor(
    key="delco.parcels_gis",
    county="delco",
    name="Delaware County GIS — Parcels Public Access layer",
    category="parcel_gis",
    access_method=AccessMethod.OFFICIAL_API,
    base_urls=(LAYER,),
    searches=(SearchDef("Query by folio", QUERY, "PARID = <folio digits as number>"),),
    input_requirements="Delco folio NN-NN-NNNNN-NN",
    parcel_rule="Remove dashes; PARID is numeric so the leading zero is dropped (01-00-00254-00 -> 1000025400)",
    field_mappings={"ADRCAT": "Site Location", "CALCULATED": "GIS-calculated acres (estimate)", "LEGAL2": "legal description",
                    "LEGAL3": "lot size text", "CLICKTHROU": "county assessment-page deep link", "MAPID": "map id"},
    rate_limit_per_minute=30,
    min_interval_seconds=1.0,
    cache_ttl_hours=24 * 7,
    retry_policy=RetryPolicy(max_attempts=4, base_delay_seconds=2.0),
    error_classification={"no feature": "no_match", "several features": "ambiguous_match", "ArcGIS error": "unexpected_failure"},
    compliance_status="automated_permitted",
    terms_notes=(
        "Public ArcGIS REST layer published by Delaware County (Office of Data & Mapping Innovation / Board of Assessment). "
        "No robots.txt on gis.delcopa.gov. Acreage is GIS-calculated from parcel geometry and labelled as an estimate; "
        "assessor acreage from the assessment page takes precedence."
    ),
    terms_url="https://delaware-county-pennsylvania-dcpd.hub.arcgis.com/",
    access_findings=("2026-09-13: PARID=1000025400 returned 3 CHESTER AVE, 0.13 ac, '2 STY HSE ADD', '60 X 95 LOT 12'.",),
)


class DelcoParcelsGisAdapter(SourceAdapter):
    descriptor = DESCRIPTOR
    parser_version = "parcels_public_access/1"

    def lookup(self, prop: PropertyRef, secrets: dict[str, str] | None = None) -> SourceOutcome:
        if not prop.parcel.valid:
            return SourceOutcome(LookupStatus.NO_MATCH, f"Folio '{prop.parcel.original}' is not a valid Delaware County folio")
        parid = int(prop.parcel.digits)
        params = {"where": f"PARID={parid}", "outFields": ",".join(FIELDS), "returnGeometry": "false", "f": "json"}
        try:
            with ComplianceHttpClient(self.descriptor) as http:
                result = http.get(QUERY, params=params)
        except SourceError as exc:
            return SourceOutcome(exc.status, exc.message, retry_after=exc.retry_after,
                                 evidence=[exc.evidence] if exc.evidence else [])
        try:
            data = json.loads(result.text)
        except ValueError:
            return SourceOutcome(LookupStatus.PARSE_FAILURE, "GIS response was not JSON", evidence=[result.evidence()])
        return self.parse_response(data, parid, result.evidence())

    @staticmethod
    def parse_response(data: dict, parid: int, evidence=None) -> SourceOutcome:
        ev = [evidence] if evidence else []
        if "error" in data:
            return SourceOutcome(LookupStatus.UNEXPECTED, f"ArcGIS error: {data['error'].get('message')}", evidence=ev)
        feats = [f["attributes"] for f in data.get("features", []) if parse_int(f["attributes"].get("PARID")) == parid]
        if not feats:
            return SourceOutcome(LookupStatus.NO_MATCH, f"No parcel with PARID {parid}", evidence=ev)
        if len(feats) > 1:
            return SourceOutcome(LookupStatus.AMBIGUOUS, f"{len(feats)} parcels with PARID {parid}", evidence=ev)
        a = feats[0]
        payload = ParcelGisPayload(
            site_address=clean_ws(a.get("ADRCAT")),
            acres=parse_float(a.get("CALCULATED")),
            legal_description=clean_ws(a.get("LEGAL2")),
            lot_size_text=clean_ws(a.get("LEGAL3")),
            map_id=clean_ws(a.get("MAPID") or a.get("PIN")),
            municipality_code=str(a.get("MUNICIPALI")) if a.get("MUNICIPALI") is not None else None,
            detail_link=clean_ws(a.get("CLICKTHROU")),
            tax_year=parse_int(a.get("TAXYR")),
        )
        return SourceOutcome(LookupStatus.SUCCESS, "Parcel located in county GIS", payload=payload, evidence=ev)
