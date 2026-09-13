"""Default, user-editable screening configuration (stored as versioned RuleSet rows)."""

from __future__ import annotations

import copy

DEFAULT_RULES: dict = {
    "valuation": {
        "interest_threshold": 200000,
    },
    "size": {
        "single_family_min_sqft": 900,
        "twin_min_sqft": 900,
        "row_min_sqft": 900,
        "condo_min_sqft": 600,
        "land_min_acres": 1.0,
        "commercial_policy": "review",      # review | pass | fail
        "multi_family_policy": "review",
        "mobile_home_policy": "review",
        "other_policy": "review",
    },
    "assessment": {
        "highlight_below": 100000,          # Delco: assessment cell orange
    },
    "tax": {
        "montco_zero_2024_balance_resolved": True,
        "delco_not_listed_policy": "removed",   # removed | review
        "missing_status_policy": "review",      # review | insufficient
        "stale_after_hours": 36,
    },
    "mortgage": {
        "since_year": 1990,
        "no_satisfaction_policy": "review",     # review | fail | informational
        "potential_match_policy": "review",
        "require_recorder_search": True,
    },
    "liens": {
        "open_lien_policy": "review",           # review | fail | informational
        "require_lien_search": False,
    },
    "decision": {
        "essential_rules": ["valuation_threshold", "property_size"],
        "min_extraction_confidence": 0.8,
    },
    "valuation_selection": {
        "strategy": "primary_then_lowest",      # primary_then_lowest | lowest_credible
        "primary_providers": ["attom", "rentcast", "zillow_bridge"],
        "min_confidence": 0.5,
        "min_match_score": 0.8,
        "credible_estimate_types": ["market_avm", "sale_estimate", "manual_observation"],
    },
}

POLICY_VALUES = {"review", "pass", "fail", "informational", "removed", "insufficient"}


def default_rules() -> dict:
    return copy.deepcopy(DEFAULT_RULES)


def merge_rules(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge_rules(out[key], value)
        else:
            out[key] = value
    return out


def validate_rules(config: dict) -> list[str]:
    errors: list[str] = []
    for section, values in DEFAULT_RULES.items():
        if section not in config:
            errors.append(f"Missing section '{section}'")
            continue
        for key, default in values.items():
            if key not in config[section]:
                errors.append(f"Missing '{section}.{key}'")
                continue
            value = config[section][key]
            if isinstance(default, bool):
                if not isinstance(value, bool):
                    errors.append(f"'{section}.{key}' must be true/false")
            elif isinstance(default, int | float):
                if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
                    errors.append(f"'{section}.{key}' must be a non-negative number")
            elif isinstance(default, list):
                if not isinstance(value, list):
                    errors.append(f"'{section}.{key}' must be a list")
            elif key.endswith("_policy") and value not in POLICY_VALUES:
                errors.append(f"'{section}.{key}' must be one of {sorted(POLICY_VALUES)}")
    return errors
