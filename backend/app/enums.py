"""Controlled vocabularies shared by the backend, the API, the grid and the XLSX export."""

from __future__ import annotations

from enum import StrEnum


class County(StrEnum):
    MONTCO = "montco"
    DELCO = "delco"


COUNTY_LABELS = {
    County.MONTCO: "Montgomery County, Pennsylvania",
    County.DELCO: "Delaware County, Pennsylvania",
}


class Role(StrEnum):
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class AccessMethod(StrEnum):
    OFFICIAL_API = "official_api"
    PUBLIC_WEB = "public_web"
    AUTHENTICATED_BROWSER = "authenticated_browser"
    USER_ASSISTED = "user_assisted"
    MANUAL_IMPORT = "manual_import"


class LookupStatus(StrEnum):
    PENDING = "pending"
    SUCCESS = "success"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous_match"
    BLOCKED = "blocked"
    AUTH_NEEDED = "authentication_needed"
    USER_ACTION = "user_action_needed"
    RATE_LIMITED = "rate_limited"
    PARSE_FAILURE = "parse_failure"
    UNEXPECTED = "unexpected_failure"
    NOT_CONFIGURED = "not_configured"
    TERMS_NOT_ACKNOWLEDGED = "terms_not_acknowledged"
    SKIPPED = "skipped"


RETRYABLE_STATUSES = {LookupStatus.RATE_LIMITED, LookupStatus.UNEXPECTED}


class DataQuality(StrEnum):
    VERIFIED = "verified"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"
    STALE = "stale"
    MANUAL = "manual"


class EntryMethod(StrEnum):
    AUTOMATED = "automated"
    USER_CAPTURE = "user_capture"
    MANUAL = "manual"
    IMPORT = "import"
    PDF = "pdf"
    FIXTURE = "fixture"


class DocCategory(StrEnum):
    DEED = "deed"
    MORTGAGE = "mortgage"
    SATISFACTION = "satisfaction"
    ASSIGNMENT = "assignment"
    RELEASE = "release"
    MODIFICATION = "modification"
    SUBORDINATION = "subordination"
    OTHER = "other"


class MortgageStatus(StrEnum):
    SATISFIED = "Satisfied"
    NO_SATISFACTION = "No satisfaction found"
    REVIEW = "Potential match — review required"
    INSUFFICIENT = "Insufficient data"


class MatchDecision(StrEnum):
    AUTO_CONFIRMED = "auto_confirmed"
    CANDIDATE = "candidate"
    USER_CONFIRMED = "user_confirmed"
    USER_REJECTED = "user_rejected"


class Decision(StrEnum):
    INTERESTED = "Interested"
    NOT_INTERESTED = "Not interested"
    REVIEW = "Review required"
    INSUFFICIENT = "Insufficient data"
    REMOVED = "Removed/resolved"


class RuleOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    REVIEW = "review"
    RESOLVED = "resolved"
    NOT_APPLICABLE = "not_applicable"


class PropertyCategory(StrEnum):
    SINGLE_FAMILY = "single_family"
    TWIN = "twin"
    ROW = "row"
    CONDO = "condo"
    MULTI_FAMILY = "multi_family"
    MOBILE_HOME = "mobile_home"
    LAND = "land"
    COMMERCIAL = "commercial"
    OTHER_RESIDENTIAL = "other_residential"
    EXEMPT = "exempt"
    OTHER = "other"
    UNKNOWN = "unknown"


class TaxStatusCategory(StrEnum):
    LISTED = "listed"
    NOT_LISTED = "not_listed"
    RESOLVED = "resolved"
    UNKNOWN = "unknown"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ImportStatus(StrEnum):
    UPLOADED = "uploaded"
    PARSING = "parsing"
    REVIEW = "review"
    COMMITTED = "committed"
    FAILED = "failed"


class EstimateType(StrEnum):
    ASSESSOR_VALUE = "assessor_value"
    MARKET_AVM = "market_avm"
    SALE_ESTIMATE = "sale_estimate"
    RENT_ESTIMATE = "rent_estimate"
    LISTING_PRICE = "listing_price"
    MANUAL_OBSERVATION = "manual_observation"
    SANDBOX_AVM = "sandbox_avm"


class CoverageStatus(StrEnum):
    MATCHED = "matched"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"
    NOT_CONFIGURED = "not_configured"
    NOT_PERMITTED = "no_permitted_source"
    ERROR = "error"
