import json

import httpx
import pytest
import respx

from app.common.parcels import normalize_parcel
from app.connectors.base import PropertyRef
from app.connectors.capture_tables import extract_tables
from app.connectors.delco.civil import DelcoCivilAdapter
from app.connectors.delco.gis import DelcoParcelsGisAdapter
from app.connectors.delco.portal import DelcoAssessmentPortalAdapter, status_category
from app.connectors.montco.civil import MontcoCivilAdapter
from app.connectors.montco.gis import MontcoAssessmentGisAdapter
from app.connectors.montco.recorder import MontcoRecorderAdapter
from app.connectors.montco.tax_claim import MontcoTaxClaimAdapter
from app.connectors.registry import all_sources, get_connector
from app.enums import AccessMethod, LookupStatus
from app.seed import fill_delco_template
from tests.conftest import FIXTURES, gis_router, taxclaim_router


def ref(county: str, parcel: str, owner: str = "SAMPLE OWNER A") -> PropertyRef:
    return PropertyRef(1, county, normalize_parcel(county, parcel), owner, "1 MAIN ST", "AMBLER", {})


def test_every_source_declares_compliance_metadata():
    keys = set()
    for adapter in all_sources():
        d = adapter.descriptor
        assert d.key not in keys
        keys.add(d.key)
        assert d.name and d.base_urls and d.searches and d.terms_notes and d.compliance_status
        assert d.rate_limit_per_minute >= 1 and d.cache_ttl_hours > 0
        if d.access_method in (AccessMethod.USER_ASSISTED, AccessMethod.AUTHENTICATED_BROWSER):
            assert adapter.capture_request(ref(d.county, "02-00-02904-00-1" if d.county == "montco" else "01-00-00254-00")) is not None
    assert {"montco.assessment_gis", "montco.tax_claim", "montco.recorder", "montco.civil", "delco.parcels_gis",
            "delco.assessment_portal", "delco.recorder_countyweb", "delco.civil"} <= keys
    assert get_connector("delco").label == "Delaware County, Pennsylvania"


def test_robots_disallowed_portals_are_not_automated():
    montco = get_connector("montco")
    portal = montco.source("montco.assessment_portal").descriptor
    recorder = montco.source("montco.recorder").descriptor
    assert not portal.automated and "Disallow" in portal.terms_notes
    assert not recorder.automated and "never stored" in recorder.terms_notes
    assert montco.source("montco.tax_claim").descriptor.requires_terms_ack


@respx.mock(assert_all_called=False)
def test_montco_gis_lookup(respx_mock):
    gis_router(respx_mock)
    outcome = MontcoAssessmentGisAdapter().lookup(ref("montco", "02-00-01016-00-8"))
    assert outcome.status == LookupStatus.SUCCESS, outcome.message
    p = outcome.payload
    assert p.land_use_description == "R - DUPLEX" and p.category == "multi_family"
    assert p.building_style == "ROW" and p.exterior_wall == "BRICK"
    assert p.living_area_sqft == 1724 and p.bedrooms == 4 and p.full_baths == 2 and p.year_built == 1880
    assert p.school_district == "UPPER MERION AREA" and p.assessed_value == 66700
    assert p.lot_size_text == "5200 SF"
    assert "last_sale_price" in p.field_notes  # $1 consideration flagged
    assert outcome.evidence and json.loads(outcome.evidence[0].content)["features"]
    missing = MontcoAssessmentGisAdapter().lookup(ref("montco", "99-00-00000-00-1"))
    assert missing.status == LookupStatus.NO_MATCH


@respx.mock(assert_all_called=False)
def test_montco_gis_commercial(respx_mock):
    gis_router(respx_mock)
    p = MontcoAssessmentGisAdapter().lookup(ref("montco", "01-00-01606-02-2")).payload
    assert p.land_use_description.startswith("C - ") and p.category == "commercial"
    assert p.gross_building_area == 4707 and p.total_living_units == 5
    assert p.structure_description == "MIXED RESIDENTIAL/COMMERCIAL" and p.year_built == 1872


@respx.mock(assert_all_called=False)
def test_montco_tax_claim_lookup(respx_mock):
    taxclaim_router(respx_mock, resolved_parcels={"02-00-02904-00-1"})
    adapter = MontcoTaxClaimAdapter()
    resolved = adapter.lookup(ref("montco", "02-00-02904-00-1"))
    assert resolved.status == LookupStatus.SUCCESS
    assert resolved.payload.tax_sale_status == "Resolved / no 2024 balance"
    y2024 = resolved.payload.year(2024)
    assert y2024.total == 7751.08 and y2024.balance == 0.0
    assert resolved.payload.year(2023).total == 7538.12 and resolved.payload.delinquent_total == 7291.94
    assert len(resolved.evidence) == 2
    open_claim = adapter.lookup(ref("montco", "02-00-01016-00-8"))
    assert open_claim.payload.status_category == "listed" and open_claim.payload.year(2024).balance == 7751.08


def test_montco_tax_claim_no_results():
    guid, status, message = MontcoTaxClaimAdapter.pick_guid("<html>Query returned no results.</html>", "01-00-00000-00-1")
    assert guid is None and status == LookupStatus.NO_MATCH and "no results" in message


@respx.mock(assert_all_called=False)
def test_delco_gis_lookup(respx_mock):
    gis_router(respx_mock)
    outcome = DelcoParcelsGisAdapter().lookup(ref("delco", "01-00-00254-00"))
    assert outcome.status == LookupStatus.SUCCESS
    assert outcome.payload.site_address == "3 CHESTER AVE"
    assert outcome.payload.detail_link.endswith("pin=01000025400")
    assert outcome.payload.acres == pytest.approx(0.13)


def delco_page(**over):
    values = dict(pin="01000025400", site_location="3 CHESTER AVE", legal="2 STY HSE ADD 60 X 95 LOT 12", school="S17 - William Penn",
                  ptype="01 - Taxable Residential", assessment="$170,490", detail_section="Residential",
                  detail_rows='<tr><td class="DataletSideHeading">Style</td><td class="DataletData">10 - TWIN</td></tr>'
                              '<tr><td class="DataletSideHeading">Base Area</td><td class="DataletData">1,148</td></tr>'
                              '<tr><td class="DataletSideHeading">Year Built</td><td class="DataletData">1920</td></tr>'
                              '<tr><td class="DataletSideHeading">Bed Rooms</td><td class="DataletData">3</td></tr>'
                              '<tr><td class="DataletSideHeading">Full Baths</td><td class="DataletData">1</td></tr>'
                              '<tr><td class="DataletSideHeading">Acres</td><td class="DataletData">.1058</td></tr>',
                  sale_date="03-28-2016", sale_price="$28,000", status="Listed for Upset Sale", face_2024="$240.00", due_2024="$266.28",
                  face_2025="$300.00", due_2025="$327.50", total_due="$1,920.92", face_2026="$310.00", balance_2026="$310.00")
    values.update(over)
    return fill_delco_template(values)


def test_delco_portal_capture_extracts_criteria_fields():
    outcome = DelcoAssessmentPortalAdapter().parse_capture(ref("delco", "01-00-00254-00"), delco_page())
    assert outcome.status == LookupStatus.SUCCESS, outcome.message
    p = outcome.payload
    assert p.site_address == "3 CHESTER AVE" and p.school_district == "S17 - William Penn"
    assert p.property_type == "01 - Taxable Residential" and p.category == "twin"
    assert p.building_style == "10 - TWIN" and p.base_area_sqft == 1148 and p.year_built == 1920
    assert p.bedrooms == 3 and p.full_baths == 1 and p.acres == pytest.approx(0.1058)
    assert p.assessed_value == 170490
    assert p.last_sale_date.isoformat() == "2016-03-28" and p.last_sale_price == 28000 and len(p.sales) == 2
    assert p.tax.tax_sale_status == "Listed for Upset Sale" and p.tax.status_category == "listed"
    assert p.tax.year(2024, "delinquent").balance == 266.28 and p.tax.year(2025, "delinquent").balance == 327.5
    assert p.tax.delinquent_total == 1920.92
    assert p.tax.year(2026, "receivable").face == 310.0


def test_delco_portal_ground_and_status_categories():
    rows = '<tr><td class="DataletSideHeading">Acres</td><td class="DataletData">1.2500</td></tr>'
    p = DelcoAssessmentPortalAdapter().parse_capture(ref("delco", "03-00-00544-00"),
                                                     delco_page(ptype="06 - Ground", detail_section="Land", detail_rows=rows, status="")).payload
    assert p.category == "land" and p.acres == 1.25 and p.tax.status_category == "unknown"
    assert status_category("Listed for Upset Sale") == "listed"
    assert status_category("Paid - Removed from Sale") == "resolved"
    assert status_category("Stayed - Payment Agreement") == "not_listed"


def test_delco_portal_rejects_unrelated_page():
    outcome = DelcoAssessmentPortalAdapter().parse_capture(ref("delco", "01-00-00254-00"), "<html><body><p>Hello</p></body></html>")
    assert outcome.status == LookupStatus.PARSE_FAILURE


def test_recorder_capture_table():
    html = (FIXTURES / "capture" / "recorder_results_synthetic.html").read_text(encoding="utf-8")
    outcome = MontcoRecorderAdapter().parse_capture(ref("montco", "02-00-02904-00-1"), html)
    assert outcome.status == LookupStatus.SUCCESS
    docs = {d.instrument_number: d for d in outcome.payload}
    assert len(docs) == 10 and docs["2021058899"].amount == 212000
    assert docs["2021061350"].related_reference == "2016041002"
    tab_text = "Instrument\tType\tRecorded Date\tGrantor\tGrantee\n111\tMortgage\t01/02/2003\tA\tB\n"
    assert len(MontcoRecorderAdapter().parse_capture(ref("montco", "02-00-02904-00-1"), tab_text).payload) == 1
    assert extract_tables("") == []


def test_montco_civil_filters_open_liens():
    html = (FIXTURES / "capture" / "montco_civil_results_synthetic.html").read_text(encoding="utf-8")
    outcome = MontcoCivilAdapter().parse_capture(ref("montco", "02-00-02904-00-1"), html)
    assert outcome.status == LookupStatus.SUCCESS
    assert [c.case_number for c, _ in outcome.payload] == ["2024-12345"]
    none = MontcoCivilAdapter().parse_capture(ref("montco", "02-00-02904-00-1"), "<html>No records found</html>")
    assert none.status == LookupStatus.NO_MATCH


def test_delco_civil_filters_by_classification_and_derives_names():
    text = (FIXTURES / "capture" / "delco_civil_results_synthetic.txt").read_text(encoding="utf-8")
    adapter = DelcoCivilAdapter()
    outcome = adapter.parse_capture(ref("delco", "01-00-00254-00"), text)
    assert sorted(c.case_number for c, _ in outcome.payload) == ["CV-2023-004512", "CV-2025-000077"]
    request = adapter.capture_request(ref("delco", "01-00-00254-00", owner="WALKER ROBERT H & WALKER JACQUELINE"))
    assert "WALKER ROBERT H" in request.search_hint and "WALKER JACQUELINE" in request.search_hint


def test_user_assisted_lookup_never_fetches():
    with respx.mock(assert_all_mocked=True):  # any real HTTP call would raise
        outcome = DelcoAssessmentPortalAdapter().lookup(ref("delco", "01-00-00254-00"))
    assert outcome.status == LookupStatus.USER_ACTION and outcome.capture.url.startswith("http://delcorealestate")


def test_arcgis_error_body_is_unexpected():
    outcome = MontcoAssessmentGisAdapter().parse_response({"error": {"message": "Unable to perform query"}}, "020002904001")
    assert outcome.status == LookupStatus.UNEXPECTED


def test_http_status_codes_mapping():
    assert httpx.codes.TOO_MANY_REQUESTS == 429
