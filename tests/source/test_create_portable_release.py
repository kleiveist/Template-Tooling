from __future__ import annotations

import importlib.util
import json
import os
import stat
import tarfile
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPOSITORY_ROOT / ".github" / "scripts" / "create_portable_release.py"


def _release_module() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "create_portable_release", SCRIPT
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _export(parent: Path, version: str = "0.4.0") -> Path:
    root = parent / f"Template-Tooling-{version}"
    (root / "tools").mkdir(parents=True)
    (root / "docs" / "toolingdocs").mkdir(parents=True)
    (root / "tools" / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (root / "tools" / "PORTABLE-PAYLOAD.json").write_text(
        json.dumps({"tooling_version": version}) + "\n",
        encoding="utf-8",
    )
    executable = root / "tools" / "control.py"
    executable.write_text("print('portable')\n", encoding="utf-8")
    executable.chmod(0o755)
    (root / "docs" / "toolingdocs" / "index.md").write_text(
        "# Portable documentation\n",
        encoding="utf-8",
    )
    return root


def test_release_bundle_is_reproducible_and_normalized(tmp_path: Path) -> None:
    module = _release_module()
    first_export = _export(tmp_path / "first")
    second_export = _export(tmp_path / "second")
    for offset, path in enumerate(sorted(second_export.rglob("*")), start=1):
        os.utime(path, (offset, offset), follow_symlinks=False)
    first_output = tmp_path / "first-output"
    second_output = tmp_path / "second-output"
    first_output.mkdir()
    second_output.mkdir()

    first = module.create_release_bundle(first_export, first_output)
    second = module.create_release_bundle(second_export, second_output)

    first_archive = Path(first["archive"])
    second_archive = Path(second["archive"])
    assert first["version"] == "0.4.0"
    assert first["tag"] == "tooling-v0.4.0"
    assert first["sha256"] == second["sha256"]
    assert first_archive.read_bytes() == second_archive.read_bytes()
    assert (first_output / "SHA256SUMS").read_text(encoding="ascii") == (
        f"{first['sha256']}  Template-Tooling-0.4.0.tar.gz\n"
    )

    with tarfile.open(first_archive, "r:gz") as archive:
        members = archive.getmembers()
        assert [member.name for member in members] == [
            "Template-Tooling-0.4.0",
            "Template-Tooling-0.4.0/docs",
            "Template-Tooling-0.4.0/tools",
            "Template-Tooling-0.4.0/docs/toolingdocs",
            "Template-Tooling-0.4.0/tools/PORTABLE-PAYLOAD.json",
            "Template-Tooling-0.4.0/tools/VERSION",
            "Template-Tooling-0.4.0/tools/control.py",
            "Template-Tooling-0.4.0/docs/toolingdocs/index.md",
        ]
        assert all(member.uid == 0 and member.gid == 0 for member in members)
        assert all(member.mtime == 0 for member in members)
        control = archive.getmember("Template-Tooling-0.4.0/tools/control.py")
        assert stat.S_IMODE(control.mode) == 0o755


def test_release_bundle_rejects_objects_outside_the_portable_boundary(
    tmp_path: Path,
) -> None:
    module = _release_module()
    export = _export(tmp_path / "source")
    (export / "README.md").write_text("repository only\n", encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir()

    with pytest.raises(
        module.ReleaseBundleError,
        match="exactly docs/ and tools/",
    ):
        module.create_release_bundle(export, output)


def test_release_bundle_rejects_symlinks_and_existing_outputs(
    tmp_path: Path,
) -> None:
    module = _release_module()
    export = _export(tmp_path / "source")
    output = tmp_path / "output"
    output.mkdir()
    (export / "tools" / "linked.py").symlink_to(export / "tools" / "control.py")

    with pytest.raises(module.ReleaseBundleError, match="refuses symlink"):
        module.create_release_bundle(export, output)

    (export / "tools" / "linked.py").unlink()
    module.create_release_bundle(export, output)
    with pytest.raises(module.ReleaseBundleError, match="already exists"):
        module.create_release_bundle(export, output)
