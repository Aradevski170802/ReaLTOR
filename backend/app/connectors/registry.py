"""County connector registry. Register new counties here."""

from __future__ import annotations

from functools import lru_cache

from app.connectors.base import CountyConnector, SourceAdapter
from app.connectors.delco import DelcoConnector
from app.connectors.montco import MontcoConnector


@lru_cache
def connectors() -> dict[str, CountyConnector]:
    return {c.county: c for c in (MontcoConnector(), DelcoConnector())}


def get_connector(county: str) -> CountyConnector:
    try:
        return connectors()[county]
    except KeyError as exc:
        raise ValueError(f"Unsupported county '{county}'. Supported: {sorted(connectors())}") from exc


def get_source(source_key: str) -> SourceAdapter:
    county = source_key.split(".", 1)[0]
    return get_connector(county).source(source_key)


def all_sources() -> list[SourceAdapter]:
    return [s for c in connectors().values() for s in c.sources()]
