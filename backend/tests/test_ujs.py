"""PA UJS portal → Montgomery MDJ civil judgments: name derivation, caption/defendant parsing, relevance, browse, wiring.

Uses a fake browser (no Chromium) shaped like the live #caseSearchResultGrid rows.
"""

from contextlib import contextmanager

from sqlalchemy import select

from app.common.parcels import normalize_parcel
from app.connectors.base import PropertyRef
from app.connectors.montco.ujs import (
    MontcoUjsJudgmentsAdapter,
    caption_defendant,
    parse_ujs_rows,
    ujs_participant_names,
    ujs_relevant,
)
from app.enums import LookupStatus
from app.models import CaptureTask, CivilLienCase, Project, Property
from app.services.rules_service import evaluate_properties


# Rows shaped like the live grid: [rowidx, expander, DOCKET, court type, caption, status, filed, participants, dob, county, office, ...]
def _row(docket, caption, status="Closed", filed="01/22/2015"):
    return {"cells": ["8", "0", docket, "Magisterial District", caption, status, filed,
                      "Smith, John", "", "Montgomery", "MDJ-38-1-24"],
            "url": f"/Report/MdjDocketSheet?docketNumber={docket}&dnh=abc%3D%3D"}


ROWS = [
    _row("MJ-38124-CV-0000031-2015", "Enterprise Leasing Company of Philadelphia v. Smith a/k/a Jack Smith, John"),
    _row("MJ-38109-CV-0000178-2025", "SILA SERVICES LLC v. SMITH, JOHN", filed="10/16/2025"),
    _row("MJ-38110-CV-0000167-2026", "Smith, John v. Acme Rentals LLC", filed="07/31/2026"),  # owner is plaintiff -> dropped
    {"cells": ["No records were found."], "url": None},
]


def test_ujs_participant_names():
    assert ujs_participant_names("SMITH JOHN &") == [("SMITH", "JOHN")]
    assert ujs_participant_names("ACME HOLDINGS LLC") == []  # entities are not run against Participant Name search


def test_caption_defendant():
    assert caption_defendant("Enterprise Leasing v. Smith, John") == "Smith, John"
    assert caption_defendant("LVNV FUNDING LLC vs. Smith") == "Smith"
    assert caption_defendant("No separator here") == "No separator here"


def test_parse_and_relevance():
    cases = parse_ujs_rows(ROWS, "SMITH")
    assert [c.case_number for c in cases] == ["MJ-38124-CV-0000031-2015", "MJ-38109-CV-0000178-2025", "MJ-38110-CV-0000167-2026"]
    assert cases[0].filing_date.isoformat() == "2015-01-22" and cases[0].classification == "Magisterial District Civil"
    assert cases[0].source_url.startswith("https://ujsportal.pacourts.us/Report/MdjDocketSheet")
    keep = [c.case_number for c in cases if ujs_relevant(c)[0]]
    # Owner is the defendant in the two creditor cases; dropped where the owner is the plaintiff.
    assert keep == ["MJ-38124-CV-0000031-2015", "MJ-38109-CV-0000178-2025"]


class FakePage:
    def __init__(self, rows):
        self._rows = rows
        self.url = "https://ujsportal.pacourts.us/CaseSearch"

    def goto(self, *a, **k):
        pass

    def wait_for_timeout(self, *a):
        pass

    def wait_for_selector(self, *a, **k):
        pass

    def evaluate(self, *a):
        return True

    def click(self, *a, **k):
        pass

    def content(self):
        return "<html>results</html>"

    def eval_on_selector_all(self, *a):
        return self._rows


class FakeRunner:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @contextmanager
    def page(self):
        yield FakePage(self._rows)


def _ref(owner="SMITH JOHN"):
    return PropertyRef(1, "montco", normalize_parcel("montco", "13-00-30188-00-3"), owner, "1 SAMPLE ST", "NORRISTOWN", {})


def test_browse_keeps_owner_defendant_cases():
    out = MontcoUjsJudgmentsAdapter().browse(FakeRunner(ROWS), _ref())
    assert out.status == LookupStatus.SUCCESS
    assert {c.case_number for c, _ in out.payload} == {"MJ-38124-CV-0000031-2015", "MJ-38109-CV-0000178-2025"}
    assert out.evidence


def test_browse_no_defendant_match_is_no_match():
    out = MontcoUjsJudgmentsAdapter().browse(FakeRunner([_row("MJ-1-CV-1-2020", "Smith, John v. Tenant")]), _ref())
    assert out.status == LookupStatus.NO_MATCH and out.payload == []


def test_enrichment_uses_browser(db, monkeypatch):
    import app.connectors.browser as browser_mod
    from app.services import enrichment

    monkeypatch.setattr(browser_mod, "browser_available", lambda: True)
    monkeypatch.setattr(browser_mod, "BrowserRunner", lambda *a, **k: FakeRunner(ROWS))

    project = Project(name="ujs", county="montco", created_by="t")
    db.add(project)
    db.flush()
    prop = Property(project_id=project.id, county="montco", parcel_original="13-00-30188-00-3",
                    parcel_normalized="13-00-30188-00-3", parcel_search_key="13003018800300",
                    owner_name="SMITH JOHN", municipality="NORRISTOWN")
    db.add(prop)
    db.flush()

    summary = enrichment.enrich_property(db, prop, source_keys=["montco.ujs_judgments"], run_valuation=False)
    assert summary["montco.ujs_judgments"] == LookupStatus.SUCCESS
    liens = db.scalars(select(CivilLienCase).where(CivilLienCase.property_id == prop.id)).all()
    assert {c.case_number for c in liens} == {"MJ-38124-CV-0000031-2015", "MJ-38109-CV-0000178-2025"}
    assert all(c.source_key == "montco.ujs_judgments" and c.is_relevant for c in liens)
    assert db.scalar(select(CaptureTask).where(CaptureTask.property_id == prop.id,
                                               CaptureTask.source_key == "montco.ujs_judgments",
                                               CaptureTask.status == "open")) is None
    evaluate_properties(db, [prop.id], "t")
