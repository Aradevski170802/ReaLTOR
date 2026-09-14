"""Automated Delco iasWorld portal: disclaimer auto-accept, datalet fetch, parse, and graceful fallback."""

import httpx
import pytest
import respx

from app.common.parcels import normalize_parcel
from app.connectors.base import PropertyRef
from app.connectors.delco.portal import DelcoAssessmentPortalAdapter
from app.connectors.iasworld_client import IasWorldPortal, IasWorldSession, reset_sessions
from app.enums import AccessMethod, LookupStatus
from tests.conftest import delco_iasworld_router
from tests.test_connectors import delco_page


def ref(parcel: str = "01-00-00254-00") -> PropertyRef:
    return PropertyRef(1, "delco", normalize_parcel("delco", parcel), "SAMPLE OWNER", "3 CHESTER AVE", "ALDAN", {})


@pytest.fixture(autouse=True)
def _reset():
    reset_sessions()
    yield
    reset_sessions()


def test_descriptor_is_now_automated_with_terms_ack():
    d = DelcoAssessmentPortalAdapter.descriptor
    assert d.access_method == AccessMethod.PUBLIC_WEB and d.automated
    assert d.requires_terms_ack and d.compliance_status == "automated_requires_terms_ack"


@respx.mock(assert_all_called=False)
def test_automated_lookup_accepts_once_and_parses(respx_mock):
    counts = delco_iasworld_router(respx_mock, delco_page())
    adapter = DelcoAssessmentPortalAdapter()
    outcome = adapter.lookup(ref())
    assert outcome.status == LookupStatus.SUCCESS, outcome.message
    p = outcome.payload
    assert p.site_address == "3 CHESTER AVE" and p.property_type == "01 - Taxable Residential"
    assert p.base_area_sqft == 1148 and p.bedrooms == 3 and p.full_baths == 1
    assert p.tax and p.tax.status_category == "listed"
    assert outcome.evidence and b"datalet mode=" in outcome.evidence[0].content
    assert counts["accept"] == 1 and counts["datalet"] >= 1

    # A second lookup in the same process reuses the accepted session (no second accept POST).
    adapter.lookup(ref("01-00-00384-00"))
    assert counts["accept"] == 1


@respx.mock(assert_all_called=False)
def test_lookup_falls_back_when_human_verification_appears(respx_mock):
    delco_iasworld_router(respx_mock, delco_page(), captcha=True)
    outcome = DelcoAssessmentPortalAdapter().lookup(ref())
    assert outcome.status == LookupStatus.USER_ACTION and "human-verification" in outcome.message.lower()
    # The manual capture path still exists as the fallback.
    assert DelcoAssessmentPortalAdapter().supports_capture


@respx.mock(assert_all_called=False)
def test_robots_disallow_blocks_automation(respx_mock):
    host = "http://delcorealestate.co.delaware.pa.us"
    respx_mock.get(f"{host}/robots.txt").mock(return_value=httpx.Response(200, text="User-agent: *\nDisallow: /pt/\n"))
    outcome = DelcoAssessmentPortalAdapter().lookup(ref())
    assert outcome.status == LookupStatus.BLOCKED


@respx.mock(assert_all_called=False)
def test_no_data_tabs_is_no_match(respx_mock):
    host = "http://delcorealestate.co.delaware.pa.us"
    respx_mock.get(f"{host}/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.route(url__startswith=f"{host}/pt/Search/Disclaimer.aspx").mock(
        side_effect=lambda req: httpx.Response(200, text='<input id="__VIEWSTATE" value="v"/><input name="btAgree"/>',
                                               headers={"set-cookie": "DISCLAIMER=1"} if req.method == "POST" else {}))
    respx_mock.get(url__startswith=f"{host}/pt/datalets/datalet.aspx").mock(return_value=httpx.Response(500))
    outcome = DelcoAssessmentPortalAdapter().lookup(ref())
    assert outcome.status == LookupStatus.NO_MATCH


@respx.mock(assert_all_called=False)
def test_session_reused_across_parcels(respx_mock):
    counts = delco_iasworld_router(respx_mock, delco_page())
    portal = IasWorldPortal(base="http://delcorealestate.co.delaware.pa.us/pt", jurisdiction="023")
    with IasWorldSession(DelcoAssessmentPortalAdapter.descriptor, portal) as session:
        session.fetch_property_html("01000025400")
        session.fetch_property_html("01000038400")
    assert counts["accept"] == 1 and counts["datalet"] >= 2
