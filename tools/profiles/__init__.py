"""Project profile loading, validation, and scaffolding helpers."""

from tools.profiles.generator import GenerationError, ScaffoldPlan, build_scaffold_plan, scaffold_project
from tools.profiles.loader import load_active_profile, load_catalog, resolve_profile
from tools.profiles.model import FeatureDefinition, ProfileCatalog, ProfileDefinition, ProjectProfile
from tools.profiles.runtime import active_profile, feature_enabled
from tools.profiles.validator import (
    CatalogValidationError,
    ProfileError,
    ProfileLookupError,
    resolve_optional_features,
    validate_catalog,
)

__all__ = [
    "CatalogValidationError",
    "FeatureDefinition",
    "GenerationError",
    "ProfileCatalog",
    "ProfileDefinition",
    "ProfileError",
    "ProfileLookupError",
    "ProjectProfile",
    "ScaffoldPlan",
    "active_profile",
    "build_scaffold_plan",
    "feature_enabled",
    "load_active_profile",
    "load_catalog",
    "resolve_profile",
    "resolve_optional_features",
    "scaffold_project",
    "validate_catalog",
]
