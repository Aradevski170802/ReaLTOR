"""Normalized relational domain model.

Core facts live in typed columns. JSON columns are used only for configuration, job payloads,
diagnostics and raw code maps, never as the primary store of property data. Raw source payloads
are kept as immutable evidence snapshots on disk and referenced from SourceEvidence.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UTCDateTime, utcnow

CASCADE = "all, delete-orphan"


def _fk(target: str, ondelete: str = "CASCADE", nullable: bool = False, index: bool = True):
    return mapped_column(ForeignKey(target, ondelete=ondelete), nullable=nullable, index=index)


# --------------------------------------------------------------------------------------------
# Users, projects, imports
# --------------------------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="analyst")
    token_hash: Mapped[str | None] = mapped_column(String(128), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    county: Mapped[str] = mapped_column(String(20), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    sale_date: Mapped[date | None] = mapped_column(Date)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    imports: Mapped[list[ImportedSaleList]] = relationship(back_populates="project", cascade=CASCADE, passive_deletes=True)
    properties: Mapped[list[Property]] = relationship(back_populates="project", cascade=CASCADE, passive_deletes=True)


class ImportedSaleList(Base):
    __tablename__ = "imported_sale_lists"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = _fk("projects.id")
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    stored_path: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(10))  # pdf | csv | xlsx
    parser_key: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="uploaded")
    page_count: Mapped[int | None] = mapped_column(Integer)
    detection: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    column_mapping: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    committed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    project: Mapped[Project] = relationship(back_populates="imports")
    rows: Mapped[list[ExtractedRow]] = relationship(
        back_populates="sale_list", cascade=CASCADE, passive_deletes=True, order_by="ExtractedRow.row_index"
    )


class ExtractedRow(Base):
    __tablename__ = "extracted_rows"
    __table_args__ = (UniqueConstraint("import_id", "row_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[int] = _fk("imported_sale_lists.id")
    row_index: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer)
    raw_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reasons: Mapped[list[str] | None] = mapped_column(JSON)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    sale_list: Mapped[ImportedSaleList] = relationship(back_populates="rows")
    values: Mapped[list[ExtractedValue]] = relationship(back_populates="row", cascade=CASCADE, passive_deletes=True)


class ExtractedValue(Base):
    __tablename__ = "extracted_values"
    __table_args__ = (UniqueConstraint("row_id", "field"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    row_id: Mapped[int] = _fk("extracted_rows.id")
    field: Mapped[str] = mapped_column(String(64))
    raw_value: Mapped[str | None] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    corrected: Mapped[bool] = mapped_column(Boolean, default=False)
    corrected_by: Mapped[str | None] = mapped_column(String(255))
    corrected_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    row: Mapped[ExtractedRow] = relationship(back_populates="values")


# --------------------------------------------------------------------------------------------
# Property and identifiers
# --------------------------------------------------------------------------------------------


class Property(Base):
    __tablename__ = "properties"
    __table_args__ = (UniqueConstraint("project_id", "parcel_normalized"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = _fk("projects.id")
    import_id: Mapped[int | None] = _fk("imported_sale_lists.id", ondelete="SET NULL", nullable=True)
    extracted_row_id: Mapped[int | None] = _fk("extracted_rows.id", ondelete="SET NULL", nullable=True)
    county: Mapped[str] = mapped_column(String(20))
    sequence: Mapped[int | None] = mapped_column(Integer)
    sale_number: Mapped[str | None] = mapped_column(String(40))
    parcel_original: Mapped[str] = mapped_column(String(64))
    parcel_normalized: Mapped[str] = mapped_column(String(64), index=True)
    parcel_search_key: Mapped[str | None] = mapped_column(String(64))
    owner_name: Mapped[str | None] = mapped_column(Text)
    mailing_address: Mapped[str | None] = mapped_column(Text)
    property_address: Mapped[str | None] = mapped_column(Text)
    municipality: Mapped[str | None] = mapped_column(String(120))
    sale_date: Mapped[date | None] = mapped_column(Date)
    listed_amount: Mapped[float | None] = mapped_column(Float)
    listed_assessment: Mapped[float | None] = mapped_column(Float)
    listed_description: Mapped[str | None] = mapped_column(String(255))
    listed_lot_size: Mapped[str | None] = mapped_column(String(255))
    source_page: Mapped[int | None] = mapped_column(Integer)
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    is_pilot: Mapped[bool] = mapped_column(Boolean, default=False)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    decision_status: Mapped[str | None] = mapped_column(String(40))
    decision_summary: Mapped[str | None] = mapped_column(Text)
    decision_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    notes: Mapped[str | None] = mapped_column(Text)
    last_enriched_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    project: Mapped[Project] = relationship(back_populates="properties")
    identifiers: Mapped[list[ParcelIdentifier]] = relationship(cascade=CASCADE, passive_deletes=True)
    tax_lines: Mapped[list[SaleListTaxLine]] = relationship(
        cascade=CASCADE, passive_deletes=True, order_by="SaleListTaxLine.tax_year"
    )
    overrides: Mapped[list[UserOverride]] = relationship(cascade=CASCADE, passive_deletes=True)


class ParcelIdentifier(Base):
    __tablename__ = "parcel_identifiers"
    __table_args__ = (UniqueConstraint("property_id", "kind"), Index("ix_parcel_identifier_kind_value", "kind", "value"))

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = _fk("properties.id")
    kind: Mapped[str] = mapped_column(String(40))
    value: Mapped[str] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(120))


class SaleListTaxLine(Base):
    """Per-year delinquent line printed on the county sale list (e.g. Delco CTY/SCH/TWN rows)."""

    __tablename__ = "sale_list_tax_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = _fk("properties.id")
    tax_year: Mapped[int] = mapped_column(Integer)
    cty_assessment: Mapped[float | None] = mapped_column(Float)
    sch_assessment: Mapped[float | None] = mapped_column(Float)
    twn_assessment: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    raw_line: Mapped[str | None] = mapped_column(Text)


class UserOverride(Base):
    __tablename__ = "user_overrides"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = _fk("properties.id")
    field: Mapped[str] = mapped_column(String(64))
    original_value: Mapped[str | None] = mapped_column(Text)
    override_value: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    superseded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


# --------------------------------------------------------------------------------------------
# Sources, evidence and provenance
# --------------------------------------------------------------------------------------------


class SourceEvidence(Base):
    __tablename__ = "source_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = _fk("projects.id", nullable=True)
    property_id: Mapped[int | None] = _fk("properties.id", nullable=True)
    source_key: Mapped[str] = mapped_column(String(80), index=True)
    source_name: Mapped[str] = mapped_column(String(255))
    access_method: Mapped[str] = mapped_column(String(40))
    url: Mapped[str | None] = mapped_column(Text)
    request_summary: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    http_status: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(120))
    sha256: Mapped[str | None] = mapped_column(String(64))
    snapshot_path: Mapped[str | None] = mapped_column(Text)
    snapshot_bytes: Mapped[int | None] = mapped_column(Integer)
    entry_method: Mapped[str] = mapped_column(String(30), default="automated")
    captured_by: Mapped[str | None] = mapped_column(String(255))
    parser_version: Mapped[str | None] = mapped_column(String(40))
    notes: Mapped[str | None] = mapped_column(Text)


class SourceLookup(Base):
    __tablename__ = "source_lookups"
    __table_args__ = (Index("ix_source_lookup_current", "property_id", "source_key", "is_current"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = _fk("properties.id")
    source_key: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40))
    message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    evidence_id: Mapped[int | None] = _fk("source_evidence.id", ondelete="SET NULL", nullable=True, index=False)
    job_id: Mapped[int | None] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)


class FieldProvenance(Base):
    __tablename__ = "field_provenance"
    __table_args__ = (Index("ix_field_provenance_current", "property_id", "field", "is_current"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = _fk("properties.id")
    field: Mapped[str] = mapped_column(String(64))
    value: Mapped[str | None] = mapped_column(Text)
    raw_value: Mapped[str | None] = mapped_column(Text)
    source_key: Mapped[str] = mapped_column(String(80))
    source_name: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(Text)
    evidence_id: Mapped[int | None] = _fk("source_evidence.id", ondelete="SET NULL", nullable=True, index=False)
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    confidence: Mapped[float | None] = mapped_column(Float)
    quality: Mapped[str] = mapped_column(String(20), default="verified")
    notes: Mapped[str | None] = mapped_column(Text)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)


class SourcePolicy(Base):
    """Operator decisions per source: enable/disable and terms-of-use acknowledgement."""

    __tablename__ = "source_policies"

    source_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    terms_acknowledged_by: Mapped[str | None] = mapped_column(String(255))
    terms_acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class CaptureTask(Base):
    """A user-assisted retrieval step (disclaimer, login, human verification, or manual lookup)."""

    __tablename__ = "capture_tasks"
    __table_args__ = (Index("ix_capture_task_open", "project_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = _fk("projects.id")
    property_id: Mapped[int] = _fk("properties.id")
    source_key: Mapped[str] = mapped_column(String(80))
    url: Mapped[str | None] = mapped_column(Text)
    search_hint: Mapped[str | None] = mapped_column(Text)
    instructions: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")
    reason: Mapped[str] = mapped_column(String(40), default="enrichment")
    last_error: Mapped[str | None] = mapped_column(Text)
    evidence_id: Mapped[int | None] = _fk("source_evidence.id", ondelete="SET NULL", nullable=True, index=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_by: Mapped[str | None] = mapped_column(String(255))


# --------------------------------------------------------------------------------------------
# Assessment and tax
# --------------------------------------------------------------------------------------------


class AssessmentRecord(Base):
    __tablename__ = "assessment_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = _fk("properties.id")
    source_key: Mapped[str] = mapped_column(String(80))
    evidence_id: Mapped[int | None] = _fk("source_evidence.id", ondelete="SET NULL", nullable=True, index=False)
    entry_method: Mapped[str] = mapped_column(String(30), default="automated")
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)

    land_use_code: Mapped[str | None] = mapped_column(String(20))
    land_use_description: Mapped[str | None] = mapped_column(String(255))
    property_class: Mapped[str | None] = mapped_column(String(60))
    property_type: Mapped[str | None] = mapped_column(String(120))
    category: Mapped[str | None] = mapped_column(String(40))
    school_district: Mapped[str | None] = mapped_column(String(255))
    site_address: Mapped[str | None] = mapped_column(Text)
    legal_description: Mapped[str | None] = mapped_column(Text)
    municipality: Mapped[str | None] = mapped_column(String(120))
    owner_name: Mapped[str | None] = mapped_column(Text)

    assessed_value: Mapped[float | None] = mapped_column(Float)
    assessed_land: Mapped[float | None] = mapped_column(Float)
    assessed_building: Mapped[float | None] = mapped_column(Float)

    lot_size_text: Mapped[str | None] = mapped_column(String(120))
    acres: Mapped[float | None] = mapped_column(Float)
    lot_sqft: Mapped[float | None] = mapped_column(Float)

    building_style: Mapped[str | None] = mapped_column(String(120))
    year_built: Mapped[int | None] = mapped_column(Integer)
    exterior_wall: Mapped[str | None] = mapped_column(String(120))
    living_area_sqft: Mapped[float | None] = mapped_column(Float)
    base_area_sqft: Mapped[float | None] = mapped_column(Float)
    total_rooms: Mapped[int | None] = mapped_column(Integer)
    bedrooms: Mapped[int | None] = mapped_column(Integer)
    full_baths: Mapped[int | None] = mapped_column(Integer)
    half_baths: Mapped[int | None] = mapped_column(Integer)

    last_sale_date: Mapped[date | None] = mapped_column(Date)
    last_sale_price: Mapped[float | None] = mapped_column(Float)

    gross_building_area: Mapped[float | None] = mapped_column(Float)
    total_living_units: Mapped[int | None] = mapped_column(Integer)
    structure_description: Mapped[str | None] = mapped_column(String(255))
    improvement_name: Mapped[str | None] = mapped_column(String(255))
    commercial_living_area: Mapped[float | None] = mapped_column(Float)
    commercial_units: Mapped[int | None] = mapped_column(Integer)
    commercial_year_built: Mapped[int | None] = mapped_column(Integer)

    detail_link: Mapped[str | None] = mapped_column(Text)
    raw_codes: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    sales: Mapped[list[AssessmentSale]] = relationship(cascade=CASCADE, passive_deletes=True)


class AssessmentSale(Base):
    __tablename__ = "assessment_sales"

    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = _fk("assessment_records.id")
    sale_date: Mapped[date | None] = mapped_column(Date)
    sale_price: Mapped[float | None] = mapped_column(Float)
    grantor: Mapped[str | None] = mapped_column(Text)
    grantee: Mapped[str | None] = mapped_column(Text)
    book_page: Mapped[str | None] = mapped_column(String(60))


class TaxClaimRecord(Base):
    __tablename__ = "tax_claim_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = _fk("properties.id")
    source_key: Mapped[str] = mapped_column(String(80))
    evidence_id: Mapped[int | None] = _fk("source_evidence.id", ondelete="SET NULL", nullable=True, index=False)
    entry_method: Mapped[str] = mapped_column(String(30), default="automated")
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)

    tax_sale_status: Mapped[str | None] = mapped_column(String(255))
    status_category: Mapped[str] = mapped_column(String(20), default="unknown")
    delinquent_total: Mapped[float | None] = mapped_column(Float)
    district: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(255))
    assessed_value: Mapped[float | None] = mapped_column(Float)
    deed_book_page: Mapped[str | None] = mapped_column(String(60))
    as_of_text: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)

    years: Mapped[list[TaxClaimYear]] = relationship(
        cascade=CASCADE, passive_deletes=True, order_by="TaxClaimYear.year"
    )


class TaxClaimYear(Base):
    __tablename__ = "tax_claim_years"

    id: Mapped[int] = mapped_column(primary_key=True)
    tax_claim_id: Mapped[int] = _fk("tax_claim_records.id")
    year: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20), default="claim")  # claim | receivable | delinquent
    face: Mapped[float | None] = mapped_column(Float)
    penalty: Mapped[float | None] = mapped_column(Float)
    interest: Mapped[float | None] = mapped_column(Float)
    total: Mapped[float | None] = mapped_column(Float)
    paid: Mapped[float | None] = mapped_column(Float)
    balance: Mapped[float | None] = mapped_column(Float)


class TaxStatusHistory(Base):
    __tablename__ = "tax_status_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = _fk("properties.id")
    source_key: Mapped[str] = mapped_column(String(80))
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    lookup_status: Mapped[str] = mapped_column(String(40))
    status_text: Mapped[str | None] = mapped_column(String(255))
    status_category: Mapped[str | None] = mapped_column(String(20))
    balance_2024: Mapped[float | None] = mapped_column(Float)
    delinquent_total: Mapped[float | None] = mapped_column(Float)
    changed: Mapped[bool] = mapped_column(Boolean, default=False)
    refresh_run_id: Mapped[int | None] = _fk("refresh_runs.id", ondelete="SET NULL", nullable=True, index=False)


# --------------------------------------------------------------------------------------------
# Recorder and civil
# --------------------------------------------------------------------------------------------


class RecorderDocument(Base):
    __tablename__ = "recorder_documents"
    __table_args__ = (UniqueConstraint("property_id", "source_key", "doc_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = _fk("properties.id")
    source_key: Mapped[str] = mapped_column(String(80))
    evidence_id: Mapped[int | None] = _fk("source_evidence.id", ondelete="SET NULL", nullable=True, index=False)
    entry_method: Mapped[str] = mapped_column(String(30), default="import")
    doc_key: Mapped[str] = mapped_column(String(160))
    instrument_number: Mapped[str | None] = mapped_column(String(80))
    book: Mapped[str | None] = mapped_column(String(20))
    page: Mapped[str | None] = mapped_column(String(20))
    doc_type_raw: Mapped[str | None] = mapped_column(String(255))
    doc_category: Mapped[str] = mapped_column(String(30))
    recorded_date: Mapped[date | None] = mapped_column(Date)
    grantors: Mapped[str | None] = mapped_column(Text)
    grantees: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[float | None] = mapped_column(Float)
    related_reference: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class MortgageRecord(Base):
    __tablename__ = "mortgage_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = _fk("properties.id")
    document_id: Mapped[int] = mapped_column(ForeignKey("recorder_documents.id", ondelete="CASCADE"), unique=True)
    recorded_date: Mapped[date | None] = mapped_column(Date)
    original_amount: Mapped[float | None] = mapped_column(Float)
    lender: Mapped[str | None] = mapped_column(Text)
    borrower: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(60))
    status_reason: Mapped[str | None] = mapped_column(Text)
    evaluated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    user_status: Mapped[str | None] = mapped_column(String(60))
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    review_note: Mapped[str | None] = mapped_column(Text)

    document: Mapped[RecorderDocument] = relationship(foreign_keys=[document_id])
    matches: Mapped[list[SatisfactionMatch]] = relationship(cascade=CASCADE, passive_deletes=True)

    @property
    def effective_status(self) -> str:
        return self.user_status or self.status


class SatisfactionMatch(Base):
    __tablename__ = "satisfaction_matches"
    __table_args__ = (UniqueConstraint("mortgage_id", "satisfaction_document_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    mortgage_id: Mapped[int] = _fk("mortgage_records.id")
    satisfaction_document_id: Mapped[int] = _fk("recorder_documents.id")
    score: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(60))
    reasons: Mapped[list[str] | None] = mapped_column(JSON)
    decision: Mapped[str] = mapped_column(String(20))
    decided_by: Mapped[str | None] = mapped_column(String(255))
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    satisfaction: Mapped[RecorderDocument] = relationship(foreign_keys=[satisfaction_document_id])


class CivilLienCase(Base):
    __tablename__ = "civil_lien_cases"
    __table_args__ = (UniqueConstraint("property_id", "source_key", "case_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = _fk("properties.id")
    source_key: Mapped[str] = mapped_column(String(80))
    evidence_id: Mapped[int | None] = _fk("source_evidence.id", ondelete="SET NULL", nullable=True, index=False)
    entry_method: Mapped[str] = mapped_column(String(30), default="import")
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    search_term: Mapped[str | None] = mapped_column(Text)
    case_number: Mapped[str] = mapped_column(String(80))
    status: Mapped[str | None] = mapped_column(String(120))
    classification: Mapped[str | None] = mapped_column(String(255))
    case_type: Mapped[str | None] = mapped_column(String(255))
    parties: Mapped[str | None] = mapped_column(Text)
    filing_date: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[float | None] = mapped_column(Float)
    source_url: Mapped[str | None] = mapped_column(Text)
    is_relevant: Mapped[bool] = mapped_column(Boolean, default=True)
    relevance_reason: Mapped[str | None] = mapped_column(Text)


# --------------------------------------------------------------------------------------------
# Valuation and images
# --------------------------------------------------------------------------------------------


class ValuationRecord(Base):
    __tablename__ = "valuation_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = _fk("properties.id")
    provider_key: Mapped[str] = mapped_column(String(60))
    provider_name: Mapped[str] = mapped_column(String(120))
    product: Mapped[str | None] = mapped_column(String(120))
    coverage_status: Mapped[str] = mapped_column(String(40))
    provider_property_id: Mapped[str | None] = mapped_column(String(120))
    estimate_type: Mapped[str | None] = mapped_column(String(40))
    low: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    point: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    estimate_date: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str | None] = mapped_column(Text)
    response_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    evidence_id: Mapped[int | None] = _fk("source_evidence.id", ondelete="SET NULL", nullable=True, index=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    confidence_label: Mapped[str | None] = mapped_column(String(40))
    match_score: Mapped[float | None] = mapped_column(Float)
    entry_method: Mapped[str] = mapped_column(String(30), default="automated")
    is_sandbox: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)


class ValuationSelection(Base):
    __tablename__ = "valuation_selections"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = _fk("properties.id")
    valuation_id: Mapped[int | None] = _fk("valuation_records.id", ondelete="SET NULL", nullable=True, index=False)
    value: Mapped[float | None] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(40))
    provider_key: Mapped[str | None] = mapped_column(String(60))
    estimate_type: Mapped[str | None] = mapped_column(String(40))
    explanation: Mapped[str] = mapped_column(Text)
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)


class PropertyImage(Base):
    __tablename__ = "property_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = _fk("properties.id")
    source_kind: Mapped[str] = mapped_column(String(40))  # provider | street_view | government | user_upload
    url: Mapped[str | None] = mapped_column(Text)
    stored_path: Mapped[str | None] = mapped_column(Text)
    provider_key: Mapped[str | None] = mapped_column(String(60))
    license_note: Mapped[str | None] = mapped_column(Text)
    attribution: Mapped[str | None] = mapped_column(Text)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ProviderConfig(Base):
    __tablename__ = "provider_configs"

    provider_key: Mapped[str] = mapped_column(String(60), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    settings: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class EncryptedSecret(Base):
    __tablename__ = "encrypted_secrets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    ciphertext: Mapped[str] = mapped_column(Text)
    hint: Mapped[str | None] = mapped_column(String(16))
    updated_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


# --------------------------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------------------------


class RuleSet(Base):
    __tablename__ = "rule_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(String(120))
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    change_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class RuleEvaluation(Base):
    __tablename__ = "rule_evaluations"
    __table_args__ = (Index("ix_rule_evaluation_current", "property_id", "is_current"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = _fk("properties.id")
    ruleset_id: Mapped[int | None] = _fk("rule_sets.id", ondelete="SET NULL", nullable=True, index=False)
    rule_key: Mapped[str] = mapped_column(String(60))
    outcome: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    inputs: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    evaluated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)


# --------------------------------------------------------------------------------------------
# Jobs, refresh history, audit, views, exports, templates
# --------------------------------------------------------------------------------------------


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "run_after"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    project_id: Mapped[int | None] = _fk("projects.id", nullable=True)
    property_id: Mapped[int | None] = mapped_column(Integer, index=True)
    parent_id: Mapped[int | None] = _fk("jobs.id", nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4)
    run_after: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    locked_by: Mapped[str | None] = mapped_column(String(80))
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    progress_done: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class RefreshRun(Base):
    __tablename__ = "refresh_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int | None] = _fk("jobs.id", ondelete="SET NULL", nullable=True)
    project_id: Mapped[int] = _fk("projects.id")
    kind: Mapped[str] = mapped_column(String(40))
    trigger: Mapped[str] = mapped_column(String(20), default="manual")  # manual | scheduled
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    total: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    changed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    user_action_needed: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    """Append-only, hash-chained audit trail. Updates and deletes are rejected at the ORM layer."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    actor: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(80))
    project_id: Mapped[int | None] = mapped_column(Integer, index=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


@event.listens_for(AuditLog, "before_update")
def _audit_no_update(mapper, connection, target):  # pragma: no cover - defensive
    raise PermissionError("audit_log is append-only")


@event.listens_for(AuditLog, "before_delete")
def _audit_no_delete(mapper, connection, target):  # pragma: no cover - defensive
    raise PermissionError("audit_log is append-only")


class SavedView(Base):
    __tablename__ = "saved_views"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = _fk("projects.id", nullable=True)
    owner: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(120))
    state: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ExportRecord(Base):
    __tablename__ = "export_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = _fk("projects.id")
    job_id: Mapped[int | None] = _fk("jobs.id", ondelete="SET NULL", nullable=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class WorkbookTemplate(Base):
    __tablename__ = "workbook_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    county: Mapped[str | None] = mapped_column(String(20))
    filename: Mapped[str | None] = mapped_column(String(255))
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
