"""ATTOM property enrichment: parsing, APN matching, secret wiring (env fallback), and enrichment persistence.

Uses a synthetic ATTOM `attomavm/detail` property block (no real PII) and monkeypatches the shared client so no
network call is made.
"""

from sqlalchemy import select

from app.common.parcels import normalize_parcel
from app.connectors.attom import DelcoAttomAdapter, MontcoAttomAdapter
from app.connectors.attom_client import AttomResponse
from app.connectors.base import PropertyRef
from app.enums import LookupStatus
from app.models import AssessmentRecord, FieldProvenance, Project, Property

# Synthetic ATTOM attomavm/detail property block, shaped like the live response.
ATTOM_PROP = {
    "identifier": {"attomId": 999001, "apn": "13-00-30188-00-3", "fips": "42091"},
    "address": {"oneLine": "1 SAMPLE ST, NORRISTOWN, PA 19401"},
    "summary": {"propclass": "Single Family Residence / Townhouse", "propertyType": "SINGLE FAMILY RESIDENCE",
                "propLandUse": "SFR", "yearbuilt": 1951, "legal1": "BLOCK-UNIT 64-79", "absenteeInd": "ABSENTEE(MAIL & SIT DIFFER)"},
    "building": {"size": {"livingsize": 1184, "grosssize": 1184, "groundfloorsize": 592},
                 "rooms": {"beds": 3, "bathstotal": 1.5, "roomsTotal": 6}, "summary": {"levels": 2, "unitsCount": 1}},
    "lot": {"depth": 139, "frontage": 16, "lotsize1": 0.051, "lotsize2": 2229},
    "sale": {"saleTransDate": "2008-01-28", "buyerName": "SAMPLE BUYER",
             "amount": {"saleamt": 126900, "saledocnum": "0056810329"}},
    "assessment": {"assessed": {"assdttlvalue": 58190}, "market": {"mktttlvalue": 175000},
                   "tax": {"taxamt": 3713.0, "taxyear": 2026}},
    "avm": {"amount": {"value": 187207, "high": 207945, "low": 166469, "scr": 89}},
    "owner": {"corporateindicator": "N", "absenteeownerstatus": "A",
              "owner1": {"fullname": "SAMPLE OWNER LLC"}, "mailingaddressoneline": "PO BOX 5, PHILADELPHIA, PA 19103"},
}


def _resp(props, status_code=200, msg="SuccessWithResult"):
    return AttomResponse(status_code=status_code, url="https://api.gateway.attomdata.com/attomavm/detail",
                         content=b"{}", content_type="application/json",
                         data={"status": {"msg": msg}, "property": props})


def _ref(county="montco", parcel="13-00-30188-00-3"):
    return PropertyRef(1, county, normalize_parcel(county, parcel), "SAMPLE OWNER LLC", "1 SAMPLE ST", "NORRISTOWN", {})


def test_parse_extracts_characteristics_owner_and_extras():
    payload, note = MontcoAttomAdapter().parse(ATTOM_PROP, _ref())
    assert note == "by APN"
    assert payload.owner_name == "SAMPLE OWNER LLC"
    assert payload.assessed_value == 58190 and payload.year_built == 1951
    assert payload.living_area_sqft == 1184 and payload.bedrooms == 3 and payload.full_baths == 1  # int(1.5)
    assert payload.acres == 0.051 and payload.lot_sqft == 2229 and payload.lot_size_text == "16 x 139"
    assert payload.last_sale_price == 126900 and payload.last_sale_date.isoformat() == "2008-01-28"
    assert payload.sales and payload.sales[0].grantee == "SAMPLE BUYER"
    raw = payload.raw_codes
    assert raw["ATTOM_MARKET_VALUE"] == 175000 and raw["ATTOM_AVM_VALUE"] == 187207 and raw["ATTOM_AVM_SCORE"] == 89
    assert raw["ABSENTEE"].startswith("ABSENTEE") and raw["OWNER_MAILING"].startswith("PO BOX 5")
    assert payload.field_notes["full_baths"] == "ATTOM total baths 1.5"


def test_apn_mismatch_is_flagged_not_dropped():
    payload, note = MontcoAttomAdapter().parse(ATTOM_PROP, _ref(parcel="30-00-99999-00-1"))
    assert "!=" in note and payload.field_notes.get("match", "").startswith("address match")


def test_lookup_not_configured_without_key():
    out = MontcoAttomAdapter().lookup(_ref(), secrets={})
    assert out.status == LookupStatus.NOT_CONFIGURED


def test_lookup_success_and_ambiguous(monkeypatch):
    import app.connectors.attom as attom_mod

    monkeypatch.setattr(attom_mod, "lookup_avm_detail", lambda **k: _resp([ATTOM_PROP]))
    out = MontcoAttomAdapter().lookup(_ref(), {"provider.attom.api_key": "k"})
    assert out.status == LookupStatus.SUCCESS and out.payload.owner_name == "SAMPLE OWNER LLC" and out.evidence

    monkeypatch.setattr(attom_mod, "lookup_avm_detail", lambda **k: _resp([ATTOM_PROP, ATTOM_PROP]))
    assert MontcoAttomAdapter().lookup(_ref(), {"provider.attom.api_key": "k"}).status == LookupStatus.AMBIGUOUS

    monkeypatch.setattr(attom_mod, "lookup_avm_detail", lambda **k: _resp([], msg="SuccessWithoutResult"))
    assert MontcoAttomAdapter().lookup(_ref(), {"provider.attom.api_key": "k"}).status == LookupStatus.NO_MATCH

    monkeypatch.setattr(attom_mod, "lookup_avm_detail", lambda **k: _resp([], status_code=401, msg="Unauthorized"))
    assert MontcoAttomAdapter().lookup(_ref(), {"provider.attom.api_key": "k"}).status == LookupStatus.AUTH_NEEDED


def test_delco_fips_and_key():
    assert DelcoAttomAdapter().descriptor.county == "delco"
    assert DelcoAttomAdapter().descriptor.parcel_rule.endswith("42045")


def test_secret_env_fallback(db, monkeypatch):
    from app.security.secrets import env_secret_var, get_secret

    assert env_secret_var("provider.attom.api_key") == "USI_SECRET_PROVIDER_ATTOM_API_KEY"
    assert get_secret(db, "provider.attom.api_key") is None
    monkeypatch.setenv("USI_SECRET_PROVIDER_ATTOM_API_KEY", "env-key-123")
    assert get_secret(db, "provider.attom.api_key") == "env-key-123"


def test_enrichment_persists_attom_assessment(db, monkeypatch):
    import app.connectors.attom as attom_mod
    from app.services import enrichment

    monkeypatch.setenv("USI_SECRET_PROVIDER_ATTOM_API_KEY", "env-key-123")
    monkeypatch.setattr(attom_mod, "lookup_avm_detail", lambda **k: _resp([ATTOM_PROP]))

    project = Project(name="attom", county="montco", created_by="t")
    db.add(project)
    db.flush()
    prop = Property(project_id=project.id, county="montco", parcel_original="13-00-30188-00-3",
                    parcel_normalized="13-00-30188-00-3", parcel_search_key="13003018800300",
                    owner_name="SAMPLE OWNER LLC", property_address="1 SAMPLE ST", municipality="NORRISTOWN")
    db.add(prop)
    db.flush()

    summary = enrichment.enrich_property(db, prop, source_keys=["montco.attom"], run_valuation=False)
    assert summary["montco.attom"] == LookupStatus.SUCCESS

    rec = db.scalar(select(AssessmentRecord).where(AssessmentRecord.property_id == prop.id,
                                                   AssessmentRecord.source_key == "montco.attom"))
    assert rec is not None and rec.owner_name == "SAMPLE OWNER LLC" and rec.year_built == 1951
    assert rec.raw_codes["ATTOM_AVM_VALUE"] == 187207
    prov = db.scalars(select(FieldProvenance).where(FieldProvenance.property_id == prop.id,
                                                    FieldProvenance.source_key == "montco.attom",
                                                    FieldProvenance.is_current.is_(True))).all()
    fields = {p.field for p in prov}
    assert {"owner_name", "assessed_value", "year_built", "living_area_sqft"} <= fields
