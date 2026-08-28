from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pytest
import tomllib

from tools.tests.acceptance.test_tooling_replacement import (
    _assert_copied_payload_is_clean,
    _product_hashes,
    _replace_fixture_payload,
    _run_json,
    _tree_snapshot,
    _write_product_sentinels,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TARGET_TOOLING_VERSION = "0.4.0"
PREINTEGRATED_ROOT_ENV = "TEMPLATE_TOOLING_PREINTEGRATED_ROOT"
PREINTEGRATED_EXPORT_ROOT_ENV = "TEMPLATE_TOOLING_PREINTEGRATED_EXPORT_ROOT"


@dataclass(frozen=True, slots=True)
class HistoricalRelease:
    tooling_version: str
    commit: str
    tools_tree: str
    docs_toolingdocs_tree: str

    @property
    def reconciliation_id(self) -> str:
        source = self.tooling_version.replace(".", "-")
        target = TARGET_TOOLING_VERSION.replace(".", "-")
        return f"reconcile-managed-payload-{source}-to-{target}"


HISTORICAL_RELEASES = (
    HistoricalRelease(
        tooling_version="0.1.0",
        commit="30b318c48d8c3d214b208620c8d21f9490136e9b",
        tools_tree="f8e9ed0908b186f2fd08ac24318c08c9d2737a4c",
        docs_toolingdocs_tree="d311d575f7e0f93ad55088ae5b0f20b8ef76ed50",
    ),
    HistoricalRelease(
        tooling_version="0.2.0",
        commit="74400bb7e7460085e5ebec3cdab7bc72e6d2cfed",
        tools_tree="a8cf7f4dafa24232a9069d5e166c6fdde544fbe1",
        docs_toolingdocs_tree="d311d575f7e0f93ad55088ae5b0f20b8ef76ed50",
    ),
    HistoricalRelease(
        tooling_version="0.3.0",
        commit="ee4d4fee50afddb96e3bf3f7d9caf4c060313d05",
        tools_tree="7a02acdfd8ab0ded4a06037892eedd26a34cafdb",
        docs_toolingdocs_tree="c7f4cc774805519526ab9e87e1dbfb062e2c8c55",
    ),
)


def _copy_historical_release(
    project_root: Path,
    release: HistoricalRelease,
) -> None:
    """Materialize one pinned real payload from complete source history."""

    git = shutil.which("git")
    assert git is not None, "git is required for the historical release fixture"
    environment = os.environ.copy()
    for key in tuple(environment):
        if key.startswith("GIT_"):
            environment.pop(key)
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull

    expected_objects = (
        release.commit,
        release.tools_tree,
        release.docs_toolingdocs_tree,
    )
    revisions = (
        f"{release.commit}^{{commit}}",
        f"{release.commit}:tools",
        f"{release.commit}:docs/toolingdocs",
    )
    resolved = subprocess.run(
        [
            git,
            "-c",
            f"safe.directory={REPOSITORY_ROOT}",
            "-C",
            str(REPOSITORY_ROOT),
            "rev-parse",
            *revisions,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )
    assert resolved.returncode == 0, (
        "the local source checkout must contain the complete Git history\n"
        f"stderr:\n{resolved.stderr}"
    )
    assert tuple(resolved.stdout.splitlines()) == expected_objects

    archived = subprocess.run(
        [
            git,
            "-c",
            f"safe.directory={REPOSITORY_ROOT}",
            "-C",
            str(REPOSITORY_ROOT),
            "archive",
            "--format=tar",
            release.commit,
            "tools",
            "docs/toolingdocs",
        ],
        check=False,
        capture_output=True,
        timeout=60,
        env=environment,
    )
    assert archived.returncode == 0, archived.stderr.decode("utf-8", errors="replace")
    with tarfile.open(fileobj=io.BytesIO(archived.stdout), mode="r:") as archive:
        for member in archive.getmembers():
            relative = PurePosixPath(member.name)
            assert not relative.is_absolute()
            assert relative.parts and ".." not in relative.parts
            assert (
                relative.parts[0] == "tools"
                or relative.parts == ("docs",)
                or relative.parts[:2] == ("docs", "toolingdocs")
            )
            target = project_root.joinpath(*relative.parts)
            assert target.resolve(strict=False).is_relative_to(project_root.resolve())
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            assert member.isfile(), f"unsafe archive member: {member.name}"
            source = archive.extractfile(member)
            assert source is not None
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())
            target.chmod(member.mode & 0o777)

    assert (project_root / "tools" / "VERSION").read_text(
        encoding="utf-8"
    ) == f"{release.tooling_version}\n"
    _assert_copied_payload_is_clean(project_root)


def _external_fixture_root(variable: str, *, require_exists: bool) -> Path | None:
    configured = os.environ.get(variable)
    if configured is None:
        return None
    root = Path(configured)
    assert root.is_absolute(), f"{variable} must be an absolute path"
    assert not root.is_symlink(), f"{variable} must not identify a symbolic link"
    resolved = root.resolve(strict=require_exists)
    repository_root = REPOSITORY_ROOT.resolve()
    assert resolved != repository_root
    assert not resolved.is_relative_to(repository_root), (
        f"{variable} must stay outside the source repository"
    )
    if require_exists:
        assert resolved.is_dir() and not resolved.is_symlink(), (
            f"{variable} must identify a regular directory"
        )
    return resolved


def _copy_preintegrated_release(
    project_root: Path,
    release: HistoricalRelease,
    fixture_root: Path,
) -> None:
    source = fixture_root / release.tooling_version
    assert source.is_dir() and not source.is_symlink(), (
        f"missing preintegrated fixture for {release.tooling_version}: {source}"
    )
    assert source.resolve().parent == fixture_root
    assert not any(path.is_symlink() for path in source.rglob("*")), (
        f"preintegrated fixture contains a symbolic link: {source}"
    )
    shutil.copytree(source, project_root)
    _assert_copied_payload_is_clean(project_root)


def _export_preintegrated_release(
    project_root: Path,
    release: HistoricalRelease,
) -> None:
    export_root = _external_fixture_root(
        PREINTEGRATED_EXPORT_ROOT_ENV,
        require_exists=False,
    )
    if export_root is None:
        return
    export_root.mkdir(parents=True, exist_ok=True)
    assert export_root.is_dir() and not export_root.is_symlink()
    target = export_root / release.tooling_version
    assert not target.exists() and not target.is_symlink(), (
        f"refusing to replace preintegrated fixture: {target}"
    )
    shutil.copytree(project_root, target)


@pytest.mark.parametrize(
    "release",
    HISTORICAL_RELEASES,
    ids=lambda release: release.tooling_version,
)
def test_real_historical_payload_upgrades_through_registered_migration(
    tmp_path: Path,
    release: HistoricalRelease,
) -> None:
    assert (REPOSITORY_ROOT / "tools" / "VERSION").read_text(
        encoding="utf-8"
    ) == f"{TARGET_TOOLING_VERSION}\n"
    project_root = tmp_path / (
        f"historical-upgrade-{release.tooling_version.replace('.', '-')}"
    )
    preintegrated_root = _external_fixture_root(
        PREINTEGRATED_ROOT_ENV,
        require_exists=True,
    )
    if preintegrated_root is None:
        assert os.name != "nt", (
            "Windows upgrade evidence requires fixtures produced by the immutable "
            "historical tooling on Linux"
        )
        project_root.mkdir()
        _copy_historical_release(project_root, release)
        _write_product_sentinels(project_root)
        integrated, _ = _run_json(
            project_root,
            "integrate",
            "--full-fix",
            "--json",
        )
        assert integrated["status"] == "INTEGRATED"
        assert integrated["tooling_version"] == release.tooling_version
        _export_preintegrated_release(project_root, release)
    else:
        _copy_preintegrated_release(project_root, release, preintegrated_root)

    assert (project_root / "tools" / "VERSION").read_text(
        encoding="utf-8"
    ) == f"{release.tooling_version}\n"
    product_before = _product_hashes(project_root)
    config = project_root / "project-tooling.toml"
    state = project_root / ".tooling-state" / "state.toml"
    config_before_copy = config.read_bytes()
    state_before_copy = state.read_bytes()

    _replace_fixture_payload(project_root, tmp_path)

    assert (project_root / "tools" / "VERSION").read_text(
        encoding="utf-8"
    ) == f"{TARGET_TOOLING_VERSION}\n"
    assert config.read_bytes() == config_before_copy
    assert state.read_bytes() == state_before_copy
    assert _product_hashes(project_root) == product_before
    tamper_target = (
        project_root / "docs" / "toolingdocs" / "development" / "refactor-inventory.md"
    )
    pristine_payload = tamper_target.read_bytes()
    tamper_target.write_bytes(pristine_payload + b"\nunauthorized payload change\n")
    before_rejected_check = _tree_snapshot(project_root)
    rejected, _ = _run_json(
        project_root,
        "tooling",
        "migrate",
        "--check",
        "--json",
        expected_returncode=1,
    )
    assert "invalid-portable-payload" in {
        conflict["code"] for conflict in rejected["plan"]["conflicts"]
    }
    assert _tree_snapshot(project_root) == before_rejected_check
    tamper_target.write_bytes(pristine_payload)
    payload_before_migration = {
        "tools": _tree_snapshot(project_root / "tools"),
        "docs": _tree_snapshot(project_root / "docs" / "toolingdocs"),
    }

    before_verify = _tree_snapshot(project_root)
    stale, _ = _run_json(
        project_root,
        "tooling",
        "verify",
        "--json",
        expected_returncode=1,
    )
    assert stale["status"] == "VERIFICATION_FAILED"
    assert {conflict["code"] for conflict in stale["plan"]["conflicts"]} == {
        "unverified-managed-tree"
    }
    assert _tree_snapshot(project_root) == before_verify

    before_check = _tree_snapshot(project_root)
    pending, _ = _run_json(
        project_root,
        "tooling",
        "migrate",
        "--check",
        "--json",
        expected_returncode=1,
    )
    assert pending["pending_migrations"] == [release.reconciliation_id]
    assert {item["path"] for item in pending["plan"]["operations"]} == {
        ".tooling-state/state.toml",
        "project-tooling.toml",
    }
    assert _tree_snapshot(project_root) == before_check

    migrated, _ = _run_json(
        project_root,
        "tooling",
        "migrate",
        "--json",
    )
    assert migrated["status"] == "INTEGRATED"
    assert migrated["applied_migrations"] == [release.reconciliation_id]
    assert migrated["report_path"] is not None
    assert _product_hashes(project_root) == product_before
    assert _tree_snapshot(project_root / "tools") == payload_before_migration["tools"]
    assert (
        _tree_snapshot(project_root / "docs" / "toolingdocs")
        == payload_before_migration["docs"]
    )

    config_payload = tomllib.loads(config.read_text(encoding="utf-8"))
    state_payload = tomllib.loads(state.read_text(encoding="utf-8"))
    assert config_payload["tooling"]["version"] == TARGET_TOOLING_VERSION
    assert config_payload["project"]["profile"] == "web-only"
    assert state_payload["tooling_version"] == TARGET_TOOLING_VERSION
    assert state_payload["applied_migrations"] == [release.reconciliation_id]

    before_verified = _tree_snapshot(project_root)
    verified, _ = _run_json(
        project_root,
        "tooling",
        "verify",
        "--json",
    )
    assert verified["status"] == "INTEGRATED"
    assert _tree_snapshot(project_root) == before_verified

    before_noop = _tree_snapshot(project_root)
    second, _ = _run_json(
        project_root,
        "tooling",
        "migrate",
        "--json",
    )
    assert second["status"] == "INTEGRATED"
    assert second["applied_migrations"] == []
    assert second["report_path"] is None
    assert _tree_snapshot(project_root) == before_noop
    assert _product_hashes(project_root) == product_before
