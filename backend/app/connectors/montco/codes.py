"""Decode Montgomery County GIS_BOA_LAND attribute codes (see scripts/build_montco_code_crosswalk.py)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

CODES_PATH = Path(__file__).with_name("codes.json")


@lru_cache
def _codes() -> dict:
    if not CODES_PATH.exists():
        return {}
    return json.loads(CODES_PATH.read_text(encoding="utf-8"))


def decode(table: str, code: str | None) -> tuple[str | None, bool]:
    """Return (description, mapped). Unmapped codes are returned as 'code NNN (not mapped)'."""
    if code is None:
        return None, False
    code = str(code).strip()
    if not code:
        return None, False
    entry = _codes().get(table, {}).get(code)
    if entry:
        return entry["description"], True
    return f"code {code} (description not mapped)", False
