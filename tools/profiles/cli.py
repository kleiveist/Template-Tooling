from __future__ import annotations

import argparse
from pathlib import Path

from tools import logger
from tools.profiles.generator import (
    GenerationError,
    ScaffoldPlan,
    build_scaffold_plan,
    scaffold_project,
)
from tools.profiles.loader import load_catalog
from tools.profiles.model import FeatureDefinition, ProfileCatalog, ProfileDefinition
from tools.profiles.validator import (
    CatalogValidationError,
    ProfileLookupError,
    resolve_optional_features,
)
from tools.template_lifecycle.model import LifecycleError
from tools.template_lifecycle.scaffold import finalize_generated_project

ROOT = Path(__file__).resolve().parents[2]


def main(args: argparse.Namespace) -> int:
    try:
        # Derived projects retain presets whose source modules may be absent.
        # The selected plan validates its own source paths before generation.
        catalog = load_catalog(ROOT / "profiles", validate_paths=False)
        explicit_profile = getattr(args, "profile", None)
        profile = _resolve_profile_choice(catalog, explicit_profile)
        requested_features = _parse_optional_features(getattr(args, "optional_features", []))
        if explicit_profile is None and not requested_features:
            requested_features = _prompt_for_optional_features(catalog, profile)
        target_dir = _resolve_target_dir(
            getattr(args, "target_dir", None),
            profile.id,
            requested_features,
        )
        plan = build_scaffold_plan(
            catalog,
            project_root=ROOT,
            target_dir=target_dir,
            profile_id=profile.id,
            optional_features=requested_features,
            project_name=getattr(args, "project_name", None),
            project_slug=getattr(args, "project_slug", None),
            identifier=getattr(args, "identifier", None),
        )
    except ProfileLookupError as exc:
        logger.fail(str(exc))
        return 2
    except (CatalogValidationError, GenerationError, OSError, ValueError) as exc:
        logger.fail(str(exc))
        return 1

    _print_plan(plan)
    dry_run = bool(getattr(args, "dry_run", False))
    try:
        scaffold_project(plan, dry_run=dry_run)
        if not dry_run:
            finalize_generated_project(plan)
    except (GenerationError, LifecycleError, OSError) as exc:
        logger.fail(str(exc))
        return 1

    if dry_run:
        logger.ok("Init dry-run completed; no files were written")
        return 0

    logger.ok(f"Generated profile '{plan.profile.profile_id}' in {plan.target_dir}")
    return 0


def _ordered_profiles(catalog: ProfileCatalog) -> list[ProfileDefinition]:
    return sorted(catalog.profiles.values(), key=lambda item: (item.order, item.name.lower(), item.id))


def _resolve_profile_choice(catalog: ProfileCatalog, explicit_profile: str | None) -> ProfileDefinition:
    if explicit_profile:
        profile = catalog.profiles.get(explicit_profile)
        if profile is None:
            known = ", ".join(item.id for item in _ordered_profiles(catalog))
            raise ProfileLookupError(f"Unknown profile '{explicit_profile}'. Available profiles: {known}.")
        return profile
    return _prompt_for_profile(catalog)


def _prompt_for_profile(catalog: ProfileCatalog) -> ProfileDefinition:
    options = _ordered_profiles(catalog)
    print("Choose project profile:\n")
    for index, profile in enumerate(options, start=1):
        print(f"{index}. {profile.name} ({profile.id})")
        print(f"   {profile.description}")
    print("")

    while True:
        try:
            choice = input(f"Selection [1-{len(options)} or q]: ").strip().lower()
        except EOFError:
            choice = "q"
            print("")

        if choice in {"q", "quit", "exit"}:
            raise GenerationError("Initialization cancelled.")

        try:
            index = int(choice)
        except ValueError:
            logger.warn("Enter a profile number or 'q' to cancel.")
            continue

        if 1 <= index <= len(options):
            return options[index - 1]
        logger.warn("Selected profile is outside the available range.")


def _parse_optional_features(values: list[str]) -> tuple[str, ...]:
    parsed: list[str] = []
    for value in values:
        for item in value.split(","):
            feature_id = item.strip()
            if feature_id and feature_id not in parsed:
                parsed.append(feature_id)
    return tuple(parsed)


def _prompt_for_optional_features(
    catalog: ProfileCatalog,
    profile: ProfileDefinition,
) -> tuple[str, ...]:
    base = tuple(profile.features)
    compatible: list[FeatureDefinition] = []
    for feature in catalog.features.values():
        if not feature.selectable:
            continue
        try:
            resolve_optional_features(base, (feature.id,), catalog)
        except CatalogValidationError:
            continue
        compatible.append(feature)

    print("Optional features:\n")
    if not compatible:
        print("No optional capabilities are compatible with this profile.\n")
        return ()

    for index, feature in enumerate(compatible, start=1):
        print(f"{index}. {feature.name} ({feature.id})")
        print(f"   {feature.description}")
    print("")

    while True:
        try:
            choice = input("Selection [comma-separated numbers, Enter for none, or q]: ").strip().lower()
        except EOFError:
            choice = "q"
            print("")
        if not choice:
            return ()
        if choice in {"q", "quit", "exit"}:
            raise GenerationError("Initialization cancelled.")

        try:
            indexes = [int(item.strip()) for item in choice.split(",") if item.strip()]
        except ValueError:
            logger.warn("Enter comma-separated feature numbers, press Enter, or use 'q' to cancel.")
            continue
        if indexes and all(1 <= index <= len(compatible) for index in indexes):
            return tuple(dict.fromkeys(compatible[index - 1].id for index in indexes))
        logger.warn("Optional feature selection is outside the available range.")


def _resolve_target_dir(
    explicit_target: str | None,
    profile_id: str,
    optional_features: tuple[str, ...],
) -> Path:
    if explicit_target:
        return Path(explicit_target).expanduser()
    suffix = "" if not optional_features else "-" + "-".join(optional_features)
    return ROOT / ".generated" / f"{profile_id}{suffix}"


def _print_plan(plan: ScaffoldPlan) -> None:
    logger.info(f"Profile: {plan.profile.name} ({plan.profile.profile_id})")
    logger.info(f"Description: {plan.profile.description}")
    logger.info(f"Enabled features: {', '.join(plan.profile.features)}")
    if plan.profile.optional_features:
        logger.info(f"Optional capabilities: {', '.join(plan.profile.optional_features)}")
    logger.info(f"Target directory: {plan.target_dir}")
    logger.info(f"Project identity: {plan.identity.name} ({plan.identity.slug}, {plan.identity.identifier})")
    logger.info("Scaffold paths:")
    for source in plan.paths:
        logger.info(f"  - {source.relative_to(plan.project_root).as_posix()}")
