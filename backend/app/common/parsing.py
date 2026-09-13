"""Tolerant scalar parsing helpers. They return None instead of guessing."""

from __future__ import annotations

import re
from datetime import date, datetime

_MONEY_RE = re.compile(r"^\(?-?\$?\s*-?[\d,]*\.?\d+\)?$")
_WS_RE = re.compile(r"\s+")

_DATE_FORMATS = (
    "%m/%d/%Y",
    "%m/%d/%y",
    "%m-%d-%Y",
    "%m-%d-%y",
    "%Y-%m-%d",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%m/%d/%Y %I:%M:%S %p",
    "%Y-%m-%dT%H:%M:%S",
)


def clean_ws(value: object | None) -> str | None:
    if value is None:
        return None
    text = _WS_RE.sub(" ", str(value).replace("\xa0", " ")).strip()
    return text or None


def parse_money(value: object | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    text = clean_ws(value)
    if not text:
        return None
    text = text.replace("USD", "").strip()
    if not _MONEY_RE.match(text.replace(" ", "")):
        return None
    negative = text.startswith("(") or "-" in text
    digits = re.sub(r"[^\d.]", "", text)
    if not digits or digits == ".":
        return None
    try:
        amount = float(digits)
    except ValueError:
        return None
    return -amount if negative else amount


def parse_int(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    text = clean_ws(value)
    if not text:
        return None
    text = text.replace(",", "")
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.0+", text):
        return int(float(text))
    return None


def parse_float(value: object | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    text = clean_ws(value)
    if not text:
        return None
    text = text.replace(",", "")
    if re.fullmatch(r"-?\d*\.?\d+", text):
        return float(text)
    return None


def parse_date(value: object | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = clean_ws(value)
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        # Two-digit years: strptime maps 00-68 -> 2000s which is what county reports mean.
        return parsed.date()
    return None


def format_money(value: float | None) -> str:
    if value is None:
        return ""
    return f"${value:,.2f}"


def normalize_name(value: str | None) -> str:
    """Uppercase, punctuation-light form used for fuzzy party comparisons."""
    if not value:
        return ""
    text = re.sub(r"[^A-Z0-9& ]", " ", value.upper())
    return _WS_RE.sub(" ", text).strip()
