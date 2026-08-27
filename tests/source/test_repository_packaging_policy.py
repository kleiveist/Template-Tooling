from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = Path("tools/quality/rust_analyzer/dist/rust_quality_analyzer.wasm")
WORKFLOW_ROOT = REPOSITORY_ROOT / ".github" / "workflows"
PAYLOAD_MANIFEST = REPOSITORY_ROOT / "tools" / "PORTABLE-PAYLOAD.json"


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


def test_hosted_workflows_are_source_only_and_excluded_from_the_payload() -> None:
    workflows = sorted(
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for pattern in ("*.yml", "*.yaml")
        for path in WORKFLOW_ROOT.glob(pattern)
    )

    assert "ci-documentation.yml" in {Path(path).name for path in workflows}
    assert all(path.startswith(".github/workflows/") for path in workflows)

    payload = json.loads(PAYLOAD_MANIFEST.read_text(encoding="utf-8"))
    payload_paths = [entry["path"] for entry in payload["files"]]
    assert payload_paths
    assert all(
        path.startswith(("tools/", "docs/toolingdocs/")) for path in payload_paths
    )
    assert not any(path.startswith(".github/") for path in payload_paths)


def test_source_only_tests_stay_physically_outside_portable_tools_tests() -> None:
    portable_root = REPOSITORY_ROOT / "tools" / "tests"
    source_root = REPOSITORY_ROOT / "tests" / "source"
    portable_sources = tuple(portable_root.rglob("*.py"))
    marker_references = [
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for path in portable_sources
        if ".template-tooling-source" in path.read_text(encoding="utf-8")
    ]

    assert marker_references == ["tools/tests/integration/test_export.py"]
    assert "skipif" not in (REPOSITORY_ROOT / marker_references[0]).read_text(
        encoding="utf-8"
    )
    assert source_root.is_dir()
    assert {path.name for path in source_root.glob("test_*.py")} >= {
        "test_historical_tooling_migration.py",
        "test_real_quality_integrations.py",
        "test_repository_documentation.py",
        "test_repository_packaging_policy.py",
        "test_repository_product_policy.py",
        "test_typescript_ast.py",
    }
    assert (source_root / "portable_customer_smoke.py").is_file()
    assert not (portable_root / "source_repository").exists()
    assert not (portable_root / "quality" / "test_typescript_ast.py").exists()
    assert (source_root / "test_historical_tooling_migration.py").is_file()
    assert (source_root / "test_typescript_ast.py").is_file()
    assert (source_root / "test_repository_documentation.py").is_file()
