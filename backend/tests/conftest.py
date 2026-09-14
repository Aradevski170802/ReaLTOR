from __future__ import annotations

import os
import re
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
MONTCO_PDF = FIXTURES / "pdf" / "montco_upset_list_subset.pdf"
DELCO_PDF = FIXTURES / "pdf" / "delco_sales_report_subset.pdf"


@pytest.fixture(scope="session", autouse=True)
def _environment(tmp_path_factory):
    data = tmp_path_factory.mktemp("usi-data")
    os.environ.update({
        "USI_DATA_DIR": str(data), "USI_AUTH_MODE": "local", "USI_APP_ENV": "test", "USI_AUTO_MIGRATE": "false",
        "USI_DEMO_MODE": "false", "USI_HTTP_USER_AGENT": "UpsetSaleIntel-tests", "USI_RUN_WORKER_IN_API": "false",
    })
    os.environ.pop("USI_DATABASE_URL", None)
    from app.config import reset_settings_cache

    reset_settings_cache()
    yield


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    from app.connectors import http as http_module

    http_module.reset_http_state()
    monkeypatch.setattr(http_module.time, "sleep", lambda *_: None)
    import app.jobs.queue as queue_module

    monkeypatch.setattr(queue_module.random, "uniform", lambda a, b: 0.0)
    from app.connectors import iasworld_client

    iasworld_client.reset_sessions()
    yield


@pytest.fixture()
def db_url(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    monkeypatch.setenv("USI_DATABASE_URL", url)
    from app.config import reset_settings_cache

    reset_settings_cache()
    yield url
    reset_settings_cache()


@pytest.fixture()
def db(db_url):
    import app.models  # noqa: F401
    from app.db import Base, init_engine, session_factory

    engine = init_engine(db_url)
    Base.metadata.create_all(engine)
    session = session_factory()()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def client(db):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def gis_response(county: str, request: httpx.Request) -> httpx.Response:
    where = request.url.params.get("where", "")
    if county == "montco":
        digits = re.search(r"(\d{12})", where)
        path = FIXTURES / "http" / f"montco_gis_{digits.group(1) if digits else 'x'}.json"
    else:
        parid = re.search(r"PARID=(\d+)", where)
        path = FIXTURES / "http" / f"delco_gis_{parid.group(1) if parid else 'x'}.json"
    if path.exists():
        return httpx.Response(200, content=path.read_bytes(), headers={"content-type": "application/json"})
    return httpx.Response(200, json={"features": []})


def taxclaim_router(respx_mock, resolved_parcels: set[str] | None = None) -> None:
    """Mock the Montco Tax Claim search/detail endpoints for any parcel using the captured fixture pages."""
    listing = (FIXTURES / "http" / "montco_taxclaim_list.html").read_text(encoding="utf-8")
    resolved = (FIXTURES / "http" / "montco_taxclaim_detail.html").read_text(encoding="utf-8")
    open2024 = (FIXTURES / "http" / "montco_taxclaim_detail_open2024.html").read_text(encoding="utf-8")
    guid_to_parcel: dict[str, str] = {}
    resolved_parcels = resolved_parcels or set()

    def search(request: httpx.Request) -> httpx.Response:
        form = dict(httpx.QueryParams(request.content.decode()))
        parcel = form.get("parcelNum", "")
        guid = f"00000000-0000-0000-0000-{abs(hash(parcel)) % 10**12:012d}"
        guid_to_parcel[guid] = parcel
        html = listing.replace("02-00-02904-00-1", parcel).replace("9b84d400-a0eb-e511-8127-c4346bb588c8", guid)
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    def detail(request: httpx.Request) -> httpx.Response:
        form = dict(httpx.QueryParams(request.content.decode()))
        parcel = guid_to_parcel.get(form.get("entity", ""), "")
        page = resolved if parcel in resolved_parcels else open2024
        return httpx.Response(200, text=page.replace("02-00-02904-00-1", parcel), headers={"content-type": "text/html"})

    respx_mock.get("https://webapp02.montcopa.org/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.post("https://webapp02.montcopa.org/taxclaim/ParcelList.aspx").mock(side_effect=search)
    respx_mock.post("https://webapp02.montcopa.org/taxclaim/ParcelInfo.aspx").mock(side_effect=detail)


def gis_router(respx_mock) -> None:
    respx_mock.get("https://gis.montcopa.org/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.get("https://gis.delcopa.gov/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.get(url__startswith="https://gis.montcopa.org/arcgis/rest/services/Parcels/GIS_BOA_LAND").mock(
        side_effect=lambda req: gis_response("montco", req))
    respx_mock.get(url__startswith="https://gis.delcopa.gov/arcgis/rest/services/Parcels/Parcels_Public_Access").mock(
        side_effect=lambda req: gis_response("delco", req))


def delco_iasworld_router(respx_mock, page_html: str, *, captcha: bool = False, tabs: dict[str, str] | None = None) -> dict[str, int]:
    """Mock the Delco iasWorld disclaimer + datalet endpoints. Returns a live hit-counter for assertions."""
    host = "http://delcorealestate.co.delaware.pa.us"
    disclaimer = f"{host}/pt/Search/Disclaimer.aspx"
    counts = {"accept": 0, "datalet": 0}
    disclaimer_html = (
        '<form id="Form1"><input id="__VIEWSTATE" value="vs"/><input id="__VIEWSTATEGENERATOR" value="g"/>'
        '<input id="__EVENTVALIDATION" value="ev"/><input name="btAgree" value="Agree"/></form>'
    )
    if captcha:
        disclaimer_html += '<script src="https://www.google.com/recaptcha/api.js"></script><div class="g-recaptcha"></div>'

    def on_disclaimer(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            counts["accept"] += 1
            return httpx.Response(200, text="<html>commonsearch results</html>",
                                  headers={"content-type": "text/html", "set-cookie": "DISCLAIMER=1; path=/"},
                                  request=request)
        return httpx.Response(200, text=disclaimer_html, headers={"content-type": "text/html"})

    def on_datalet(request: httpx.Request) -> httpx.Response:
        counts["datalet"] += 1
        mode = httpx.QueryParams(request.url.query.decode() if isinstance(request.url.query, bytes) else request.url.query).get("mode", "")
        body = (tabs or {}).get(mode, page_html)
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    respx_mock.get(f"{host}/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.route(url__startswith=disclaimer).mock(side_effect=on_disclaimer)
    respx_mock.get(url__startswith=f"{host}/pt/datalets/datalet.aspx").mock(side_effect=on_datalet)
    return counts
