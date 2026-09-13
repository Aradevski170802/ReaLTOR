"""County-specific parcel/folio normalization.

Montgomery County PA parcel:  NN-NN-NNNNN-NN-N  (12 digits, last digit is a check digit)
Delaware County PA folio:     NN-NN-NNNNN-NN    (11 digits)

The original value is always preserved by callers; these helpers only derive search keys.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_OCR_FIXES = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "S": "5", "B": "8"})

PATTERNS = {
    "montco": (re.compile(r"^(\d{2})-(\d{2})-(\d{5})-(\d{2})-(\d)$"), (2, 2, 5, 2, 1)),
    "delco": (re.compile(r"^(\d{2})-(\d{2})-(\d{5})-(\d{2})$"), (2, 2, 5, 2)),
}


@dataclass
class ParcelParts:
    county: str
    original: str
    normalized: str
    digits: str
    valid: bool
    repaired: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        if not self.valid:
            return 0.3
        return 0.85 if self.repaired else 1.0


def _group(digits: str, sizes: tuple[int, ...]) -> str:
    parts, pos = [], 0
    for size in sizes:
        parts.append(digits[pos : pos + size])
        pos += size
    return "-".join(parts)


def normalize_parcel(county: str, raw: str | None) -> ParcelParts:
    original = (raw or "").strip()
    pattern, sizes = PATTERNS[county]
    compact = re.sub(r"\s+", "", original)
    if pattern.match(compact):
        digits = re.sub(r"\D", "", compact)
        return ParcelParts(county, original, compact, digits, True)

    notes: list[str] = []
    candidate = compact
    if re.search(r"[OoIlSB]", candidate):
        fixed = candidate.translate(_OCR_FIXES)
        if fixed != candidate:
            notes.append("Replaced OCR look-alike characters")
            candidate = fixed
    digits = re.sub(r"\D", "", candidate)
    expected = sum(sizes)
    if len(digits) == expected:
        normalized = _group(digits, sizes)
        if normalized != compact:
            notes.append("Reformatted separators")
        return ParcelParts(county, original, normalized, digits, True, repaired=True, notes=notes)

    notes.append(f"Expected {expected} digits for {county} parcel, found {len(digits)}")
    return ParcelParts(county, original, compact, digits, False, repaired=bool(notes[:-1]), notes=notes)


def montco_gis_key(parts: ParcelParts) -> str:
    """GIS_BOA_LAND.PARCEL_NUMBER form: 12 digits, no separators."""
    return parts.digits


def montco_civil_key(parts: ParcelParts) -> str:
    """Prothonotary search form: parcel number with dashes removed."""
    return parts.digits


def delco_parid(parts: ParcelParts) -> int:
    """Parcels_Public_Access.PARID is stored numerically (leading zero dropped)."""
    return int(parts.digits)


def delco_datalet_pin(parts: ParcelParts) -> str:
    return parts.digits
