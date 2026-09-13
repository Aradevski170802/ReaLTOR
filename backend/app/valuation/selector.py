"""Transparent "Selected Market Valuation" derivation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.enums import CoverageStatus, EstimateType

PROVIDER_LABELS = {"attom": "ATTOM AVM", "rentcast": "RentCast AVM", "zillow_bridge": "Zillow Zestimate (Bridge)",
                   "sandbox": "SANDBOX AVM"}


@dataclass
class EstimateView:
    id: int
    provider_key: str
    provider_name: str
    coverage_status: str
    estimate_type: str | None
    point: float | None
    low: float | None
    high: float | None
    confidence: float | None
    match_score: float | None
    estimate_date: date | None
    is_sandbox: bool
    entry_method: str

    @property
    def label(self) -> str:
        return PROVIDER_LABELS.get(self.provider_key, self.provider_name)


@dataclass
class Selection:
    value: float | None
    method: str  # primary_avm | lowest_credible | unknown
    provider_key: str | None
    estimate_type: str | None
    valuation_id: int | None
    explanation: str


def _money(v: float | None) -> str:
    return "n/a" if v is None else f"${v:,.0f}"


def select_valuation(estimates: list[EstimateView], config: dict, is_demo: bool, configured_providers: list[str]) -> Selection:
    strategy = config.get("strategy", "primary_then_lowest")
    min_conf = float(config.get("min_confidence", 0.5))
    min_match = float(config.get("min_match_score", 0.8))
    credible_types = set(config.get("credible_estimate_types", [EstimateType.MARKET_AVM, EstimateType.SALE_ESTIMATE,
                                                                 EstimateType.MANUAL_OBSERVATION]))
    if is_demo:
        credible_types.add(EstimateType.SANDBOX_AVM)

    def usable(e: EstimateView) -> tuple[bool, str]:
        if e.is_sandbox and not is_demo:
            return False, "sandbox value ignored outside demo projects"
        if e.coverage_status != CoverageStatus.MATCHED:
            return False, f"coverage {e.coverage_status}"
        if e.point is None:
            return False, "no point estimate"
        if e.estimate_type not in credible_types:
            return False, f"estimate type {e.estimate_type} not accepted"
        if e.entry_method != "manual" and e.match_score is not None and e.match_score < min_match:
            return False, f"address match {e.match_score:.2f} < {min_match}"
        return True, ""

    considered = []
    usable_list: list[EstimateView] = []
    for e in estimates:
        ok, why = usable(e)
        considered.append(f"{e.label}: {_money(e.point)}" + ("" if ok else f" (excluded: {why})"))
        if ok:
            usable_list.append(e)

    if strategy == "primary_then_lowest":
        for key in config.get("primary_providers", ["attom", "rentcast", "zillow_bridge"]) + (["sandbox"] if is_demo else []):
            for e in usable_list:
                if e.provider_key == key and e.estimate_type in (EstimateType.MARKET_AVM, EstimateType.SANDBOX_AVM) \
                        and (e.confidence is None or e.confidence >= min_conf):
                    conf = "" if e.confidence is None else f", confidence {e.confidence:.2f}"
                    return Selection(
                        e.point, "primary_avm", e.provider_key, e.estimate_type, e.id,
                        f"Primary AVM: {e.label} point estimate {_money(e.point)}{conf}"
                        + (f", as of {e.estimate_date.isoformat()}" if e.estimate_date else "")
                        + (f". Also considered: {'; '.join(considered)}" if len(considered) > 1 else ""),
                    )

    if usable_list:
        lowest = min(usable_list, key=lambda e: e.point or 0)
        return Selection(
            lowest.point, "lowest_credible", lowest.provider_key, lowest.estimate_type, lowest.id,
            f"Lowest credible published estimate: {lowest.label} {_money(lowest.point)} "
            f"({lowest.estimate_type}). Considered: {'; '.join(considered)}",
        )

    if not estimates:
        reason = ("No permitted valuation source available — configure ATTOM, RentCast or an approved Zillow Bridge "
                  "integration, or enter a value you looked up yourself") if not configured_providers else \
                 "Configured providers have not returned an estimate yet"
    else:
        reason = "No credible estimate: " + "; ".join(considered)
    return Selection(None, "unknown", None, None, None, reason)
