from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import tomllib

from tools.profiles.loader import load_catalog, load_project_profile
from tools.template_lifecycle.manifest import (
    inspect_relative,
    load_manifest,
    project_owned_paths,
    safe_relative_path,
    validate_project_symlinks,
)
from tools.template_lifecycle.migrations import REGISTRY, MigrationRegistry
from tools.template_lifecycle.model import (
    LifecycleError,
    LifecycleState,
    ProductIdentity,
    VerificationFinding,
    VerificationResult,
)
from tools.template_lifecycle.state import (
    BASELINE_RELATIVE_PATH,
    STATE_RELATIVE_PATH,
    load_state,
)

SEMVER = re.compile(
    r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?"
)


def verify_project(project_root: Path, *, registry: MigrationRegistry = REGISTRY) -> VerificationResult:
    root = project_root.resolve()
    findings, state, manifest = _verify_lifecycle_metadata(root)
    if state is None:
        return VerificationResult(tuple(findings))
    if not _check_product_symlinks(root, findings):
        return VerificationResult(tuple(findings))
    _check_source_reproducibility(state, findings)
    _check_profile(root, state, findings)
    _check_identity(root, state.identity, findings)
    _check_versions(root, findings)
    _check_migrations(state, registry, findings)
    if manifest is not None:
        _check_drift(root, manifest, findings)
    return VerificationResult(tuple(findings))


def verify_lifecycle_metadata(project_root: Path) -> VerificationResult:
    """Validate only managed lifecycle files, without inspecting product drift."""

    findings, _state, _manifest = _verify_lifecycle_metadata(project_root.resolve())
    return VerificationResult(tuple(findings))


def _verify_lifecycle_metadata(
    root: Path,
) -> tuple[list[VerificationFinding], LifecycleState | None, Any]:
    findings: list[VerificationFinding] = []
    safe_to_read = _check_lifecycle_paths(root, findings)
    if not safe_to_read:
        return findings, None, None
    state = _load_state_finding(root, findings)
    if state is None:
        return findings, None, None
    manifest = _load_manifest_finding(root, state, findings)
    return findings, state, manifest


def product_version(project_root: Path) -> str:
    try:
        value = _read_product_text(project_root.resolve(), "VERSION").strip()
    except LifecycleError as exc:
        raise LifecycleError("Product VERSION is missing or unreadable.") from exc
    if not SEMVER.fullmatch(value):
        raise LifecycleError("Product VERSION is not a valid SemVer value.")
    return value


def identity_issues(project_root: Path, identity: ProductIdentity) -> tuple[str, ...]:
    root = project_root.resolve()
    issues: list[str] = []
    _check_json_value(
        root,
        "frontend/package.json",
        ("name",),
        f"{identity.slug}-frontend",
        issues,
    )
    _check_json_value(
        root,
        "frontend/package-lock.json",
        ("name",),
        f"{identity.slug}-frontend",
        issues,
    )
    _check_json_value(
        root,
        "frontend/package-lock.json",
        ("packages", "", "name"),
        f"{identity.slug}-frontend",
        issues,
    )
    _check_text_contains(root, "frontend/index.html", identity.name, issues)
    _check_text_contains(root, "frontend/src/main.ts", identity.name, issues)
    _check_text_contains(root, "backend/app/api/health.py", f"{identity.slug}-backend", issues)
    _check_text_contains(root, "tools/inst/build.py", f"{identity.slug}-web.zip", issues)
    _check_text_contains(root, "deployment/compose.yaml", identity.slug, issues)
    _check_tauri_identity(root, identity, issues)
    return tuple(issues)


def drift_counts(project_root: Path, manifest: Any) -> tuple[int, int, int]:
    modified = 0
    missing = 0
    for expected in manifest.files:
        actual = inspect_relative(project_root, expected.path)
        if actual is None:
            missing += 1
        elif actual != expected:
            modified += 1
    return modified, missing, len(project_owned_paths(project_root, manifest))


def _load_state_finding(root: Path, findings: list[VerificationFinding]) -> LifecycleState | None:
    try:
        state = load_state(root)
    except LifecycleError as exc:
        findings.append(VerificationFinding("state", "FAIL", str(exc)))
        return None
    findings.append(VerificationFinding("state", "PASS", "Lifecycle state schema and provenance are valid."))
    return state


def _load_manifest_finding(root: Path, state: LifecycleState, findings: list[VerificationFinding]) -> Any:
    try:
        manifest = load_manifest(root / state.baseline.manifest)
    except LifecycleError as exc:
        findings.append(VerificationFinding("manifest", "FAIL", str(exc)))
        return None
    if manifest.digest != state.baseline.digest:
        findings.append(
            VerificationFinding(
                "manifest",
                "FAIL",
                "State digest does not match the baseline manifest digest.",
            )
        )
        return None
    findings.append(VerificationFinding("manifest", "PASS", "Baseline manifest schema and digest are valid."))
    return manifest


def _check_lifecycle_paths(root: Path, findings: list[VerificationFinding]) -> bool:
    lifecycle_dir = root / ".template"
    paths = (root / STATE_RELATIVE_PATH, root / BASELINE_RELATIVE_PATH)
    unsafe_symlink = lifecycle_dir.is_symlink() or any(path.is_symlink() for path in paths)
    if unsafe_symlink or any(path.is_symlink() or not path.is_file() for path in paths):
        findings.append(
            VerificationFinding(
                "lifecycle-paths",
                "FAIL",
                "Lifecycle files must be regular files inside .template/.",
            )
        )
        return not unsafe_symlink
    else:
        findings.append(
            VerificationFinding(
                "lifecycle-paths",
                "PASS",
                "Lifecycle files are regular project-local files.",
            )
        )
        return True


def _check_source_reproducibility(state: LifecycleState, findings: list[VerificationFinding]) -> None:
    if state.source_dirty:
        findings.append(
            VerificationFinding(
                "source-reproducibility",
                "WARN",
                "Baseline came from a dirty template working tree; update --apply is blocked until clean re-adoption.",
            )
        )
    else:
        findings.append(
            VerificationFinding(
                "source-reproducibility",
                "PASS",
                "Baseline is bound to a clean template commit.",
            )
        )


def _check_product_symlinks(root: Path, findings: list[VerificationFinding]) -> bool:
    try:
        validate_project_symlinks(root)
    except LifecycleError as exc:
        findings.append(
            VerificationFinding(
                "product-paths",
                "FAIL",
                _relative_error(root, exc),
            )
        )
        return False
    return True


def _check_profile(root: Path, state: LifecycleState, findings: list[VerificationFinding]) -> None:
    try:
        catalog = load_catalog(_safe_product_path(root, "profiles"), validate_paths=False)
        profile = load_project_profile(
            _safe_product_path(root, "project-profile.toml"),
            catalog=catalog,
        )
    except (OSError, ValueError, LifecycleError) as exc:
        findings.append(
            VerificationFinding(
                "profile",
                "FAIL",
                f"Could not validate project profile: {_relative_error(root, exc)}",
            )
        )
        return
    expected = state.selection
    if (
        profile.profile_id != expected.profile
        or profile.optional_features != expected.optional_features
        or profile.features != expected.resolved_features
    ):
        findings.append(
            VerificationFinding(
                "profile",
                "FAIL",
                "Active profile or capabilities differ from lifecycle state; explicitly re-adopt before updating.",
            )
        )
    else:
        findings.append(VerificationFinding("profile", "PASS", "Profile and resolved capabilities match state."))


def _check_identity(root: Path, identity: ProductIdentity, findings: list[VerificationFinding]) -> None:
    issues = identity_issues(root, identity)
    if issues:
        findings.append(VerificationFinding("identity", "FAIL", "; ".join(issues)))
    else:
        findings.append(VerificationFinding("identity", "PASS", "Stored product identity matches product metadata."))


def _check_versions(root: Path, findings: list[VerificationFinding]) -> None:
    try:
        expected = product_version(root)
        mismatches = _version_mismatches(root, expected)
    except (
        LifecycleError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        tomllib.TOMLDecodeError,
    ) as exc:
        findings.append(
            VerificationFinding(
                "product-version",
                "FAIL",
                _relative_error(root, exc),
            )
        )
        return
    if mismatches:
        findings.append(VerificationFinding("product-version", "FAIL", "; ".join(mismatches)))
    else:
        findings.append(
            VerificationFinding(
                "product-version",
                "PASS",
                f"Product version mirrors remain synchronized at {expected}.",
            )
        )


def _version_mismatches(root: Path, expected: str) -> list[str]:
    mismatches: list[str] = []
    package = _optional_json(root, "frontend/package.json")
    lock = _optional_json(root, "frontend/package-lock.json")
    tauri = _optional_json(root, "src-tauri/tauri.conf.json")
    _compare_optional(package, ("version",), expected, "frontend/package.json", mismatches)
    _compare_optional(lock, ("version",), expected, "frontend/package-lock.json", mismatches)
    _compare_optional(lock, ("packages", "", "version"), expected, "package-lock root", mismatches)
    _compare_optional(tauri, ("version",), expected, "src-tauri/tauri.conf.json", mismatches)
    cargo_text = _optional_product_text(root, "src-tauri/Cargo.toml")
    lock_text = _optional_product_text(root, "src-tauri/Cargo.lock")
    if cargo_text is not None:
        cargo = tomllib.loads(cargo_text)
        package_table = cargo.get("package", {})
        actual = package_table.get("version") if isinstance(package_table, dict) else None
        name = package_table.get("name") if isinstance(package_table, dict) else None
        if actual != expected:
            mismatches.append(f"src-tauri/Cargo.toml={actual!r}, expected {expected}")
        if lock_text is not None and isinstance(name, str):
            cargo_lock = tomllib.loads(lock_text)
            locked = next(
                (
                    item.get("version")
                    for item in cargo_lock.get("package", [])
                    if isinstance(item, dict) and item.get("name") == name
                ),
                None,
            )
            if locked != expected:
                mismatches.append(f"src-tauri/Cargo.lock root={locked!r}, expected {expected}")
    return mismatches


def _check_migrations(
    state: LifecycleState,
    registry: MigrationRegistry,
    findings: list[VerificationFinding],
) -> None:
    unknown = sorted(set(state.baseline.applied_migrations) - set(registry.ids))
    if unknown:
        findings.append(
            VerificationFinding(
                "migrations",
                "FAIL",
                f"Unknown applied migration ids: {', '.join(unknown)}.",
            )
        )
    else:
        findings.append(VerificationFinding("migrations", "PASS", "Applied migration ids are unique and known."))


def _check_drift(root: Path, manifest: Any, findings: list[VerificationFinding]) -> None:
    try:
        modified, missing, product_owned = drift_counts(root, manifest)
    except LifecycleError as exc:
        findings.append(VerificationFinding("baseline-files", "FAIL", _relative_error(root, exc)))
        return
    status = "WARN" if missing else "PASS"
    findings.append(
        VerificationFinding(
            "baseline-files",
            status,
            f"Managed drift: {modified} modified, {missing} missing; {product_owned} product-owned files preserved.",
        )
    )


def _check_tauri_identity(root: Path, identity: ProductIdentity, issues: list[str]) -> None:
    try:
        payload = _optional_json(root, "src-tauri/tauri.conf.json")
    except LifecycleError:
        issues.append("src-tauri/tauri.conf.json is unreadable")
        return
    if payload is None:
        return
    for key, expected in (
        ("productName", identity.name),
        ("identifier", identity.identifier),
        ("mainBinaryName", identity.binary),
    ):
        if not isinstance(payload, dict) or payload.get(key) != expected:
            issues.append(f"Tauri {key} does not match stored identity")
    app = payload.get("app")
    windows = app.get("windows") if isinstance(app, dict) else None
    main_window = (
        next(
            (window for window in windows if isinstance(window, dict) and window.get("label") == "main"),
            None,
        )
        if isinstance(windows, list)
        else None
    )
    if not isinstance(main_window, dict) or main_window.get("title") != identity.name:
        issues.append("Tauri main window title does not match stored identity")
    _check_text_contains(root, "src-tauri/Cargo.toml", f'name = "{identity.binary}"', issues)
    _check_text_contains(root, "src-tauri/Cargo.lock", f'name = "{identity.binary}"', issues)
    _check_text_contains(root, "src-tauri/app-icon.svg", identity.name, issues)


def _check_json_value(
    root: Path,
    relative: str,
    keys: tuple[str, ...],
    expected: str,
    issues: list[str],
) -> None:
    try:
        payload: Any = _optional_json(root, relative)
    except LifecycleError:
        issues.append(f"{relative} is unreadable")
        return
    if payload is None:
        return
    for key in keys:
        payload = payload.get(key) if isinstance(payload, dict) else None
    if payload != expected:
        issues.append(f"{relative} does not match stored identity")


def _check_text_contains(root: Path, relative: str, expected: str, issues: list[str]) -> None:
    try:
        content = _optional_product_text(root, relative)
        if content is not None and expected not in content:
            issues.append(f"{relative} does not match stored identity")
    except LifecycleError:
        issues.append(f"{relative} is unreadable")


def _optional_json(root: Path, relative: str) -> dict[str, Any] | None:
    content = _optional_product_text(root, relative)
    if content is None:
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LifecycleError(f"{relative} is invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise LifecycleError(f"{relative} must contain a JSON object.")
    return payload


def _optional_product_text(root: Path, relative: str) -> str | None:
    path = _safe_product_path(root, relative)
    if not path.exists() and not path.is_symlink():
        return None
    return _read_product_text(root, relative)


def _read_product_text(root: Path, relative: str) -> str:
    path = _safe_product_path(root, relative)
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise LifecycleError(f"Could not read product path {relative}.") from exc


def _safe_product_path(root: Path, relative: str) -> Path:
    safe = safe_relative_path(relative)
    resolved_root = root.resolve()
    candidate = resolved_root / Path(safe)
    ancestor = candidate if candidate.exists() or candidate.is_symlink() else candidate.parent
    while not ancestor.exists() and not ancestor.is_symlink() and ancestor != resolved_root:
        ancestor = ancestor.parent
    try:
        resolved_ancestor = ancestor.resolve(strict=True)
    except OSError as exc:
        raise LifecycleError(f"Product path is broken or unreadable: {relative}.") from exc
    if not resolved_ancestor.is_relative_to(resolved_root):
        raise LifecycleError(f"Product path uses an external symbolic link: {relative}.")
    return candidate


def _relative_error(root: Path, error: BaseException) -> str:
    return str(error).replace(str(root.resolve()), ".")


def _compare_optional(
    payload: dict[str, Any] | None,
    keys: tuple[str, ...],
    expected: str,
    label: str,
    mismatches: list[str],
) -> None:
    if payload is None:
        return
    value: Any = payload
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    if value != expected:
        mismatches.append(f"{label}={value!r}, expected {expected}")
