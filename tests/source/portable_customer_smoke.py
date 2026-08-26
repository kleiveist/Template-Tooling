"""Exercise one exported payload as an independent copied customer installation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any


class SmokeFailure(RuntimeError):
    """Raised when the exported customer workflow violates its contract."""


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(root: Path) -> dict[str, tuple[str, str | None]]:
    entries: dict[str, tuple[str, str | None]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise SmokeFailure(f"symbolic link found in fixture: {relative}")
        if stat.S_ISDIR(metadata.st_mode):
            entries[relative] = ("directory", None)
        elif stat.S_ISREG(metadata.st_mode):
            entries[relative] = ("file", _digest(path))
        else:
            raise SmokeFailure(f"unsupported filesystem object in fixture: {relative}")
    return entries


def _environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONHASHSEED"] = "0"
    environment["PYTHONNOUSERSITE"] = "1"
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONPYCACHEPREFIX", None)
    for key in tuple(environment):
        if key.startswith("GIT_"):
            environment.pop(key)
    return environment


def _run(
    project: Path,
    *arguments: str,
    expected: int,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        (sys.executable, "tools/control.py", *arguments),
        cwd=project,
        env=_environment(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
        shell=False,
    )
    if completed.returncode != expected:
        raise SmokeFailure(
            f"{' '.join(arguments)} returned {completed.returncode}, expected {expected}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def _json_result(
    project: Path,
    *arguments: str,
    expected: int,
) -> dict[str, Any]:
    completed = _run(project, *arguments, "--json", expected=expected)
    if completed.stderr:
        raise SmokeFailure(
            f"{' '.join(arguments)} wrote unexpected stderr:\n{completed.stderr}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SmokeFailure(
            f"{' '.join(arguments)} did not emit JSON: {completed.stdout}"
        ) from exc
    if not isinstance(payload, dict):
        raise SmokeFailure(f"{' '.join(arguments)} emitted a non-object JSON value")
    return payload


def _validate_export_shape(export: Path) -> str:
    if export.is_symlink() or not export.is_dir():
        raise SmokeFailure(f"export root must be a real directory: {export}")
    children = sorted(path.name for path in export.iterdir())
    if children != ["docs", "tools"]:
        raise SmokeFailure(f"export root contains unexpected entries: {children}")
    docs_children = sorted(path.name for path in (export / "docs").iterdir())
    if docs_children != ["toolingdocs"]:
        raise SmokeFailure(f"export docs contains unexpected entries: {docs_children}")

    version = (export / "tools" / "VERSION").read_text(encoding="utf-8").strip()
    if export.name != f"Template-Tooling-{version}":
        raise SmokeFailure(
            f"export directory {export.name!r} does not match tooling version {version!r}"
        )
    if not (export / "tools" / "PORTABLE-PAYLOAD.json").is_file():
        raise SmokeFailure("export does not contain its portable payload manifest")

    forbidden_names = {
        ".git",
        ".github",
        ".runtime",
        ".template-tooling-source",
        ".tooling-state",
        ".venv",
        "__pycache__",
        "target",
    }
    forbidden_suffixes = {".aux", ".log", ".pdf", ".pyc", ".tmp"}
    for path in export.rglob("*"):
        relative = path.relative_to(export).as_posix()
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise SmokeFailure(f"export contains a symbolic link: {relative}")
        if any(part.casefold() in forbidden_names for part in path.parts):
            raise SmokeFailure(f"export contains a forbidden path: {relative}")
        if path.is_file() and path.suffix.casefold() in forbidden_suffixes:
            raise SmokeFailure(f"export contains a build artifact: {relative}")
    return version


def run(export: Path, work: Path) -> None:
    """Run the complete copy/check/fix/test/repeat customer proof."""

    export = export.resolve(strict=True)
    version = _validate_export_shape(export)
    if work.exists() or work.is_symlink():
        raise SmokeFailure(f"work directory must not already exist: {work}")
    work.mkdir(parents=True)

    product_files = {
        "customer/data.bin": b"\x00customer-owned\xffpayload",
        "customer/notes.txt": b"portable tooling must preserve this file\n",
    }
    for relative, content in product_files.items():
        target = work / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    product_hashes = {relative: _digest(work / relative) for relative in product_files}

    shutil.copytree(export / "tools", work / "tools", symlinks=True)
    (work / "docs").mkdir()
    shutil.copytree(
        export / "docs" / "toolingdocs",
        work / "docs" / "toolingdocs",
        symlinks=True,
    )
    copied_before_check = _snapshot(work)

    first_check = _json_result(work, "integrate", "--check", expected=1)
    if first_check.get("plan", {}).get("required_changes", 0) <= 0:
        raise SmokeFailure("first integration check did not report required changes")
    if _snapshot(work) != copied_before_check:
        raise SmokeFailure("integrate --check changed the copied project")

    first_fix = _json_result(work, "integrate", "--full-fix", expected=0)
    if first_fix.get("status") != "INTEGRATED":
        raise SmokeFailure("first full-fix did not integrate the exported payload")
    if first_fix.get("tooling_version") != version:
        raise SmokeFailure("first full-fix reported the wrong tooling version")

    before_second_check = _snapshot(work)
    second_check = _json_result(work, "integrate", "--check", expected=0)
    if second_check.get("plan", {}).get("required_changes") != 0:
        raise SmokeFailure("second integration check is not a no-op")
    if _snapshot(work) != before_second_check:
        raise SmokeFailure("integrated check changed the copied project")

    completed_tests = _run(
        work,
        "test",
        "--suite",
        "all",
        expected=0,
        timeout=900,
    )
    if "Overall test status: OK" not in completed_tests.stdout:
        raise SmokeFailure("portable all-suite did not report an OK result")

    before_second_fix = _snapshot(work)
    second_fix = _json_result(work, "integrate", "--full-fix", expected=0)
    if second_fix.get("status") != "INTEGRATED":
        raise SmokeFailure("second full-fix did not report integrated state")
    if second_fix.get("actions") != [] or second_fix.get("report_path") is not None:
        raise SmokeFailure("second full-fix was not an action-free no-op")
    if _snapshot(work) != before_second_fix:
        raise SmokeFailure("second full-fix changed the copied project")

    observed_hashes = {relative: _digest(work / relative) for relative in product_files}
    if observed_hashes != product_hashes:
        raise SmokeFailure("customer-owned product files changed during the workflow")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Exercise an exported payload in an independent customer fixture."
    )
    parser.add_argument("--export-root", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        run(args.export_root, args.work_root)
    except (OSError, SmokeFailure, subprocess.TimeoutExpired) as exc:
        print(f"portable customer smoke failed: {exc}", file=sys.stderr)
        return 1
    print("portable customer smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
