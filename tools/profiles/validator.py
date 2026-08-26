from __future__ import annotations

import re
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
from typing import Iterable

from tools.profiles.model import FeatureDefinition, ProfileCatalog, ProfileDefinition


class ProfileError(RuntimeError):
    """Base error for profile loading and runtime validation."""


class CatalogValidationError(ProfileError):
    """Raised when a feature or profile catalog is internally inconsistent."""


class ProfileLookupError(ProfileError):
    """Raised when a requested profile id does not exist."""


ID_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")


def _validate_id(value: str, *, kind: str) -> str | None:
    if ID_PATTERN.fullmatch(value):
        return None
    return f"{kind} id '{value}' must use lowercase kebab-case."


def _validate_relative_path(value: str, *, context: str) -> str | None:
    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    raw_parts = value.split("/")

    if "\\" in value:
        return f"{context} path '{value}' must use forward slashes."
    if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        return f"{context} path '{value}' must be relative to the repository root."
    if not posix_path.parts or any(part in {"", ".", ".."} for part in raw_parts):
        return f"{context} path '{value}' must not be empty or contain '.' or '..' segments."
    return None


def _dependency_cycle(catalog: ProfileCatalog) -> tuple[str, ...] | None:
    visited: set[str] = set()
    active: list[str] = []
    active_set: set[str] = set()

    def visit(feature_id: str) -> tuple[str, ...] | None:
        if feature_id in active_set:
            start = active.index(feature_id)
            return tuple(active[start:] + [feature_id])
        if feature_id in visited:
            return None

        active.append(feature_id)
        active_set.add(feature_id)
        for dependency in catalog.features[feature_id].requires:
            if dependency in catalog.features:
                cycle = visit(dependency)
                if cycle is not None:
                    return cycle
        active.pop()
        active_set.remove(feature_id)
        visited.add(feature_id)
        return None

    for feature_id in catalog.features:
        cycle = visit(feature_id)
        if cycle is not None:
            return cycle
    return None


def validate_feature_selection(feature_ids: Iterable[str], catalog: ProfileCatalog) -> tuple[str, ...]:
    selected = tuple(dict.fromkeys(feature_ids))
    errors: list[str] = []
    enabled = set(selected)

    for feature_id in selected:
        if feature_id not in catalog.features:
            errors.append(f"Unknown feature '{feature_id}'.")

    if errors:
        raise CatalogValidationError("\n".join(errors))

    for feature_id in selected:
        feature = catalog.features[feature_id]
        for dependency in feature.requires:
            if dependency not in enabled:
                errors.append(f"Feature '{feature_id}' requires feature '{dependency}'.")

    if errors:
        raise CatalogValidationError("\n".join(errors))

    return selected


def resolve_optional_features(
    base_feature_ids: Iterable[str],
    requested_feature_ids: Iterable[str],
    catalog: ProfileCatalog,
) -> tuple[str, ...]:
    selected = list(dict.fromkeys(base_feature_ids))
    enabled = set(selected)
    requested = tuple(dict.fromkeys(requested_feature_ids))

    unknown = [feature_id for feature_id in requested if feature_id not in catalog.features]
    if unknown:
        raise CatalogValidationError("\n".join(f"Unknown optional feature '{item}'." for item in unknown))

    non_optional = [feature_id for feature_id in requested if not catalog.features[feature_id].optional]
    if non_optional:
        raise CatalogValidationError(
            "\n".join(
                f"Feature '{item}' is provided by project profiles and cannot be selected with '--with'."
                for item in non_optional
            )
        )

    resolving: set[str] = set()

    def add(feature_id: str, *, requested_by: str) -> None:
        if feature_id in enabled:
            return
        if feature_id in resolving:
            raise CatalogValidationError(f"Feature dependency cycle encountered while resolving '{feature_id}'.")

        feature = catalog.features[feature_id]
        resolving.add(feature_id)
        for dependency in feature.requires:
            if dependency in enabled:
                continue
            dependency_feature = catalog.features[dependency]
            if not dependency_feature.optional:
                raise CatalogValidationError(
                    f"Optional feature '{requested_by}' requires feature '{dependency}', "
                    "which is not enabled by the selected project profile."
                )
            add(dependency, requested_by=requested_by)
        resolving.remove(feature_id)
        selected.append(feature_id)
        enabled.add(feature_id)

    for feature_id in requested:
        add(feature_id, requested_by=feature_id)

    return validate_feature_selection(selected, catalog)


def _catalog_path_error(
    relative: str,
    *,
    context: str,
    root: Path | None,
    validate_paths: bool,
) -> str | None:
    path_error = _validate_relative_path(relative, context=context)
    if path_error or not validate_paths or root is None:
        return path_error
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        return f"{context} path '{relative}' resolves outside {root}."
    if not candidate.exists():
        return f"{context} path '{relative}' does not exist under {root}."
    return None


def _core_errors(catalog: ProfileCatalog, root: Path | None, validate_paths: bool) -> list[str]:
    errors: list[str] = []
    if not catalog.core_paths:
        errors.append("Profile catalog must define at least one core path.")
    for relative in catalog.core_paths:
        error = _catalog_path_error(relative, context="Core", root=root, validate_paths=validate_paths)
        if error:
            errors.append(error)
    return errors


def _feature_errors(
    feature: FeatureDefinition,
    catalog: ProfileCatalog,
    root: Path | None,
    validate_paths: bool,
) -> list[str]:
    errors: list[str] = []
    id_error = _validate_id(feature.id, kind="Feature")
    if id_error:
        errors.append(id_error)
    errors.extend(
        f"Feature '{feature.id}' requires unknown feature '{dependency}'."
        for dependency in feature.requires
        if dependency not in catalog.features
    )
    if feature.selectable and not feature.optional:
        errors.append(f"Feature '{feature.id}' is selectable but is not marked optional.")
    for relative in feature.paths:
        error = _catalog_path_error(
            relative,
            context=f"Feature '{feature.id}'",
            root=root,
            validate_paths=validate_paths,
        )
        if error:
            errors.append(error)
    return errors


def _profile_errors(profile: ProfileDefinition, catalog: ProfileCatalog) -> list[str]:
    errors: list[str] = []
    id_error = _validate_id(profile.id, kind="Profile")
    if id_error:
        errors.append(id_error)
    if profile.schema_version != catalog.schema_version:
        errors.append(
            f"Profile '{profile.id}' uses schema version {profile.schema_version}; "
            f"catalog version is {catalog.schema_version}."
        )
    try:
        validate_feature_selection(profile.features, catalog)
    except CatalogValidationError as exc:
        errors.append(f"Profile '{profile.id}' is invalid: {exc}")
    optional = [
        feature_id
        for feature_id in profile.features
        if catalog.features.get(feature_id) is not None and catalog.features[feature_id].optional
    ]
    if optional:
        errors.append(f"Profile '{profile.id}' must not hardcode optional feature(s): {', '.join(optional)}.")
    return errors


def validate_catalog(
    catalog: ProfileCatalog,
    *,
    project_root: Path | None = None,
    validate_paths: bool = True,
) -> None:
    root = project_root.resolve() if project_root is not None else None
    errors = _core_errors(catalog, root, validate_paths)
    if not catalog.features:
        errors.append("Profile catalog must define at least one feature.")
    if not catalog.profiles:
        errors.append("Profile catalog must define at least one profile.")
    for feature in catalog.features.values():
        errors.extend(_feature_errors(feature, catalog, root, validate_paths))
    cycle = _dependency_cycle(catalog)
    if cycle is not None:
        errors.append(f"Feature dependency cycle detected: {' -> '.join(cycle)}.")
    for profile in catalog.profiles.values():
        errors.extend(_profile_errors(profile, catalog))
    if errors:
        raise CatalogValidationError("\n".join(errors))
