"""Montgomery County, Pennsylvania connector."""

from __future__ import annotations

from app.common.parcels import ParcelParts
from app.connectors.attom import MontcoAttomAdapter
from app.connectors.base import CountyConnector, SourceAdapter
from app.connectors.montco.civil import MontcoCivilAdapter
from app.connectors.montco.gis import MontcoAssessmentGisAdapter
from app.connectors.montco.portal import MontcoAssessmentPortalAdapter
from app.connectors.montco.recorder import MontcoRecorderAdapter
from app.connectors.montco.tax_claim import MontcoTaxClaimAdapter
from app.connectors.montco.ujs import MontcoUjsJudgmentsAdapter


class MontcoConnector(CountyConnector):
    county = "montco"
    label = "Montgomery County, Pennsylvania"
    parcel_label = "Parcel"
    sale_list_parser_key = "montco_tcb_upset_list_v1"
    workbook_spec_key = "montco"

    def __init__(self):
        self._sources: list[SourceAdapter] = [
            MontcoAssessmentGisAdapter(),
            MontcoAttomAdapter(),
            MontcoTaxClaimAdapter(),
            MontcoAssessmentPortalAdapter(),
            MontcoRecorderAdapter(),
            MontcoCivilAdapter(),
            MontcoUjsJudgmentsAdapter(),
        ]

    def sources(self) -> list[SourceAdapter]:
        return self._sources

    def identifiers(self, parts: ParcelParts) -> dict[str, str]:
        return {
            "parcel_dashed": parts.normalized,
            "gis_parcel_number": parts.digits,
            "civil_search_key": parts.digits,
            "tax_claim_search": parts.normalized,
        }
