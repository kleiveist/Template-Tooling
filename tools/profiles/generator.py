from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.config.loader import load_contract, render_env_example
from tools.profiles.loader import resolve_profile
from tools.profiles.model import ProfileCatalog, ProjectProfile

IGNORED_NAMES = {
    ".git",
    ".generated",
    ".dist",
    ".report",
    ".runtime",
    ".venv",
    ".pytest_cache",
    "__pycache__",
    "coverage",
    "dist",
    "node_modules",
    "playwright-report",
    "target",
    "test-results",
}
REQUIRED_SCAFFOLD_ARTIFACTS = (Path("tools/quality/rust_analyzer/dist/rust_quality_analyzer.wasm"),)
MASTER_ONLY_ROOT_PAGES = ("CODE_OF_CONDUCT.md", "CONTRIBUTING.md")


class GenerationError(RuntimeError):
    """Raised when a scaffold target cannot be prepared safely."""


@dataclass(frozen=True, slots=True)
class ProjectIdentity:
    name: str
    slug: str
    identifier: str
    binary: str


@dataclass(frozen=True, slots=True)
class _SourceIdentity:
    name: str
    slug: str
    binary: str
    service: str


@dataclass(frozen=True, slots=True)
class ScaffoldPlan:
    project_root: Path
    target_dir: Path
    profile: ProjectProfile
    paths: tuple[Path, ...]
    env_example: str
    identity: ProjectIdentity


def build_scaffold_plan(
    catalog: ProfileCatalog,
    *,
    project_root: Path,
    target_dir: Path,
    profile_id: str,
    optional_features: tuple[str, ...] = (),
    project_name: str | None = None,
    project_slug: str | None = None,
    identifier: str | None = None,
) -> ScaffoldPlan:
    root = project_root.resolve()
    target = target_dir.resolve()
    profile = resolve_profile(catalog, profile_id, optional_features=optional_features)
    relative_paths = _ordered_relative_paths(catalog, profile)
    source_paths = tuple(root / relative for relative in relative_paths)
    contract = load_contract(root / "config" / "environment.toml")
    env_example = render_env_example(contract, profile.features)
    identity = resolve_project_identity(
        profile,
        project_name=project_name,
        project_slug=project_slug,
        identifier=identifier,
    )

    _validate_sources(source_paths, root)
    _validate_required_artifacts(root)
    _validate_target(root, target)

    return ScaffoldPlan(
        project_root=root,
        target_dir=target,
        profile=profile,
        paths=source_paths,
        env_example=env_example,
        identity=identity,
    )


def scaffold_project(plan: ScaffoldPlan, *, dry_run: bool = False) -> None:
    if dry_run:
        return

    plan.target_dir.mkdir(parents=True, exist_ok=True)

    for source in plan.paths:
        destination = plan.target_dir / source.relative_to(plan.project_root)
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True, ignore=_ignore_transient_content)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    _copy_required_artifacts(plan)
    _remove_master_only_readme_content(plan.target_dir)
    _write_project_profile(plan.target_dir, plan.profile)
    _write_frontend_profile_module(plan.target_dir, plan.profile)
    _configure_frontend_dependencies(plan.target_dir, plan.profile)
    _configure_env_example(plan.target_dir, plan.env_example)
    _configure_project_identity(plan.target_dir, plan.profile, plan.identity)


def resolve_project_identity(
    profile: ProjectProfile,
    *,
    project_name: str | None,
    project_slug: str | None,
    identifier: str | None,
) -> ProjectIdentity:
    customized = any(value is not None for value in (project_name, project_slug, identifier))
    if not customized:
        return ProjectIdentity(
            "Template Project",
            "template-project",
            "com.example.templateproject",
            "project-template",
        )

    name = (project_name or "").strip()
    if not name:
        if project_slug:
            name = " ".join(part.capitalize() for part in project_slug.split("-"))
        else:
            raise GenerationError("--name is required when customizing project identity.")
    slug = (project_slug or _slugify(name)).strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", slug):
        raise GenerationError("Project slug must use lowercase kebab-case and start with a letter.")

    resolved_identifier = (identifier or "").strip()
    if profile.has_feature("tauri") and not resolved_identifier:
        raise GenerationError("--identifier is required when customizing a Tauri project identity.")
    if resolved_identifier and not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9-]*){2,}",
        resolved_identifier,
    ):
        raise GenerationError("Tauri identifier must be a reverse-domain value such as com.customer.app.")
    if not resolved_identifier:
        resolved_identifier = "com.example.templateproject"
    return ProjectIdentity(name, slug, resolved_identifier, slug)


def _slugify(value: str) -> str:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", value)
    return re.sub(r"[^a-zA-Z0-9]+", "-", separated).strip("-").lower()


def render_project_profile(profile: ProjectProfile) -> str:
    feature_lines = ", ".join(_quoted(value) for value in profile.features)
    optional_lines = ", ".join(_quoted(value) for value in profile.optional_features)
    return (
        f"schema_version = {profile.schema_version}\n"
        f"id = {_quoted(profile.profile_id)}\n"
        f"name = {_quoted(profile.name)}\n"
        f"description = {_quoted(profile.description)}\n"
        f"optional_features = [{optional_lines}]\n"
        f"features = [{feature_lines}]\n"
    )


def render_frontend_profile_module(profile: ProjectProfile) -> str:
    feature_lines = ", ".join(_quoted(value) for value in profile.features)
    return (
        f"export const activeProfileId = {_quoted(profile.profile_id)};\n"
        f"export const activeProfileName = {_quoted(profile.name)};\n"
        f"export const enabledFeatures = [{feature_lines}] as const;\n"
        f"export type ProjectFeature = (typeof enabledFeatures)[number];\n\n"
        "const featureSet = new Set<string>(enabledFeatures);\n\n"
        "export function hasFeature(feature: string): boolean {\n"
        "  return featureSet.has(feature);\n"
        "}\n"
    )


def _quoted(value: str) -> str:
    # JSON string syntax is valid for TOML basic strings and TypeScript literals.
    return json.dumps(value, ensure_ascii=False)


def _ordered_relative_paths(catalog: ProfileCatalog, profile: ProjectProfile) -> tuple[Path, ...]:
    ordered: list[Path] = []
    seen: set[Path] = set()

    def add(relative: str) -> None:
        path = Path(relative)
        if path in seen:
            return
        ordered.append(path)
        seen.add(path)

    for relative in catalog.core_paths:
        add(relative)
    for feature_id in profile.features:
        for relative in catalog.features[feature_id].paths:
            add(relative)

    return tuple(ordered)


def _validate_sources(source_paths: tuple[Path, ...], project_root: Path) -> None:
    outside = [path for path in source_paths if not path.resolve().is_relative_to(project_root)]
    if outside:
        raise GenerationError(f"Scaffold source path resolves outside the template repository: {outside[0]}.")

    missing = [path.relative_to(project_root).as_posix() for path in source_paths if not path.exists()]
    if missing:
        raise GenerationError(f"Scaffold source path(s) are missing: {', '.join(missing)}.")

    for source in source_paths:
        if not source.is_dir():
            continue
        for directory, dirnames, filenames in os.walk(source, topdown=True, followlinks=False):
            parent = Path(directory)
            dirnames[:] = [name for name in dirnames if not _is_ignored_name(name)]
            for name in (*dirnames, *filenames):
                if _is_ignored_name(name):
                    continue
                candidate = parent / name
                if candidate.is_symlink():
                    _validate_symlink(candidate, project_root)


def _validate_required_artifacts(project_root: Path) -> None:
    for relative in REQUIRED_SCAFFOLD_ARTIFACTS:
        artifact = project_root / relative
        if not artifact.is_file():
            raise GenerationError(f"Required scaffold artifact is missing: {relative.as_posix()}.")
        if artifact.is_symlink():
            _validate_symlink(artifact, project_root)


def _copy_required_artifacts(plan: ScaffoldPlan) -> None:
    for relative in REQUIRED_SCAFFOLD_ARTIFACTS:
        destination = plan.target_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(plan.project_root / relative, destination)


def _validate_symlink(path: Path, project_root: Path) -> None:
    try:
        link_target = path.resolve(strict=True)
    except OSError as exc:
        raise GenerationError(f"Scaffold source contains a broken symbolic link: {path}.") from exc
    if not link_target.is_relative_to(project_root):
        raise GenerationError(f"Scaffold source symbolic link points outside the template repository: {path}.")


def _validate_target(project_root: Path, target_dir: Path) -> None:
    if target_dir == project_root:
        raise GenerationError("Refusing to scaffold into the template repository root.")

    if project_root in target_dir.parents:
        relative = target_dir.relative_to(project_root)
        if relative.parts and relative.parts[0] != ".generated":
            raise GenerationError(
                "Refusing to scaffold into an arbitrary subdirectory of the template repository. "
                "Use '.generated/<profile-id>' or an external target directory."
            )

    if target_dir.exists() and any(target_dir.iterdir()):
        raise GenerationError(f"Target directory is not empty: {target_dir}")


def _ignore_transient_content(_directory: str, names: list[str]) -> list[str]:
    return [name for name in names if _is_ignored_name(name)]


def _is_ignored_name(name: str) -> bool:
    return name in IGNORED_NAMES or name.endswith((".pyc", ".pyo"))


def _write_project_profile(target_dir: Path, profile: ProjectProfile) -> None:
    path = target_dir / "project-profile.toml"
    path.write_text(render_project_profile(profile), encoding="utf-8", newline="\n")


def _write_frontend_profile_module(target_dir: Path, profile: ProjectProfile) -> None:
    frontend_dir = target_dir / "frontend" / "src"
    if not frontend_dir.exists():
        return
    module_path = frontend_dir / "project-profile.ts"
    module_path.write_text(render_frontend_profile_module(profile), encoding="utf-8", newline="\n")


def _configure_frontend_dependencies(target_dir: Path, profile: ProjectProfile) -> None:
    if profile.has_feature("tauri"):
        return

    package_path = target_dir / "frontend" / "package.json"
    if not package_path.exists():
        return

    package = _read_json_object(package_path)
    scripts = package.get("scripts")
    if isinstance(scripts, dict):
        scripts.pop("tauri", None)
    dev_dependencies = package.get("devDependencies")
    if isinstance(dev_dependencies, dict):
        dev_dependencies.pop("@tauri-apps/cli", None)
    _write_json(package_path, package)

    lock_path = target_dir / "frontend" / "package-lock.json"
    if not lock_path.exists():
        return

    lock = _read_json_object(lock_path)
    packages = lock.get("packages")
    if isinstance(packages, dict):
        root_package = packages.get("")
        if isinstance(root_package, dict):
            root_dev_dependencies = root_package.get("devDependencies")
            if isinstance(root_dev_dependencies, dict):
                root_dev_dependencies.pop("@tauri-apps/cli", None)
        for key in list(packages):
            if key == "node_modules/@tauri-apps/cli" or key.startswith("node_modules/@tauri-apps/cli-"):
                del packages[key]
    dependencies = lock.get("dependencies")
    if isinstance(dependencies, dict):
        dependencies.pop("@tauri-apps/cli", None)
    _write_json(lock_path, lock)


def _configure_env_example(target_dir: Path, content: str) -> None:
    path = target_dir / ".env.example"
    path.write_text(content, encoding="utf-8", newline="\n")


def _remove_master_only_readme_content(target_dir: Path) -> None:
    path = target_dir / "README.md"
    if not path.exists():
        return

    content = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"^<!-- MASTER-ONLY START -->\n.*?^<!-- MASTER-ONLY END -->\n?",
        flags=re.MULTILINE | re.DOTALL,
    )
    content = pattern.sub("", content)
    for root_page in MASTER_ONLY_ROOT_PAGES:
        index_entry = re.compile(rf"^- .*\]\({re.escape(root_page)}(?:#[^)]*)?\)\n?", flags=re.MULTILINE)
        content = index_entry.sub("", content)
    path.write_text(content, encoding="utf-8", newline="\n")


def _source_identity(target_dir: Path) -> _SourceIdentity:
    source_name = "Template Project"
    source_slug = "template-project"
    source_binary = "project-template"
    existing_package_path = target_dir / "frontend" / "package.json"
    if existing_package_path.exists():
        existing_package = _read_json_object(existing_package_path)
        package_name = existing_package.get("name")
        if isinstance(package_name, str) and package_name.endswith("-frontend"):
            source_slug = package_name.removesuffix("-frontend")

    existing_index_path = target_dir / "frontend" / "index.html"
    if existing_index_path.exists():
        title_match = re.search(r"<title>([^<]+)</title>", existing_index_path.read_text(encoding="utf-8"))
        if title_match:
            source_name = title_match.group(1)

    existing_tauri_path = target_dir / "src-tauri" / "tauri.conf.json"
    if existing_tauri_path.exists():
        existing_tauri = _read_json_object(existing_tauri_path)
        product_name = existing_tauri.get("productName")
        binary_name = existing_tauri.get("mainBinaryName")
        if isinstance(product_name, str) and product_name.strip():
            source_name = product_name
        if isinstance(binary_name, str) and binary_name.strip():
            source_binary = binary_name

    source_service = f"{source_slug}-backend"
    existing_health_path = target_dir / "backend" / "app" / "api" / "health.py"
    if existing_health_path.exists():
        service_match = re.search(
            r'"service"\s*:\s*"([^"]+)"',
            existing_health_path.read_text(encoding="utf-8"),
        )
        if service_match:
            source_service = service_match.group(1)
    return _SourceIdentity(source_name, source_slug, source_binary, source_service)


def _configure_project_identity(
    target_dir: Path,
    profile: ProjectProfile,
    identity: ProjectIdentity,
) -> None:
    source = _source_identity(target_dir)

    package_path = target_dir / "frontend" / "package.json"
    if package_path.exists():
        package = _read_json_object(package_path)
        package["name"] = f"{identity.slug}-frontend"
        _write_json(package_path, package)

    lock_path = target_dir / "frontend" / "package-lock.json"
    if lock_path.exists():
        lock = _read_json_object(lock_path)
        lock["name"] = f"{identity.slug}-frontend"
        packages = lock.get("packages")
        if isinstance(packages, dict) and isinstance(packages.get(""), dict):
            packages[""]["name"] = f"{identity.slug}-frontend"
        _write_json(lock_path, lock)

    _replace_text(target_dir / "frontend" / "index.html", source.name, identity.name)
    _replace_text(target_dir / "frontend" / "src" / "main.ts", source.name, identity.name)

    if profile.has_feature("backend"):
        _replace_text(target_dir / ".env.example", f"{source.name} API", f"{identity.name} API")
        _replace_text(target_dir / "config" / "environment.toml", f"{source.name} API", f"{identity.name} API")
        _replace_text(
            target_dir / "backend" / "app" / "config" / "settings.py",
            f"{source.name} API",
            f"{identity.name} API",
        )
        _replace_text(
            target_dir / "backend" / "app" / "api" / "health.py",
            source.service,
            f"{identity.slug}-backend",
        )
        _replace_text(
            target_dir / "backend" / "tests" / "api" / "test_health.py",
            source.service,
            f"{identity.slug}-backend",
        )

    _replace_text(
        target_dir / "tools" / "inst" / "build.py",
        f"{source.slug}-web.zip",
        f"{identity.slug}-web.zip",
    )

    if profile.has_feature("cloud"):
        _replace_text(target_dir / "deployment" / "compose.yaml", source.slug, identity.slug)
        _replace_text(target_dir / "deployment" / "compose.yaml", source.name, identity.name)

    if not profile.has_feature("tauri"):
        return

    tauri_path = target_dir / "src-tauri" / "tauri.conf.json"
    tauri = _read_json_object(tauri_path)
    tauri["productName"] = identity.name
    tauri["identifier"] = identity.identifier
    tauri["mainBinaryName"] = identity.binary
    app = tauri.get("app")
    if isinstance(app, dict):
        windows = app.get("windows")
        if isinstance(windows, list):
            for window in windows:
                if isinstance(window, dict) and window.get("label") == "main":
                    window["title"] = identity.name
    _write_json(tauri_path, tauri)

    cargo_path = target_dir / "src-tauri" / "Cargo.toml"
    _replace_first(cargo_path, f'name = "{source.binary}"', f'name = "{identity.binary}"')
    _replace_text(cargo_path, f"{source.name} Contributors", f"{identity.name} Contributors")
    cargo_lock_path = target_dir / "src-tauri" / "Cargo.lock"
    _replace_first(cargo_lock_path, f'name = "{source.binary}"', f'name = "{identity.binary}"')
    _replace_text(target_dir / "src-tauri" / "app-icon.svg", source.name, identity.name)


def _replace_text(path: Path, old: str, new: str) -> None:
    if not path.exists():
        return
    content = path.read_text(encoding="utf-8")
    path.write_text(content.replace(old, new), encoding="utf-8", newline="\n")


def _replace_first(path: Path, old: str, new: str) -> None:
    if not path.exists():
        return
    content = path.read_text(encoding="utf-8")
    if old not in content:
        raise GenerationError(f"Expected identity marker not found in {path}: {old}")
    path.write_text(content.replace(old, new, 1), encoding="utf-8", newline="\n")


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GenerationError(f"Could not read generated JSON file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise GenerationError(f"Generated JSON file must contain an object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
