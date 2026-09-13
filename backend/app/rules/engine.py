"""Investment-screening rule engine.

Pure functions over a RuleInputs snapshot so every evaluation is reproducible and auditable. Each rule yields
pass / fail / unknown / review / resolved with a plain-language message and the inputs it used.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

from app.enums import Decision, MortgageStatus, RuleOutcome, TaxStatusCategory
from app.enums import PropertyCategory as C

RESIDENTIAL_MIN_KEYS = {C.SINGLE_FAMILY: "single_family_min_sqft", C.TWIN: "twin_min_sqft", C.ROW: "row_min_sqft",
                        C.CONDO: "condo_min_sqft"}
CATEGORY_LABEL = {C.SINGLE_FAMILY: "single-family", C.TWIN: "twin", C.ROW: "row home", C.CONDO: "condo", C.LAND: "land",
                  C.COMMERCIAL: "commercial", C.MULTI_FAMILY: "multi-family", C.MOBILE_HOME: "mobile home",
                  C.OTHER_RESIDENTIAL: "other residential", C.EXEMPT: "exempt", C.OTHER: "other", C.UNKNOWN: "unknown"}


@dataclass
class RuleInputs:
    county: str
    category: str | None = None
    category_basis: str | None = None
    living_area_sqft: float | None = None
    living_area_source: str | None = None
    acres: float | None = None
    acres_estimated: bool = False
    gross_building_area: float | None = None
    units: int | None = None
    selected_value: float | None = None
    selected_method: str | None = None
    selected_explanation: str | None = None
    assessment_value: float | None = None
    tax_checked: bool = False
    tax_status_category: str | None = None
    tax_status_text: str | None = None
    balance_2024: float | None = None
    has_2024_claim: bool = False
    tax_checked_at: datetime | None = None
    recorder_searched: bool = False
    mortgage_statuses: list[str] = field(default_factory=list)
    lien_searched: bool = False
    open_lien_cases: int = 0
    extraction_confidence: float | None = None
    extraction_needs_review: bool = False
    tax_resolution_override: str | None = None
    decision_override: str | None = None
    decision_override_reason: str | None = None
    now: datetime | None = None


@dataclass
class RuleResult:
    rule_key: str
    title: str
    outcome: str
    message: str
    inputs: dict


@dataclass
class DecisionResult:
    decision: str
    summary: str
    results: list[RuleResult]
    flags: dict


def _money(v: float | None) -> str:
    return "n/a" if v is None else f"${v:,.0f}"


def _policy_outcome(policy: str) -> str:
    return {"fail": RuleOutcome.FAIL, "pass": RuleOutcome.PASS, "informational": RuleOutcome.PASS}.get(policy, RuleOutcome.REVIEW)


def rule_tax_status(i: RuleInputs, cfg: dict) -> RuleResult:
    tax = cfg["tax"]
    inputs = {"county": i.county, "status": i.tax_status_text, "category": i.tax_status_category, "balance_2024": i.balance_2024,
              "checked_at": i.tax_checked_at.isoformat() if i.tax_checked_at else None}
    title = "Tax sale status"
    if i.tax_resolution_override:
        return RuleResult("tax_status", title, RuleOutcome.PASS, f"User override of tax-status interpretation: {i.tax_resolution_override}", inputs)
    if not i.tax_checked:
        outcome = RuleOutcome.UNKNOWN if tax["missing_status_policy"] == "insufficient" else RuleOutcome.REVIEW
        return RuleResult("tax_status", title, outcome, "Tax-sale status has not been retrieved", inputs)
    if i.now and i.tax_checked_at and (i.now - i.tax_checked_at).total_seconds() > tax["stale_after_hours"] * 3600:
        hours = int((i.now - i.tax_checked_at).total_seconds() // 3600)
        stale_note = f" (STALE: last checked {hours}h ago)"
    else:
        stale_note = ""
    if i.county == "montco":
        if i.has_2024_claim and i.balance_2024 is not None and abs(i.balance_2024) < 0.005 and tax["montco_zero_2024_balance_resolved"]:
            return RuleResult("tax_status", title, RuleOutcome.RESOLVED,
                              "Claim-year 2024 balance is $0.00 — Resolved / no 2024 balance (entire row highlighted red)" + stale_note, inputs)
        if i.has_2024_claim and (i.balance_2024 or 0) > 0:
            outcome = RuleOutcome.REVIEW if stale_note else RuleOutcome.PASS
            return RuleResult("tax_status", title, outcome, f"2024 claim balance ${i.balance_2024:,.2f} outstanding" + stale_note, inputs)
        return RuleResult("tax_status", title, RuleOutcome.REVIEW, "No 2024 claim year found on the Tax Claim Bureau record" + stale_note, inputs)
    # Delaware County
    if i.tax_status_category == TaxStatusCategory.LISTED:
        outcome = RuleOutcome.REVIEW if stale_note else RuleOutcome.PASS
        return RuleResult("tax_status", title, outcome, "Status 'Listed for Upset Sale' (green)" + stale_note, inputs)
    if i.tax_status_category in (TaxStatusCategory.NOT_LISTED, TaxStatusCategory.RESOLVED):
        if tax["delco_not_listed_policy"] == "removed":
            return RuleResult("tax_status", title, RuleOutcome.RESOLVED,
                              f"Status '{i.tax_status_text}' — not listed for upset sale (red)" + stale_note, inputs)
        return RuleResult("tax_status", title, RuleOutcome.REVIEW, f"Status '{i.tax_status_text}' — verify (red)" + stale_note, inputs)
    return RuleResult("tax_status", title, RuleOutcome.REVIEW, "Tax Sale Information status is missing (red)" + stale_note, inputs)


def rule_valuation(i: RuleInputs, cfg: dict) -> RuleResult:
    threshold = cfg["valuation"]["interest_threshold"]
    inputs = {"selected_value": i.selected_value, "method": i.selected_method, "threshold": threshold}
    title = "Market valuation"
    if i.selected_value is None:
        return RuleResult("valuation_threshold", title, RuleOutcome.UNKNOWN,
                          f"No selected market valuation ({i.selected_explanation or 'no permitted source'})", inputs)
    if i.selected_value < threshold:
        return RuleResult("valuation_threshold", title, RuleOutcome.FAIL,
                          f"Selected valuation {_money(i.selected_value)} is below {_money(threshold)} — not interested (orange)", inputs)
    return RuleResult("valuation_threshold", title, RuleOutcome.PASS,
                      f"Selected valuation {_money(i.selected_value)} meets {_money(threshold)}", inputs)


def rule_size(i: RuleInputs, cfg: dict) -> RuleResult:
    size = cfg["size"]
    cat = i.category or C.UNKNOWN
    label = CATEGORY_LABEL.get(cat, cat)
    inputs = {"category": cat, "basis": i.category_basis, "living_area_sqft": i.living_area_sqft, "acres": i.acres,
              "acres_estimated": i.acres_estimated, "gross_building_area": i.gross_building_area, "units": i.units}
    title = "Property type & size"
    if cat in RESIDENTIAL_MIN_KEYS:
        minimum = size[RESIDENTIAL_MIN_KEYS[cat]]
        inputs["minimum_sqft"] = minimum
        if i.living_area_sqft is None:
            return RuleResult("property_size", title, RuleOutcome.UNKNOWN, f"{label.capitalize()}: living area unknown (minimum {minimum:,} sq ft)", inputs)
        if i.living_area_sqft < minimum:
            return RuleResult("property_size", title, RuleOutcome.FAIL,
                              f"{label.capitalize()}: {i.living_area_sqft:,.0f} sq ft is below the {minimum:,} sq ft minimum", inputs)
        return RuleResult("property_size", title, RuleOutcome.PASS,
                          f"{label.capitalize()}: {i.living_area_sqft:,.0f} sq ft meets the {minimum:,} sq ft minimum", inputs)
    if cat == C.LAND:
        minimum = size["land_min_acres"]
        inputs["minimum_acres"] = minimum
        if i.acres is None:
            return RuleResult("property_size", title, RuleOutcome.UNKNOWN, f"Land: acreage unknown (minimum {minimum} acre)", inputs)
        est = " (GIS-calculated estimate)" if i.acres_estimated else ""
        if i.acres < minimum:
            outcome = RuleOutcome.REVIEW if i.acres_estimated and i.acres >= minimum * 0.9 else RuleOutcome.FAIL
            return RuleResult("property_size", title, outcome, f"Land: {i.acres:.3f} acres{est} is below the {minimum} acre minimum", inputs)
        outcome = RuleOutcome.REVIEW if i.acres_estimated and i.acres < minimum * 1.1 else RuleOutcome.PASS
        return RuleResult("property_size", title, outcome, f"Land: {i.acres:.3f} acres{est} meets the {minimum} acre minimum", inputs)
    policy_key = {C.COMMERCIAL: "commercial_policy", C.MULTI_FAMILY: "multi_family_policy",
                  C.MOBILE_HOME: "mobile_home_policy"}.get(cat, "other_policy")
    if cat == C.UNKNOWN:
        return RuleResult("property_size", title, RuleOutcome.UNKNOWN, "Property type unknown; size rule cannot be applied", inputs)
    metrics = ", ".join(x for x in (
        f"gross building area {i.gross_building_area:,.0f} sq ft" if i.gross_building_area else None,
        f"{i.units} unit(s)" if i.units else None,
        f"living area {i.living_area_sqft:,.0f} sq ft" if i.living_area_sqft else None,
    ) if x) or "no size metrics"
    policy = size[policy_key]
    return RuleResult("property_size", title, _policy_outcome(policy),
                      f"{label.capitalize()}: no automatic size rule (policy '{policy}'); {metrics}", inputs)


def rule_mortgages(i: RuleInputs, cfg: dict) -> RuleResult:
    m = cfg["mortgage"]
    counts: dict[str, int] = {}
    for s in i.mortgage_statuses:
        counts[s] = counts.get(s, 0) + 1
    inputs = {"searched": i.recorder_searched, "counts": counts, "since_year": m["since_year"]}
    title = f"Mortgages since {m['since_year']}"
    if not i.recorder_searched:
        return RuleResult("mortgages", title, RuleOutcome.UNKNOWN, "Recorder of Deeds documents have not been captured/searched", inputs)
    total = len(i.mortgage_statuses)
    if total == 0:
        return RuleResult("mortgages", title, RuleOutcome.PASS, f"No mortgages recorded since {m['since_year']} in the searched index", inputs)
    unsat = counts.get(MortgageStatus.NO_SATISFACTION, 0)
    review = counts.get(MortgageStatus.REVIEW, 0) + counts.get(MortgageStatus.INSUFFICIENT, 0)
    parts = [f"{total} mortgage(s)", f"{counts.get(MortgageStatus.SATISFIED, 0)} satisfied"]
    if unsat:
        parts.append(f"{unsat} with no satisfaction found (red)")
    if review:
        parts.append(f"{review} needing review (yellow)")
    message = ", ".join(parts)
    if unsat:
        policy = m["no_satisfaction_policy"]
        return RuleResult("mortgages", title, _policy_outcome(policy), message + f" — policy '{policy}'", inputs)
    if review:
        return RuleResult("mortgages", title, _policy_outcome(m["potential_match_policy"]), message, inputs)
    return RuleResult("mortgages", title, RuleOutcome.PASS, message, inputs)


def rule_liens(i: RuleInputs, cfg: dict) -> RuleResult:
    policy = cfg["liens"]["open_lien_policy"]
    inputs = {"searched": i.lien_searched, "open_lien_cases": i.open_lien_cases}
    title = "Civil lien cases"
    if not i.lien_searched:
        return RuleResult("liens", title, RuleOutcome.UNKNOWN, "Civil/lien case search not completed", inputs)
    if i.open_lien_cases:
        return RuleResult("liens", title, _policy_outcome(policy), f"{i.open_lien_cases} relevant lien case(s) found — policy '{policy}'", inputs)
    return RuleResult("liens", title, RuleOutcome.PASS, "No relevant lien cases found", inputs)


def rule_data_quality(i: RuleInputs, cfg: dict) -> RuleResult:
    minimum = cfg["decision"]["min_extraction_confidence"]
    inputs = {"extraction_confidence": i.extraction_confidence, "needs_review": i.extraction_needs_review}
    title = "Sale-list extraction"
    if i.extraction_needs_review or (i.extraction_confidence is not None and i.extraction_confidence < minimum):
        return RuleResult("data_quality", title, RuleOutcome.REVIEW, "Sale-list row was flagged for extraction review", inputs)
    return RuleResult("data_quality", title, RuleOutcome.PASS, "Sale-list values extracted with high confidence", inputs)


def rule_assessment_flag(i: RuleInputs, cfg: dict) -> RuleResult:
    threshold = cfg["assessment"]["highlight_below"]
    inputs = {"assessment": i.assessment_value, "threshold": threshold}
    title = "Assessment (informational)"
    if i.assessment_value is None:
        return RuleResult("assessment_flag", title, RuleOutcome.NOT_APPLICABLE, "Assessment value not available", inputs)
    if i.assessment_value < threshold:
        return RuleResult("assessment_flag", title, RuleOutcome.NOT_APPLICABLE,
                          f"Assessment {_money(i.assessment_value)} is below {_money(threshold)} (highlighted orange; informational)", inputs)
    return RuleResult("assessment_flag", title, RuleOutcome.NOT_APPLICABLE, f"Assessment {_money(i.assessment_value)}", inputs)


RULES = (rule_tax_status, rule_valuation, rule_size, rule_mortgages, rule_liens, rule_data_quality, rule_assessment_flag)
MARK = {RuleOutcome.PASS: "PASS", RuleOutcome.FAIL: "FAIL", RuleOutcome.UNKNOWN: "UNKNOWN", RuleOutcome.REVIEW: "REVIEW",
        RuleOutcome.RESOLVED: "RESOLVED", RuleOutcome.NOT_APPLICABLE: "INFO"}


def evaluate(i: RuleInputs, cfg: dict) -> DecisionResult:
    results = [rule(i, cfg) for rule in RULES]
    by_key = {r.rule_key: r for r in results}
    essential = cfg["decision"]["essential_rules"]
    blocking = ["tax_status", "valuation_threshold", "property_size", "mortgages", "data_quality", "liens"]

    def unknown_blocks(key: str) -> bool:
        if key == "mortgages":
            return cfg["mortgage"]["require_recorder_search"]
        if key == "liens":
            return cfg["liens"]["require_lien_search"]
        return True

    if by_key["tax_status"].outcome == RuleOutcome.RESOLVED:
        decision = Decision.REMOVED
    elif any(by_key[k].outcome == RuleOutcome.FAIL for k in blocking):
        decision = Decision.NOT_INTERESTED
    elif any(by_key[k].outcome == RuleOutcome.UNKNOWN for k in essential):
        decision = Decision.INSUFFICIENT
    elif any(by_key[k].outcome == RuleOutcome.REVIEW for k in blocking) or any(
        by_key[k].outcome == RuleOutcome.UNKNOWN and unknown_blocks(k) for k in blocking
    ):
        decision = Decision.REVIEW
    else:
        decision = Decision.INTERESTED

    if i.decision_override:
        summary_prefix = f"MANUAL OVERRIDE ({i.decision_override_reason or 'no reason given'}); computed: {decision}. "
        decision = i.decision_override
    else:
        summary_prefix = ""
    lines = [f"{MARK.get(r.outcome, r.outcome.upper())}: {r.message}" for r in results if r.rule_key != "assessment_flag"]
    summary = summary_prefix + " | ".join(lines)
    flags = {
        "valuation_orange": by_key["valuation_threshold"].outcome == RuleOutcome.FAIL,
        "assessment_orange": i.assessment_value is not None and i.assessment_value < cfg["assessment"]["highlight_below"],
        "row_red": i.county == "montco" and by_key["tax_status"].outcome == RuleOutcome.RESOLVED,
        "status_green": i.county == "delco" and i.tax_status_category == TaxStatusCategory.LISTED,
        "status_red": i.county == "delco" and i.tax_status_category != TaxStatusCategory.LISTED,
    }
    return DecisionResult(decision=str(decision), summary=summary, results=results, flags=flags)


def inputs_dict(i: RuleInputs) -> dict:
    data = asdict(i)
    for key, value in data.items():
        if isinstance(value, datetime):
            data[key] = value.isoformat()
    return data
