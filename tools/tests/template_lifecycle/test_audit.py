from __future__ import annotations

import subprocess
from pathlib import Path

from tools import control


def _git_status(root: Path) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout


def test_audit_works_without_state_and_is_read_only(lifecycle_fixture, tmp_path: Path, capsys) -> None:
    target = lifecycle_fixture.legacy_product(tmp_path / "legacy product")
    before = _git_status(target)

    code = control.main(
        [
            "template",
            "audit",
            "--target-dir",
            str(target),
            "--source-dir",
            str(lifecycle_fixture.source_root),
            "--to-ref",
            lifecycle_fixture.v2,
            "--profile",
            "web-only",
            "--name",
            lifecycle_fixture.identity.name,
            "--slug",
            lifecycle_fixture.identity.slug,
            "--identifier",
            lifecycle_fixture.identity.identifier,
        ]
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "Potential conflicts:" in output
    assert "Missing template files:" in output
    assert "Product-owned files:" in output
    assert not (target / ".template").exists()
    assert _git_status(target) == before == ""


def test_audit_json_has_stable_schema(lifecycle_fixture, tmp_path: Path, capsys) -> None:
    target = lifecycle_fixture.legacy_product(tmp_path / "legacy-json")
    code = control.main(
        [
            "template",
            "audit",
            "--target-dir",
            str(target),
            "--source-dir",
            str(lifecycle_fixture.source_root),
            "--to-ref",
            lifecycle_fixture.v2,
            "--profile",
            "web-only",
            "--name",
            lifecycle_fixture.identity.name,
            "--slug",
            lifecycle_fixture.identity.slug,
            "--identifier",
            lifecycle_fixture.identity.identifier,
            "--format",
            "json",
        ]
    )

    import json

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["schema_version"] == 1
    assert payload["repository_kind"] == "legacy"
    assert payload["target_commit"] == lifecycle_fixture.v2
    assert payload["plan"]["schema_version"] == 1
