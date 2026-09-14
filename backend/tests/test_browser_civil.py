"""Browser-driven Delaware C-Track civil lien automation: pure parsing + enrichment wiring against a fake browser.

No real Chromium is launched: FakeRunner/FakePage stand in for BrowserRunner so the whole path (including the real
DelcoCivilAdapter.browse) is exercised in CI without Playwright installed.
"""

from contextlib import contextmanager

from sqlalchemy import select

from app.common.parcels import normalize_parcel
from app.connectors.base import PropertyRef
from app.connectors.delco.civil import DelcoCivilAdapter, ctrack_party_name, parse_ctrack_rows
from app.connectors.names import party_search_terms
from app.enums import LookupStatus
from app.models import CaptureTask, CivilLienCase, Project, Property
from app.services.rules_service import evaluate_properties

# Real C-Track party-search layout: [Case Number, Case Classification, Case Filed Date, Party Name, Party Role].
PARTY_ROWS = [
    ["CV-2010-007187", "Civil NR - Municipal Lien - Waste", "06/14/2010", "ROSINI, MARK A", "Defendant"],
    ["CV-2012-063410", "Civil NR - Municipal Lien - Waste", "07/06/2012", "ROSINI, MARK A", "Defendant"],
    ["CV-2010-002090", "Civil - Real Property - Mortgage Foreclosure: Residential", "02/25/2010", "ROSINI, MARK A", "Defendant"],
    ["No records were found."],
]


def test_ctrack_party_name_formats():
    person = party_search_terms("ROSINI MARK A &")[0]
    assert ctrack_party_name(person) == "ROSINI, MARK A"
    entity = party_search_terms("ACTIVE INVESTORS LLC")[0]
    assert ctrack_party_name(entity) == "ACTIVE INVESTORS LLC"


def test_parse_ctrack_rows_party_and_case_views():
    cases = parse_ctrack_rows(PARTY_ROWS, "ROSINI, MARK A")
    assert [c.case_number for c in cases] == ["CV-2010-007187", "CV-2012-063410", "CV-2010-002090"]
    assert cases[0].classification == "Civil NR - Municipal Lien - Waste"
    assert cases[0].parties == "ROSINI, MARK A (Defendant)"
    assert cases[0].filing_date.isoformat() == "2010-06-14"
    # The other C-Track layout, [Case Number, Case Title, Case Classification, Case Filed Date], is also handled.
    case_view = parse_ctrack_rows([["CV-2007-018883", "DARBY BORO v. X", "Civil NR - Municipal Lien - Waste", "12/29/2007"]], "X")
    assert case_view[0].parties == "DARBY BORO v. X"
    assert case_view[0].classification.endswith("Waste")


def test_parse_ctrack_rows_rejects_non_case_rows():
    # Header rows, blank rows and "no records" rows must never be turned into cases (this was the false-lien bug).
    junk = [["Case Number", "Classification", "Filed", "Party", "Role"], [""], ["No records were found."], ["not-a-case"]]
    assert parse_ctrack_rows(junk, "X") == []


def test_relevance_keeps_only_liens():
    adapter = DelcoCivilAdapter()
    kept = [c.case_number for c in parse_ctrack_rows(PARTY_ROWS, "ROSINI, MARK A") if adapter.relevance(c)[0]]
    # Two municipal liens kept; the mortgage foreclosure (no 'Lien' in classification) is dropped.
    assert kept == ["CV-2010-007187", "CV-2012-063410"]


class FakePage:
    """Minimal stand-in for a Playwright Page that returns canned C-Track result rows."""

    def __init__(self, rows):
        self._rows = rows
        self.url = "https://delcopublicaccess.co.delaware.pa.us/search/party"

    def goto(self, *a, **k):
        pass

    def wait_for_timeout(self, *a):
        pass

    def wait_for_selector(self, *a, **k):
        pass

    def evaluate(self, *a):
        return "party-name-input"  # resolved field id

    def fill(self, *a):
        pass

    def content(self):
        return "<html>results</html>"

    def eval_on_selector_all(self, *a):
        return self._rows

    def get_by_text(self, *a, **k):
        return self

    def get_by_role(self, *a, **k):
        return self

    @property
    def first(self):
        return self

    @property
    def last(self):
        return self

    def click(self, *a, **k):
        pass


class FakeRunner:
    """Context-manager stand-in for BrowserRunner; hands out FakePage instances."""

    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @contextmanager
    def page(self):
        yield FakePage(self._rows)


def _ref():
    return PropertyRef(1, "delco", normalize_parcel("delco", "11-00-01036-00"), "ROSINI MARK A &",
                       "124 HILLSIDE AVE", "COLLINGDALE", {})


def test_browse_with_fake_runner_keeps_liens():
    out = DelcoCivilAdapter().browse(FakeRunner(PARTY_ROWS), _ref())
    assert out.status == LookupStatus.SUCCESS
    assert {c.case_number for c, _ in out.payload} == {"CV-2010-007187", "CV-2012-063410"}
    assert out.evidence  # a page snapshot was captured


def test_browse_no_liens_is_no_match():
    only_foreclosure = [["CV-2010-002090", "Civil - Real Property - Mortgage Foreclosure: Residential", "02/25/2010", "ROSINI, MARK A", "Defendant"]]
    out = DelcoCivilAdapter().browse(FakeRunner(only_foreclosure), _ref())
    assert out.status == LookupStatus.NO_MATCH and out.payload == []


def test_enrichment_uses_browser_when_available(db, monkeypatch):
    import app.connectors.browser as browser_mod
    from app.services import enrichment

    project = Project(name="delco browser", county="delco", created_by="t")
    db.add(project)
    db.flush()
    prop = Property(project_id=project.id, county="delco", parcel_original="11-00-01036-00", parcel_normalized="11-00-01036-00",
                    parcel_search_key="11000103600", owner_name="ROSINI MARK A &", municipality="COLLINGDALE")
    db.add(prop)
    db.flush()

    # Pretend browser automation is available; BrowserRunner is a FakeRunner so no real Chromium is launched.
    # Both names are imported lazily from app.connectors.browser inside enrichment, so patch them there.
    monkeypatch.setattr(browser_mod, "browser_available", lambda: True)
    monkeypatch.setattr(browser_mod, "BrowserRunner", lambda *a, **k: FakeRunner(PARTY_ROWS))

    summary = enrichment.enrich_property(db, prop, source_keys=["delco.civil"], run_valuation=False)

    assert summary["delco.civil"] == LookupStatus.SUCCESS
    liens = db.scalars(select(CivilLienCase).where(CivilLienCase.property_id == prop.id)).all()
    assert {c.case_number for c in liens} == {"CV-2010-007187", "CV-2012-063410"}
    assert all(c.is_relevant for c in liens)
    # The browser handled the source, so no user-assisted capture task should be left open.
    open_task = db.scalar(select(CaptureTask).where(CaptureTask.property_id == prop.id, CaptureTask.source_key == "delco.civil",
                                                    CaptureTask.status == "open"))
    assert open_task is None
    evaluate_properties(db, [prop.id], "t")


def test_enrichment_falls_back_to_capture_without_browser(db, monkeypatch):
    import app.connectors.browser as browser_mod
    from app.services import enrichment

    project = Project(name="delco no-browser", county="delco", created_by="t")
    db.add(project)
    db.flush()
    prop = Property(project_id=project.id, county="delco", parcel_original="11-00-01036-00", parcel_normalized="11-00-01036-00",
                    parcel_search_key="11000103600", owner_name="ROSINI MARK A &", municipality="COLLINGDALE")
    db.add(prop)
    db.flush()

    monkeypatch.setattr(browser_mod, "browser_available", lambda: False)

    summary = enrichment.enrich_property(db, prop, source_keys=["delco.civil"], run_valuation=False)

    assert summary["delco.civil"] == LookupStatus.USER_ACTION
    assert db.scalars(select(CivilLienCase).where(CivilLienCase.property_id == prop.id)).all() == []
    open_task = db.scalar(select(CaptureTask).where(CaptureTask.property_id == prop.id, CaptureTask.source_key == "delco.civil",
                                                    CaptureTask.status == "open"))
    assert open_task is not None
