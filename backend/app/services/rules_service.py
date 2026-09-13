"""Active ruleset management and per-property evaluation."""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.db import utcnow
from app.enums import TaxStatusCategory
from app.models import Property, RuleEvaluation, RuleSet
from app.rules.defaults import default_rules, merge_rules, validate_rules
from app.rules.engine import DecisionResult, RuleInputs, evaluate
from app.services.snapshot import PropertySnapshot, load_snapshots


def active_ruleset(session: Session) -> RuleSet:
    rs = session.scalar(select(RuleSet).where(RuleSet.is_active.is_(True)).order_by(RuleSet.version.desc()))
    if rs is None:
        rs = RuleSet(version=1, name="Default screening criteria", config=default_rules(), is_active=True,
                     change_reason="Initial defaults from Criteria.txt", created_by="system")
        session.add(rs)
        session.flush()
    else:
        merged = merge_rules(default_rules(), rs.config)
        if merged != rs.config:
            rs.config = merged  # new keys introduced by an upgrade
    return rs


def active_config(session: Session) -> dict:
    return active_ruleset(session).config


def update_ruleset(session: Session, patch: dict, actor: str, reason: str) -> RuleSet:
    current = active_ruleset(session)
    config = merge_rules(current.config, patch)
    errors = validate_rules(config)
    if errors:
        raise ValueError("; ".join(errors))
    version = (session.scalar(select(func.max(RuleSet.version))) or 0) + 1
    session.execute(update(RuleSet).values(is_active=False))
    rs = RuleSet(version=version, name=f"Screening criteria v{version}", config=config, is_active=True,
                 change_reason=reason, created_by=actor)
    session.add(rs)
    session.flush()
    record_audit(session, actor, "rules.update", "rule_set", rs.id, None,
                 {"version": version, "reason": reason, "patch": patch, "previous_version": current.version})
    return rs


def build_inputs(snap: PropertySnapshot) -> RuleInputs:
    p = snap.prop
    county = p.county
    tax = snap.tax_claim
    y2024 = snap.tax_year(2024, "claim")
    living = snap.value("living_area_sqft")
    living_source = "Sq Ft Living Area"
    if county == "delco":
        base = snap.value("base_area_sqft")
        if base is not None:
            living, living_source = base, "Base Area"
    acres_cell = snap.cell("acres")
    overrides = snap.overrides
    tax_ov = overrides.get("tax_status_interpretation")
    dec_ov = overrides.get("decision_status")
    return RuleInputs(
        county=county,
        category=snap.value("category"),
        category_basis=snap.cell("category").notes,
        living_area_sqft=living,
        living_area_source=living_source,
        acres=acres_cell.value,
        acres_estimated=acres_cell.quality == "estimated",
        gross_building_area=snap.value("gross_building_area"),
        units=snap.value("total_living_units") or snap.value("commercial_units"),
        selected_value=snap.value("selected_valuation"),
        selected_method=snap.value("selected_valuation_method"),
        selected_explanation=snap.cell("valuation_detail").value,
        assessment_value=snap.value("assessed_value"),
        tax_checked=tax is not None,
        tax_status_category=tax.status_category if tax else None,
        tax_status_text=tax.tax_sale_status if tax else None,
        balance_2024=y2024.balance if y2024 else None,
        has_2024_claim=y2024 is not None,
        tax_checked_at=tax.retrieved_at if tax else None,
        recorder_searched=snap.recorder_searched,
        mortgage_statuses=[m.effective_status for m in snap.mortgages_since()],
        lien_searched=snap.lien_searched,
        open_lien_cases=len([c for c in snap.liens if c.is_relevant]),
        extraction_confidence=p.extraction_confidence,
        extraction_needs_review=p.needs_review,
        tax_resolution_override=f"{tax_ov.override_value} ({tax_ov.reason})" if tax_ov else None,
        decision_override=dec_ov.override_value if dec_ov else None,
        decision_override_reason=dec_ov.reason if dec_ov else None,
        now=snap.now,
    )


def evaluate_snapshot(session: Session, snap: PropertySnapshot, ruleset: RuleSet, actor: str = "system") -> DecisionResult:
    result = evaluate(build_inputs(snap), ruleset.config)
    prop = snap.prop
    session.execute(update(RuleEvaluation).where(RuleEvaluation.property_id == prop.id, RuleEvaluation.is_current.is_(True))
                    .values(is_current=False))
    now = utcnow()
    for r in result.results:
        session.add(RuleEvaluation(property_id=prop.id, ruleset_id=ruleset.id, rule_key=r.rule_key, outcome=r.outcome,
                                   message=r.message, inputs=r.inputs, evaluated_at=now))
    changed = prop.decision_status != result.decision
    previous = prop.decision_status
    prop.decision_status = result.decision
    prop.decision_summary = result.summary
    prop.decision_at = now
    if changed:
        record_audit(session, actor, "rules.decision_changed", "property", prop.id, prop.project_id,
                     {"from": previous, "to": result.decision, "ruleset_version": ruleset.version})
    return result


def evaluate_properties(session: Session, property_ids: list[int], actor: str = "system") -> dict[int, str]:
    ruleset = active_ruleset(session)
    out = {}
    snaps = load_snapshots(session, property_ids, ruleset.config)
    for pid, snap in snaps.items():
        out[pid] = evaluate_snapshot(session, snap, ruleset, actor).decision
    session.flush()
    return out


def project_property_ids(session: Session, project_id: int) -> list[int]:
    return list(session.scalars(select(Property.id).where(Property.project_id == project_id, Property.excluded.is_(False))))


__all__ = ["active_ruleset", "active_config", "update_ruleset", "evaluate_properties", "build_inputs", "TaxStatusCategory"]
