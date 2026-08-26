from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib

from tools.template_lifecycle.manifest import safe_relative_path
from tools.template_lifecycle.model import LifecycleError
from tools.template_lifecycle.state import _atomic_write

MIGRATION_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SUPPORTED_OPERATIONS = {
    "move_path",
    "copy_path",
    "delete_path",
    "rename_key",
    "set_default",
    "transform_json",
    "transform_toml",
    "transform_text",
    "record_notice",
}
SUPPORTED_CONDITIONS = {
    "path_exists",
    "path_missing",
    "json_key_equals",
    "toml_key_equals",
    "text_contains",
}
DATA_FILE_SUFFIXES = {".db", ".duckdb", ".mdb", ".sqlite", ".sqlite3"}
DATA_DIRECTORY_NAMES = {"user-data", "userdata", "uploads"}


@dataclass(frozen=True, slots=True)
class MigrationCondition:
    kind: str
    path: str
    key: str | None = None
    value: Any = None


@dataclass(frozen=True, slots=True)
class MigrationOperation:
    kind: str
    path: str | None = None
    source: str | None = None
    destination: str | None = None
    key: str | None = None
    new_key: str | None = None
    value: Any = None
    old: str | None = None
    new: str | None = None
    notice: str | None = None


@dataclass(frozen=True, slots=True)
class MigrationRange:
    source_versions: tuple[str, ...] = ()
    target_version: str | None = None
    source_commits: tuple[str, ...] = ()
    target_commit: str | None = None


@dataclass(frozen=True, slots=True)
class Migration:
    migration_id: str
    description: str
    order: int
    applies: MigrationRange
    operations: tuple[MigrationOperation, ...]
    preconditions: tuple[MigrationCondition, ...] = ()
    postconditions: tuple[MigrationCondition, ...] = ()
    architecture_change: bool = False


@dataclass(frozen=True, slots=True)
class MigrationRun:
    applied_ids: tuple[str, ...]
    notices: tuple[str, ...]
    moves: tuple[tuple[str, str], ...]
    affected_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _MigrationScope:
    root: Path
    owned_paths: frozenset[str]


class MigrationRegistry:
    def __init__(self, migrations: Iterable[Migration] = ()) -> None:
        ordered = sorted(migrations, key=lambda migration: (migration.order, migration.migration_id))
        identifiers = [migration.migration_id for migration in ordered]
        if len(identifiers) != len(set(identifiers)):
            duplicate = next(identifier for identifier in identifiers if identifiers.count(identifier) > 1)
            raise LifecycleError(f"Duplicate lifecycle migration id: {duplicate}.")
        for migration in ordered:
            _validate_migration(migration)
        self._migrations = tuple(ordered)

    @property
    def migrations(self) -> tuple[Migration, ...]:
        return self._migrations

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(migration.migration_id for migration in self._migrations)

    def select(
        self,
        *,
        source_version: str,
        source_commit: str,
        target_version: str,
        target_commit: str,
        applied: tuple[str, ...],
    ) -> tuple[Migration, ...]:
        applied_set = set(applied)
        return tuple(
            migration
            for migration in self._migrations
            if migration.migration_id not in applied_set
            and _matches(
                migration.applies,
                source_version,
                source_commit,
                target_version,
                target_commit,
            )
        )


REGISTRY = MigrationRegistry()


def run_migrations(
    root: Path,
    migrations: tuple[Migration, ...],
    *,
    owned_paths: Iterable[str],
    already_applied: tuple[str, ...] = (),
) -> MigrationRun:
    scope = _migration_scope(root, owned_paths)
    applied = list(already_applied)
    notices: list[str] = []
    moves: list[tuple[str, str]] = []
    affected_paths: set[str] = set()
    for migration in sorted(migrations, key=lambda item: (item.order, item.migration_id)):
        _validate_migration(migration)
        if migration.migration_id in applied:
            continue
        _check_conditions(
            scope,
            migration.preconditions,
            phase="precondition",
            migration=migration,
        )
        for operation in migration.operations:
            notice, move, paths = _apply_operation(scope, operation)
            if notice:
                notices.append(notice)
            if move:
                moves.append(move)
            affected_paths.update(paths)
        _check_conditions(
            scope,
            migration.postconditions,
            phase="postcondition",
            migration=migration,
        )
        applied.append(migration.migration_id)
    return MigrationRun(
        tuple(applied),
        tuple(notices),
        tuple(moves),
        tuple(sorted(affected_paths)),
    )


def validate_migration_postconditions(
    root: Path,
    migrations: tuple[Migration, ...],
    *,
    owned_paths: Iterable[str],
) -> None:
    """Revalidate migration promises after later staged plan operations."""

    scope = _migration_scope(root, owned_paths)
    for migration in sorted(migrations, key=lambda item: (item.order, item.migration_id)):
        _validate_migration(migration)
        _check_conditions(
            scope,
            migration.postconditions,
            phase="final postcondition",
            migration=migration,
        )


def _validate_migration(migration: Migration) -> None:
    if not MIGRATION_ID.fullmatch(migration.migration_id):
        raise LifecycleError(f"Invalid lifecycle migration id: {migration.migration_id!r}.")
    if not migration.description.strip():
        raise LifecycleError(f"Migration {migration.migration_id} has no description.")
    if not migration.operations:
        raise LifecycleError(f"Migration {migration.migration_id} declares no operations.")
    if not migration.preconditions or not migration.postconditions:
        raise LifecycleError(
            f"Migration {migration.migration_id} must declare non-empty preconditions and postconditions."
        )
    if not (migration.applies.source_versions or migration.applies.source_commits) or not (
        migration.applies.target_version or migration.applies.target_commit
    ):
        raise LifecycleError(f"Migration {migration.migration_id} must declare source and target applicability.")
    for operation in migration.operations:
        if operation.kind not in SUPPORTED_OPERATIONS:
            raise LifecycleError(f"Migration {migration.migration_id} uses unsupported operation '{operation.kind}'.")
    for condition in (*migration.preconditions, *migration.postconditions):
        if condition.kind not in SUPPORTED_CONDITIONS:
            raise LifecycleError(f"Migration {migration.migration_id} uses unsupported condition '{condition.kind}'.")
        safe_relative_path(condition.path)


def _matches(
    applies: MigrationRange,
    source_version: str,
    source_commit: str,
    target_version: str,
    target_commit: str,
) -> bool:
    source_matches = (not applies.source_versions or source_version in applies.source_versions) and (
        not applies.source_commits or source_commit in applies.source_commits
    )
    target_matches = (applies.target_version in {None, target_version}) and (
        applies.target_commit in {None, target_commit}
    )
    return source_matches and target_matches


def _apply_operation(
    scope: _MigrationScope, operation: MigrationOperation
) -> tuple[str | None, tuple[str, str] | None, tuple[str, ...]]:
    if operation.kind == "record_notice":
        if not operation.notice:
            raise LifecycleError("record_notice requires a non-empty notice.")
        return operation.notice, None, ()
    if operation.kind in {"move_path", "copy_path"}:
        source = _required(operation.source, operation.kind, "source")
        destination = _required(operation.destination, operation.kind, "destination")
        _copy_or_move(scope, source, destination, move=operation.kind == "move_path")
        if operation.kind == "move_path":
            return None, (source, destination), (source, destination)
        return None, None, (destination,)
    path = _required(operation.path, operation.kind, "path")
    candidate = _authorized_candidate(
        scope,
        path,
        recursive=operation.kind == "delete_path",
        dereference=operation.kind != "delete_path",
    )
    if operation.kind == "delete_path":
        _delete(candidate)
    elif operation.kind in {"rename_key", "set_default"}:
        _edit_structured(candidate, operation)
    elif operation.kind in {"transform_json", "transform_toml"}:
        _merge_structured(candidate, operation)
    elif operation.kind == "transform_text":
        _transform_text(candidate, operation)
    else:
        raise LifecycleError(f"Unsupported migration operation: {operation.kind}.")
    return None, None, (path,)


def _copy_or_move(
    scope: _MigrationScope,
    source: str,
    destination: str,
    *,
    move: bool,
) -> None:
    source_path = _authorized_candidate(scope, source, recursive=True, dereference=False)
    destination_path = _authorized_candidate(scope, destination, recursive=True, dereference=False)
    if not source_path.exists() and not source_path.is_symlink():
        if move and (destination_path.exists() or destination_path.is_symlink()):
            return
        raise LifecycleError(f"Migration source path does not exist: {source}.")
    if destination_path.exists() or destination_path.is_symlink():
        raise LifecycleError(f"Migration destination already exists: {destination}.")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(str(source_path), str(destination_path))
    elif source_path.is_dir() and not source_path.is_symlink():
        shutil.copytree(source_path, destination_path, symlinks=True)
    elif source_path.is_symlink():
        os.symlink(os.readlink(source_path), destination_path)
    else:
        shutil.copy2(source_path, destination_path)


def _edit_structured(path: Path, operation: MigrationOperation) -> None:
    payload, file_type = _load_structured(path)
    key = _required(operation.key, operation.kind, "key")
    if operation.kind == "rename_key":
        new_key = _required(operation.new_key, operation.kind, "new_key")
        found, value = _pop_dotted(payload, key)
        if found:
            _set_dotted(payload, new_key, value, only_missing=True)
    else:
        _set_dotted(payload, key, operation.value, only_missing=True)
    _write_structured(path, payload, file_type)


def _merge_structured(path: Path, operation: MigrationOperation) -> None:
    payload, file_type = _load_structured(path)
    expected_type = "json" if operation.kind == "transform_json" else "toml"
    if file_type != expected_type:
        raise LifecycleError(f"{operation.kind} requires a .{expected_type} path.")
    if not isinstance(operation.value, dict):
        raise LifecycleError(f"{operation.kind} requires an object value.")
    for key in sorted(operation.value):
        if not isinstance(key, str):
            raise LifecycleError(f"{operation.kind} keys must be strings.")
        _set_dotted(payload, key, operation.value[key], only_missing=False)
    _write_structured(path, payload, file_type)


def _transform_text(path: Path, operation: MigrationOperation) -> None:
    old = _required(operation.old, operation.kind, "old")
    new = operation.new
    if new is None:
        raise LifecycleError("transform_text requires a new value.")
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise LifecycleError(f"Could not read migration text path: {path.name}.") from exc
    if old not in content:
        if new in content:
            return
        raise LifecycleError(f"transform_text marker was not found in {path.name}.")
    _atomic_write(path, content.replace(old, new).encode("utf-8"), mode=_mode(path))


def _load_structured(path: Path) -> tuple[dict[str, Any], str]:
    try:
        content = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            payload = json.loads(content)
            file_type = "json"
        elif path.suffix.lower() == ".toml":
            payload = tomllib.loads(content)
            file_type = "toml"
        else:
            raise LifecycleError(f"Structured migration requires JSON or TOML: {path.name}.")
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        tomllib.TOMLDecodeError,
    ) as exc:
        raise LifecycleError(f"Could not parse migration file {path.name}: {exc}.") from exc
    if not isinstance(payload, dict):
        raise LifecycleError(f"Migration file must contain an object/table: {path.name}.")
    return payload, file_type


def _write_structured(path: Path, payload: dict[str, Any], file_type: str) -> None:
    if file_type == "json":
        content = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    else:
        content = _render_toml(payload)
    _atomic_write(path, content.encode("utf-8"), mode=_mode(path))


def _render_toml(payload: dict[str, Any]) -> str:
    lines: list[str] = []

    def render_table(table: dict[str, Any], prefix: tuple[str, ...]) -> None:
        primitives = [(key, value) for key, value in table.items() if not isinstance(value, dict)]
        children = [(key, value) for key, value in table.items() if isinstance(value, dict)]
        if prefix:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append("[" + ".".join(_toml_key(part) for part in prefix) + "]")
        for key, value in sorted(primitives):
            lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
        for key, value in sorted(children):
            render_table(value, (*prefix, key))

    render_table(payload, ())
    return "\n".join(lines) + "\n"


def _toml_key(value: str) -> str:
    return value if re.fullmatch(r"[A-Za-z0-9_-]+", value) else json.dumps(value, ensure_ascii=False)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise LifecycleError(f"Unsupported TOML migration value: {type(value).__name__}.")


def _check_conditions(
    scope: _MigrationScope,
    conditions: tuple[MigrationCondition, ...],
    *,
    phase: str,
    migration: Migration,
) -> None:
    for condition in conditions:
        dereference = condition.kind not in {"path_exists", "path_missing"}
        path = _authorized_candidate(
            scope,
            condition.path,
            recursive=not dereference,
            dereference=dereference,
        )
        if condition.kind == "path_exists":
            passed = path.exists() or path.is_symlink()
        elif condition.kind == "path_missing":
            passed = not path.exists() and not path.is_symlink()
        elif condition.kind in {"json_key_equals", "toml_key_equals"}:
            payload, file_type = _load_structured(path)
            expected = condition.kind.removesuffix("_key_equals")
            found, value = _get_dotted(payload, condition.key or "")
            passed = file_type == expected and found and value == condition.value
        elif condition.kind == "text_contains":
            passed = isinstance(condition.value, str) and condition.value in _read_condition_text(path, condition.path)
        else:
            raise LifecycleError(f"Unsupported migration condition: {condition.kind}.")
        if not passed:
            raise LifecycleError(
                f"Migration {migration.migration_id} {phase} failed: {condition.kind} {condition.path}."
            )


def _read_condition_text(path: Path, relative: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise LifecycleError(f"Could not read migration condition path: {relative}.") from exc


def _migration_scope(root: Path, owned_paths: Iterable[str]) -> _MigrationScope:
    resolved_root = root.resolve()
    if not resolved_root.is_dir():
        raise LifecycleError("Migration staging root does not exist.")
    owned: set[str] = set()
    for value in owned_paths:
        if not isinstance(value, str):
            raise LifecycleError("Migration ownership paths must be strings.")
        relative = safe_relative_path(value)
        _reject_data_path(relative)
        owned.add(relative)
    return _MigrationScope(resolved_root, frozenset(owned))


def _authorized_candidate(
    scope: _MigrationScope,
    relative: str,
    *,
    recursive: bool,
    dereference: bool,
) -> Path:
    normalized = safe_relative_path(relative)
    _reject_data_path(normalized)
    _reject_symlink_alias(scope.root, normalized, include_leaf=dereference)
    candidate = _candidate(scope.root, normalized)
    if candidate.is_dir() and not candidate.is_symlink() and recursive:
        _authorize_tree(scope, candidate)
    elif candidate.exists() or candidate.is_symlink():
        _require_owned(scope, normalized)
    elif not _owned_covers(scope.owned_paths, normalized):
        raise LifecycleError(f"Migration path is not template-owned: {normalized}.")
    return candidate


def _candidate(root: Path, relative: str) -> Path:
    candidate = root / Path(relative)
    ancestor = candidate if candidate.exists() or candidate.is_symlink() else candidate.parent
    while not ancestor.exists() and ancestor != root:
        ancestor = ancestor.parent
    try:
        resolved_ancestor = ancestor.resolve(strict=True)
    except OSError as exc:
        raise LifecycleError(f"Could not resolve migration path: {relative}.") from exc
    if not resolved_ancestor.is_relative_to(root):
        raise LifecycleError(f"Migration path escapes the staging root: {relative}.")
    return candidate


def _reject_symlink_alias(root: Path, relative: str, *, include_leaf: bool) -> None:
    current = root
    parts = Path(relative).parts
    for index, part in enumerate(parts):
        current /= part
        if current.is_symlink() and (include_leaf or index < len(parts) - 1):
            raise LifecycleError(f"Migration path traverses a symbolic-link alias: {relative}.")


def _authorize_tree(scope: _MigrationScope, directory: Path) -> None:
    relative_root = directory.relative_to(scope.root).as_posix()
    if not _owned_covers(scope.owned_paths, relative_root):
        raise LifecycleError(f"Migration directory is not template-owned: {relative_root}.")
    for current, dirnames, filenames in os.walk(directory, topdown=True, followlinks=False):
        parent = Path(current)
        for name in tuple(dirnames):
            child = parent / name
            relative = child.relative_to(scope.root).as_posix()
            safe_relative_path(relative)
            _reject_data_path(relative)
            if not _owned_covers(scope.owned_paths, relative):
                raise LifecycleError(f"Migration directory contains a product-owned path: {relative}.")
            if child.is_symlink():
                _candidate(scope.root, relative)
                _require_owned(scope, relative)
                dirnames.remove(name)
        for name in filenames:
            child = parent / name
            relative = child.relative_to(scope.root).as_posix()
            safe_relative_path(relative)
            _reject_data_path(relative)
            _candidate(scope.root, relative)
            _require_owned(scope, relative)


def _require_owned(scope: _MigrationScope, relative: str) -> None:
    if relative not in scope.owned_paths:
        raise LifecycleError(f"Migration path is product-owned and cannot be changed: {relative}.")


def _owned_covers(owned: frozenset[str], relative: str) -> bool:
    marker = f"{relative}/"
    return relative in owned or any(path.startswith(marker) for path in owned)


def _reject_data_path(relative: str) -> None:
    path = Path(relative)
    lowered_parts = {part.casefold() for part in path.parts}
    if lowered_parts & DATA_DIRECTORY_NAMES or path.suffix.casefold() in DATA_FILE_SUFFIXES:
        raise LifecycleError(f"Migration path is recognized as product or user data: {relative}.")


def _delete(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _required(value: str | None, kind: str, field: str) -> str:
    if value is None or not value:
        raise LifecycleError(f"{kind} requires {field}.")
    return value


def _mode(path: Path) -> int:
    try:
        return path.stat().st_mode & 0o777
    except OSError:
        return 0o644


def _parts(key: str) -> tuple[str, ...]:
    parts = tuple(key.split("."))
    if not parts or any(not part for part in parts):
        raise LifecycleError(f"Invalid dotted migration key: {key!r}.")
    return parts


def _get_dotted(payload: dict[str, Any], key: str) -> tuple[bool, Any]:
    current: Any = payload
    for part in _parts(key):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _pop_dotted(payload: dict[str, Any], key: str) -> tuple[bool, Any]:
    parts = _parts(key)
    current: Any = payload
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    if not isinstance(current, dict) or parts[-1] not in current:
        return False, None
    return True, current.pop(parts[-1])


def _set_dotted(payload: dict[str, Any], key: str, value: Any, *, only_missing: bool) -> None:
    parts = _parts(key)
    current = payload
    for part in parts[:-1]:
        child = current.setdefault(part, {})
        if not isinstance(child, dict):
            raise LifecycleError(f"Migration key parent is not a table/object: {key}.")
        current = child
    if not only_missing or parts[-1] not in current:
        current[parts[-1]] = value
