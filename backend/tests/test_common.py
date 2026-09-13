from datetime import date

from app.common.parcels import delco_parid, montco_gis_key, normalize_parcel
from app.common.parsing import parse_date, parse_float, parse_int, parse_money
from app.connectors.names import party_search_terms


def test_montco_parcel_normalization():
    p = normalize_parcel("montco", "02-00-02904-00-1")
    assert p.valid and not p.repaired and p.normalized == "02-00-02904-00-1"
    assert montco_gis_key(p) == "020002904001"
    repaired = normalize_parcel("montco", "02 00 O2904 00 1")
    assert repaired.valid and repaired.repaired and repaired.normalized == "02-00-02904-00-1"
    assert repaired.confidence < 1
    bad = normalize_parcel("montco", "02-00-02904-00")
    assert not bad.valid and "Expected 12 digits" in bad.notes[-1]


def test_delco_folio_normalization():
    p = normalize_parcel("delco", "01-00-00254-00")
    assert p.valid and p.digits == "01000025400"
    assert delco_parid(p) == 1000025400
    assert normalize_parcel("delco", "01000025400").normalized == "01-00-00254-00"
    assert not normalize_parcel("delco", "02-00-02904-00-1").valid


def test_scalar_parsers():
    assert parse_money("$1,335.45") == 1335.45
    assert parse_money("(1,000.00)") == -1000.0
    assert parse_money("n/a") is None
    assert parse_int("1,724") == 1724 and parse_int("1.5") is None
    assert parse_float(".1315") == 0.1315
    assert parse_date("09/24/26") == date(2026, 9, 24)
    assert parse_date("06-MAY-2009") == date(2009, 5, 6)
    assert parse_date("2016-09-23") == date(2016, 9, 23)
    assert parse_date("not a date") is None


def test_party_search_terms():
    terms = party_search_terms("RUTKO WALTER & SALLY M")
    assert [t.party_name for t in terms] == ["RUTKO WALTER", "RUTKO SALLY M"]
    trust = party_search_terms("DIGIROLAMO ALFONSO TRUSTEE OF THE 108 SOUTH ELM TRUST")
    assert ("person", "DIGIROLAMO ALFONSO") in [(t.kind, t.party_name) for t in trust]
    assert ("entity", "108 SOUTH ELM TRUST") in [(t.kind, t.party_name) for t in trust]
    assert party_search_terms("ACTIVE INVESTORS LLC")[0].kind == "entity"
    assert party_search_terms("BENZ PAUL A JR")[0].party_name == "BENZ PAUL A"
    assert party_search_terms("HAHN DANIEL J &")[0].party_name == "HAHN DANIEL J"
    assert party_search_terms(None) == []
