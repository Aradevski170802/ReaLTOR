"""Sale-list parser registry and signature-based detection."""

from __future__ import annotations

from pathlib import Path

import pdfplumber

from app.ingestion.parsers.delco_tcb import DelcoSalesReportParser
from app.ingestion.parsers.generic_table import GenericTablePdfParser
from app.ingestion.parsers.montco_tcb import MontcoUpsetListParser

PDF_PARSERS = {
    DelcoSalesReportParser.key: DelcoSalesReportParser,
    MontcoUpsetListParser.key: MontcoUpsetListParser,
    GenericTablePdfParser.key: GenericTablePdfParser,
}

COUNTY_DEFAULT_PARSER = {
    "delco": DelcoSalesReportParser.key,
    "montco": MontcoUpsetListParser.key,
}


def detect_pdf_parser(path: str | Path, county: str | None = None, sample_pages: int = 6) -> str:
    text = ""
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages[:sample_pages]:
            text += (page.extract_text() or "") + "\n"
    for parser_cls in (DelcoSalesReportParser, MontcoUpsetListParser):
        if parser_cls.matches(text):
            return parser_cls.key
    if county in COUNTY_DEFAULT_PARSER and not text.strip():
        # Image-only document for a known county: use the county parser (OCR words feed it).
        return COUNTY_DEFAULT_PARSER[county]
    return GenericTablePdfParser.key


def parse_pdf(path: str | Path, county: str | None = None, parser_key: str | None = None):
    key = parser_key or detect_pdf_parser(path, county)
    return PDF_PARSERS[key]().parse(path)
