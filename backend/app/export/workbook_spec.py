"""Main-sheet column layout shared by the web grid and the XLSX export.

Montgomery columns A–AJ reproduce the reference workbook ("Updated Sale List") header text, order and widths.
Columns after AJ are application additions (auditability, decision, provenance) with a distinct header colour.
Delaware County has no reference workbook, so it mirrors the same grouping with Delco-specific fields.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

REFERENCE_HEADER_FILL = "FF01696F"
ADDITION_HEADER_FILL = "FF424242"
MONEY = '"$"#,##0.00'
MONEY0 = '"$"#,##0'
INT = "0"
NUM = "#,##0"
DATE = "mm/dd/yyyy"
DATETIME = "mm/dd/yyyy hh:mm"


@dataclass(frozen=True)
class ColumnSpec:
    key: str
    header: str
    width: float
    kind: str = "text"  # text | money | int | number | date | datetime | link
    group: str = "sale_list"
    reference: bool = True
    pinned: bool = False
    hidden: bool = False
    editable: bool = False
    number_format: str | None = None
    header_font_size: int = 10
    description: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def C(key, header, width, kind="text", group="sale_list", **kw) -> ColumnSpec:  # noqa: N802 - table readability
    fmt = kw.pop("number_format", None) or {"money": MONEY, "int": INT, "number": NUM, "date": DATE, "datetime": DATETIME}.get(kind)
    return ColumnSpec(key, header, width, kind, group, number_format=fmt, **kw)


def _valuation_header(config: dict) -> str:
    strategy = config.get("valuation_selection", {}).get("strategy", "primary_then_lowest")
    return "Lowest Across Sites" if strategy == "lowest_credible" else "Selected Market Valuation"


MONTCO_REFERENCE = [
    C("municipality", "Municipality", 16, pinned=True, editable=True),
    C("sale_number", "Sale Number", 12, pinned=True),
    C("parcel", "Parcel", 18, "link", pinned=True),
    C("owner_name", "Owner Name", 22, editable=True),
    C("full_location", "Location", 34),
    C("listed_amount", "Approx. Sale Price", 14, "money"),
    C("land_use_description", "Land Use Description", 17.57, group="assessment", editable=True),
    C("school_district", "School District", 14.43, group="assessment", header_font_size=11),
    C("assessed_value", "Assessed Value", 16, "money", group="assessment", number_format=MONEY0, header_font_size=11),
    C("lot_size", "Lot Size", 10.57, group="assessment"),
    C("building_style", "Building Style (R-)", 10.57, group="assessment"),
    C("year_built", "Year Built", 8.14, "int", group="assessment"),
    C("exterior_wall", "Exterior Wall Material (R-)", 8, group="assessment"),
    C("living_area_sqft", "Sq Ft Living Area (R-)", 7.14, "number", group="assessment", editable=True),
    C("total_rooms", "Total Rooms (R-)", 4.57, "int", group="assessment"),
    C("bedrooms", "Bedrooms (R-)", 6.57, "int", group="assessment"),
    C("full_baths", "Bathrooms (R-)", 5.29, "int", group="assessment"),
    C("half_baths", "Half Baths (R-)", 4.57, "int", group="assessment"),
    C("last_sale_date", "Sale Date", 10.71, "date", group="assessment"),
    C("last_sale_price", "Sale Price", 9.71, "money", group="assessment", number_format=MONEY0),
    C("gross_building_area", "Gross Building Area (C-)", 9, "number", group="assessment"),
    C("total_living_units", "Total Living Units (C-)", 7.71, "int", group="assessment"),
    C("structure_description", "Structure (C-)", 14.43, group="assessment"),
    C("val_homes_com", "Homes.com (min of range)", 11.43, "money", group="valuation", number_format=MONEY0, editable=True),
    C("val_zillow", "Zillow Zestimate", 10.57, "money", group="valuation", number_format=MONEY0, editable=True),
    C("val_realtor", "Realtor.com Real Estimate", 9.86, "money", group="valuation", number_format=MONEY0, editable=True),
    C("val_redfin", "Redfin Median Sale Price", 10.57, "money", group="valuation", number_format=MONEY0, editable=True),
    C("val_trulia", "Trulia Estimate", 11.14, "money", group="valuation", number_format=MONEY0, editable=True),
    C("selected_valuation", "Lowest Across Sites", 13, "money", group="valuation", number_format=MONEY0),
    C("market_value_flag", "Market Value Flag", 14.43, group="valuation"),
    C("property_photo", "Property Photo", 30, "link", group="valuation"),
    C("tax_2024_total", "Tax 2024 Total", 16, "money", group="tax"),
    C("tax_2025_total", "Tax 2025 Total", 13, "money", group="tax"),
    C("tax_2024_balance", "2024 Balance", 13, "money", group="tax"),
    C("delinquent_total", "Tax Claim Delinquent Total", 11.14, "money", group="tax"),
    C("mortgage_summary", "Mortgage Summary", 15.86, group="mortgage"),
]

DELCO_MAIN = [
    C("municipality", "Municipality", 16, pinned=True, editable=True),
    C("sale_number", "Record #", 9, pinned=True),
    C("parcel", "Folio-NBR", 16, "link", pinned=True),
    C("owner_name", "Owner Name", 24, editable=True),
    C("site_address", "Site Location", 30),
    C("listed_amount", "Total Due (per PDF)", 13, "money"),
    C("property_type", "Property Type", 18, group="assessment", editable=True),
    C("school_district", "School District", 16, group="assessment"),
    C("assessed_value", "Assessment Value", 14, "money", group="assessment", number_format=MONEY0),
    C("acres", "Acres", 8, "number", group="assessment", number_format="0.000"),
    C("building_style", "Style", 14, group="assessment"),
    C("year_built", "Year Built", 8, "int", group="assessment"),
    C("base_area_sqft", "Base Area (sq ft)", 9, "number", group="assessment", editable=True),
    C("bedrooms", "Bed Rooms", 6.5, "int", group="assessment"),
    C("full_baths", "Full Baths", 6.5, "int", group="assessment"),
    C("last_sale_date", "Sale Date", 10.71, "date", group="assessment"),
    C("last_sale_price", "Sale Price", 11, "money", group="assessment", number_format=MONEY0),
    C("improvement_name", "Improvement Name (Commercial)", 16, group="assessment"),
    C("commercial_living_area", "Living Area (Commercial)", 10, "number", group="assessment"),
    C("commercial_year_built", "Year Built (Commercial)", 8, "int", group="assessment"),
    C("commercial_units", "Units (Commercial)", 7, "int", group="assessment"),
    C("legal_description", "Legal Description", 22, group="assessment"),
    C("val_zillow", "Zillow Zestimate", 11, "money", group="valuation", number_format=MONEY0, editable=True),
    C("val_realtor", "Realtor.com Real Estimate (lowest)", 11, "money", group="valuation", number_format=MONEY0, editable=True),
    C("selected_valuation", "Lowest Across Sites", 13, "money", group="valuation", number_format=MONEY0),
    C("market_value_flag", "Market Value Flag", 14.43, group="valuation"),
    C("property_photo", "Property Photo", 30, "link", group="valuation"),
    C("tax_sale_status", "Tax Sale Status", 18, group="tax"),
    C("tax_2024_due", "2024 Taxes Due", 12, "money", group="tax"),
    C("tax_2025_due", "2025 Taxes Due", 12, "money", group="tax"),
    C("delinquent_total", "Delinquent Total Due (all years)", 13, "money", group="tax"),
    C("mortgage_summary", "Mortgage Summary", 15.86, group="mortgage"),
]

COMMON_ADDITIONS = [
    C("tax_status_override", "Tax Interpretation Override", 16, group="tax", reference=False),
    C("tax_checked_at", "Tax Status Checked", 15, "datetime", group="tax", reference=False),
    C("val_attom", "ATTOM AVM", 11, "money", group="valuation", reference=False, number_format=MONEY0),
    C("val_rentcast", "RentCast AVM", 11, "money", group="valuation", reference=False, number_format=MONEY0),
    C("selected_valuation_method", "Valuation Source & Method", 26, group="valuation", reference=False),
    C("category", "Property Category", 14, group="assessment", reference=False),
    C("unsatisfied_mortgages", "Mortgages w/o Satisfaction", 10, "int", group="mortgage", reference=False),
    C("review_mortgages", "Mortgages Needing Review", 10, "int", group="mortgage", reference=False),
    C("lien_summary", "Lien Cases", 14, group="liens", reference=False),
    C("decision_status", "Decision", 16, group="decision", reference=False, editable=True),
    C("decision_summary", "Decision Explanation", 70, group="decision", reference=False),
    C("pending_actions", "Pending User Actions", 28, group="audit", reference=False),
    C("source_status", "Source Status", 34, group="audit", reference=False, hidden=True),
    C("source_page", "PDF Page", 7, "int", group="audit", reference=False),
    C("extraction_confidence", "Extraction Confidence", 9, "number", group="audit", reference=False, number_format="0.00"),
    C("parcel_original", "Parcel (as printed)", 18, group="audit", reference=False, hidden=True),
    C("notes", "Notes", 30, group="audit", reference=False, editable=True),
]

MONTCO_ADDITIONS = [
    C("tax_2023_total", "Tax 2023 Total", 13, "money", group="tax", reference=False),
    C("tax_2025_balance", "2025 Balance", 13, "money", group="tax", reference=False),
    C("tax_sale_status", "Tax Claim Status", 20, group="tax", reference=False),
    C("acres", "Acres", 8, "number", group="assessment", reference=False, number_format="0.000"),
]


def main_columns(county: str, config: dict, is_demo: bool = False) -> list[ColumnSpec]:
    base = MONTCO_REFERENCE if county == "montco" else DELCO_MAIN
    extra = (MONTCO_ADDITIONS if county == "montco" else []) + COMMON_ADDITIONS
    if is_demo:
        extra = extra + [C("val_sandbox", "SANDBOX AVM (demo only)", 12, "money", group="valuation", reference=False, number_format=MONEY0)]
    header = _valuation_header(config)
    cols = []
    for col in base + extra:
        if col.key == "selected_valuation":
            col = ColumnSpec(**{**col.as_dict(), "header": header})
        cols.append(col)
    return cols


def column_letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters
