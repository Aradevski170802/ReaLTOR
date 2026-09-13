"""Property helpers: parcel parts, identifiers, overrides, current-record supersession."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.common.parcels import ParcelParts, normalize_parcel
from app.connectors.base import PropertyRef
from app.models import ParcelIdentifier, Property, UserOverride


def parcel_parts(prop: Property) -> ParcelParts:
    return normalize_parcel(prop.county, prop.parcel_normalized)


def active_overrides(prop: Property) -> dict[str, UserOverride]:
    out: dict[str, UserOverride] = {}
    for ov in sorted(prop.overrides, key=lambda o: o.created_at):
        if ov.active:
            out[ov.field] = ov
    return out


def effective_attr(prop: Property, field: str) -> str | None:
    ov = active_overrides(prop).get(field)
    if ov is not None:
        return ov.override_value
    value = getattr(prop, field, None)
    return None if value is None else str(value)


def property_ref(prop: Property) -> PropertyRef:
    return PropertyRef(
        id=prop.id,
        county=prop.county,
        parcel=parcel_parts(prop),
        owner_name=effective_attr(prop, "owner_name"),
        property_address=effective_attr(prop, "property_address"),
        municipality=effective_attr(prop, "municipality"),
        identifiers={pi.kind: pi.value for pi in prop.identifiers},
    )


def set_identifier(session: Session, prop: Property, kind: str, value: str | None, source: str) -> None:
    if not value:
        return
    existing = next((pi for pi in prop.identifiers if pi.kind == kind), None)
    if existing is None:
        prop.identifiers.append(ParcelIdentifier(kind=kind, value=value[:64], source=source))
    elif existing.value != value:
        existing.value = value[:64]
        existing.source = source


def supersede(session: Session, model, property_id: int, source_key: str | None = None, **filters) -> None:
    stmt = update(model).where(model.property_id == property_id, model.is_current.is_(True))
    if source_key is not None:
        stmt = stmt.where(model.source_key == source_key)
    for name, value in filters.items():
        stmt = stmt.where(getattr(model, name) == value)
    session.execute(stmt.values(is_current=False))


def get_property(session: Session, property_id: int) -> Property | None:
    return session.scalar(select(Property).where(Property.id == property_id))
