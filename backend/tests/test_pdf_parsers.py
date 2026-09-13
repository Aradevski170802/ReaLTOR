import os
from pathlib import Path

import pdfplumber
import pymupdf as fitz
import pytest

from app.ingestion.ocr import Word, parse_tesseract_tsv
from app.ingestion.parsers import detect_pdf_parser, parse_pdf
from app.ingestion.parsers.delco_tcb import DelcoSalesReportParser
from app.ingestion.parsers.montco_tcb import MontcoUpsetListParser
from app.ingestion.tabular_import import parse_tabular
from tests.conftest import DELCO_PDF, MONTCO_PDF


@pytest.fixture(scope="module")
def delco():
    return {r.value("sale_number"): r for r in parse_pdf(DELCO_PDF, "delco").records}


@pytest.fixture(scope="module")
def montco():
    return parse_pdf(MONTCO_PDF, "montco")


def test_detects_parsers():
    assert detect_pdf_parser(DELCO_PDF) == DelcoSalesReportParser.key
    assert detect_pdf_parser(MONTCO_PDF) == MontcoUpsetListParser.key


def test_delco_record_fields_and_provenance(delco):
    rec = delco["1"]
    assert rec.page_number == 1
    assert rec.value("parcel") == "01-00-00254-00"
    assert rec.value("property_address") == "3 CHESTER AVE"
    assert rec.value("municipality") == "ALDAN"
    assert rec.value("sale_date") == "2026-09-24"
    assert rec.value("assessment") == "170490"
    assert rec.value("amount_due") == "217.84"
    assert rec.value("description") == "2 STY HSE ADD"
    assert rec.value("lot_size") == "60 X 95 LOT 12"
    assert rec.value("mailing_address").endswith("PA 19018")
    assert "RECORD# 1" in rec.raw_text
    assert rec.tax_lines[0]["tax_year"] == 2024 and rec.tax_lines[0]["sch_assessment"] == 170490
    assert not rec.needs_review


def test_delco_long_owner_lines_split_on_fixed_column(delco):
    rec = delco["3"]  # long owner lines sit right next to the location / description column
    assert rec.value("property_address") == "108 S ELM AVE"
    assert rec.value("description") == "GRD"
    assert "TRUSTEE OF THE 108" in rec.value("owner_name")
    assert rec.value("mailing_address").startswith("PO BOX 332")
    assert not rec.review_reasons


def test_delco_overflow_heuristic_flags_review():
    # Synthetic line where a 33-character owner pushes the location off the fixed 201pt column.
    pitch, origin = 4.8, 52.6
    text = "VERYLONGOWNERNAMEFORTESTING TRUST 412 N MAIN ST"
    words, col = [], 0
    for token in text.split(" "):
        words.append(Word(token, origin + col * pitch, origin + (col + len(token)) * pitch, 83.5, 91.5))
        col += len(token) + 1
    left, right, overflow = DelcoSalesReportParser._split(words, "location")
    assert overflow
    assert " ".join(w.text for w in left) == "VERYLONGOWNERNAMEFORTESTING TRUST"
    assert " ".join(w.text for w in right) == "412 N MAIN ST"


def test_delco_multi_year_sums_to_total_due(delco):
    rec = delco["28"]
    assert [t["tax_year"] for t in rec.tax_lines] == [2018, 2019, 2020, 2021, 2022, 2023, 2024]
    assert round(sum(t["amount"] for t in rec.tax_lines), 2) == float(rec.value("amount_due"))
    assert not any("do not sum" in r for r in rec.review_reasons)


def test_delco_last_record_ignores_group_footer(delco):
    rec = delco["1742"]
    assert rec.value("municipality") == "CHESTER CITY"
    assert rec.value("property_address") == "W 10TH ST"
    assert "[following text not part of record]" in rec.raw_text


def test_montco_table_parse(montco):
    assert montco.metadata["list_as_of"] == "9/01/2026"
    assert montco.metadata["sale_date"] == "September 24, 2026"
    kinds = {p.number: p.kind for p in montco.pages}
    assert kinds[1] == "notice"
    records = montco.records
    assert len(records) == 55
    assert len({r.value("sale_number") for r in records}) == 55
    first = records[0]
    assert first.value("parcel") == "01-00-01606-02-2" and first.value("amount_due") == "6638.20"
    assert first.value("property_address") == "224 -228 FOREST AVE"
    wrapped = [r for r in records if r.value("municipality") == "Upper Salford"]
    assert wrapped and wrapped[0].value("property_address") == "2154 PERKIOMENVILLE RD"
    assert all(not r.review_reasons for r in records)


def test_montco_positional_fallback(monkeypatch):
    monkeypatch.setattr(MontcoUpsetListParser, "_from_tables", lambda self, tables, page: [])
    result = MontcoUpsetListParser(ocr=None).parse(MONTCO_PDF)
    assert len(result.records) == 55
    assert all(any("fallback" in r for r in rec.review_reasons) for rec in result.records)
    assert result.records[0].value("parcel") == "01-00-01606-02-2"
    assert any("positional parser" in w for w in result.warnings)


class FakeOcr:
    """Returns the text-layer words of the original page, simulating a perfect OCR engine with 90% confidence."""

    name = "fake-ocr"

    def __init__(self, source: Path):
        self.source = source

    def available(self):
        return True

    def words_for_page(self, pdf_path, page_index):
        with pdfplumber.open(self.source) as pdf:
            return [Word(w["text"], w["x0"], w["x1"], w["top"], w["bottom"], 0.9)
                    for w in pdf.pages[page_index].extract_words(x_tolerance=1.5, y_tolerance=2)]


def _image_only_copy(src: Path, dst: Path, pages: int = 1) -> None:
    doc = fitz.open(src)
    out = fitz.open()
    for i in range(pages):
        pix = doc[i].get_pixmap(dpi=110)
        page = out.new_page(width=doc[i].rect.width, height=doc[i].rect.height)
        page.insert_image(page.rect, pixmap=pix)
    out.save(dst)


def test_scanned_page_uses_ocr_and_flags_review(tmp_path):
    scanned = tmp_path / "scanned.pdf"
    _image_only_copy(DELCO_PDF, scanned)
    result = DelcoSalesReportParser(ocr=FakeOcr(DELCO_PDF)).parse(scanned)
    assert result.pages[0].kind == "scanned" and result.pages[0].ocr_applied
    assert len(result.records) == 5
    assert all(any("OCR" in r for r in rec.review_reasons) for rec in result.records)
    assert result.records[0].value("parcel") == "01-00-00254-00"


def test_scanned_page_without_ocr_is_reported(tmp_path, monkeypatch):
    scanned = tmp_path / "scanned.pdf"
    _image_only_copy(DELCO_PDF, scanned)
    monkeypatch.setattr("app.ingestion.parsers.delco_tcb.get_ocr_engine", lambda: None)
    result = DelcoSalesReportParser().parse(scanned)
    assert result.records == []
    assert any("no OCR engine" in w for w in result.warnings)


def test_tesseract_tsv_parsing():
    tsv = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n" \
          "5\t1\t1\t1\t1\t1\t300\t600\t120\t40\t91.5\tRECORD#\n4\t1\t1\t1\t1\t0\t0\t0\t0\t0\t-1\t\n"
    words = parse_tesseract_tsv(tsv, scale=72 / 300)
    assert len(words) == 1 and words[0].text == "RECORD#" and round(words[0].x0, 1) == 72.0 and words[0].conf == 0.915


def test_tabular_import_csv(tmp_path):
    csv = tmp_path / "list.csv"
    csv.write_text("Folio-NBR,Owner,Site Location,Total Due,Sale Date,Notes\n01-00-00254-00,DOE JANE,3 CHESTER AVE,\"$217.84\",09/24/26,x\n"
                   "BAD,NOBODY,1 MAIN ST,$1.00,09/24/26,y\n", encoding="utf-8")
    result = parse_tabular(csv, "delco")
    assert result.metadata["column_mapping"]["0"] == "parcel"
    assert result.records[0].value("amount_due") == "217.84"
    assert result.records[0].value("sale_date") == "2026-09-24"
    assert result.records[0].fields["extra:Notes"].value == "x"
    assert result.records[1].review_reasons


@pytest.mark.source_pdf
@pytest.mark.skipif(not os.environ.get("USI_SOURCE_PDF_DELCO"), reason="set USI_SOURCE_PDF_DELCO to the full county PDF")
def test_full_delco_pdf():
    result = parse_pdf(os.environ["USI_SOURCE_PDF_DELCO"], "delco")
    assert len(result.records) == 1742
    assert sum(r.needs_review for r in result.records) <= 40


@pytest.mark.source_pdf
@pytest.mark.skipif(not os.environ.get("USI_SOURCE_PDF_MONTCO"), reason="set USI_SOURCE_PDF_MONTCO to the full county PDF")
def test_full_montco_pdf():
    result = parse_pdf(os.environ["USI_SOURCE_PDF_MONTCO"], "montco")
    assert len(result.records) == 484
