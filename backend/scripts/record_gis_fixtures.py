"""Record official county GIS responses for the demo/test parcels (the first 8 rows of each anonymized PDF fixture).

Both endpoints are public ArcGIS REST services published by the counties for programmatic use. Owner name fields are
anonymized before saving. Re-run only when fixtures need refreshing:

    python scripts/record_gis_fixtures.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.connectors.delco.gis import FIELDS as DELCO_FIELDS  # noqa: E402
from app.connectors.delco.gis import QUERY as DELCO_QUERY  # noqa: E402
from app.connectors.montco.gis import FIELDS as MONTCO_FIELDS  # noqa: E402
from app.connectors.montco.gis import QUERY as MONTCO_QUERY  # noqa: E402
from app.ingestion.parsers import parse_pdf  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
UA = {"User-Agent": "UpsetSaleIntel fixture recorder (official GIS REST, 1 request/second)"}


def main(count: int = 8) -> None:
    out = FIXTURES / "http"
    out.mkdir(parents=True, exist_ok=True)
    montco = parse_pdf(FIXTURES / "pdf" / "montco_upset_list_subset.pdf", "montco").records[:count]
    delco = parse_pdf(FIXTURES / "pdf" / "delco_sales_report_subset.pdf", "delco").records[:count]
    with httpx.Client(timeout=60, headers=UA) as client:
        for record in montco:
            digits = "".join(ch for ch in record.value("parcel") if ch.isdigit())
            resp = client.get(MONTCO_QUERY, params={"where": f"PARCEL_NUMBER LIKE '{digits}%'", "outFields": ",".join(MONTCO_FIELDS),
                                                    "returnGeometry": "false", "f": "json"})
            resp.raise_for_status()
            data = resp.json()
            for feature in data.get("features", []):
                feature["attributes"]["OWN1"] = "SAMPLE OWNER"
                feature["attributes"]["OWN2"] = ""
            (out / f"montco_gis_{digits}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
            print("montco", record.value("parcel"), len(data.get("features", [])))
            time.sleep(1.0)
        for record in delco:
            parid = int("".join(ch for ch in record.value("parcel") if ch.isdigit()))
            resp = client.get(DELCO_QUERY, params={"where": f"PARID={parid}", "outFields": ",".join(DELCO_FIELDS),
                                                   "returnGeometry": "false", "f": "json"})
            resp.raise_for_status()
            data = resp.json()
            (out / f"delco_gis_{parid}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
            print("delco", record.value("parcel"), len(data.get("features", [])))
            time.sleep(1.0)


if __name__ == "__main__":
    main()
