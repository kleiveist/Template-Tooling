from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from tools.template_lifecycle.manifest import (
    create_manifest,
    inspect_relative,
    load_manifest,
    manifest_to_dict,
    render_manifest,
    safe_relative_path,
    validate_manifest,
    write_manifest,
)
from tools.template_lifecycle.model import (
    MANIFEST_SCHEMA_VERSION,
    BaselineManifest,
    LifecycleError,
)


def _write_tree(root: Path) -> None:
    (root / "nested").mkdir(parents=True)
    (root / "nested/über.txt").write_text("Grüße\n", encoding="utf-8")
    (root / "alpha.txt").write_text("alpha\n", encoding="utf-8")
    (root / "binary.bin").write_bytes(b"\x00\xffpayload")
    executable = root / "run-tool"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    (root / ".env").write_text("SECRET=not-recorded\n", encoding="utf-8")
    (root / ".env.production").write_text(
        "DATABASE_URL=not-recorded\n",
        encoding="utf-8",
    )
    (root / ".env.example").write_text("SECRET=\n", encoding="utf-8")
    (root / "credentials.json").write_text('{"token": "not-recorded"}\n', encoding="utf-8")
    (root / "customer.sqlite3").write_bytes(b"not-recorded")
    (root / "data").mkdir()
    (root / "data/customer.json").write_text("not-recorded\n", encoding="utf-8")
    (root / ".template").mkdir()
    (root / ".template/state.toml").write_text("ignored\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules/dependency.js").write_text("ignored\n", encoding="utf-8")
    (root / ".ruff_cache").mkdir()
    (root / ".ruff_cache/cache-key").write_text("ignored\n", encoding="utf-8")
    (root / "playwright-report").mkdir()
    (root / "playwright-report/index.html").write_text("ignored\n", encoding="utf-8")
    (root / "test-results").mkdir()
    (root / "test-results/.last-run.json").write_text("ignored\n", encoding="utf-8")


def test_manifest_is_deterministic_sorted_and_content_free(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    _write_tree(first_root)
    _write_tree(second_root)

    first = create_manifest(first_root)
    repeated = create_manifest(first_root)
    second = create_manifest(second_root)
    paths = [entry.path for entry in first.files]

    assert first == repeated == second
    assert paths == sorted(paths)
    assert ".env" not in paths
    assert ".env.production" not in paths
    assert ".env.example" in paths
    assert "credentials.json" not in paths
    assert "customer.sqlite3" not in paths
    assert not any(path.startswith("data/") for path in paths)
    assert not any(path.startswith(".template/") for path in paths)
    assert not any(path.startswith("node_modules/") for path in paths)
    assert not any(path.startswith(".ruff_cache/") for path in paths)
    assert not any(path.startswith("playwright-report/") for path in paths)
    assert not any(path.startswith("test-results/") for path in paths)
    assert first.by_path()["binary.bin"].kind == "binary"
    assert first.by_path()["run-tool"].executable is True
    assert "Grüße" not in render_manifest(first)


def test_manifest_write_load_and_render_are_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "product"
    root.mkdir()
    _write_tree(root)
    manifest = create_manifest(root)
    path = tmp_path / "baseline.json"

    write_manifest(path, manifest)
    first_bytes = path.read_bytes()
    write_manifest(path, manifest)

    assert load_manifest(path) == manifest
    assert path.read_bytes() == first_bytes
    assert path.read_text(encoding="utf-8") == render_manifest(manifest)


def test_inspection_reports_a_file_with_a_missing_parent_as_absent(
    tmp_path: Path,
) -> None:
    root = tmp_path / "product"
    root.mkdir()

    assert inspect_relative(root, "removed-directory/managed.txt") is None


def test_manifest_rejects_schema_digest_path_and_protected_path_tampering(
    tmp_path: Path,
) -> None:
    root = tmp_path / "product"
    root.mkdir()
    (root / "managed.txt").write_text("managed\n", encoding="utf-8")
    manifest = create_manifest(root)
    path = tmp_path / "tampered.json"

    payload = manifest_to_dict(manifest)
    payload["digest"] = "sha256:" + "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LifecycleError, match="digest mismatch"):
        load_manifest(path)

    with pytest.raises(LifecycleError, match="Unsupported baseline manifest schema"):
        validate_manifest(replace(manifest, schema_version=MANIFEST_SCHEMA_VERSION + 1))

    entry = manifest.files[0]
    unsafe = BaselineManifest(
        MANIFEST_SCHEMA_VERSION,
        (replace(entry, path="../outside.txt"),),
        manifest.digest,
    )
    with pytest.raises(LifecycleError, match="Unsafe lifecycle path"):
        validate_manifest(unsafe)

    protected = BaselineManifest(
        MANIFEST_SCHEMA_VERSION,
        (replace(entry, path=".env"),),
        manifest.digest,
    )
    with pytest.raises(LifecycleError, match="protected path"):
        validate_manifest(protected)


@pytest.mark.parametrize(
    "path",
    (
        "../outside.txt",
        "/absolute.txt",
        "C:/outside.txt",
        "nested\\windows.txt",
        ".git/config",
        ".env.local",
        ".env.production",
        "prod.env",
        "credentials.json",
        "secrets/.env.example",
        "private.pem",
        "customer.sqlite3",
        "data/customer.json",
        "uploads/avatar.png",
        "target/output.bin",
        ".ruff_cache/cache-key",
    ),
)
def test_safe_relative_path_rejects_unsafe_or_protected_paths(path: str) -> None:
    with pytest.raises(LifecycleError):
        safe_relative_path(path)


def test_safe_relative_path_allows_env_example_and_database_source_paths() -> None:
    assert safe_relative_path(".env.example") == ".env.example"
    assert safe_relative_path("backend/app/db/models.py") == "backend/app/db/models.py"


def test_manifest_rejects_direct_and_parent_external_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "product"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    secret = external / "secret.txt"
    secret.write_text("outside\n", encoding="utf-8")
    direct_link = root / "outside.txt"
    try:
        direct_link.symlink_to(secret)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")

    with pytest.raises(LifecycleError, match="outside its root"):
        create_manifest(root)

    direct_link.unlink()
    directory_link = root / "linked-directory"
    os.symlink(external, directory_link, target_is_directory=True)
    with pytest.raises(LifecycleError, match="outside its root"):
        create_manifest(root)
    with pytest.raises(LifecycleError, match="resolves outside its root"):
        inspect_relative(root, "linked-directory/secret.txt")


def test_manifest_load_never_follows_manifest_file_symlink(tmp_path: Path) -> None:
    root = tmp_path / "product"
    root.mkdir()
    (root / "managed.txt").write_text("managed\n", encoding="utf-8")
    manifest = create_manifest(root)
    external = tmp_path / "external-baseline.json"
    write_manifest(external, manifest)
    linked = tmp_path / "baseline.json"
    try:
        linked.symlink_to(external)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")

    with pytest.raises(LifecycleError, match="regular file, not a symbolic link"):
        load_manifest(linked)


def test_manifest_rejects_symlink_alias_to_protected_secret(tmp_path: Path) -> None:
    root = tmp_path / "product"
    root.mkdir()
    secret = root / ".env.production"
    secret.write_text("TOKEN=not-recorded\n", encoding="utf-8")
    alias = root / "config.txt"
    try:
        alias.symlink_to(secret.name)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")

    with pytest.raises(LifecycleError, match="protected path"):
        create_manifest(root)
