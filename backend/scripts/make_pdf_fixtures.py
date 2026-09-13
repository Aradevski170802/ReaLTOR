"""Create small, anonymized PDF fixtures from the county sale lists.

Each selected page is rebuilt from scratch: vector drawings (table rulings) are replayed and every word is
re-rendered at its original position and size. Owner names and owner mailing-address words are replaced
by deterministic pseudonyms of the same length; site locations, parcel/folio numbers, municipalities and
amounts (public sale-list facts) are kept. The geometry the parsers depend on is therefore preserved.

Usage:
    python scripts/make_pdf_fixtures.py --delco "<UpsetSaleList_8-28-2026.PDF>" --montco "<2026 Upset Sale List 9.01.26.pdf>"
"""

from __future__ import annotations

import argparse
import hashlib
import re
import string
import sys
from pathlib import Path

import pdfplumber
import pymupdf as fitz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ingestion.ocr import Word  # noqa: E402
from app.ingestion.parsers.delco_tcb import (  # noqa: E402
    ASSESS_RE,
    DESC_VOCAB,
    HEADER_RE,
    YEAR_RE,
    YEAR_X_RANGE,
    DelcoSalesReportParser,
)
from app.ingestion.pdf_layout import group_lines  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "pdf"
KEEP_TOKENS = {
    "&", "TRUSTEE", "TRUSTEES", "TR", "LLC", "INC", "CORP", "CO", "LP", "LTD", "EST", "ESTATE", "OF", "THE", "JR", "SR",
    "II", "III", "IV", "ETAL", "ET", "AL", "C/O", "PO", "BOX", "PA", "NJ", "DE", "MD", "NY", "FL", "TRUST", "AND",
    "HEIRS", "AKA", "FKA", "DBA", "RD", "ST", "AVE", "DR", "LN", "CT", "BLVD", "PIKE", "WAY", "CIR", "PL", "TER", "TERR",
    "N", "S", "E", "W", "APT", "UNIT", "STE",
}


def pseudonym(token: str) -> str:
    upper = token.upper()
    if upper in KEEP_TOKENS or not re.search(r"[A-Za-z]", token):
        return token
    digest = hashlib.sha256(("usi-fixture:" + upper).encode()).digest()
    out = [string.ascii_uppercase[digest[i % len(digest)] % 26] if ch.isalpha() else ch for i, ch in enumerate(token)]
    fake = "".join(out)
    if fake.upper() in DESC_VOCAB or fake.upper() in KEEP_TOKENS:
        fake = "Q" + fake[1:]
    return fake


def plumber_words(page) -> list[dict]:
    return page.extract_words(keep_blank_chars=False, use_text_flow=False, x_tolerance=1.5, y_tolerance=2, extra_attrs=["size"])


def word_runs(words: list[dict]) -> list[list[dict]]:
    """Group words on the same baseline whose gap is about one space wide into runs."""
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(w["top"] - lines[-1][0]["top"]) <= 1.5:
            lines[-1].append(w)
        else:
            lines.append([w])
    runs: list[list[dict]] = []
    for line in lines:
        line.sort(key=lambda w: w["x0"])
        current = [line[0]]
        for w in line[1:]:
            size = float(w.get("size") or 8)
            if 0 <= w["x0"] - current[-1]["x1"] < size * 0.42:
                current.append(w)
            else:
                runs.append(current)
                current = [w]
        runs.append(current)
    return runs


def delco_targets(page) -> set[tuple[float, float]]:
    raw = plumber_words(page)
    words = [Word(w["text"], w["x0"], w["x1"], w["top"], w["bottom"]) for w in raw]
    lines = group_lines(words, 1)
    targets: set[tuple[float, float]] = set()
    body_index = None
    for line in lines:
        if HEADER_RE.search(line.text):
            body_index = 0
            continue
        if body_index is None:
            continue
        first = line.words[0]
        if YEAR_RE.match(first.text) and YEAR_X_RANGE[0] <= first.x0 <= YEAR_X_RANGE[1]:
            body_index = None
            continue
        ws = list(line.words)
        if ASSESS_RE.search(line.text):
            cut = next((k for k in range(len(ws) - 1) if ws[k].text == "CURRENT" and ws[k + 1].text == "ASSESSMENT"), len(ws))
            ws = ws[:cut]
        role = ["location", "description", "lot"][body_index] if body_index < 3 else "extra"
        left, _right, _ = DelcoSalesReportParser._split(ws, role)
        for w in left:
            if re.fullmatch(r"[A-Z]{2}|\d{5}(-[\d`']{0,4})?|\d+[A-Z]?", w.text):
                continue
            targets.add((round(w.x0, 2), round(w.top, 2)))
        body_index += 1
    return targets


def montco_targets(page) -> set[tuple[float, float]]:
    text = page.extract_text() or ""
    if not re.search(r"\bU\d{2}-\d{4}\b", text):
        return set()
    return {(round(w["x0"], 2), round(w["top"], 2)) for w in plumber_words(page)
            if 316 <= w["x0"] < 545 and w["text"] not in ("BOA", "Owner", "Name")}


def replay_drawings(src_page: fitz.Page, dst_page: fitz.Page) -> int:
    count = 0
    for drawing in src_page.get_drawings():
        shape = dst_page.new_shape()
        for item in drawing["items"]:
            op = item[0]
            if op == "l":
                shape.draw_line(item[1], item[2])
            elif op == "re":
                shape.draw_rect(item[1])
            elif op == "qu":
                shape.draw_quad(item[1])
            elif op == "c":
                shape.draw_bezier(item[1], item[2], item[3], item[4])
        shape.finish(color=drawing.get("color"), fill=drawing.get("fill"), width=drawing.get("width") or 0.5,
                     closePath=drawing.get("closePath", False))
        shape.commit()
        count += 1
    return count


def build(src: str, pages: list[int], out: Path, target_fn, font: str) -> None:
    src_doc = fitz.open(src)
    dst = fitz.open()
    with pdfplumber.open(src) as plumber:
        for number in pages:
            ppage = plumber.pages[number - 1]
            spage = src_doc[number - 1]
            dpage = dst.new_page(width=float(ppage.width), height=float(ppage.height))
            replay_drawings(spage, dpage)
            targets = target_fn(ppage)
            # TextWriter embeds the font with a proper width table, so text extractors measure glyphs exactly.
            # Words separated by a normal space are written as one run *including* the space glyph; otherwise narrow
            # proportional-font gaps would fall under the extractor's word-merge tolerance.
            writer = fitz.TextWriter(dpage.rect)
            face = fitz.Font(font)
            for run in word_runs(plumber_words(ppage)):
                text = " ".join(pseudonym(w["text"]) if (round(w["x0"], 2), round(w["top"], 2)) in targets else w["text"] for w in run)
                size = float(run[0].get("size") or 8)
                width = float(run[-1]["x1"] - run[0]["x0"])
                fs = size
                while face.text_length(text, fontsize=fs) > width + 0.3 and fs > 3:
                    fs -= 0.05
                writer.append((float(run[0]["x0"]), float(run[0]["bottom"]) - size * 0.21), text, font=face, fontsize=fs)
            writer.write_text(dpage)
    dst.set_metadata({"title": f"Anonymized fixture derived from {Path(src).name} pages {pages}", "author": "Upset Sale Intel fixture"})
    out.parent.mkdir(parents=True, exist_ok=True)
    dst.save(out, garbage=4, deflate=True)
    dst.close()
    src_doc.close()
    print(f"wrote {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delco")
    ap.add_argument("--montco")
    args = ap.parse_args()
    if args.delco:
        # p1-2: records 1-10 (owner overflow cases); p6: multi-year record 28; p350: last record + group footer
        build(args.delco, [1, 2, 6, 350], OUT_DIR / "delco_sales_report_subset.pdf", delco_targets, "cour")
    if args.montco:
        # p1: notice (signature, sale date); p4: header + wrapped rows; p21: last table page
        build(args.montco, [1, 4, 21], OUT_DIR / "montco_upset_list_subset.pdf", montco_targets, "helv")


if __name__ == "__main__":
    main()
