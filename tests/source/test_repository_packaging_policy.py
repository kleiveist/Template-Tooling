from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = Path("tools/quality/rust_analyzer/dist/rust_quality_analyzer.wasm")


def _git_check_ignore(root: Path, relative: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "git",
            "-c",
            f"core.excludesFile={os.devnull}",
            "check-ignore",
            "-q",
            "--",
            relative.as_posix(),
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )


def test_only_checked_in_rust_analyzer_wasm_is_not_gitignored(
    tmp_path: Path,
) -> None:
    assert (REPOSITORY_ROOT / ARTIFACT).is_file()
    repository_ignore = REPOSITORY_ROOT / ".gitignore"

    isolated_root = tmp_path / "project"
    isolated_root.mkdir()
    shutil.copy2(repository_ignore, isolated_root / ".gitignore")
    isolated_artifact = isolated_root / ARTIFACT
    isolated_artifact.parent.mkdir(parents=True)
    shutil.copy2(REPOSITORY_ROOT / ARTIFACT, isolated_artifact)
    ignored_probe = isolated_root / "frontend" / "dist" / "probe.js"
    ignored_probe.parent.mkdir(parents=True)
    ignored_probe.write_text("ignored build output\n", encoding="utf-8")
    analyzer_probe = isolated_artifact.with_name("probe.map")
    analyzer_probe.write_text("ignored analyzer build output\n", encoding="utf-8")

    initialized = subprocess.run(
        ["git", "init", "--quiet"],
        cwd=isolated_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert initialized.returncode == 0, initialized.stderr

    top_level = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=isolated_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert top_level.returncode == 0, top_level.stderr
    assert Path(top_level.stdout.strip()).resolve() == isolated_root.resolve()

    ignored = _git_check_ignore(isolated_root, ignored_probe.relative_to(isolated_root))
    assert ignored.returncode == 0, ignored.stderr
    analyzer_ignored = _git_check_ignore(
        isolated_root, analyzer_probe.relative_to(isolated_root)
    )
    assert analyzer_ignored.returncode == 0, analyzer_ignored.stderr

    artifact_result = _git_check_ignore(isolated_root, ARTIFACT)
    assert artifact_result.returncode == 1, artifact_result.stderr
    assert not artifact_result.stderr
