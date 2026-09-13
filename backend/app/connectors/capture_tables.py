"""Extract recorder-document and civil-case rows from captured result pages or CSV/XLSX imports."""

from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup

from app.common.parsing import clean_ws, parse_date, parse_money
from app.connectors.iasworld import norm_label
from app.connectors.payloads import LienCasePayload, RecorderDocumentPayload

RECORDER_SYNONYMS: dict[str, list[str]] = {
    "instrument_number": ["instrument", "instrument #", "instrument number", "instrument no", "doc #", "doc number",
                          "document number", "document #", "document no", "inst #", "inst no", "file number", "doc id"],
    "doc_type_raw": ["type", "doc type", "document type", "instrument type", "kind of instrument", "description"],
    "recorded_date": ["recorded date", "record date", "recording date", "date recorded", "rec date", "filed date",
                      "recorded", "file date"],
    "grantors": ["grantor", "grantors", "from", "party 1", "mortgagor", "direct name", "direct party"],
    "grantees": ["grantee", "grantees", "to", "party 2", "mortgagee", "reverse name", "reverse party", "indirect name"],
    "amount": ["amount", "consideration", "mortgage amt", "mortgage amount", "loan amount", "value"],
    "book": ["book", "bk"],
    "page": ["page", "pg"],
    "book_page": ["book/page", "book-page", "bk/pg", "book page"],
    "related_reference": ["assoc instruments", "associated instruments", "related", "related documents", "reference",
                          "ref instrument", "marginal reference", "references"],
    "source_url": ["url", "link", "document link"],
    "parcel": ["parcel", "parcel id", "folio", "folio-nbr", "parcel number"],
}

CIVIL_SYNONYMS: dict[str, list[str]] = {
    "case_number": ["case number", "case #", "case no", "docket", "docket number", "case", "case id"],
    "status": ["status", "case status"],
    "classification": ["case classification", "classification", "class", "case category", "category"],
    "case_type": ["case type", "type", "judgment type"],
    "parties": ["parties", "party", "party name", "name", "plaintiff", "defendant", "caption", "style", "case title"],
    "filing_date": ["filing date", "filed", "file date", "date filed", "commenced", "filed date", "initiation date"],
    "amount": ["amount", "judgment amount", "claim amount", "lien amount"],
    "source_url": ["url", "link"],
    "parcel": ["parcel", "parcel id", "folio", "parcel #"],
}

INSTRUMENT_LABEL_RE = re.compile(r"^(?P<code>[A-Z]{2,6})\s+(?P<book>\d{1,6})\s+(?P<page>\d{1,6})$")
BOOK_PAGE_RE = re.compile(r"(?:BK|BOOK)\s*(\d+)\s*(?:PG|PAGE)\s*(\d+)", re.I)


def extract_tables(content: str) -> list[list[list[str]]]:
    if re.search(r"<(table|tr)\b", content[:500_000], re.I):
        soup = BeautifulSoup(content, "lxml")
        tables = []
        for table in soup.find_all("table"):
            rows = []
            for tr in table.find_all("tr"):
                if tr.find_parent("table") is not table:
                    continue
                cells = [clean_ws(c.get_text(" ", strip=True)) or "" for c in tr.find_all(["td", "th"], recursive=False)]
                links = [a.get("href") for a in tr.find_all("a") if a.get("href", "").startswith("http")]
                if links:
                    cells.append(links[0])
                if any(cells):
                    rows.append(cells)
            if rows:
                tables.append(rows)
        return tables
    rows: list[list[str]] = []
    for line in content.splitlines():
        if not line.strip():
            continue
        cells = [clean_ws(c) or "" for c in (line.split("\t") if "\t" in line else re.split(r"\s{2,}", line.strip()))]
        rows.append(cells)
    return [rows] if rows else []


def _map_header(cells: list[str], synonyms: dict[str, list[str]]) -> dict[int, str]:
    lookup = {norm_label(s): key for key, syns in synonyms.items() for s in syns}
    mapping: dict[int, str] = {}
    for idx, cell in enumerate(cells):
        key = lookup.get(norm_label(cell))
        if key and key not in mapping.values():
            mapping[idx] = key
    return mapping


def rows_from_tables(tables: list[list[list[str]]], synonyms: dict[str, list[str]], required: set[str]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for table in tables:
        mapping: dict[int, str] | None = None
        for cells in table:
            candidate = _map_header(cells, synonyms)
            if required.issubset(set(candidate.values())) and len(candidate) >= 2:
                mapping = candidate
                continue
            if mapping is None:
                continue
            row = {key: cells[idx] for idx, key in mapping.items() if idx < len(cells) and cells[idx]}
            if len(cells) > len(mapping) and cells[-1].startswith("http") and "source_url" not in row:
                row["source_url"] = cells[-1]
            if row:
                out.append(row)
    return out


def rows_from_file(path: str | Path, synonyms: dict[str, list[str]], required: set[str]) -> list[dict[str, str]]:
    from app.ingestion.tabular_import import read_rows

    table = [[c or "" for c in row] for row in read_rows(path)]
    return rows_from_tables([table], synonyms, required)


def to_recorder_documents(rows: list[dict[str, str]]) -> list[RecorderDocumentPayload]:
    docs = []
    for row in rows:
        instrument = row.get("instrument_number")
        book, page = row.get("book"), row.get("page")
        if instrument and (m := INSTRUMENT_LABEL_RE.match(instrument.upper())) and not (book and page):
            book, page = m.group("book"), m.group("page")
        if row.get("book_page") and not (book and page):
            parts = re.split(r"[/\-\s]+", row["book_page"].strip())
            if len(parts) >= 2:
                book, page = parts[0], parts[1]
        doc_type = row.get("doc_type_raw")
        amount = parse_money(row.get("amount"))
        if amount is None and doc_type and (m := re.search(r"\(\$([\d,]+(?:\.\d{2})?)\)", doc_type)):
            amount = parse_money(m.group(1))
        if not (instrument or (book and page)):
            continue
        docs.append(
            RecorderDocumentPayload(
                instrument_number=instrument,
                doc_type_raw=doc_type,
                recorded_date=parse_date(row.get("recorded_date")),
                grantors=row.get("grantors"),
                grantees=row.get("grantees"),
                amount=amount,
                book=book,
                page=page,
                related_reference=row.get("related_reference"),
                source_url=row.get("source_url"),
            )
        )
    return docs


def to_lien_cases(rows: list[dict[str, str]], search_term: str | None = None) -> list[LienCasePayload]:
    cases = []
    for row in rows:
        number = row.get("case_number")
        if not number:
            continue
        cases.append(
            LienCasePayload(
                case_number=number,
                status=row.get("status"),
                classification=row.get("classification"),
                case_type=row.get("case_type"),
                parties=row.get("parties"),
                filing_date=parse_date(row.get("filing_date")),
                amount=parse_money(row.get("amount")),
                source_url=row.get("source_url"),
                search_term=search_term,
            )
        )
    return cases
