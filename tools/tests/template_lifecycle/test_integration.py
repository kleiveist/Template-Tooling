from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import tomllib

from tools import control
from tools.template_lifecycle import service
from tools.template_lifecycle.manifest import load_manifest
from tools.template_lifecycle.migrations import MigrationRegistry
from tools.template_lifecycle.state import load_state


def _command(lifecycle_fixture, target: Path, action: str, *, apply: bool = False) -> list[str]:
    arguments = [
        "template",
        action,
        "--target-dir",
        str(target),
        "--source-dir",
        str(lifecycle_fixture.source_root),
        "--to-ref",
        lifecycle_fixture.v2,
    ]
    return [*arguments, "--apply"] if apply else arguments


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()


def _install_fixture_registry(lifecycle_fixture, monkeypatch) -> MigrationRegistry:
    migration = lifecycle_fixture.migration
    assert migration.preconditions
    assert migration.postconditions
    registry = MigrationRegistry((migration,))
    assert registry.select(
        source_version="1.0.0",
        source_commit=lifecycle_fixture.v1,
        target_version="1.1.0",
        target_commit=lifecycle_fixture.v2,
        applied=(),
    ) == (migration,)
    monkeypatch.setattr(service, "REGISTRY", registry)
    return registry


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _assert_product_metadata_preserved(lifecycle_fixture, target: Path) -> None:
    identity = lifecycle_fixture.identity
    version = lifecycle_fixture.product_version
    state = load_state(target)
    assert state.identity == identity

    package = _json(target / "frontend/package.json")
    package_lock = _json(target / "frontend/package-lock.json")
    lock_root = package_lock["packages"][""]
    assert package["name"] == f"{identity.slug}-frontend"
    assert package_lock["name"] == f"{identity.slug}-frontend"
    assert lock_root["name"] == f"{identity.slug}-frontend"
    assert identity.name in (target / "frontend/index.html").read_text(encoding="utf-8")
    assert identity.name in (target / "frontend/src/main.ts").read_text(encoding="utf-8")

    tauri = _json(target / "src-tauri/tauri.conf.json")
    assert tauri["productName"] == identity.name
    assert tauri["identifier"] == identity.identifier
    assert tauri["mainBinaryName"] == identity.binary
    assert tauri["app"]["windows"][0]["title"] == identity.name
    assert identity.name in (target / "src-tauri/app-icon.svg").read_text(encoding="utf-8")

    cargo = tomllib.loads((target / "src-tauri/Cargo.toml").read_text(encoding="utf-8"))
    cargo_lock = tomllib.loads((target / "src-tauri/Cargo.lock").read_text(encoding="utf-8"))
    locked_root = next(item for item in cargo_lock["package"] if item["name"] == identity.binary)
    assert cargo["package"]["name"] == identity.binary
    assert lifecycle_fixture.backend_service in (target / "backend/app/api/health.py").read_text(encoding="utf-8")

    assert (target / "VERSION").read_text(encoding="utf-8") == f"{version}\n"
    assert package["version"] == version
    assert package_lock["version"] == version
    assert lock_root["version"] == version
    assert tauri["version"] == version
    assert cargo["package"]["version"] == version
    assert locked_root["version"] == version


def test_end_to_end_plan_update_verify_and_repeat_noop(
    lifecycle_fixture,
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    _install_fixture_registry(lifecycle_fixture, monkeypatch)
    target = lifecycle_fixture.managed_product(
        tmp_path / "managed product",
        rename_edit=True,
    )

    assert control.main(_command(lifecycle_fixture, target, "plan")) == 0
    plan_output = capsys.readouterr().out
    assert "MERGE" in plan_output
    assert "MOVE" in plan_output
    assert lifecycle_fixture.renamed_to in plan_output
    assert "template-added.txt" in plan_output
    assert "obsolete.txt" in plan_output
    assert (target / lifecycle_fixture.renamed_from).exists()
    assert not (target / lifecycle_fixture.renamed_to).exists()
    assert _git(target, "status", "--porcelain", "--untracked-files=all") == ""

    assert control.main(_command(lifecycle_fixture, target, "update")) == 0
    assert _git(target, "status", "--porcelain", "--untracked-files=all") == ""

    assert control.main(_command(lifecycle_fixture, target, "update", apply=True)) == 0
    capsys.readouterr()
    assert (target / "managed.txt").read_text(encoding="utf-8") == ("alpha-local\nmiddle\nomega-template\n")
    assert (target / "template-added.txt").read_text(encoding="utf-8") == "new template file\n"
    assert not (target / "obsolete.txt").exists()
    assert not (target / lifecycle_fixture.renamed_from).exists()
    assert (target / lifecycle_fixture.renamed_to).read_text(encoding="utf-8") == (
        "product customization\nunchanged separator\nshared template update\n"
    )
    assert (target / "product-owned.txt").read_text(encoding="utf-8") == "keep product data\n"
    state = load_state(target)
    manifest = load_manifest(target / state.baseline.manifest)
    assert state.source.commit == lifecycle_fixture.v2
    assert state.source.version == "1.1.0"
    assert state.baseline.applied_migrations == (lifecycle_fixture.migration.migration_id,)
    assert state.baseline.digest == manifest.digest
    assert lifecycle_fixture.renamed_from not in manifest.by_path()
    assert lifecycle_fixture.renamed_to in manifest.by_path()
    assert "product-owned.txt" not in manifest.by_path()
    _assert_product_metadata_preserved(lifecycle_fixture, target)
    assert control.main(["template", "verify", "--target-dir", str(target)]) == 0

    _git(target, "add", "--all")
    _git(target, "commit", "--quiet", "-m", "apply template update")
    assert control.main(_command(lifecycle_fixture, target, "update", apply=True)) == 0
    repeat_output = capsys.readouterr().out
    assert "Operations: 0" in repeat_output
    assert _git(target, "status", "--porcelain", "--untracked-files=all") == ""


def test_conflict_blocks_all_product_and_state_changes(
    lifecycle_fixture,
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    _install_fixture_registry(lifecycle_fixture, monkeypatch)
    target = lifecycle_fixture.managed_product(tmp_path / "conflicting product", conflict=True)
    state_before = (target / ".template/state.toml").read_bytes()
    baseline_before = (target / ".template/baseline.json").read_bytes()
    managed_before = (target / "managed.txt").read_bytes()

    assert control.main(_command(lifecycle_fixture, target, "plan")) == 1
    assert "CONFLICT" in capsys.readouterr().out
    assert control.main(_command(lifecycle_fixture, target, "update", apply=True)) == 1
    capsys.readouterr()

    assert (target / "managed.txt").read_bytes() == managed_before
    assert (target / ".template/state.toml").read_bytes() == state_before
    assert (target / ".template/baseline.json").read_bytes() == baseline_before
    assert b"<<<<<<<" not in managed_before
    assert _git(target, "status", "--porcelain", "--untracked-files=all") == ""


def test_migration_destination_collision_is_reported_and_never_applied(
    lifecycle_fixture,
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    _install_fixture_registry(lifecycle_fixture, monkeypatch)
    target = lifecycle_fixture.managed_product(tmp_path / "migration collision product")
    destination = target / lifecycle_fixture.renamed_to
    destination.write_text("product-owned collision\n", encoding="utf-8")
    _git(target, "add", "--all")
    _git(target, "commit", "--quiet", "-m", "add colliding product path")
    state_before = (target / ".template/state.toml").read_bytes()
    baseline_before = (target / ".template/baseline.json").read_bytes()
    source_before = (target / lifecycle_fixture.renamed_from).read_bytes()

    assert control.main(_command(lifecycle_fixture, target, "plan")) == 1
    output = capsys.readouterr().out
    assert "CONFLICT" in output
    assert lifecycle_fixture.renamed_to in output
    assert control.main(_command(lifecycle_fixture, target, "update", apply=True)) == 1
    capsys.readouterr()

    assert destination.read_text(encoding="utf-8") == "product-owned collision\n"
    assert (target / lifecycle_fixture.renamed_from).read_bytes() == source_before
    assert (target / ".template/state.toml").read_bytes() == state_before
    assert (target / ".template/baseline.json").read_bytes() == baseline_before
    assert _git(target, "status", "--porcelain", "--untracked-files=all") == ""
