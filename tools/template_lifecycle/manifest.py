from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterator
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from tools.template_lifecycle.model import (
    MANIFEST_SCHEMA_VERSION,
    BaselineManifest,
    LifecycleError,
    ManifestEntry,
)
from tools.template_lifecycle.state import _atomic_write, _read_regular_text

IGNORED_DIRECTORIES = {
    ".git",
    ".template",
    ".venv",
    "node_modules",
    "target",
    "dist",
    "coverage",
    "playwright-report",
    "test-results",
    ".generated",
    ".dist",
    ".report",
    ".runtime",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
}
IGNORED_FILE_NAMES = {".env", ".env.local"}
SENSITIVE_ROOT_DIRECTORIES = {
    ".data",
    "data",
    "storage",
    "uploads",
    "user-data",
    "user_data",
    "userdata",
}
SENSITIVE_DIRECTORIES = {".secrets", "credentials", "secrets"}
SENSITIVE_FILE_NAMES = {
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "credentials.toml",
    "credentials.yaml",
    "credentials.yml",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "secrets.json",
    "secrets.toml",
    "secrets.yaml",
    "secrets.yml",
    "service-account.json",
    "service_account.json",
}
SENSITIVE_FILE_SUFFIXES = {
    ".db",
    ".jks",
    ".key",
    ".keystore",
    ".p12",
    ".pem",
    ".pfx",
    ".sqlite",
    ".sqlite3",
}


def create_manifest(root: Path) -> BaselineManifest:
    resolved_root = root.resolve()
    if not resolved_root.is_dir():
        raise LifecycleError(f"Manifest root is not a directory: {root}.")
    entries = tuple(_entry_for_path(resolved_root, relative) for relative in _iter_relative_paths(resolved_root))
    digest = _manifest_digest(entries)
    return BaselineManifest(MANIFEST_SCHEMA_VERSION, entries, digest)


def write_manifest(path: Path, manifest: BaselineManifest) -> None:
    validate_manifest(manifest)
    _atomic_write(path, render_manifest(manifest).encode("utf-8"), mode=0o644)


def render_manifest(manifest: BaselineManifest) -> str:
    validate_manifest(manifest)
    return json.dumps(manifest_to_dict(manifest), indent=2, ensure_ascii=False) + "\n"


def manifest_to_dict(manifest: BaselineManifest) -> dict[str, Any]:
    return {
        "schema_version": manifest.schema_version,
        "files": [_entry_to_dict(entry) for entry in manifest.files],
        "digest": manifest.digest,
    }


def load_manifest(path: Path) -> BaselineManifest:
    try:
        payload = json.loads(_read_regular_text(path, label="Baseline manifest"))
    except json.JSONDecodeError as exc:
        raise LifecycleError(f"Baseline manifest is invalid JSON: {exc}.") from exc
    if not isinstance(payload, dict):
        raise LifecycleError("Baseline manifest must contain a JSON object.")
    schema_version = payload.get("schema_version")
    raw_files = payload.get("files")
    digest = payload.get("digest")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise LifecycleError("Baseline manifest schema_version must be an integer.")
    if not isinstance(raw_files, list):
        raise LifecycleError("Baseline manifest files must be a list.")
    if not isinstance(digest, str):
        raise LifecycleError("Baseline manifest digest must be a string.")
    entries = tuple(_parse_entry(item) for item in raw_files)
    manifest = BaselineManifest(schema_version, entries, digest)
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest: BaselineManifest) -> None:
    if manifest.schema_version != MANIFEST_SCHEMA_VERSION:
        raise LifecycleError(
            f"Unsupported baseline manifest schema {manifest.schema_version}; expected {MANIFEST_SCHEMA_VERSION}."
        )
    paths = [entry.path for entry in manifest.files]
    if paths != sorted(paths):
        raise LifecycleError("Baseline manifest paths must be sorted alphabetically.")
    if len(paths) != len(set(paths)):
        raise LifecycleError("Baseline manifest contains duplicate paths.")
    for entry in manifest.files:
        _validate_relative_path(entry.path)
        if _is_ignored_relative(entry.path):
            raise LifecycleError(f"Baseline manifest contains protected path: {entry.path}.")
        if entry.kind not in {"text", "binary", "symlink"}:
            raise LifecycleError(f"Unsupported manifest file kind '{entry.kind}' for {entry.path}.")
        if not _valid_raw_sha(entry.sha256):
            raise LifecycleError(f"Invalid file SHA-256 for {entry.path}.")
        if entry.size < 0:
            raise LifecycleError(f"Invalid negative file size for {entry.path}.")
    expected = _manifest_digest(manifest.files)
    if manifest.digest != expected:
        raise LifecycleError(f"Baseline manifest digest mismatch: expected {expected}, got {manifest.digest}.")


def inspect_relative(root: Path, relative: str) -> ManifestEntry | None:
    _validate_relative_path(relative)
    resolved_root = root.resolve()
    candidate = resolved_root / Path(relative)
    _validate_candidate_parent(resolved_root, candidate, relative)
    if not candidate.exists() and not candidate.is_symlink():
        return None
    return _entry_for_path(resolved_root, relative)


def project_owned_paths(root: Path, manifest: BaselineManifest) -> tuple[str, ...]:
    managed = set(manifest.by_path())
    return tuple(path for path in _iter_relative_paths(root.resolve()) if path not in managed)


def validate_project_symlinks(root: Path) -> None:
    """Reject external, broken, or protected-target symlinks without reading files."""

    resolved_root = root.resolve()
    if not resolved_root.is_dir():
        raise LifecycleError("Product root is not a directory.")
    tuple(_iter_relative_paths(resolved_root))


def safe_relative_path(value: str) -> str:
    _validate_relative_path(value)
    if _is_ignored_relative(value):
        raise LifecycleError(f"Lifecycle operation targets protected path: {value}.")
    return value


def _iter_relative_paths(root: Path) -> Iterator[str]:
    pending = [root]
    collected: list[str] = []
    while pending:
        directory = pending.pop()
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name, reverse=True)
        except OSError as exc:
            raise LifecycleError(f"Could not scan lifecycle tree: {directory}.") from exc
        for child in children:
            path = Path(child.path)
            relative = path.relative_to(root).as_posix()
            if child.is_symlink():
                _validate_symlink(path, root)
                if not _is_ignored_relative(relative):
                    collected.append(relative)
            elif _is_ignored_relative(relative):
                continue
            elif child.is_dir(follow_symlinks=False):
                pending.append(path)
            elif child.is_file(follow_symlinks=False):
                collected.append(relative)
    yield from sorted(collected)


def _entry_for_path(root: Path, relative: str) -> ManifestEntry:
    _validate_relative_path(relative)
    path = root / Path(relative)
    _validate_candidate_parent(root, path, relative)
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            _validate_symlink(path, root)
            raw = os.readlink(path).encode("utf-8")
            kind = "symlink"
            executable = False
        elif stat.S_ISREG(metadata.st_mode):
            raw = path.read_bytes()
            kind = _file_kind(raw)
            executable = bool(metadata.st_mode & 0o111)
        else:
            raise LifecycleError(f"Unsupported filesystem entry in lifecycle tree: {relative}.")
    except OSError as exc:
        raise LifecycleError(f"Could not inspect lifecycle path: {relative}.") from exc
    return ManifestEntry(
        path=relative,
        sha256=hashlib.sha256(raw).hexdigest(),
        size=len(raw),
        kind=kind,
        executable=executable,
    )


def _validate_candidate_parent(root: Path, path: Path, relative: str) -> None:
    try:
        resolved_parent = path.parent.resolve(strict=False)
    except OSError as exc:
        raise LifecycleError(f"Lifecycle path parent could not be resolved safely: {relative}.") from exc
    if not resolved_parent.is_relative_to(root):
        raise LifecycleError(f"Lifecycle path resolves outside its root through a symbolic link: {relative}.")


def _validate_symlink(path: Path, root: Path) -> None:
    try:
        target = path.resolve(strict=True)
    except OSError as exc:
        raise LifecycleError(f"Lifecycle tree contains a broken symbolic link: {path.relative_to(root)}.") from exc
    if not target.is_relative_to(root):
        raise LifecycleError(f"Lifecycle tree symbolic link points outside its root: {path.relative_to(root)}.")
    target_relative = target.relative_to(root).as_posix()
    if target_relative != "." and _is_ignored_relative(target_relative):
        raise LifecycleError(f"Lifecycle tree symbolic link points to a protected path: {path.relative_to(root)}.")


def _file_kind(raw: bytes) -> str:
    if b"\0" in raw:
        return "binary"
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return "binary"
    return "text"


def _manifest_digest(entries: tuple[ManifestEntry, ...]) -> str:
    canonical = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "files": [_entry_to_dict(entry) for entry in entries],
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _entry_to_dict(entry: ManifestEntry) -> dict[str, Any]:
    return {
        "path": entry.path,
        "sha256": entry.sha256,
        "size": entry.size,
        "kind": entry.kind,
        "executable": entry.executable,
    }


def _parse_entry(value: Any) -> ManifestEntry:
    if not isinstance(value, dict):
        raise LifecycleError("Every baseline manifest file entry must be an object.")
    path = value.get("path")
    sha256 = value.get("sha256")
    size = value.get("size")
    kind = value.get("kind")
    executable = value.get("executable", False)
    if not isinstance(path, str) or not isinstance(sha256, str) or not isinstance(kind, str):
        raise LifecycleError("Baseline manifest file fields path, sha256, and kind must be strings.")
    if not isinstance(size, int) or isinstance(size, bool):
        raise LifecycleError(f"Baseline manifest size must be an integer for {path}.")
    if not isinstance(executable, bool):
        raise LifecycleError(f"Baseline manifest executable must be boolean for {path}.")
    return ManifestEntry(path, sha256, size, kind, executable)


def _validate_relative_path(value: str) -> None:
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
        raise LifecycleError(f"Unsafe lifecycle path: {value!r}.")


def _is_ignored_relative(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    if any(part in IGNORED_DIRECTORIES for part in parts):
        return True
    lowered = tuple(part.casefold() for part in parts)
    name = lowered[-1]
    if lowered[0] in SENSITIVE_ROOT_DIRECTORIES:
        return True
    if any(part in SENSITIVE_DIRECTORIES for part in lowered):
        return True
    if name == ".env.example":
        return False
    if name.startswith(".env") or name.endswith(".env"):
        return True
    if name in IGNORED_FILE_NAMES or name in SENSITIVE_FILE_NAMES:
        return True
    return any(name.endswith(suffix) for suffix in SENSITIVE_FILE_SUFFIXES)


def _valid_raw_sha(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
