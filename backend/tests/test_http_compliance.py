import httpx
import pytest
import respx

from app.connectors.base import RetryPolicy, SearchDef, SourceDescriptor, SourceError
from app.connectors.http import ComplianceHttpClient, RateLimiter
from app.enums import AccessMethod, LookupStatus

DESC = SourceDescriptor(
    key="test.source", county="montco", name="Test source", category="assessment", access_method=AccessMethod.PUBLIC_WEB,
    base_urls=("https://example.test/",), searches=(SearchDef("s", "https://example.test/s", "x"),), input_requirements="x",
    parcel_rule="x", field_mappings={}, rate_limit_per_minute=100, min_interval_seconds=0, cache_ttl_hours=1,
    retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0.01), error_classification={}, compliance_status="automated_permitted",
    terms_notes="test",
)


@respx.mock
def test_robots_disallow_blocks(respx_mock):
    respx_mock.get("https://example.test/robots.txt").mock(return_value=httpx.Response(200, text="User-agent: *\nDisallow: /pt/\n"))
    page = respx_mock.get("https://example.test/pt/search").mock(return_value=httpx.Response(200, text="ok"))
    with pytest.raises(SourceError) as err, ComplianceHttpClient(DESC) as client:
        client.get("https://example.test/pt/search")
    assert err.value.status == LookupStatus.BLOCKED and not page.called


@respx.mock
def test_robots_unreachable_is_conservative(respx_mock):
    respx_mock.get("https://example.test/robots.txt").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(SourceError) as err, ComplianceHttpClient(DESC) as client:
        client.get("https://example.test/data")
    assert err.value.status == LookupStatus.BLOCKED


@respx.mock
def test_captcha_page_requires_user_action(respx_mock):
    respx_mock.get("https://example.test/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.get("https://example.test/case").mock(return_value=httpx.Response(
        200, text="<html><head><title>Validating</title></head><div class='cf-turnstile'></div></html>", headers={"content-type": "text/html"}))
    with pytest.raises(SourceError) as err, ComplianceHttpClient(DESC) as client:
        client.get("https://example.test/case")
    assert err.value.status == LookupStatus.USER_ACTION and "not bypassed" in err.value.message


@respx.mock
def test_disclaimer_redirect_requires_user_action(respx_mock):
    respx_mock.get("https://example.test/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.get("https://example.test/pt/search").mock(return_value=httpx.Response(302, headers={"location": "/pt/Search/Disclaimer.aspx"}))
    with pytest.raises(SourceError) as err, ComplianceHttpClient(DESC) as client:
        client.get("https://example.test/pt/search")
    assert err.value.status == LookupStatus.USER_ACTION


@respx.mock
def test_rate_limited_then_success_retries(respx_mock):
    respx_mock.get("https://example.test/robots.txt").mock(return_value=httpx.Response(404))
    route = respx_mock.get("https://example.test/api").mock(side_effect=[
        httpx.Response(429, headers={"retry-after": "1"}), httpx.Response(503), httpx.Response(200, json={"ok": True})])
    with ComplianceHttpClient(DESC) as client:
        result = client.get("https://example.test/api", params={"apikey": "SECRET123"})
    assert result.status == 200 and route.call_count == 3
    assert "SECRET123" not in result.request_summary


@respx.mock
def test_retries_exhausted_reports_rate_limit(respx_mock):
    respx_mock.get("https://example.test/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.get("https://example.test/api").mock(return_value=httpx.Response(429))
    with pytest.raises(SourceError) as err, ComplianceHttpClient(DESC) as client:
        client.get("https://example.test/api")
    assert err.value.status == LookupStatus.RATE_LIMITED


@respx.mock
def test_forbidden_and_auth(respx_mock):
    respx_mock.get("https://example.test/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.get("https://example.test/forbidden").mock(return_value=httpx.Response(403))
    respx_mock.get("https://example.test/login").mock(return_value=httpx.Response(401))
    with ComplianceHttpClient(DESC) as client:
        with pytest.raises(SourceError) as forbidden:
            client.get("https://example.test/forbidden")
        with pytest.raises(SourceError) as auth:
            client.get("https://example.test/login")
    assert forbidden.value.status == LookupStatus.BLOCKED and auth.value.status == LookupStatus.AUTH_NEEDED


def test_rate_limiter_enforces_interval_and_budget():
    clock = {"t": 0.0}
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        clock["t"] += seconds

    limiter = RateLimiter(per_minute=2, min_interval=5)
    for _ in range(3):
        limiter.acquire(sleep=sleep, clock=lambda: clock["t"])
    assert slept[0] == pytest.approx(5)
    assert clock["t"] >= 60  # third request waits for the per-minute window
