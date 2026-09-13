"""Delaware County PA Tax Claim Bureau "SALES REPORT" parser.

Layout (fixed-pitch line printer report, 5 records per page):

    RECORD# 3   FOLIO-NBR 01-00-00406-00     SALE DATE: 09/24/26         ALDAN
    DIGIROLAMO ALFONSO TRUSTEE 108 S ELM AVE                                   <- owner overflows location column
    OF THE 108 SOUTH ELM TRUST GRD               CURRENT ASSESSMENT 20,230  TOTAL DUE: 1,330.67
    PO BOX 332           70 X 125
    DREXEL HILL PA 19026
       2024                     CTY   20,230     SCH  20,230    TWN  20,230          1,330.67

Left column: owner line(s), mailing street line(s), city/state/zip. Right column (x ~ 201pt): site
location, description, lot size. One line per delinquent tax year follows. Validation: year amounts
must sum to TOTAL DUE; anything heuristic is flagged for review instead of silently trusted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

from app.common.parcels import normalize_parcel
from app.common.parsing import parse_date, parse_money
from app.ingestion.ocr import OcrEngine, Word, get_ocr_engine
from app.ingestion.pdf_layout import Line, classify_page, group_lines, join_words, page_words
from app.ingestion.types import ExtractedField, ParsedRecord, ParseResult

SIGNATURE = re.compile(r"DELAWARE\s+COUNTY,?\s*PA\s*-\s*TAX\s+CLAIM\s+BUREAU", re.I)
HEADER_RE = re.compile(
    r"RECORD#\s*(?P<rec>\d+)\s+FOLIO-NBR\s+(?P<folio>\S+)\s+SALE\s+DATE:\s*(?P<date>\d{1,2}/\d{1,2}/\d{2,4})\s*(?P<muni>.*)$"
)
ASSESS_RE = re.compile(
    r"CURRENT\s+ASSESSMENT\s+(?:(?P<assess>[\d,]+)\s+)?TOTAL\s+DUE:\s*(?P<due>[\d,]+(?:\.\d+)?)"
)
PAGE_HEADER_PATTERNS = [
    SIGNATURE,
    re.compile(r"^DATE:\s*\S+.*SALES\s+REPORT", re.I),
    re.compile(r"^PAGE:\s*\d+$", re.I),
    re.compile(r"^FOLIO-NBR/OWNER/ADDRESS", re.I),
]
REPORT_DATE_RE = re.compile(r"DATE:\s*(\d{2}/\d{2}/\d{2,4})")
INTEREST_RE = re.compile(r"INTEREST\s+CALCULATED\s+AS\s+OF:\s*(\d{2}/\d{2}/\d{4})")
YEAR_RE = re.compile(r"^(19|20)\d{2}$")
AMOUNT_RE = re.compile(r"^[\d,]+(\.\d{1,2})?$")
CSZ_RE = re.compile(r"\b[A-Z]{2}\s+\d{5}(-[\d`']{0,4})?$")  # the county report occasionally prints a garbled ZIP+4 ("19050-`")
ZIP_ONLY_RE = re.compile(r"^\d{5}(-\d{4})?$")
STATE_END_RE = re.compile(r"\b[A-Z]{2}$")
HOUSE_NO_RE = re.compile(r"^\d+[A-Z]?(-\d+[A-Z]?)?$")
MAIL_START_RE = re.compile(r"^(\d+[A-Z]?\b|P\s*O\s*BOX\b|BOX\s+\d|C/O\b|RR\s*\d|R\s*D\s*\d|HC\s*\d)")
UNIT_CONTINUATION_RE = re.compile(r"\b(BX|BOX|STE|SUITE|APT|UNIT|FL|RM)$")

RIGHT_COL_X = 201.0
COL_TOL = 3.5
SPLIT_X = 196.0
MIN_OVERFLOW_X = 140.0  # owner text is at least ~18 characters before it can overflow
YEAR_X_RANGE = (62.0, 94.0)
AMOUNT_MIN_X = 560.0

DESC_VOCAB = {
    "GRD", "GROUND", "HSE", "HOUSE", "STY", "STORY", "APT", "APTS", "BLDG", "BLD", "STORE", "GAR", "GARAGE", "DUP",
    "DUPLEX", "COM", "COMM", "CONDO", "TWIN", "ROW", "OFFICE", "OFF", "WAREHOUSE", "WHSE", "CHURCH", "PARKING", "PKG",
    "MOBILE", "TRAILER", "FACTORY", "SHED", "BARN", "RES", "VAC", "VACANT", "LOT", "LOTS", "DWG", "BUNG", "BUNGALOW",
    "RANCH", "SPLIT", "CAPE", "MILL", "SHOP", "REST", "HOTEL", "MOTEL", "BANK", "SCHOOL", "CLUB", "STA", "STATION",
    "CELLULAR",
}
STORY_TOKENS = {"STY", "STORY", "STORIES"}
ACRE_TOKENS = {"AC", "ACRE", "ACRES"}


@dataclass
class _Block:
    header: Line
    match: re.Match
    lines: list[Line] = field(default_factory=list)


class DelcoSalesReportParser:
    key = "delco_tcb_sales_report_v1"
    county = "delco"

    def __init__(self, ocr: OcrEngine | None = None):
        self._ocr = ocr

    @staticmethod
    def matches(text: str) -> bool:
        return bool(SIGNATURE.search(text)) and "FOLIO-NBR" in text

    # ------------------------------------------------------------------ parse
    def parse(self, path: str | Path) -> ParseResult:
        ocr = self._ocr if self._ocr is not None else get_ocr_engine()
        result = ParseResult(parser_key=self.key, records=[])
        stream: list[Line] = []
        with pdfplumber.open(str(path)) as pdf:
            for index, page in enumerate(pdf.pages):
                info = classify_page(page, index + 1)
                words = page_words(path, page, info, ocr, result.warnings)
                result.pages.append(info)
                for line in group_lines(words, info.number):
                    if self._is_page_header(line.text, result.metadata):
                        continue
                    stream.append(line)
                page.close()  # release pdfplumber's per-page object cache (keeps 350-page lists within small instances)

        blocks: list[_Block] = []
        orphans: list[Line] = []
        for line in stream:
            match = HEADER_RE.search(line.text)
            if match:
                blocks.append(_Block(header=line, match=match))
            elif blocks:
                blocks[-1].lines.append(line)
            else:
                orphans.append(line)
        if orphans:
            result.warnings.append(f"{len(orphans)} line(s) before the first RECORD# were ignored")

        page_counts: dict[int, int] = {}
        for i, block in enumerate(blocks):
            record = self._parse_block(i, block)
            result.records.append(record)
            page_counts[block.header.page] = page_counts.get(block.header.page, 0) + 1
        for info in result.pages:
            info.records = page_counts.get(info.number, 0)

        numbers = [int(r.value("sale_number") or 0) for r in result.records]
        if numbers:
            missing = sorted(set(range(min(numbers), max(numbers) + 1)) - set(numbers))
            if missing:
                result.warnings.append(f"Record numbers missing from sequence: {missing[:25]}{'…' if len(missing) > 25 else ''}")
        return result

    @staticmethod
    def _is_page_header(text: str, meta: dict) -> bool:
        if (m := REPORT_DATE_RE.search(text)) and "SALES REPORT" in text.upper():
            meta.setdefault("report_date", m.group(1))
            if im := INTEREST_RE.search(text):
                meta.setdefault("interest_calculated_as_of", im.group(1))
            return True
        return any(p.search(text) for p in PAGE_HEADER_PATTERNS)

    # ------------------------------------------------------------------ block
    def _parse_block(self, index: int, block: _Block) -> ParsedRecord:
        m = block.match
        reasons: list[str] = []
        notes: list[str] = []
        min_conf = min([block.header.min_conf] + [ln.min_conf for ln in block.lines])
        ocr_penalty = 0.0 if min_conf >= 0.99 else 0.25

        body: list[Line] = []
        year_lines: list[Line] = []
        trailing: list[Line] = []
        for line in block.lines:
            first = line.words[0]
            if YEAR_RE.match(first.text) and YEAR_X_RANGE[0] <= first.x0 <= YEAR_X_RANGE[1] and len(line.words) >= 2:
                year_lines.append(line)
            elif year_lines:
                trailing.append(line)
            else:
                body.append(line)

        assessment = total_due = None
        assessment_line_found = False
        cleaned: list[list[Word]] = []
        assess_line_idx = None
        for i, line in enumerate(body):
            words = list(line.words)
            am = ASSESS_RE.search(line.text)
            if am:
                assessment_line_found = True
                assessment = parse_money(am.group("assess")) if am.group("assess") else None
                total_due = parse_money(am.group("due"))
                assess_line_idx = i
                cut = next(
                    (k for k in range(len(words) - 1) if words[k].text == "CURRENT" and words[k + 1].text == "ASSESSMENT"),
                    len(words),
                )
                words = words[:cut]
            cleaned.append(words)

        roles = ["location", "description", "lot"]
        lefts: list[str] = []
        rights: list[str] = []
        overflow_lines: list[int] = []
        for i, words in enumerate(cleaned):
            role = roles[i] if i < len(roles) else "extra"
            left, right, overflow = self._split(words, role)
            lefts.append(join_words(left))
            rights.append(join_words(right))
            if overflow:
                overflow_lines.append(i + 1)
        if overflow_lines:
            reasons.append(
                f"Owner/mailing text overflows into the location column on line(s) {overflow_lines}; split heuristically"
            )

        owner_lines, mailing_lines, mailing_ok = self._owner_and_mailing([t for t in lefts if t])
        if not mailing_ok:
            reasons.append("Could not identify the owner mailing address (no city/state/ZIP line)")
        owner = " ".join(owner_lines).strip() or None
        mailing = ", ".join(mailing_lines) or None
        if not owner:
            reasons.append("Owner name missing")
        if len(body) > 4 and mailing_ok:
            notes.append(f"Owner/mailing block spans {len(body)} lines (resolved)")
        elif len(body) < 3:
            reasons.append(f"Unexpected record layout ({len(body)} address lines)")

        location = rights[0] if rights else None
        desc_idx = assess_line_idx if assess_line_idx is not None else 1
        description = rights[desc_idx] if len(rights) > desc_idx else None
        lot_parts = [r for j, r in enumerate(rights) if j not in (0, desc_idx) and r]
        lot_size = " ".join(lot_parts) or None
        if not assessment_line_found:
            reasons.append("CURRENT ASSESSMENT / TOTAL DUE line not found")
        elif assessment is None:
            notes.append("CURRENT ASSESSMENT is blank on the sale list")

        tax_lines = [self._parse_year(line) for line in year_lines]
        if not tax_lines:
            reasons.append("No delinquent tax-year lines found")
        elif total_due is not None:
            summed = round(sum(t["amount"] or 0 for t in tax_lines), 2)
            if abs(summed - total_due) > 0.05:
                reasons.append(f"Tax-year amounts ({summed:,.2f}) do not sum to TOTAL DUE ({total_due:,.2f})")

        parcel = normalize_parcel("delco", m.group("folio"))
        if not parcel.valid:
            reasons.append(f"Folio number could not be validated: {'; '.join(parcel.notes)}")
        elif parcel.repaired:
            reasons.append(f"Folio number repaired: {'; '.join(parcel.notes)}")

        sale_date = parse_date(m.group("date"))
        municipality = re.sub(r"\s+", " ", m.group("muni")).strip() or None
        split_conf = 0.72 if overflow_lines else 0.97
        owner_conf = split_conf if len(body) <= 4 else min(split_conf, 0.9)

        fields = {
            "sale_number": ExtractedField(m.group("rec"), m.group("rec"), 1.0 - ocr_penalty),
            "parcel": ExtractedField(m.group("folio"), parcel.normalized, parcel.confidence - ocr_penalty),
            "owner_name": ExtractedField(" | ".join(owner_lines) or None, owner,
                                         (owner_conf if owner else 0.2) - ocr_penalty),
            "mailing_address": ExtractedField(" | ".join(mailing_lines) or None, mailing,
                                              (0.9 if mailing_ok else 0.3) - ocr_penalty),
            "property_address": ExtractedField(location, location,
                                               (split_conf if location else 0.3) - ocr_penalty),
            "municipality": ExtractedField(m.group("muni"), municipality, 0.98 - ocr_penalty),
            "sale_date": ExtractedField(m.group("date"), sale_date.isoformat() if sale_date else None,
                                        (1.0 if sale_date else 0.3) - ocr_penalty),
            "amount_due": ExtractedField(None if total_due is None else str(total_due),
                                         None if total_due is None else f"{total_due:.2f}",
                                         (1.0 if total_due is not None else 0.3) - ocr_penalty),
            "assessment": ExtractedField(None if assessment is None else str(assessment),
                                         None if assessment is None else f"{assessment:.0f}",
                                         (1.0 if assessment_line_found else 0.3) - ocr_penalty),
            "description": ExtractedField(description, description, (split_conf if description else 0.5) - ocr_penalty),
            "lot_size": ExtractedField(lot_size, lot_size, (split_conf if lot_size else 0.5) - ocr_penalty),
        }
        if ocr_penalty:
            reasons.append("Values were read with OCR; verify against the PDF page")

        raw_lines = [block.header.text] + [ln.text for ln in body] + [ln.text for ln in year_lines]
        if trailing:
            raw_lines += ["[following text not part of record] " + ln.text for ln in trailing]
        if notes:
            raw_lines += [f"[parser note] {n}" for n in notes]
        return ParsedRecord(
            row_index=index,
            page_number=block.header.page,
            raw_text="\n".join(raw_lines),
            fields=fields,
            tax_lines=tax_lines,
            review_reasons=reasons,
        )

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _owner_and_mailing(lines: list[str]) -> tuple[list[str], list[str], bool]:
        merged: list[str] = []
        for text in lines:
            if merged and ZIP_ONLY_RE.match(text) and STATE_END_RE.search(merged[-1]) or merged and re.fullmatch(r"\d{1,5}[A-Z]?", text) and UNIT_CONTINUATION_RE.search(merged[-1]):
                merged[-1] = f"{merged[-1]} {text}"
            else:
                merged.append(text)
        if len(merged) < 2 or not CSZ_RE.search(merged[-1]):
            return merged, [], False
        csz_idx = len(merged) - 1
        start = next((i for i in range(1, csz_idx) if MAIL_START_RE.match(merged[i])), None)
        if start is None:
            start = max(1, csz_idx - 1)
        return merged[:start], merged[start:], True

    @staticmethod
    def _split(words: list[Word], role: str) -> tuple[list[Word], list[Word], bool]:
        if not words:
            return [], [], False
        for k, w in enumerate(words):
            if abs(w.x0 - RIGHT_COL_X) <= COL_TOL:
                return words[:k], words[k:], False
        if all(w.x1 <= SPLIT_X + 1 for w in words):
            return words, [], False
        if all(w.x0 >= SPLIT_X for w in words):
            return [], words, False

        candidates = [k for k in range(1, len(words)) if words[k].x0 >= MIN_OVERFLOW_X]
        tokens = [w.text.upper() for w in words]

        def pick(pred) -> int | None:
            return next((k for k in candidates if pred(k)), None)

        def numeric(i: int) -> bool:
            return re.match(r"^\d+(\.\d+)?$", tokens[i]) is not None

        k: int | None = None
        if role == "location":
            k = pick(lambda i: HOUSE_NO_RE.match(tokens[i]) is not None)
        elif role == "description":
            k = pick(
                lambda i: tokens[i] in DESC_VOCAB
                or (re.match(r"^\d(\.\d)?$", tokens[i]) is not None and i + 1 < len(tokens) and tokens[i + 1] in STORY_TOKENS)
            )
        elif role == "lot":
            k = pick(
                lambda i: numeric(i) and i + 1 < len(tokens) and (tokens[i + 1] == "X" or tokens[i + 1] in ACRE_TOKENS)
            )
        if k is None:
            k = next((i for i, w in enumerate(words) if w.x0 >= SPLIT_X or w.x1 > SPLIT_X + 6), len(words))
        return words[:k], words[k:], True

    @staticmethod
    def _parse_year(line: Line) -> dict:
        tokens = [w.text for w in line.words]
        values: dict[str, float | None] = {"CTY": None, "SCH": None, "TWN": None}
        i = 1
        while i < len(tokens) - 1:
            if tokens[i] in values and AMOUNT_RE.match(tokens[i + 1]):
                values[tokens[i]] = parse_money(tokens[i + 1])
                i += 2
            else:
                i += 1
        last = line.words[-1]
        amount = parse_money(last.text) if last.x0 >= AMOUNT_MIN_X and AMOUNT_RE.match(last.text) else None
        return {
            "tax_year": int(tokens[0]),
            "cty_assessment": values["CTY"],
            "sch_assessment": values["SCH"],
            "twn_assessment": values["TWN"],
            "amount": amount,
            "raw_line": line.text,
        }
