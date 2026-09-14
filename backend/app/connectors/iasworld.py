"""Generic parser for Tyler iasWorld public-access "datalet" pages (Montco and Delco property portals).

Used ONLY for content a user captured from the official portal after accepting its disclaimer
(saved HTML, or page text copied from the browser). The page structure is:

    <table class="DataletHeader"> section title (e.g. "Owner History", "Residential", "Tax Sale Information")
    <table class="DataletTable"> rows of <td class="DataletSideHeading">Label</td><td class="DataletData">Value</td>
                                 or a grid with DataletTopHeading header cells.

The parser is deliberately label-driven so small layout differences between counties or versions
do not silently shift values; unmatched labels are reported back to the user in the capture preview.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

from app.common.parsing import clean_ws

_LABEL_NORM = re.compile(r"[^a-z0-9]+")

KNOWN_SECTIONS = (
    "site information", "parcel", "profile", "owner", "owner history", "current owner", "residential", "dwelling",
    "commercial", "sales", "sale history", "assessment", "values", "original current year assessment", "land",
    "tax sale information", "delinquent tax", "outstanding delinquent taxes", "delinquent tax - all years combined",
    "county tax receivable", "legal", "homestead",
)


def norm_label(text: str | None) -> str:
    return _LABEL_NORM.sub(" ", (text or "").lower()).strip()


@dataclass
class Section:
    title: str
    pairs: dict[str, str] = field(default_factory=dict)
    rows: list[dict[str, str]] = field(default_factory=list)


@dataclass
class DataletDocument:
    sections: list[Section]
    page_title: str | None = None

    def section(self, *names: str) -> list[Section]:
        wanted = {norm_label(n) for n in names}
        return [s for s in self.sections if norm_label(s.title) in wanted or any(w in norm_label(s.title) for w in wanted)]

    def find(self, *labels: str, sections: tuple[str, ...] | None = None) -> str | None:
        wanted = [norm_label(label) for label in labels]
        pool = self.section(*sections) if sections else self.sections
        for w in wanted:
            for s in pool:
                for key, value in s.pairs.items():
                    if norm_label(key) == w and value:
                        return value
        return None

    def rows(self, *section_names: str) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for s in self.section(*section_names):
            out.extend(s.rows)
        return out

    def all_labels(self) -> list[str]:
        return [f"{s.title}: {k}" for s in self.sections for k in s.pairs]


def _cell_text(cell: Tag) -> str:
    return clean_ws(cell.get_text(" ", strip=True)) or ""


def parse_datalet(content: str) -> DataletDocument:
    if "<" in content[:2000] and re.search(r"<(table|html|body|div)\b", content, re.I):
        return _parse_html(content)
    return _parse_text(content)


def parse_datalets(content: str) -> DataletDocument:
    """Parse content that may contain several concatenated HTML documents (multiple portal tabs).

    An HTML parser keeps only the first <html> document when several are concatenated, so the app's automated
    multi-tab fetch and a user pasting several saved pages must be split first, then merged. Splits on the
    app's `<!-- datalet mode=... -->` markers, or on <!doctype/<html boundaries when several are present.
    """
    parts = re.split(r"<!--\s*datalet mode=[^>]*-->", content)
    parts = [p for p in parts if p.strip()]
    if len(parts) <= 1:
        boundaries = list(re.finditer(r"(?is)<!doctype\b|<html\b", content))
        if len(boundaries) > 1:
            starts = [m.start() for m in boundaries] + [len(content)]
            parts = [content[starts[i]:starts[i + 1]] for i in range(len(boundaries))]
        else:
            parts = [content]
    merged = DataletDocument(sections=[])
    for part in parts:
        doc = parse_datalet(part)
        merged.sections.extend(doc.sections)
        merged.page_title = merged.page_title or doc.page_title
    return merged


def _parse_html(content: str) -> DataletDocument:
    soup = BeautifulSoup(content, "lxml")
    title = clean_ws(soup.title.get_text()) if soup.title else None
    sections: list[Section] = []
    current = Section(title="Page")
    sections.append(current)
    top_headings: list[str] | None = None

    # Row-centric pass over the whole document. iasWorld nests its data tables inside layout tables, so a
    # table-by-table walk that skips wrappers loses the residential/commercial/tax rows; every <tr> appears
    # exactly once in this pass regardless of nesting.
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"], recursive=False)
        if not cells:
            continue
        classes = [" ".join(c.get("class") or []) for c in cells]
        texts = [_cell_text(c) for c in cells]
        joined = " ".join(classes)
        # Section markers: iasWorld uses DataletHeader (grey bar) and DataletTitleColor ("Residential", "Commercial", ...)
        if "DataletHeader" in joined or "DataletTitleColor" in joined:
            title = next((t for t in texts if t), "Section")
            if title and not title.startswith("PARID"):
                current = Section(title=title)
                sections.append(current)
            top_headings = None
            continue
        if "DataletHeaderTop" in joined or "DataletHeaderBottom" in joined:
            continue  # the PARID / owner banner repeated on every tab
        # Grid column headings (sales, delinquent-tax and receivable grids)
        if texts and all("DataletTopHeading" in c for c in classes):
            top_headings = [t for t in texts]
            continue
        # Label/value pairs, including rows that pack several pairs side by side (label|value|label|value|...)
        side_idx = [i for i, c in enumerate(classes) if "DataletSideHeading" in c]
        if side_idx:
            for i in side_idx:
                label = texts[i].rstrip(":").strip()
                value = texts[i + 1] if i + 1 < len(texts) else ""
                if label and label != "&nbsp;":
                    current.pairs.setdefault(label, value)
            continue
        if top_headings and len(texts) == len(top_headings) and any(texts):
            current.rows.append(dict(zip(top_headings, texts, strict=True)))
            continue
        if len(cells) == 2 and texts[0] and texts[0].endswith(":"):
            current.pairs.setdefault(texts[0].rstrip(":"), texts[1])
    # Delco/Montco also render some values as "<span>Label:</span> value" outside tables
    for label_el in soup.find_all(string=re.compile(r"^\s*[A-Z][A-Za-z /#&.-]{2,40}:\s*$")):
        parent = label_el.parent
        sibling = parent.find_next_sibling() if parent else None
        if sibling is not None:
            sections[0].pairs.setdefault(str(label_el).strip().rstrip(":"), _cell_text(sibling))
    return DataletDocument(sections=[s for s in sections if s.pairs or s.rows], page_title=title)


def _parse_text(content: str) -> DataletDocument:
    sections: list[Section] = [Section(title="Page")]
    current = sections[0]
    headings: list[str] | None = None
    for raw in content.splitlines():
        line = raw.replace("\xa0", " ").rstrip()
        stripped = line.strip()
        if not stripped:
            headings = None
            continue
        if norm_label(stripped) in {norm_label(s) for s in KNOWN_SECTIONS}:
            current = Section(title=stripped)
            sections.append(current)
            headings = None
            continue
        if "\t" in line:
            cells = [clean_ws(c) or "" for c in line.split("\t")]
            if len(cells) == 2 and cells[0]:
                if cells[0].endswith(":") or not headings:
                    current.pairs.setdefault(cells[0].rstrip(":"), cells[1])
                    continue
            if headings and len(cells) == len(headings):
                current.rows.append(dict(zip(headings, cells, strict=True)))
            elif len(cells) >= 2:
                headings = cells
            continue
        m = re.match(r"^([A-Za-z][A-Za-z0-9 /#&().'-]{1,50}?):\s+(.+)$", stripped)
        if m:
            current.pairs.setdefault(m.group(1).strip(), m.group(2).strip())
            continue
        cols = [c for c in re.split(r"\s{2,}", stripped) if c]
        if len(cols) >= 2:
            if headings and len(cols) == len(headings):
                current.rows.append(dict(zip(headings, cols, strict=True)))
            elif not any(re.search(r"\d", c) for c in cols):
                headings = cols
            elif len(cols) == 2:
                current.pairs.setdefault(cols[0].rstrip(":"), cols[1])
    return DataletDocument(sections=[s for s in sections if s.pairs or s.rows])


def row_value(row: dict[str, str], *labels: str) -> str | None:
    wanted = [norm_label(label) for label in labels]
    for w in wanted:
        for key, value in row.items():
            if norm_label(key) == w:
                return value or None
    for w in wanted:
        for key, value in row.items():
            if w and w in norm_label(key):
                return value or None
    return None
