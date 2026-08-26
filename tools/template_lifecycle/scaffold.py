from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import tomllib

from tools.template_lifecycle.manifest import create_manifest, write_manifest
from tools.template_lifecycle.model import (
    STATE_SCHEMA_VERSION,
    TEMPLATE_ID,
    BaselineState,
    LifecycleError,
    LifecycleState,
    ProductIdentity,
    SelectionState,
    SourceState,
)
from tools.template_lifecycle.source import (
    SEMVER,
    LocalTemplateSource,
    ResolvedTemplateRef,
    resolve_ref,
    resolve_source,
    temporary_worktree,
)
from tools.template_lifecycle.state import (
    BASELINE_RELATIVE_PATH,
    validate_lifecycle_directory,
    write_state,
)

if TYPE_CHECKING:
    from tools.profiles.generator import ScaffoldPlan


@dataclass(frozen=True, slots=True)
class ScaffoldRequest:
    profile: str
    optional_features: tuple[str, ...]
    identity: ProductIdentity
    product_version: str


def request_from_state(state: LifecycleState, product_root: Path) -> ScaffoldRequest:
    return ScaffoldRequest(
        profile=state.selection.profile,
        optional_features=state.selection.optional_features,
        identity=state.identity,
        product_version=read_product_version(product_root),
    )


def reconstruct_scaffold(
    source: LocalTemplateSource,
    template_ref: ResolvedTemplateRef | str,
    request: ScaffoldRequest,
    destination: Path,
) -> Path:
    """Generate a normalized scaffold using the generator at an exact commit."""

    resolved_ref = resolve_ref(source, template_ref) if isinstance(template_ref, str) else template_ref
    _validate_product_version(request.product_version)
    target = destination.expanduser().resolve()
    _validate_destination(source.root, target)

    with temporary_worktree(source, resolved_ref.commit) as checkout:
        control = checkout / "tools" / "control.py"
        if not control.is_file():
            raise LifecycleError(f"Template commit {resolved_ref.commit} does not contain tools/control.py.")
        _run_historical_init(checkout, control, target, request, resolved_ref.commit)

    if not target.is_dir():
        raise LifecycleError("Template generator completed without creating the requested scaffold directory.")
    _remove_reconstructed_state(target)
    normalize_product_identity(target, request.identity)
    normalize_product_version(target, request.product_version)
    return target


def finalize_generated_project(plan: ScaffoldPlan) -> LifecycleState:
    """Record deterministic provenance after the ordinary init transformations."""

    source = resolve_source(plan.project_root)
    target = plan.target_dir.resolve()
    manifest = create_manifest(target)
    identity = ProductIdentity(
        name=plan.identity.name,
        slug=plan.identity.slug,
        identifier=plan.identity.identifier,
        binary=plan.identity.binary,
    )
    provenance = "working-tree" if source.dirty else "generated"
    state = LifecycleState(
        schema_version=STATE_SCHEMA_VERSION,
        repository_kind="product",
        template_id=TEMPLATE_ID,
        provenance=provenance,
        source_dirty=source.dirty,
        source=SourceState(
            url=source.origin,
            version=source.version,
            ref=source.head_commit,
            commit=source.head_commit,
            tree_digest=manifest.digest,
        ),
        selection=SelectionState(
            profile=plan.profile.profile_id,
            optional_features=plan.profile.optional_features,
            resolved_features=plan.profile.features,
        ),
        identity=identity,
        baseline=BaselineState(
            manifest=BASELINE_RELATIVE_PATH,
            digest=manifest.digest,
            applied_migrations=(),
        ),
    )
    validate_lifecycle_directory(target)
    write_manifest(target / BASELINE_RELATIVE_PATH, manifest)
    write_state(target, state)
    return state


def read_product_version(product_root: Path) -> str:
    path = product_root.resolve() / "VERSION"
    try:
        version = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise LifecycleError("Product VERSION is missing or unreadable.") from exc
    _validate_product_version(version)
    return version


def normalize_product_version(root: Path, product_version: str) -> None:
    """Set only product-version mirrors in a reconstructed scratch scaffold."""

    _validate_product_version(product_version)
    resolved_root = root.resolve()
    (resolved_root / "VERSION").write_text(f"{product_version}\n", encoding="utf-8", newline="\n")

    package_path = resolved_root / "frontend" / "package.json"
    if package_path.exists():
        package = _read_json(package_path)
        package["version"] = product_version
        _write_json(package_path, package)

    package_lock_path = resolved_root / "frontend" / "package-lock.json"
    if package_lock_path.exists():
        package_lock = _read_json(package_lock_path)
        package_lock["version"] = product_version
        packages = package_lock.get("packages")
        if isinstance(packages, dict) and isinstance(packages.get(""), dict):
            packages[""]["version"] = product_version
        _write_json(package_lock_path, package_lock)

    tauri_path = resolved_root / "src-tauri" / "tauri.conf.json"
    if tauri_path.exists():
        tauri = _read_json(tauri_path)
        tauri["version"] = product_version
        _write_json(tauri_path, tauri)

    cargo_path = resolved_root / "src-tauri" / "Cargo.toml"
    if not cargo_path.exists():
        return
    cargo_text = cargo_path.read_text(encoding="utf-8")
    package_name = _cargo_package_name(cargo_text, cargo_path)
    cargo_text = _replace_package_field(cargo_text, "version", product_version, path=cargo_path)
    cargo_path.write_text(cargo_text, encoding="utf-8", newline="\n")

    cargo_lock_path = resolved_root / "src-tauri" / "Cargo.lock"
    if cargo_lock_path.exists():
        lock_text = cargo_lock_path.read_text(encoding="utf-8")
        lock_text = _replace_locked_package_field(
            lock_text,
            package_name=package_name,
            field="version",
            value=product_version,
            path=cargo_lock_path,
        )
        cargo_lock_path.write_text(lock_text, encoding="utf-8", newline="\n")


def normalize_product_identity(root: Path, identity: ProductIdentity) -> None:
    """Normalize identity values the historical init CLI could not express."""

    resolved_root = root.resolve()
    tauri_path = resolved_root / "src-tauri" / "tauri.conf.json"
    if tauri_path.exists():
        tauri = _read_json(tauri_path)
        tauri["productName"] = identity.name
        tauri["identifier"] = identity.identifier
        tauri["mainBinaryName"] = identity.binary
        _normalize_window_title(tauri, identity.name)
        _write_json(tauri_path, tauri)

    cargo_path = resolved_root / "src-tauri" / "Cargo.toml"
    if not cargo_path.exists():
        return
    cargo_text = cargo_path.read_text(encoding="utf-8")
    old_name = _cargo_package_name(cargo_text, cargo_path)
    cargo_text = _replace_package_field(cargo_text, "name", identity.binary, path=cargo_path)
    cargo_path.write_text(cargo_text, encoding="utf-8", newline="\n")

    cargo_lock_path = resolved_root / "src-tauri" / "Cargo.lock"
    if cargo_lock_path.exists():
        lock_text = cargo_lock_path.read_text(encoding="utf-8")
        lock_text = _replace_locked_package_field(
            lock_text,
            package_name=old_name,
            field="name",
            value=identity.binary,
            path=cargo_lock_path,
        )
        cargo_lock_path.write_text(lock_text, encoding="utf-8", newline="\n")


def _init_command(control: Path, target: Path, request: ScaffoldRequest) -> list[str]:
    command = [
        sys.executable,
        str(control),
        "init",
        "--profile",
        request.profile,
        "--target-dir",
        str(target),
        "--name",
        request.identity.name,
        "--slug",
        request.identity.slug,
        "--identifier",
        request.identity.identifier,
    ]
    for feature in request.optional_features:
        command.extend(["--with", feature])
    return command


def _run_historical_init(
    checkout: Path,
    control: Path,
    target: Path,
    request: ScaffoldRequest,
    commit: str,
) -> None:
    command = _init_command(control, target, request)
    allowed_environment = {
        "PATH",
        "PATHEXT",
        "SystemRoot",
        "WINDIR",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed_environment}
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(
            command,
            cwd=checkout,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=False,
        )
    except (OSError, UnicodeError) as exc:
        raise LifecycleError(f"Could not run the generator at template commit {commit}: {exc}.") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise LifecycleError(f"Template scaffold reconstruction failed: {detail}.")


def _normalize_window_title(payload: dict[str, Any], product_name: str) -> None:
    app = payload.get("app")
    if not isinstance(app, dict):
        return
    windows = app.get("windows")
    if not isinstance(windows, list):
        return
    for window in windows:
        if isinstance(window, dict) and window.get("label") == "main":
            window["title"] = product_name


def _validate_destination(source_root: Path, destination: Path) -> None:
    if destination == source_root or source_root in destination.parents:
        raise LifecycleError("Scaffold reconstruction destination must be outside the template source checkout.")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise LifecycleError(f"Scaffold reconstruction destination is not empty: {destination}.")


def _remove_reconstructed_state(root: Path) -> None:
    lifecycle_dir = root / ".template"
    if lifecycle_dir.is_symlink():
        lifecycle_dir.unlink()
    elif lifecycle_dir.exists():
        shutil.rmtree(lifecycle_dir)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleError(f"Could not read scaffold JSON metadata {path}: {exc}.") from exc
    if not isinstance(payload, dict):
        raise LifecycleError(f"Scaffold JSON metadata must contain an object: {path}.")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    path.write_text(content, encoding="utf-8", newline="\n")


def _cargo_package_name(content: str, path: Path) -> str:
    try:
        payload = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise LifecycleError(f"Could not parse scaffold Cargo metadata {path}: {exc}.") from exc
    package = payload.get("package")
    name = package.get("name") if isinstance(package, dict) else None
    if not isinstance(name, str) or not name:
        raise LifecycleError(f"Scaffold Cargo metadata has no package name: {path}.")
    return name


def _replace_package_field(content: str, field: str, value: str, *, path: Path) -> str:
    pattern = re.compile(
        rf'(^\[package\]\s*$(?:(?!^\[).)*?^{re.escape(field)}\s*=\s*")[^"]+("\s*$)',
        flags=re.MULTILINE | re.DOTALL,
    )
    updated, replacements = pattern.subn(rf"\g<1>{value}\g<2>", content, count=1)
    if replacements != 1:
        raise LifecycleError(f"Could not locate Cargo package {field} in {path}.")
    return updated


def _replace_locked_package_field(
    content: str,
    *,
    package_name: str,
    field: str,
    value: str,
    path: Path,
) -> str:
    block_pattern = re.compile(
        rf'(^\[\[package\]\]\s*$\s*^name\s*=\s*"{re.escape(package_name)}"\s*$)'
        r"(?P<body>.*?)(?=^\[\[package\]\]\s*$|\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )
    match = block_pattern.search(content)
    if match is None:
        raise LifecycleError(f"Could not locate root package '{package_name}' in {path}.")
    if field == "name":
        replacement = re.sub(
            rf'(^name\s*=\s*"){re.escape(package_name)}("\s*$)',
            rf"\g<1>{value}\g<2>",
            match.group(0),
            count=1,
            flags=re.MULTILINE,
        )
    else:
        replacement, replacements = re.subn(
            rf'(^\s*{re.escape(field)}\s*=\s*")[^"]+("\s*$)',
            rf"\g<1>{value}\g<2>",
            match.group(0),
            count=1,
            flags=re.MULTILINE,
        )
        if replacements != 1:
            raise LifecycleError(f"Could not locate root package {field} in {path}.")
    return content[: match.start()] + replacement + content[match.end() :]


def _validate_product_version(value: str) -> None:
    if not SEMVER.fullmatch(value):
        raise LifecycleError(f"Product VERSION is missing or not valid SemVer: {value or '<empty>'}.")


reconstruct = reconstruct_scaffold
