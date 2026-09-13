"""Build the Montgomery County code -> description crosswalk.

The county's official GIS_BOA_LAND table (ArcGIS REST) publishes assessment attributes as codes
(LAND_USE=1132, STYLE=04, EXTWALL=2, SCH_DIST=840, STRUCTURE=105). The public property-record
portal shows descriptions ("R - DUPLEX", "ROW", "BRICK", "UPPER MERION AREA", ...).

This script pairs the two using a reviewed reference workbook in which descriptions were recorded
per parcel, then writes app/connectors/montco/codes.json with support counts and conflicts so
unmapped or ambiguous codes are surfaced rather than guessed.

Usage:
    python scripts/build_montco_code_crosswalk.py "path/to/Montgomery County Upset Sale 2026 ... .xlsx"
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import httpx
import openpyxl

ENDPOINT = "https://gis.montcopa.org/arcgis/rest/services/Parcels/GIS_BOA_LAND/FeatureServer/0/query"
OUT = Path(__file__).resolve().parent.parent / "app" / "connectors" / "montco" / "codes.json"

# workbook header -> GIS field
PAIRS = {
    "land_use": ("Land Use Description", "LAND_USE"),
    "school_district": ("School District", "SCH_DIST"),
    "style": ("Building Style (R-)", "STYLE"),
    "exterior_wall": ("Exterior Wall Material (R-)", "EXTWALL"),
    "structure": ("Structure (C-)", "STRUCTURE"),
}

STATIC_CLASS = {
    "R": "Residential",
    "C": "Commercial",
    "A": "Apartment",
    "I": "Industrial",
    "F": "Farm",
    "E": "Exempt",
    "U": "Utility",
    "M": "Mixed",
}


def main(workbook_path: str) -> None:
    wb = openpyxl.load_workbook(workbook_path, read_only=True, data_only=True)
    ws = wb["Updated Sale List"]
    rows = list(ws.iter_rows(values_only=True))
    header = [str(h).strip() if h else "" for h in rows[0]]
    col = {name: header.index(name) for name, _ in PAIRS.values()}
    parcel_idx = header.index("Parcel")

    wanted: dict[str, dict[str, str]] = {}
    for row in rows[1:]:
        parcel = row[parcel_idx]
        if not parcel:
            continue
        digits = re.sub(r"\D", "", str(parcel))
        if len(digits) != 12:
            continue
        wanted[digits] = {key: (str(row[col[wb_col]]).strip() if row[col[wb_col]] else "") for key, (wb_col, _) in PAIRS.items()}

    observed: dict[str, dict[str, Counter]] = {key: defaultdict(Counter) for key in PAIRS}
    fields = ",".join(["PARCEL_NUMBER", "CLASS"] + [gis for _, gis in PAIRS.values()])
    keys = list(wanted)
    with httpx.Client(timeout=60, headers={"User-Agent": "UpsetSaleIntel code-crosswalk builder"}) as client:
        for start in range(0, len(keys), 25):
            batch = keys[start : start + 25]
            where = " OR ".join(f"PARCEL_NUMBER LIKE '{k}%'" for k in batch)
            resp = client.get(ENDPOINT, params={"where": where, "outFields": fields, "returnGeometry": "false", "f": "json"})
            resp.raise_for_status()
            for feature in resp.json().get("features", []):
                attrs = feature["attributes"]
                digits = (attrs.get("PARCEL_NUMBER") or "").strip()
                desc = wanted.get(digits)
                if not desc:
                    continue
                for key, (_, gis) in PAIRS.items():
                    code = (attrs.get(gis) or "").strip()
                    text = desc[key]
                    if code and text:
                        observed[key][code][text] += 1
            time.sleep(0.5)

    out: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "method": "Paired GIS_BOA_LAND codes with descriptions recorded per parcel in a reviewed reference workbook",
        "source_gis": ENDPOINT,
        "reference_parcels": len(wanted),
        "class": {code: {"description": text, "support": None} for code, text in STATIC_CLASS.items()},
    }
    for key, codes in observed.items():
        mapping = {}
        for code, counter in sorted(codes.items()):
            (best, support), *rest = counter.most_common()
            mapping[code] = {
                "description": best,
                "support": support,
                "conflicts": {text: n for text, n in rest} or None,
            }
        out[key] = mapping

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, sort_keys=False), encoding="utf-8")
    print(f"Wrote {OUT} ({', '.join(f'{k}={len(v)}' for k, v in out.items() if isinstance(v, dict))})")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
