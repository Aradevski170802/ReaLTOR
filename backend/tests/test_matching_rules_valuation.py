from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import respx

from app.common.parsing import parse_date
from app.connectors.capture_tables import RECORDER_SYNONYMS, extract_tables, rows_from_tables, to_recorder_documents
from app.enums import Decision, MatchDecision, MortgageStatus, RuleOutcome
from app.recorder.matching import DocView, apply_user_decisions, classify_document, evaluate_mortgages, name_similarity
from app.rules.defaults import default_rules, merge_rules, validate_rules
from app.rules.engine import RuleInputs, evaluate
from app.valuation.base import ValuationSubject, address_match_score
from app.valuation.providers import AttomProvider, RentCastProvider, SandboxProvider, ZillowBridgeProvider
from app.valuation.selector import EstimateView, select_valuation
from tests.conftest import FIXTURES


def synthetic_docs() -> list[DocView]:
    html = (FIXTURES / "capture" / "recorder_results_synthetic.html").read_text(encoding="utf-8")
    docs = to_recorder_documents(rows_from_tables(extract_tables(html), RECORDER_SYNONYMS, {"doc_type_raw"}))
    return [DocView(i, classify_document(d.doc_type_raw), d.doc_type_raw, d.instrument_number, d.book, d.page, d.recorded_date,
                    d.grantors, d.grantees, d.amount, d.related_reference) for i, d in enumerate(docs, start=1)]


def test_document_classification():
    assert classify_document("Satisfaction of Mortgage") == "satisfaction"
    assert classify_document("Deed Miscellaneous (ASSIGNMENT)") == "assignment"
    assert classify_document("Mortgage ($260,000.00)") == "mortgage"
    assert classify_document("Mortgage Release") == "release"
    assert classify_document("Mortgage Subordination") == "subordination"
    assert classify_document("Sheriffs Deed") == "deed"
    assert classify_document(None, "SAT 1362 01201") == "satisfaction"


def test_mortgage_matching_semantics():
    docs = synthetic_docs()
    by_inst = {d.instrument_number: d for d in docs}
    results = {next(d.instrument_number for d in docs if d.id == ev.mortgage_id): ev for ev in evaluate_mortgages(docs)}
    assert "1987051169" not in results  # before 1990 is not evaluated
    assert results["2016041002"].status == MortgageStatus.SATISFIED and "references the mortgage" in results["2016041002"].reason
    assert results["2011012155"].status == MortgageStatus.SATISFIED  # satisfied by the assignee, same borrower and amount
    assert results["2021058899"].status == MortgageStatus.NO_SATISFACTION  # the only later satisfaction cites another instrument
    assert results["1998021477"].status == MortgageStatus.REVIEW  # borrower matches, releasing party differs: never "unsatisfied"
    assert by_inst["2021061350"].related_reference == "2016041002"


def test_user_decisions_update_status():
    docs = synthetic_docs()
    review = next(ev for ev in evaluate_mortgages(docs) if ev.status == MortgageStatus.REVIEW)
    sat_id = review.candidates[0].satisfaction_id
    confirmed = apply_user_decisions(review, {sat_id}, set())
    assert confirmed.status == MortgageStatus.SATISFIED and confirmed.candidates[0].decision == MatchDecision.USER_CONFIRMED
    assert review.candidates[0].decision == MatchDecision.CANDIDATE  # input not mutated
    partially = apply_user_decisions(review, set(), {sat_id})
    assert partially.status == (MortgageStatus.REVIEW if len(review.candidates) > 1 else MortgageStatus.NO_SATISFACTION)
    rejected = apply_user_decisions(review, set(), {c.satisfaction_id for c in review.candidates})
    assert rejected.status == MortgageStatus.NO_SATISFACTION


def test_insufficient_data_and_unsearched():
    m = DocView(1, "mortgage", "Mortgage", None, None, None, None, None, None, 1000, None)
    assert evaluate_mortgages([m])[0].status == MortgageStatus.INSUFFICIENT
    m2 = DocView(2, "mortgage", "Mortgage", "2001000001", None, None, date(2001, 1, 1), "A", "B", 1000, None)
    assert evaluate_mortgages([m2], searched=False)[0].status == MortgageStatus.INSUFFICIENT


def test_name_similarity_tolerates_typos_and_ignores_boilerplate():
    assert name_similarity("CARRAIGE HOUSE EAST LLC", "CARRIAGE HOUSE EAST LLC; MARELLA ROBERT MG") >= 0.8
    assert name_similarity("WELLS FARGO BANK NA", "WELLS FARGO BANK N A") == 1.0
    assert name_similarity("BANK OF AMERICA", "CITIBANK NA") == 0.0
    assert name_similarity("MORTGAGE ELECTRONIC REGISTRATION SYSTEMS", "MERS") == 0.0


# ------------------------------------------------------------------------------------------------ rules


def inputs(**kw) -> RuleInputs:
    now = datetime.now(UTC)
    base = dict(county="delco", category="single_family", living_area_sqft=1200, selected_value=250000, tax_checked=True,
                tax_status_category="listed", tax_status_text="Listed for Upset Sale", tax_checked_at=now, now=now,
                recorder_searched=True, mortgage_statuses=[], lien_searched=True)
    base.update(kw)
    return RuleInputs(**base)


def test_rules_defaults_validate_and_merge():
    cfg = default_rules()
    assert validate_rules(cfg) == []
    patched = merge_rules(cfg, {"valuation": {"interest_threshold": 250000}})
    assert patched["valuation"]["interest_threshold"] == 250000 and patched["size"]["condo_min_sqft"] == 600
    assert validate_rules(merge_rules(cfg, {"size": {"commercial_policy": "maybe"}}))


def test_interested_when_all_rules_pass():
    result = evaluate(inputs(), default_rules())
    assert result.decision == Decision.INTERESTED
    assert "PASS: Selected valuation $250,000" in result.summary


def test_valuation_below_threshold_is_not_interested_and_orange():
    result = evaluate(inputs(selected_value=161600), default_rules())
    assert result.decision == Decision.NOT_INTERESTED and result.flags["valuation_orange"]


@pytest.mark.parametrize("category,sqft,acres,expected", [
    ("single_family", 899, None, RuleOutcome.FAIL), ("twin", 900, None, RuleOutcome.PASS), ("row", None, None, RuleOutcome.UNKNOWN),
    ("condo", 600, None, RuleOutcome.PASS), ("condo", 599, None, RuleOutcome.FAIL), ("land", None, 0.5, RuleOutcome.FAIL),
    ("land", None, 1.0, RuleOutcome.PASS), ("land", None, None, RuleOutcome.UNKNOWN), ("commercial", None, None, RuleOutcome.REVIEW),
    ("unknown", None, None, RuleOutcome.UNKNOWN),
])
def test_size_rules(category, sqft, acres, expected):
    result = evaluate(inputs(category=category, living_area_sqft=sqft, acres=acres), default_rules())
    size = next(r for r in result.results if r.rule_key == "property_size")
    assert size.outcome == expected, size.message


def test_estimated_acreage_near_threshold_requires_review():
    result = evaluate(inputs(category="land", acres=0.95, acres_estimated=True), default_rules())
    assert next(r for r in result.results if r.rule_key == "property_size").outcome == RuleOutcome.REVIEW


def test_montco_zero_2024_balance_resolved_and_override():
    cfg = default_rules()
    resolved = evaluate(inputs(county="montco", has_2024_claim=True, balance_2024=0.0), cfg)
    assert resolved.decision == Decision.REMOVED and resolved.flags["row_red"]
    overridden = evaluate(inputs(county="montco", has_2024_claim=True, balance_2024=0.0, tax_resolution_override="Paid by check that bounced"), cfg)
    assert overridden.decision == Decision.INTERESTED


def test_delco_status_not_listed_or_missing():
    cfg = default_rules()
    removed = evaluate(inputs(tax_status_category="resolved", tax_status_text="Paid"), cfg)
    assert removed.decision == Decision.REMOVED and removed.flags["status_red"]
    missing = evaluate(inputs(tax_status_category="unknown", tax_status_text=None), cfg)
    assert missing.decision == Decision.REVIEW


def test_missing_essentials_and_mortgage_policies():
    cfg = default_rules()
    assert evaluate(inputs(selected_value=None), cfg).decision == Decision.INSUFFICIENT
    assert evaluate(inputs(recorder_searched=False), cfg).decision == Decision.REVIEW
    assert evaluate(inputs(mortgage_statuses=[MortgageStatus.NO_SATISFACTION]), cfg).decision == Decision.REVIEW
    strict = merge_rules(cfg, {"mortgage": {"no_satisfaction_policy": "fail"}})
    assert evaluate(inputs(mortgage_statuses=[MortgageStatus.NO_SATISFACTION]), strict).decision == Decision.NOT_INTERESTED
    assert evaluate(inputs(open_lien_cases=1), cfg).decision == Decision.REVIEW
    stale = evaluate(inputs(tax_checked_at=datetime.now(UTC) - timedelta(hours=72)), cfg)
    assert stale.decision == Decision.REVIEW and "STALE" in stale.summary


def test_decision_override_is_explained():
    result = evaluate(inputs(selected_value=100000, decision_override="Interested", decision_override_reason="Comparable sales support value"), default_rules())
    assert result.decision == "Interested" and "MANUAL OVERRIDE" in result.summary and "Not interested" in result.summary


# ------------------------------------------------------------------------------------------------ valuation


SUBJECT = ValuationSubject(1, "montco", "02-00-01016-00-8", "1002 DEKALB ST", "Bridgeport", zip_code="19405", category="multi_family")


def test_provider_response_parsing():
    rc = RentCastProvider.parse({"price": 315000, "priceRangeLow": 280000, "priceRangeHigh": 350000,
                                 "subjectProperty": {"id": "x1", "formattedAddress": "1002 Dekalb St, Bridgeport, PA 19405"},
                                 "comparables": [{}, {}]}, SUBJECT)
    assert rc.coverage_status == "matched" and rc.point == 315000 and rc.match_score == 1.0 and 0 < rc.confidence < 1
    wrong = RentCastProvider.parse({"price": 1, "subjectProperty": {"formattedAddress": "1004 Dekalb St, Bridgeport, PA"}}, SUBJECT)
    assert wrong.coverage_status == "ambiguous"
    attom = AttomProvider.parse({"status": {"msg": "SuccessWithResult"}, "property": [{"identifier": {"attomId": 42},
                                 "address": {"oneLine": "1002 DEKALB ST, BRIDGEPORT, PA 19405"},
                                 "avm": {"eventDate": "2026-08-01", "amount": {"value": 319700, "low": 290000, "high": 350000, "scr": 88}}}]}, SUBJECT)
    assert attom.point == 319700 and attom.confidence == 0.88 and attom.estimate_date == parse_date("2026-08-01")
    assert AttomProvider.parse({"status": {"msg": "SuccessWithoutResult"}, "property": []}, SUBJECT).coverage_status == "no_match"
    zb = ZillowBridgeProvider.parse({"bundle": [{"zpid": 9, "zestimate": 300000, "lowPercent": 10, "highPercent": 10}]}, SUBJECT)
    assert zb.low == 270000 and zb.high == 330000


def test_missing_keys_report_not_configured():
    assert RentCastProvider().estimate(SUBJECT, {}, {}).coverage_status == "not_configured"
    assert AttomProvider().estimate(SUBJECT, {}, {}).coverage_status == "not_configured"


@respx.mock
def test_rentcast_http_call_uses_header_key(respx_mock):
    route = respx_mock.get("https://api.rentcast.io/v1/avm/value").mock(return_value=httpx.Response(
        200, json={"price": 305000, "priceRangeLow": 290000, "priceRangeHigh": 320000,
                   "subjectProperty": {"formattedAddress": "1002 Dekalb St, Bridgeport, PA 19405"}}))
    result = RentCastProvider().estimate(SUBJECT, {"provider.rentcast.api_key": "rc_test_key_123"}, {})
    assert result.point == 305000 and route.calls[0].request.headers["X-Api-Key"] == "rc_test_key_123"
    assert "rc_test_key_123" not in (result.evidence.url or "")


def test_sandbox_is_deterministic():
    a = SandboxProvider().estimate(SUBJECT, {}, {})
    assert a.point == SandboxProvider().estimate(SUBJECT, {}, {}).point and a.estimate_type == "sandbox_avm"


def ev(id_, key, point, **kw):
    base = dict(id=id_, provider_key=key, provider_name=key, coverage_status="matched", estimate_type="market_avm", point=point,
                low=None, high=None, confidence=0.8, match_score=1.0, estimate_date=None, is_sandbox=False, entry_method="automated")
    base.update(kw)
    return EstimateView(**base)


def test_selector_strategies():
    cfg = default_rules()["valuation_selection"]
    estimates = [ev(1, "rentcast", 305000), ev(2, "attom", 319700), ev(3, "zillow", 290000, estimate_type="manual_observation", entry_method="manual")]
    primary = select_valuation(estimates, cfg, False, ["attom", "rentcast"])
    assert primary.method == "primary_avm" and primary.value == 319700 and "ATTOM" in primary.explanation
    lowest = select_valuation(estimates, cfg | {"strategy": "lowest_credible"}, False, ["attom"])
    assert lowest.method == "lowest_credible" and lowest.value == 290000
    low_conf = select_valuation([ev(1, "attom", 300000, confidence=0.2), ev(2, "zillow", 250000, estimate_type="manual_observation", entry_method="manual")], cfg, False, ["attom"])
    assert low_conf.method == "lowest_credible" and low_conf.value == 250000
    sandbox = [ev(1, "sandbox", 222000, estimate_type="sandbox_avm", is_sandbox=True)]
    assert select_valuation(sandbox, cfg, False, []).value is None
    assert select_valuation(sandbox, cfg, True, ["sandbox"]).value == 222000
    none = select_valuation([], cfg, False, [])
    assert none.value is None and "No permitted valuation source" in none.explanation


def test_address_match_score():
    assert address_match_score("1002 DEKALB ST, Bridgeport, PA", "1002 Dekalb Street, Bridgeport") == 1.0
    assert address_match_score("1002 DEKALB ST", "1004 DEKALB ST") == 0.0
