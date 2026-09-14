"""Delaware County, Pennsylvania connector (not the State of Delaware)."""

from __future__ import annotations

from app.common.parcels import ParcelParts
from app.connectors.attom import DelcoAttomAdapter
from app.connectors.base import CountyConnector, SourceAdapter
from app.connectors.delco.civil import DelcoCivilAdapter
from app.connectors.delco.gis import DelcoParcelsGisAdapter
from app.connectors.delco.portal import DelcoAssessmentPortalAdapter
from app.connectors.delco.recorder import DelcoCountywebRecorderAdapter, DelcoPublicSearchRecorderAdapter
from app.connectors.delco.treasurer import DelcoTreasurerAdapter


class DelcoConnector(CountyConnector):
    county = "delco"
    label = "Delaware County, Pennsylvania"
    parcel_label = "Folio-NBR"
    sale_list_parser_key = "delco_tcb_sales_report_v1"
    workbook_spec_key = "delco"

    def __init__(self):
        self._sources: list[SourceAdapter] = [
            DelcoParcelsGisAdapter(),
            DelcoAttomAdapter(),
            DelcoAssessmentPortalAdapter(),
            DelcoTreasurerAdapter(),
            DelcoPublicSearchRecorderAdapter(),
            DelcoCountywebRecorderAdapter(),
            DelcoCivilAdapter(),
        ]

    def sources(self) -> list[SourceAdapter]:
        return self._sources

    def identifiers(self, parts: ParcelParts) -> dict[str, str]:
        ids = {"folio": parts.normalized, "folio_digits": parts.digits, "datalet_pin": parts.digits}
        if parts.valid:
            ids["gis_parid"] = str(int(parts.digits))
        return ids
