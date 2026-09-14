"""Valuation provider implementations: RentCast AVM, ATTOM AVM, Zillow Bridge (scaffold), Sandbox."""

from __future__ import annotations

import hashlib
import json
from datetime import date

import httpx

from app.common.parsing import parse_date, parse_float
from app.config import get_settings
from app.connectors.base import EvidenceDraft
from app.enums import CoverageStatus, EstimateType
from app.security.secrets import redact_text
from app.valuation.base import (
    PROPERTY_TYPE_LABELS,
    ProviderDescriptor,
    ValuationProvider,
    ValuationResult,
    ValuationSubject,
    address_match_score,
)


def _client(timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers={"User-Agent": get_settings().http_user_agent})


def _evidence(resp: httpx.Response, summary: str) -> EvidenceDraft:
    return EvidenceDraft(url=redact_text(str(resp.request.url)), content=resp.content,
                         content_type=resp.headers.get("content-type", "application/json"),
                         http_status=resp.status_code, request_summary=redact_text(summary))


def _attom_evidence(resp, summary: str) -> EvidenceDraft:
    return EvidenceDraft(url=redact_text(resp.url), content=resp.content, content_type=resp.content_type,
                         http_status=resp.status_code, request_summary=redact_text(summary))


def _attom_failure(resp, provider: str = "ATTOM") -> ValuationResult | None:
    """Failure mapping for the shared ATTOM client's AttomResponse (mirrors _http_failure for httpx responses)."""
    if resp.status_code in (401, 403):
        return ValuationResult(CoverageStatus.NOT_CONFIGURED, notes=f"{provider} rejected the API key (HTTP {resp.status_code})",
                               evidence=_attom_evidence(resp, "auth failure"))
    if resp.status_code == 429:
        return ValuationResult(CoverageStatus.ERROR, notes=f"{provider} rate limit (HTTP 429)")
    if resp.status_code >= 400 and not resp.properties:
        return ValuationResult(CoverageStatus.ERROR, notes=f"{provider} HTTP {resp.status_code}", evidence=_attom_evidence(resp, "error"))
    return None


def _http_failure(resp: httpx.Response, provider: str) -> ValuationResult | None:
    if resp.status_code in (401, 403):
        return ValuationResult(CoverageStatus.NOT_CONFIGURED, notes=f"{provider} rejected the API key (HTTP {resp.status_code})",
                               evidence=_evidence(resp, "auth failure"))
    if resp.status_code == 404:
        return ValuationResult(CoverageStatus.NO_MATCH, notes=f"{provider} has no record for this address",
                               evidence=_evidence(resp, "no match"))
    if resp.status_code == 429:
        retry = parse_float(resp.headers.get("retry-after"))
        return ValuationResult(CoverageStatus.ERROR, notes=f"{provider} rate limit (HTTP 429)", retry_after=retry)
    if resp.status_code >= 400:
        return ValuationResult(CoverageStatus.ERROR, notes=f"{provider} HTTP {resp.status_code}", evidence=_evidence(resp, "error"))
    return None


def _confidence_from_range(point: float | None, low: float | None, high: float | None) -> float | None:
    if not point or low is None or high is None or point <= 0:
        return None
    spread = (high - low) / point
    return round(max(0.0, min(1.0, 1.0 - spread / 2)), 3)


# ------------------------------------------------------------------------------------------ RentCast


class RentCastProvider(ValuationProvider):
    ENDPOINT = "https://api.rentcast.io/v1/avm/value"
    descriptor = ProviderDescriptor(
        key="rentcast",
        name="RentCast",
        product="Property Value Estimate (AVM) API — GET /v1/avm/value",
        estimate_types=(EstimateType.MARKET_AVM,),
        docs_url="https://developers.rentcast.io/reference/value-estimate",
        secret_names=("provider.rentcast.api_key",),
        setup_steps=(
            "Create an account at https://app.rentcast.io and subscribe to an API plan (a limited free tier exists).",
            "Copy the API key from the RentCast API dashboard.",
            "Settings → Valuation providers → RentCast → paste the key → Save → Enable → Test.",
        ),
        coverage_notes="National residential coverage including Montgomery and Delaware Counties PA; land and commercial coverage is limited.",
        terms_notes="Licensed API; estimates are automated valuations, not appraisals. Respect plan request limits.",
        default_settings={"comp_count": 15},
    )

    def estimate(self, subject: ValuationSubject, secrets: dict[str, str], settings: dict) -> ValuationResult:
        if self.missing_secrets(secrets):
            return ValuationResult.status_only(CoverageStatus.NOT_CONFIGURED, "RentCast API key not configured")
        if not subject.street:
            return ValuationResult.status_only(CoverageStatus.NO_MATCH, "No street address available for AVM lookup")
        params: dict[str, str | int | float] = {"address": subject.one_line, "compCount": int(settings.get("comp_count", 15))}
        if subject.category in PROPERTY_TYPE_LABELS:
            params["propertyType"] = PROPERTY_TYPE_LABELS[subject.category]
        for key, value in (("bedrooms", subject.bedrooms), ("bathrooms", subject.bathrooms), ("squareFootage", subject.square_feet)):
            if value:
                params[key] = value
        with _client() as client:
            resp = client.get(self.ENDPOINT, params=params, headers={"X-Api-Key": secrets["provider.rentcast.api_key"], "Accept": "application/json"})
        if failure := _http_failure(resp, "RentCast"):
            return failure
        return self.parse(resp.json(), subject, _evidence(resp, f"GET /v1/avm/value address={subject.one_line}"))

    @staticmethod
    def parse(data: dict, subject: ValuationSubject, evidence: EvidenceDraft | None = None) -> ValuationResult:
        point, low, high = parse_float(data.get("price")), parse_float(data.get("priceRangeLow")), parse_float(data.get("priceRangeHigh"))
        subject_prop = data.get("subjectProperty") or {}
        returned = subject_prop.get("formattedAddress") or subject_prop.get("addressLine1")
        match = address_match_score(subject.one_line, returned) if returned else None
        if point is None:
            return ValuationResult(CoverageStatus.NO_MATCH, notes="RentCast returned no price", evidence=evidence)
        status = CoverageStatus.MATCHED if (match is None or match >= 0.8) else CoverageStatus.AMBIGUOUS
        return ValuationResult(
            coverage_status=status, estimate_type=EstimateType.MARKET_AVM, point=point, low=low, high=high,
            estimate_date=date.today(), provider_property_id=subject_prop.get("id"),
            source_url="https://app.rentcast.io/app", confidence=_confidence_from_range(point, low, high),
            confidence_label="derived from price range width", match_score=match, matched_address=returned,
            notes=f"{len(data.get('comparables') or [])} comparables", evidence=evidence,
        )


# ------------------------------------------------------------------------------------------ ATTOM


class AttomProvider(ValuationProvider):
    ENDPOINT = "https://api.gateway.attomdata.com/propertyapi/v1.0.0/attomavm/detail"
    descriptor = ProviderDescriptor(
        key="attom",
        name="ATTOM Data",
        product="Property API — AVM detail (attomavm/detail)",
        estimate_types=(EstimateType.MARKET_AVM,),
        docs_url="https://api.developer.attomdata.com/docs",
        secret_names=("provider.attom.api_key",),
        setup_steps=(
            "Request an ATTOM API trial or subscription that includes the AVM endpoint (https://api.developer.attomdata.com).",
            "Copy the API key ('apikey' header).",
            "Settings → Valuation providers → ATTOM → paste the key → Save → Enable → Test.",
        ),
        coverage_notes="National coverage for most residential parcels; returns a confidence score (scr 0-100).",
        terms_notes="Licensed data; display and retention subject to the ATTOM agreement.",
    )

    def estimate(self, subject: ValuationSubject, secrets: dict[str, str], settings: dict) -> ValuationResult:
        if self.missing_secrets(secrets):
            return ValuationResult.status_only(CoverageStatus.NOT_CONFIGURED, "ATTOM API key not configured")
        from app.connectors.attom_client import lookup_avm_detail

        address2 = ", ".join(x for x in (subject.city, " ".join(y for y in (subject.state, subject.zip_code) if y)) if x)
        resp = lookup_avm_detail(key=secrets["provider.attom.api_key"], county=subject.county, parcel=subject.parcel,
                                 address1=subject.street, address2=address2)
        if resp is None:
            return ValuationResult.status_only(CoverageStatus.NO_MATCH, "No parcel or address available for AVM lookup")
        if failure := _attom_failure(resp):
            return failure
        return self.parse(resp.data, subject, _attom_evidence(resp, "GET attomavm/detail"))

    @staticmethod
    def parse(data: dict, subject: ValuationSubject, evidence: EvidenceDraft | None = None) -> ValuationResult:
        status = (data.get("status") or {}).get("msg", "")
        props = data.get("property") or []
        if not props or "WithoutResult" in status:
            return ValuationResult(CoverageStatus.NO_MATCH, notes=f"ATTOM: {status or 'no property'}", evidence=evidence)
        if len(props) > 1:
            return ValuationResult(CoverageStatus.AMBIGUOUS, notes=f"ATTOM returned {len(props)} properties", evidence=evidence)
        prop = props[0]
        avm = prop.get("avm") or {}
        amount = avm.get("amount") or {}
        point = parse_float(amount.get("value"))
        if point is None:
            return ValuationResult(CoverageStatus.NO_MATCH, notes="ATTOM property has no AVM value", evidence=evidence)
        returned = (prop.get("address") or {}).get("oneLine") or (prop.get("address") or {}).get("line1")
        match = address_match_score(subject.one_line, returned) if returned else None
        scr = parse_float(amount.get("scr"))
        return ValuationResult(
            coverage_status=CoverageStatus.MATCHED if (match is None or match >= 0.8) else CoverageStatus.AMBIGUOUS,
            estimate_type=EstimateType.MARKET_AVM, point=point, low=parse_float(amount.get("low")), high=parse_float(amount.get("high")),
            estimate_date=parse_date(avm.get("eventDate")), provider_property_id=str((prop.get("identifier") or {}).get("attomId") or "") or None,
            source_url="https://api.developer.attomdata.com/", confidence=None if scr is None else round(scr / 100, 3),
            confidence_label=None if scr is None else f"ATTOM confidence score {scr:.0f}", match_score=match,
            matched_address=returned, evidence=evidence,
        )


# ------------------------------------------------------------------------------------------ Zillow Bridge


class ZillowBridgeProvider(ValuationProvider):
    ENDPOINT = "https://api.bridgedataoutput.com/api/v2/zestimates_v2/zestimates"
    descriptor = ProviderDescriptor(
        key="zillow_bridge",
        name="Zillow Zestimate via Bridge Interactive",
        product="Bridge API — Zestimates (requires Zillow approval)",
        estimate_types=(EstimateType.MARKET_AVM,),
        docs_url="https://bridgedataoutput.com/docs/platform/",
        secret_names=("provider.zillow_bridge.access_token",),
        setup_steps=(
            "Apply for Zestimate API access through Zillow Group / Bridge Interactive and obtain written approval for your use case.",
            "Copy the server access token issued for the Zestimates dataset.",
            "Settings → Valuation providers → Zillow Bridge → paste token → Test against a known address and compare the response "
            "fields with your Bridge contract documentation before enabling.",
        ),
        coverage_notes="Coverage and permitted display depend on the Bridge/Zillow agreement.",
        terms_notes="Zillow data may only be used under an approved Bridge agreement; zillow.com pages are never scraped.",
        verified_integration=False,
    )

    def estimate(self, subject: ValuationSubject, secrets: dict[str, str], settings: dict) -> ValuationResult:
        if self.missing_secrets(secrets):
            return ValuationResult.status_only(CoverageStatus.NOT_CONFIGURED, "Zillow Bridge access token not configured (Zillow approval required)")
        with _client() as client:
            resp = client.get(self.ENDPOINT, params={"access_token": secrets["provider.zillow_bridge.access_token"], "address": subject.one_line})
        if failure := _http_failure(resp, "Zillow Bridge"):
            return failure
        return self.parse(resp.json(), subject, _evidence(resp, f"GET zestimates address={subject.one_line}"))

    @staticmethod
    def parse(data: dict, subject: ValuationSubject, evidence: EvidenceDraft | None = None) -> ValuationResult:
        bundle = data.get("bundle") or []
        if not bundle:
            return ValuationResult(CoverageStatus.NO_MATCH, notes="No Zestimate returned", evidence=evidence)
        if len(bundle) > 1:
            return ValuationResult(CoverageStatus.AMBIGUOUS, notes=f"{len(bundle)} Zestimate records returned", evidence=evidence)
        item = bundle[0]
        point = parse_float(item.get("zestimate"))
        if point is None:
            return ValuationResult(CoverageStatus.NO_MATCH, notes="Record has no zestimate value", evidence=evidence)
        low_pct, high_pct = parse_float(item.get("lowPercent")), parse_float(item.get("highPercent"))
        low = round(point * (1 - low_pct / 100)) if low_pct is not None else None
        high = round(point * (1 + high_pct / 100)) if high_pct is not None else None
        returned = item.get("address") if isinstance(item.get("address"), str) else None
        match = address_match_score(subject.one_line, returned) if returned else None
        return ValuationResult(
            coverage_status=CoverageStatus.MATCHED if (match is None or match >= 0.8) else CoverageStatus.AMBIGUOUS,
            estimate_type=EstimateType.MARKET_AVM, point=point, low=low, high=high,
            estimate_date=parse_date(str(item.get("timestamp") or "")[:10]), provider_property_id=str(item.get("zpid") or "") or None,
            source_url=f"https://www.zillow.com/homedetails/{item.get('zpid')}_zpid/" if item.get("zpid") else None,
            confidence=_confidence_from_range(point, low, high), confidence_label="derived from Zestimate range",
            match_score=match, matched_address=returned,
            notes="Unverified field mapping — confirm against your Bridge documentation", evidence=evidence,
        )


# ------------------------------------------------------------------------------------------ Sandbox


class SandboxProvider(ValuationProvider):
    descriptor = ProviderDescriptor(
        key="sandbox",
        name="SANDBOX (demo only)",
        product="Deterministic fake AVM for demos and tests",
        estimate_types=(EstimateType.SANDBOX_AVM,),
        docs_url="",
        secret_names=(),
        setup_steps=("Enabled automatically in demo projects (USI_DEMO_MODE=true or a project marked as demo).",),
        coverage_notes="Every address 'matches'. Values are synthetic.",
        terms_notes="NOT REAL DATA. Never used for non-demo projects; always labelled SANDBOX in the grid and export.",
        is_sandbox=True,
    )

    BASE = {"single_family": 260_000, "twin": 210_000, "row": 165_000, "condo": 190_000, "multi_family": 300_000,
            "land": 60_000, "commercial": 420_000, "mobile_home": 70_000}

    def estimate(self, subject: ValuationSubject, secrets: dict[str, str], settings: dict) -> ValuationResult:
        digest = int(hashlib.sha256(f"{subject.parcel}|{subject.street}".encode()).hexdigest()[:8], 16)
        base = self.BASE.get(subject.category or "", 180_000)
        factor = 0.55 + (digest % 1000) / 1000 * 0.9
        point = round(base * factor / 100) * 100
        payload = {"sandbox": True, "parcel": subject.parcel, "point": point}
        return ValuationResult(
            coverage_status=CoverageStatus.MATCHED, estimate_type=EstimateType.SANDBOX_AVM, point=point,
            low=round(point * 0.9), high=round(point * 1.1), estimate_date=date.today(), confidence=0.5,
            confidence_label="synthetic", match_score=1.0, matched_address=subject.one_line,
            notes="SANDBOX — synthetic value for demonstration only",
            evidence=EvidenceDraft(url=None, content=json.dumps(payload).encode(), content_type="application/json",
                                   request_summary="sandbox estimate", entry_method="fixture"),
        )


# ------------------------------------------------------------------------------------------ Local estimate (free, no key)


class LocalEstimateProvider(ValuationProvider):
    """Keyless market-value estimate for every property, from data already on hand — no API, no per-call cost.

    Two standard Pennsylvania methods, best-of:
      * Assessment ratio: market ≈ assessed value × the county's STEB Common Level Ratio (CLR) factor. The factor is
        editable per county in Settings; PA publishes it annually. This runs for every parcel that has an assessed value.
      * Indexed sale: when there is a recent arm's-length sale, market ≈ sale price grown by an annual appreciation rate.
    Both are clearly labelled estimates (not appraisals) with the method and factor shown, and low confidence so a real
    AVM (ATTOM/RentCast) or a value you record yourself always takes precedence in the selector.
    """

    DEFAULTS = {
        # Editable in Settings. Verify against the current STEB Common Level Ratio for each county.
        # Montgomery has not reassessed since 1998 (assessed << market) so its factor is high; Delaware reassessed
        # effective 2021 (assessed ~ market) so its factor is near 1.
        "factors": {"montco": 2.02, "delco": 1.40},
        "default_factor": 1.60,
        "annual_appreciation": 0.05,
        "max_sale_age_years": 20,
        "min_sale_price": 5000,
    }
    descriptor = ProviderDescriptor(
        key="local_estimate",
        name="Local estimate (assessment ratio / indexed sale)",
        product="Keyless PA valuation from assessed value and last sale",
        estimate_types=(EstimateType.ASSESSMENT_RATIO, EstimateType.SALE_ESTIMATE),
        docs_url="https://www.pa.gov/agencies/steb.html",
        secret_names=(),
        setup_steps=("Runs automatically for every property — no API key needed.",
                     "Set each county's current Common Level Ratio factor under Settings → Valuation providers if you want to fine-tune it."),
        coverage_notes="Covers every property that has an assessed value (from the county or the sale list). It is an estimate, not an appraisal.",
        terms_notes="Derived from public assessed values and the state Common Level Ratio; no third-party data or API is used.",
        default_settings=DEFAULTS,
    )

    def estimate(self, subject: ValuationSubject, secrets: dict[str, str], settings: dict) -> ValuationResult:
        cfg = {**self.DEFAULTS, **(settings or {})}
        factors = {**self.DEFAULTS["factors"], **(cfg.get("factors") or {})}
        factor = factors.get(subject.county, cfg.get("default_factor"))
        candidates: list[tuple[float, str, str, float]] = []  # (point, estimate_type, note, confidence)

        if subject.assessed_value and factor:
            clr = round(subject.assessed_value * float(factor) / 100) * 100
            candidates.append((clr, EstimateType.ASSESSMENT_RATIO,
                               f"Assessed ${subject.assessed_value:,.0f} × CLR factor {factor} (editable in Settings)", 0.45))

        if subject.last_sale_price and subject.last_sale_price >= cfg["min_sale_price"] and subject.last_sale_date:
            years = (date.today() - subject.last_sale_date).days / 365.25
            if 0 <= years <= cfg["max_sale_age_years"]:
                appr = float(cfg["annual_appreciation"])
                indexed = round(subject.last_sale_price * (1 + appr) ** years / 100) * 100
                conf = 0.55 if years <= 8 else 0.4
                note = f"${subject.last_sale_price:,.0f} sale on {subject.last_sale_date:%m/%d/%Y} grown {appr:.0%}/yr for {years:.0f} yr"
                # Prefer the indexed sale only when it is sane relative to the assessment estimate.
                clr_point = candidates[0][0] if candidates else None
                if clr_point is None or 0.5 * clr_point <= indexed <= 3 * clr_point:
                    candidates.insert(0, (indexed, EstimateType.SALE_ESTIMATE, note, conf))
                else:
                    candidates.append((indexed, EstimateType.SALE_ESTIMATE, note + " (diverges from assessment; not selected)", 0.25))

        if not candidates:
            reason = "No assessed value available to estimate from" if not subject.assessed_value else "No county CLR factor configured"
            return ValuationResult.status_only(CoverageStatus.NO_MATCH, reason)

        point, est_type, note, conf = candidates[0]
        payload = {"method": est_type, "point": point, "factor": factor, "assessed": subject.assessed_value,
                   "candidates": [{"point": p, "type": t, "note": n} for p, t, n, _ in candidates]}
        return ValuationResult(
            coverage_status=CoverageStatus.MATCHED, estimate_type=est_type, point=point,
            low=round(point * 0.85 / 100) * 100, high=round(point * 1.15 / 100) * 100, estimate_date=date.today(),
            confidence=conf, confidence_label="local estimate", match_score=1.0, matched_address=subject.one_line,
            notes=note,
            evidence=EvidenceDraft(url=None, content=json.dumps(payload).encode(), content_type="application/json",
                                   request_summary=f"local estimate ({est_type}) for parcel {subject.parcel}", entry_method="automated"),
        )


PROVIDERS: dict[str, ValuationProvider] = {
    p.descriptor.key: p for p in (AttomProvider(), RentCastProvider(), ZillowBridgeProvider(), LocalEstimateProvider(), SandboxProvider())
}

# Reference-workbook valuation columns that users may fill with values they looked up themselves.
MANUAL_SITES = {
    "homes_com": "Homes.com (min of range)",
    "zillow": "Zillow Zestimate",
    "realtor": "Realtor.com Real Estimate",
    "redfin": "Redfin Median Sale Price",
    "trulia": "Trulia Estimate",
    "other": "Other (manual)",
}
