"""ATTOM Data property enrichment (licensed Property API).

One `attomavm/detail` call per property (shared with the ATTOM valuation provider through attom_client) yields a rich
record: owner, absentee status and mailing address, assessed and market value, building characteristics, lot size and
the most recent sale. It is stored as an assessment record with full field provenance, and high-value extras that have
no dedicated column (market value, tax, AVM, absentee flag, owner mailing address, ATTOM id) are retained in raw_codes.

Keyed by parcel (APN + county FIPS) with an address fallback; the returned APN is checked against our parcel so a
wrong-address match is flagged rather than trusted.
"""

from __future__ import annotations

import re

from app.common.parsing import parse_date, parse_float, parse_int
from app.connectors.attom_client import SECRET_NAME, county_fips, lookup_avm_detail
from app.connectors.base import (
    EvidenceDraft,
    PropertyRef,
    RetryPolicy,
    SearchDef,
    SourceAdapter,
    SourceDescriptor,
    SourceOutcome,
)
from app.connectors.payloads import AssessmentPayload, SalePayload
from app.enums import AccessMethod, LookupStatus
from app.security.secrets import redact_text


def _descriptor(county: str) -> SourceDescriptor:
    return SourceDescriptor(
        key=f"{county}.attom",
        county=county,
        name="ATTOM Data — Property detail & owner (Property API)",
        category="assessment",
        access_method=AccessMethod.OFFICIAL_API,
        base_urls=("https://api.gateway.attomdata.com/propertyapi/v1.0.0/attomavm/detail",),
        searches=(SearchDef("attomavm/detail by APN+FIPS or address", "https://api.gateway.attomdata.com/", "Parcel (APN) + county FIPS, else street address"),),
        input_requirements="ATTOM API key (Settings → Valuation providers → ATTOM, or USI_SECRET_PROVIDER_ATTOM_API_KEY)",
        parcel_rule=f"APN = parcel number; FIPS = {county_fips(county)}",
        field_mappings={"owner.owner1.fullname": "owner_name", "assessment.assessed.assdttlvalue": "assessed_value",
                        "assessment.market.mktttlvalue": "raw_codes.ATTOM_MARKET_VALUE", "summary.yearbuilt": "year_built",
                        "building.size.livingsize": "living_area_sqft", "building.rooms.beds": "bedrooms",
                        "building.rooms.bathstotal": "full_baths", "lot.lotsize1": "acres", "sale.amount.saleamt": "last_sale_price",
                        "owner.absenteeownerstatus": "raw_codes.ABSENTEE", "owner.mailingaddressoneline": "raw_codes.OWNER_MAILING"},
        rate_limit_per_minute=60,
        min_interval_seconds=0.5,
        cache_ttl_hours=24 * 30,
        retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=2.0),
        error_classification={"api key": "not_configured", "no property": "no_match", "http 401": "authentication_needed"},
        compliance_status="automated_permitted",
        terms_notes=("Licensed ATTOM Property API used with the account holder's own API key. Display and retention are "
                     "subject to the ATTOM data agreement. The key is stored encrypted and never written to logs."),
        terms_url="https://api.developer.attomdata.com/",
        access_findings=("2026-09-15: attomavm/detail returns owner, absentee status, mailing address, assessed & market "
                         "value, building/lot detail and last sale, keyed by APN+FIPS or address.",),
    )


def _bool_absentee(summary_ind: str | None, owner_status: str | None) -> str | None:
    text = (summary_ind or "").strip() or (owner_status or "").strip()
    return text or None


class _AttomPropertyAdapter(SourceAdapter):
    parser_version = "attom-avmdetail/1"
    credential_secrets = (SECRET_NAME,)

    def lookup(self, prop: PropertyRef, secrets: dict[str, str] | None = None) -> SourceOutcome:
        key = (secrets or {}).get(SECRET_NAME)
        if not key:
            return SourceOutcome(LookupStatus.NOT_CONFIGURED, "ATTOM API key not configured")
        addr2 = ", ".join(x for x in (prop.municipality, "PA") if x) or None
        resp = lookup_avm_detail(key=key, county=prop.county, parcel=prop.parcel.normalized,
                                 address1=prop.property_address, address2=addr2)
        if resp is None:
            return SourceOutcome(LookupStatus.NO_MATCH, "No parcel or address available to query ATTOM")
        if resp.status_code in (401, 403):
            return SourceOutcome(LookupStatus.AUTH_NEEDED, f"ATTOM rejected the API key (HTTP {resp.status_code})")
        if resp.status_code == 429:
            return SourceOutcome(LookupStatus.RATE_LIMITED, "ATTOM rate limit (HTTP 429)")
        if resp.status_code >= 400 and not resp.properties:
            return SourceOutcome(LookupStatus.UNEXPECTED, f"ATTOM HTTP {resp.status_code}: {resp.status_msg or 'error'}")
        props = resp.properties
        if not props or "WithoutResult" in resp.status_msg:
            return SourceOutcome(LookupStatus.NO_MATCH, f"ATTOM: {resp.status_msg or 'no property'}",
                                 evidence=[self._evidence(resp)])
        if len(props) > 1:
            return SourceOutcome(LookupStatus.AMBIGUOUS, f"ATTOM returned {len(props)} properties",
                                 evidence=[self._evidence(resp)])
        payload, notes = self.parse(props[0], prop)
        message = f"ATTOM property matched ({notes})" if notes else "ATTOM property matched"
        return SourceOutcome(LookupStatus.SUCCESS, message, payload=payload, evidence=[self._evidence(resp)])

    @staticmethod
    def _evidence(resp) -> EvidenceDraft:
        return EvidenceDraft(url=redact_text(resp.url), content=resp.content, content_type=resp.content_type,
                             http_status=resp.status_code, request_summary=redact_text("ATTOM attomavm/detail"),
                             notes="Licensed ATTOM Property API response")

    @staticmethod
    def parse(p: dict, prop: PropertyRef) -> tuple[AssessmentPayload, str]:
        summary = p.get("summary") or {}
        building = p.get("building") or {}
        size = building.get("size") or {}
        rooms = building.get("rooms") or {}
        bsummary = building.get("summary") or {}
        lot = p.get("lot") or {}
        sale = p.get("sale") or {}
        sale_amt = (sale.get("amount") or {})
        assessment = p.get("assessment") or {}
        assessed = assessment.get("assessed") or {}
        market = assessment.get("market") or {}
        tax = assessment.get("tax") or {}
        owner = p.get("owner") or {}
        owner1 = owner.get("owner1") or {}
        avm = (p.get("avm") or {}).get("amount") or {}
        ident = p.get("identifier") or {}
        address = p.get("address") or {}

        depth, frontage = parse_float(lot.get("depth")), parse_float(lot.get("frontage"))
        lot_size_text = f"{frontage:g} x {depth:g}" if depth and frontage else None
        bathstotal = parse_float(rooms.get("bathstotal"))

        raw: dict[str, object] = {}
        for label, value in (
            ("ATTOM_ID", ident.get("attomId")),
            ("ATTOM_APN", ident.get("apn")),
            ("ATTOM_MARKET_VALUE", parse_float(market.get("mktttlvalue"))),
            ("ATTOM_TAX_AMOUNT", parse_float(tax.get("taxamt"))),
            ("ATTOM_TAX_YEAR", parse_int(tax.get("taxyear"))),
            ("ATTOM_AVM_VALUE", parse_float(avm.get("value"))),
            ("ATTOM_AVM_HIGH", parse_float(avm.get("high"))),
            ("ATTOM_AVM_LOW", parse_float(avm.get("low"))),
            ("ATTOM_AVM_SCORE", parse_float(avm.get("scr"))),
            ("ABSENTEE", _bool_absentee(summary.get("absenteeInd"), owner.get("absenteeownerstatus"))),
            ("OWNER_MAILING", owner.get("mailingaddressoneline")),
            ("CORPORATE_OWNER", owner.get("corporateindicator")),
            ("LEVELS", parse_float(bsummary.get("levels"))),
        ):
            if value not in (None, ""):
                raw[label] = value

        field_notes: dict[str, str] = {}
        if bathstotal is not None:
            field_notes["full_baths"] = f"ATTOM total baths {bathstotal:g}"
        absentee = raw.get("ABSENTEE")
        if absentee:
            field_notes["owner_name"] = f"ATTOM absentee status: {absentee}; mailing {raw.get('OWNER_MAILING', 'n/a')}"

        payload = AssessmentPayload(
            property_class=summary.get("propclass"),
            property_type=summary.get("propertyType") or summary.get("proptype"),
            land_use_description=summary.get("propLandUse") or summary.get("propsubtype"),
            site_address=address.get("oneLine"),
            legal_description=summary.get("legal1"),
            owner_name=owner1.get("fullname"),
            assessed_value=parse_float(assessed.get("assdttlvalue")),
            acres=parse_float(lot.get("lotsize1")),
            lot_sqft=parse_float(lot.get("lotsize2")),
            lot_size_text=lot_size_text,
            year_built=parse_int(summary.get("yearbuilt")),
            living_area_sqft=parse_float(size.get("livingsize")),
            gross_building_area=parse_float(size.get("grosssize")),
            base_area_sqft=parse_float(size.get("groundfloorsize")),
            total_rooms=parse_int(rooms.get("roomsTotal")),
            bedrooms=parse_int(rooms.get("beds")),
            full_baths=None if bathstotal is None else int(bathstotal),
            total_living_units=parse_int(bsummary.get("unitsCount")),
            last_sale_date=parse_date(sale.get("saleTransDate") or sale_amt.get("salerecdate")),
            last_sale_price=parse_float(sale_amt.get("saleamt")),
            raw_codes=raw,
            field_notes=field_notes,
        )
        if payload.last_sale_date or payload.last_sale_price:
            payload.sales.append(SalePayload(sale_date=payload.last_sale_date, sale_price=payload.last_sale_price,
                                             grantee=sale.get("buyerName"), book_page=sale_amt.get("saledocnum")))

        # Confirm the returned parcel matches ours; flag (do not discard) a mismatch for review.
        attom_apn = re.sub(r"\D", "", str(ident.get("apn") or ""))
        ours = prop.parcel.digits or ""
        note = "by APN" if attom_apn and ours and attom_apn == ours else (
            f"address match; ATTOM APN {ident.get('apn')} != parcel {prop.parcel.normalized}" if attom_apn and ours and attom_apn != ours
            else "by address")
        if "!=" in note:
            field_notes["match"] = note
        return payload, note


class MontcoAttomAdapter(_AttomPropertyAdapter):
    descriptor = _descriptor("montco")


class DelcoAttomAdapter(_AttomPropertyAdapter):
    descriptor = _descriptor("delco")
