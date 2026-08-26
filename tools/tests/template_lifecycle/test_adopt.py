from __future__ import annotations

import subprocess
from pathlib import Path

from tools import control
from tools.template_lifecycle.manifest import load_manifest
from tools.template_lifecycle.state import load_state


def _arguments(lifecycle_fixture, target: Path, *, apply: bool) -> list[str]:
    arguments = [
        "template",
        "adopt",
        "--target-dir",
        str(target),
        "--source-dir",
        str(lifecycle_fixture.source_root),
        "--baseline-ref",
        lifecycle_fixture.v1,
        "--profile",
        "web-only",
        "--name",
        lifecycle_fixture.identity.name,
        "--slug",
        lifecycle_fixture.identity.slug,
        "--identifier",
        lifecycle_fixture.identity.identifier,
    ]
    return [*arguments, "--apply"] if apply else arguments


def _tracked_digest(root: Path) -> str:
    return subprocess.run(
        ["git", "diff", "--no-ext-diff", "--binary"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout


def test_adoption_preview_writes_nothing(lifecycle_fixture, tmp_path: Path) -> None:
    target = lifecycle_fixture.legacy_product(tmp_path / "preview product")

    assert control.main(_arguments(lifecycle_fixture, target, apply=False)) == 0

    assert not (target / ".template").exists()
    assert _tracked_digest(target) == ""


def test_adoption_writes_only_deterministic_lifecycle_metadata(lifecycle_fixture, tmp_path: Path) -> None:
    target = lifecycle_fixture.legacy_product(tmp_path / "adopted product")
    product_before = (target / "managed.txt").read_bytes()

    assert control.main(_arguments(lifecycle_fixture, target, apply=True)) == 0

    state = load_state(target)
    manifest = load_manifest(target / ".template/baseline.json")
    assert state.provenance == "adopted"
    assert state.source.commit == lifecycle_fixture.v1
    assert state.baseline.digest == manifest.digest
    assert (target / "managed.txt").read_bytes() == product_before
    assert _tracked_digest(target) == ""
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=target,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.splitlines()
    assert status == ["?? .template/baseline.json", "?? .template/state.toml"]


def test_dirty_product_blocks_adoption_apply(lifecycle_fixture, tmp_path: Path) -> None:
    target = lifecycle_fixture.legacy_product(tmp_path / "dirty product")
    (target / "untracked.txt").write_text("collision\n", encoding="utf-8")

    assert control.main(_arguments(lifecycle_fixture, target, apply=True)) == 1

    assert not (target / ".template").exists()
