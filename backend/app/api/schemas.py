from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    county: Literal["montco", "delco"]
    description: str | None = None
    sale_date: date | None = None
    is_demo: bool = False


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    sale_date: date | None = None
    archived: bool | None = None


class ProjectOut(ORM):
    id: int
    name: str
    county: str
    description: str | None
    sale_date: date | None
    is_demo: bool
    archived: bool
    created_by: str | None
    created_at: datetime


class ImportOut(ORM):
    id: int
    project_id: int
    filename: str
    kind: str
    status: str
    parser_key: str | None
    page_count: int | None
    stats: dict[str, Any] | None
    detection: dict[str, Any] | None
    column_mapping: dict[str, Any] | None
    error: str | None
    created_at: datetime
    committed_at: datetime | None


class ParseIn(BaseModel):
    parser_key: str | None = None
    confirm_overwrite: bool = False
    column_mapping: dict[str, str] | None = None


class CorrectionIn(BaseModel):
    field: str = Field(min_length=1, max_length=64)
    value: str | None = None


class RowPatch(BaseModel):
    excluded: bool | None = None
    reviewed: bool | None = None


class CommitIn(BaseModel):
    confirm_overwrite: bool = False
    row_ids: list[int] | None = None


class PilotIn(BaseModel):
    property_ids: list[int] | None = None
    count: int | None = Field(default=None, ge=1, le=50)


class EnrichIn(BaseModel):
    selection: Literal["pilot", "all"] | list[int] = "pilot"
    sources: list[str] | None = None
    force: bool = False


class RerunIn(BaseModel):
    sources: list[str] | None = None
    force: bool = True


class CaptureIn(BaseModel):
    source_key: str
    content: str = ""
    no_results: bool = False
    preview: bool = False
    url: str | None = None


class OverrideIn(BaseModel):
    field: str = Field(min_length=1, max_length=64)
    value: str | None = None
    reason: str = Field(min_length=3, max_length=2000)


class OverrideRevokeIn(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class PropertyPatch(BaseModel):
    notes: str | None = None
    excluded: bool | None = None


class ManualValuationIn(BaseModel):
    site: str
    value: float = Field(gt=0)
    observed_on: date | None = None
    url: str | None = None
    note: str | None = None


class MatchReviewIn(BaseModel):
    satisfaction_document_id: int
    confirm: bool
    note: str | None = None


class MortgageStatusIn(BaseModel):
    status: str | None = None
    note: str = Field(min_length=3)


class RulesUpdateIn(BaseModel):
    patch: dict[str, Any]
    reason: str = Field(min_length=3)


class SecretIn(BaseModel):
    value: str = Field(min_length=4, max_length=4000)


class ProviderConfigIn(BaseModel):
    enabled: bool | None = None
    settings: dict[str, Any] | None = None


class ProviderTestIn(BaseModel):
    street: str
    city: str
    zip_code: str | None = None
    category: str | None = "single_family"


class SourcePolicyIn(BaseModel):
    enabled: bool | None = None
    acknowledge_terms: bool | None = None
    note: str | None = None


class SavedViewIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    project_id: int | None = None
    state: dict[str, Any]


class ExportIn(BaseModel):
    property_ids: list[int] | None = None


class RefreshIn(BaseModel):
    property_ids: list[int] | None = None
