from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import tomllib

from tools.template_lifecycle.model import (
    STATE_SCHEMA_VERSION,
    TEMPLATE_ID,
    TEMPLATE_URL,
    BaselineState,
    LifecycleError,
    LifecycleState,
    ProductIdentity,
    SelectionState,
    SourceState,
)

STATE_RELATIVE_PATH = ".template/state.toml"
BASELINE_RELATIVE_PATH = ".template/baseline.json"
FULL_SHA = re.compile(r"[0-9a-f]{40}")
SEMVER = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?")
PROVENANCE_VALUES = {"generated", "adopted", "working-tree"}


def load_state(project_root: Path) -> LifecycleState:
    root = project_root.resolve()
    _validate_lifecycle_directory(root, create=False)
    path = root / STATE_RELATIVE_PATH
    try:
        payload = tomllib.loads(_read_regular_text(path, label=f"Lifecycle state {STATE_RELATIVE_PATH}"))
    except tomllib.TOMLDecodeError as exc:
        raise LifecycleError(f"Lifecycle state is invalid TOML: {exc}.") from exc
    if not isinstance(payload, dict):
        raise LifecycleError("Lifecycle state must contain a TOML document table.")
    return _parse_state(payload)


def _parse_state(payload: dict[str, Any]) -> LifecycleState:
    source = _table(payload, "source")
    selection = _table(payload, "selection")
    identity = _table(payload, "identity")
    baseline = _table(payload, "baseline")
    state = LifecycleState(
        schema_version=_integer(payload, "schema_version"),
        repository_kind=_string(payload, "repository_kind"),
        template_id=_string(payload, "template_id"),
        provenance=_string(payload, "provenance"),
        source_dirty=_boolean(payload, "source_dirty"),
        source=SourceState(
            url=_string(source, "url"),
            version=_string(source, "version"),
            ref=_string(source, "ref"),
            commit=_string(source, "commit"),
            tree_digest=_digest(source, "tree_digest"),
        ),
        selection=SelectionState(
            profile=_string(selection, "profile"),
            optional_features=_strings(selection, "optional_features"),
            resolved_features=_strings(selection, "resolved_features"),
        ),
        identity=ProductIdentity(
            name=_string(identity, "name"),
            slug=_string(identity, "slug"),
            identifier=_string(identity, "identifier"),
            binary=_string(identity, "binary"),
        ),
        baseline=BaselineState(
            manifest=_string(baseline, "manifest"),
            digest=_digest(baseline, "digest"),
            applied_migrations=_strings(baseline, "applied_migrations"),
        ),
    )
    validate_state(state)
    return state


def validate_state(state: LifecycleState) -> None:
    if state.schema_version != STATE_SCHEMA_VERSION:
        raise LifecycleError(
            f"Unsupported lifecycle state schema {state.schema_version}; expected {STATE_SCHEMA_VERSION}."
        )
    if state.repository_kind != "product":
        raise LifecycleError("Lifecycle state repository_kind must be 'product'.")
    if state.template_id != TEMPLATE_ID:
        raise LifecycleError(f"Unknown template id '{state.template_id}'; expected '{TEMPLATE_ID}'.")
    if state.provenance not in PROVENANCE_VALUES:
        raise LifecycleError(f"Unsupported provenance value '{state.provenance}'.")
    if state.source_dirty != (state.provenance == "working-tree"):
        raise LifecycleError("source_dirty and working-tree provenance are inconsistent.")
    if state.source.url != TEMPLATE_URL:
        raise LifecycleError("Lifecycle source URL must be the canonical credential-free template URL.")
    if not FULL_SHA.fullmatch(state.source.commit):
        raise LifecycleError("The installed template commit must be a full lowercase 40-character SHA.")
    if not SEMVER.fullmatch(state.source.version):
        raise LifecycleError(f"Installed template version is not SemVer: {state.source.version}.")
    if state.source.ref != state.source.commit:
        raise LifecycleError("Lifecycle state must store the resolved full commit in source.ref.")
    _validate_relative_manifest_path(state.baseline.manifest)
    if state.baseline.manifest != BASELINE_RELATIVE_PATH:
        raise LifecycleError(f"Baseline manifest must be stored at {BASELINE_RELATIVE_PATH}.")
    if state.baseline.digest != state.source.tree_digest:
        raise LifecycleError("Baseline digest and source tree digest do not match.")
    if len(set(state.baseline.applied_migrations)) != len(state.baseline.applied_migrations):
        raise LifecycleError("Lifecycle state contains duplicate applied migration ids.")


def render_state(state: LifecycleState) -> str:
    validate_state(state)
    optional = ", ".join(_quote(item) for item in state.selection.optional_features)
    resolved = ", ".join(_quote(item) for item in state.selection.resolved_features)
    migrations = ", ".join(_quote(item) for item in state.baseline.applied_migrations)
    return "\n".join(
        [
            f"schema_version = {state.schema_version}",
            f"repository_kind = {_quote(state.repository_kind)}",
            f"template_id = {_quote(state.template_id)}",
            f"provenance = {_quote(state.provenance)}",
            f"source_dirty = {'true' if state.source_dirty else 'false'}",
            "",
            "[source]",
            f"url = {_quote(state.source.url)}",
            f"version = {_quote(state.source.version)}",
            f"ref = {_quote(state.source.ref)}",
            f"commit = {_quote(state.source.commit)}",
            f"tree_digest = {_quote(state.source.tree_digest)}",
            "",
            "[selection]",
            f"profile = {_quote(state.selection.profile)}",
            f"optional_features = [{optional}]",
            f"resolved_features = [{resolved}]",
            "",
            "[identity]",
            f"name = {_quote(state.identity.name)}",
            f"slug = {_quote(state.identity.slug)}",
            f"identifier = {_quote(state.identity.identifier)}",
            f"binary = {_quote(state.identity.binary)}",
            "",
            "[baseline]",
            f"manifest = {_quote(state.baseline.manifest)}",
            f"digest = {_quote(state.baseline.digest)}",
            f"applied_migrations = [{migrations}]",
            "",
        ]
    )


def state_digest(state: LifecycleState) -> str:
    return "sha256:" + hashlib.sha256(render_state(state).encode("utf-8")).hexdigest()


def write_state(project_root: Path, state: LifecycleState) -> None:
    lifecycle_dir = validate_lifecycle_directory(project_root)
    path = lifecycle_dir / Path(STATE_RELATIVE_PATH).name
    _atomic_write(path, render_state(state).encode("utf-8"), mode=0o644)


def validate_lifecycle_directory(project_root: Path) -> Path:
    """Create and return a project-local, non-symlink lifecycle directory."""

    return _validate_lifecycle_directory(project_root.resolve(), create=True)


def _validate_lifecycle_directory(root: Path, *, create: bool) -> Path:
    if not root.is_dir():
        raise LifecycleError(f"Project root is not a directory: {root}.")
    lifecycle_dir = root / ".template"
    if lifecycle_dir.is_symlink():
        raise LifecycleError("Lifecycle directory must not be a symbolic link.")
    if lifecycle_dir.exists() and not lifecycle_dir.is_dir():
        raise LifecycleError("Lifecycle path .template must be a directory.")
    if not lifecycle_dir.exists():
        if not create:
            return lifecycle_dir
        lifecycle_dir.mkdir(mode=0o755)
    try:
        resolved = lifecycle_dir.resolve(strict=True)
    except OSError as exc:
        raise LifecycleError("Lifecycle directory could not be resolved safely.") from exc
    if resolved != lifecycle_dir or not resolved.is_relative_to(root):
        raise LifecycleError("Lifecycle directory resolves outside the project root.")
    return lifecycle_dir


def _atomic_write(path: Path, content: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_regular_text(path: Path, *, label: str) -> str:
    """Read one regular file without ever following a symbolic link."""

    descriptor: int | None = None
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise LifecycleError(f"{label} must be a regular file, not a symbolic link.")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_dev != before.st_dev or opened.st_ino != before.st_ino:
            raise LifecycleError(f"{label} changed while it was being opened safely.")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = None
            return handle.read()
    except LifecycleError:
        raise
    except (OSError, UnicodeError) as exc:
        raise LifecycleError(f"{label} is missing or unreadable.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _validate_relative_manifest_path(value: str) -> None:
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        not value
        or "\\" in value
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise LifecycleError("Baseline manifest path is unsafe.")


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _table(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise LifecycleError(f"Lifecycle state must define a [{key}] table.")
    return value


def _string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LifecycleError(f"Lifecycle state field '{key}' must be a non-empty string.")
    return value.strip()


def _integer(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise LifecycleError(f"Lifecycle state field '{key}' must be an integer.")
    return value


def _boolean(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise LifecycleError(f"Lifecycle state field '{key}' must be a boolean.")
    return value


def _strings(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise LifecycleError(f"Lifecycle state field '{key}' must be a list of non-empty strings.")
    return tuple(value)


def _digest(payload: dict[str, Any], key: str) -> str:
    value = _string(payload, key)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise LifecycleError(f"Lifecycle state field '{key}' must be a SHA-256 digest.")
    return value
