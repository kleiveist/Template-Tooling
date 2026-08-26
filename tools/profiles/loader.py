from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from tools.profiles.model import FeatureDefinition, ProfileCatalog, ProfileDefinition, ProjectProfile
from tools.profiles.validator import (
    ProfileLookupError,
    resolve_optional_features,
    validate_catalog,
    validate_feature_selection,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILES_DIR = ROOT / "profiles"
DEFAULT_PROJECT_PROFILE = ROOT / "project-profile.toml"
SUPPORTED_SCHEMA_VERSION = 1


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except OSError as exc:
        raise OSError(f"Could not read TOML file: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid TOML in {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a TOML table at the document root.")
    return payload


def _require_str(payload: dict[str, Any], key: str, *, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must define a non-empty string '{key}'.")
    return value.strip()


def _optional_int(payload: dict[str, Any], key: str, *, default: int) -> int:
    value = payload.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"'{key}' must be an integer when present.")
    return value


def _optional_bool(payload: dict[str, Any], key: str, *, default: bool = False) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"'{key}' must be a boolean when present.")
    return value


def _schema_version(payload: dict[str, Any], *, context: str) -> int:
    value = payload.get("schema_version", SUPPORTED_SCHEMA_VERSION)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{context} must define an integer 'schema_version'.")
    if value != SUPPORTED_SCHEMA_VERSION:
        raise ValueError(
            f"{context} uses unsupported schema version {value}; "
            f"this tooling supports version {SUPPORTED_SCHEMA_VERSION}."
        )
    return value


def _require_str_list(value: Any, *, context: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{context} must define a non-empty list of strings.")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{context} must contain only non-empty strings.")
        items.append(item.strip())
    return tuple(dict.fromkeys(items))


def _optional_str_list(value: Any, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        return default
    if not isinstance(value, list):
        raise ValueError("Expected a list of strings.")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("List values must be non-empty strings.")
        items.append(item.strip())
    return tuple(dict.fromkeys(items))


def load_catalog(
    profiles_dir: Path | None = None,
    *,
    validate_paths: bool = True,
) -> ProfileCatalog:
    directory = (profiles_dir or DEFAULT_PROFILES_DIR).resolve()
    features_path = directory / "features.toml"
    payload = _read_toml(features_path)

    schema_version = _schema_version(payload, context=str(features_path))

    core_section = payload.get("core")
    if not isinstance(core_section, dict):
        raise ValueError(f"{features_path} must define a [core] table.")
    core_paths = _require_str_list(core_section.get("paths"), context=f"{features_path}: [core].paths")

    features_section = payload.get("features")
    if not isinstance(features_section, dict) or not features_section:
        raise ValueError(f"{features_path} must define a non-empty [features] table.")

    features: dict[str, FeatureDefinition] = {}
    for feature_id, raw in features_section.items():
        if not isinstance(raw, dict):
            raise ValueError(f"{features_path}: [features.{feature_id}] must be a table.")
        features[feature_id] = FeatureDefinition(
            id=feature_id,
            name=_require_str(raw, "name", context=f"{features_path}: [features.{feature_id}]"),
            description=_require_str(raw, "description", context=f"{features_path}: [features.{feature_id}]"),
            paths=_optional_str_list(raw.get("paths")),
            requires=_optional_str_list(raw.get("requires")),
            optional=_optional_bool(raw, "optional"),
            selectable=_optional_bool(raw, "selectable"),
        )

    profiles: dict[str, ProfileDefinition] = {}
    for path in sorted(directory.glob("*.toml")):
        if path.name == "features.toml":
            continue
        profile = _load_profile_definition(path)
        if profile.id in profiles:
            raise ValueError(f"Duplicate profile id '{profile.id}' in {directory}.")
        profiles[profile.id] = profile

    catalog = ProfileCatalog(
        schema_version=schema_version,
        core_paths=core_paths,
        features=features,
        profiles=profiles,
    )
    validate_catalog(catalog, project_root=directory.parent, validate_paths=validate_paths)
    return catalog


def _load_profile_definition(path: Path) -> ProfileDefinition:
    payload = _read_toml(path)
    schema_version = _schema_version(payload, context=str(path))

    profile_id = _require_str(payload, "id", context=str(path))
    if path.stem != profile_id:
        raise ValueError(f"{path} must use the same file name and profile id ('{path.stem}' != '{profile_id}').")

    return ProfileDefinition(
        schema_version=schema_version,
        id=profile_id,
        order=_optional_int(payload, "order", default=1000),
        name=_require_str(payload, "name", context=str(path)),
        description=_require_str(payload, "description", context=str(path)),
        features=_require_str_list(payload.get("features"), context=f"{path}: features"),
    )


def resolve_profile(
    catalog: ProfileCatalog,
    profile_id: str,
    *,
    optional_features: tuple[str, ...] = (),
) -> ProjectProfile:
    profile = catalog.profiles.get(profile_id)
    if profile is None:
        known = ", ".join(sorted(catalog.profiles))
        raise ProfileLookupError(f"Unknown profile '{profile_id}'. Available profiles: {known}.")

    base_features = validate_feature_selection(profile.features, catalog)
    features = resolve_optional_features(base_features, optional_features, catalog)
    return ProjectProfile(
        schema_version=catalog.schema_version,
        profile_id=profile.id,
        name=profile.name,
        description=profile.description,
        features=features,
        optional_features=optional_features,
    )


def load_project_profile(
    profile_path: Path | None = None,
    *,
    catalog: ProfileCatalog | None = None,
) -> ProjectProfile:
    path = (profile_path or DEFAULT_PROJECT_PROFILE).resolve()
    payload = _read_toml(path)
    schema_version = _schema_version(payload, context=str(path))

    features = _require_str_list(payload.get("features"), context=f"{path}: features")
    requested_features = _optional_str_list(payload.get("optional_features"))
    profile_id = _require_str(payload, "id", context=str(path))
    if catalog is not None:
        features = validate_feature_selection(features, catalog)
        base = resolve_profile(catalog, profile_id)
        if not requested_features:
            requested_features = tuple(item for item in features if item not in base.features)
        declared = resolve_profile(catalog, profile_id, optional_features=requested_features)
        if features != declared.features:
            raise ValueError(
                f"{path} features do not match declared profile '{profile_id}': "
                f"expected {list(declared.features)}, got {list(features)}."
            )

    return ProjectProfile(
        schema_version=schema_version,
        profile_id=profile_id,
        name=_require_str(payload, "name", context=str(path)),
        description=_require_str(payload, "description", context=str(path)),
        features=features,
        optional_features=requested_features,
    )


def load_active_profile(project_root: Path | None = None) -> ProjectProfile:
    root = (project_root or ROOT).resolve()
    profiles_dir = root / "profiles"
    if not profiles_dir.exists():
        return _infer_profile_from_scaffold(root)

    # A derived project retains the complete catalog for reference, but only
    # enabled feature paths are expected to exist in that scaffold.
    catalog = load_catalog(profiles_dir, validate_paths=False)
    profile_path = root / "project-profile.toml"
    if profile_path.exists():
        return load_project_profile(profile_path, catalog=catalog)
    return resolve_profile(catalog, "full-platform")


def _infer_profile_from_scaffold(project_root: Path) -> ProjectProfile:
    features: list[str] = []

    if (project_root / "frontend" / "package.json").exists():
        features.append("frontend")

    if (project_root / "backend" / "app" / "main.py").exists():
        features.append("backend")

    if (project_root / "src-tauri" / "tauri.conf.json").exists():
        features.append("tauri")

    optional_features: list[str] = []
    if (project_root / "backend" / "app" / "db").exists():
        features.append("database")
        optional_features.append("database")
    if (project_root / "backend" / "requirements-postgres.txt").exists():
        features.append("postgres")
        optional_features.append("postgres")

    return ProjectProfile(
        schema_version=1,
        profile_id="inferred-project",
        name="Inferred project",
        description="Profile inferred from the directories present in the current project root.",
        features=tuple(features),
        optional_features=tuple(optional_features),
    )
