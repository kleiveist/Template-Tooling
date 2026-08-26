from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from tools.control_parser import build_parser
from tools.tests.test_portable_documentation import (
    MARKDOWN_LINK,
    _documented_commands,
    _link_path,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
README = REPOSITORY_ROOT / "README.md"


def test_root_readme_declares_its_repository_only_export_boundary() -> None:
    readme = README.read_text(encoding="utf-8").casefold()

    assert "repository-only" in readme
    assert "not included" in readme and "export" in readme
    assert "# full-stack project template" not in readme


def test_root_readme_links_resolve_inside_repository() -> None:
    root = REPOSITORY_ROOT.resolve()
    issues: list[str] = []
    for raw_target in MARKDOWN_LINK.findall(README.read_text(encoding="utf-8")):
        target = _link_path(README, raw_target)
        if target is None:
            continue
        try:
            target.relative_to(root)
        except ValueError:
            issues.append(f"README.md escapes root: {raw_target}")
            continue
        if not target.exists():
            issues.append(f"README.md is missing: {raw_target}")

    assert issues == []


def test_root_readme_control_commands_match_parser_and_live_help() -> None:
    commands = _documented_commands(README)
    assert commands

    parser = build_parser()
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    issues: list[str] = []
    for command in commands:
        arguments = shlex.split(command)
        try:
            parser.parse_args(arguments[2:])
        except (ValueError, SystemExit) as exc:
            if not (isinstance(exc, SystemExit) and exc.code == 0):
                issues.append(f"{command}: parser rejected command ({exc})")
                continue
        completed = subprocess.run(
            (
                sys.executable,
                str(REPOSITORY_ROOT / "tools" / "control.py"),
                *arguments[2:],
                "--help",
            ),
            cwd=REPOSITORY_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        output = f"{completed.stdout}\n{completed.stderr}".casefold()
        if completed.returncode != 0 or "usage:" not in output:
            issues.append(
                f"{command}: exit {completed.returncode}; {output.strip()[-500:]}"
            )

    assert issues == []
