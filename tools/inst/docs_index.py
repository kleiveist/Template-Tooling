from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

from tools import logger

ROOT = Path(__file__).resolve().parents[2]
INDEX_START = "<!-- AUTO-GENERATED:docs-index START -->"
INDEX_END = "<!-- AUTO-GENERATED:docs-index END -->"
BACKLINK_START = "<!-- AUTO-GENERATED:backlink START -->"
BACKLINK_END = "<!-- AUTO-GENERATED:backlink END -->"
SCRIPT_ENV = "PYGITINDEX_PATH"
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

ENGLISH_REPLACEMENTS = {
    "(keine Seiten)": "(no pages)",
    "(keine Markdown-Dateien im Projekt-Root)": "(no Markdown files in the project root)",
}


def _script_candidates(explicit: str | None = None) -> list[Path]:
    candidates: list[Path] = []
    configured = explicit or os.environ.get(SCRIPT_ENV)
    if configured:
        candidates.append(Path(configured).expanduser())

    for command_name in ("PyGitIndex", "PyGitIndex.py", "pygitindex"):
        command = shutil.which(command_name)
        if command:
            candidates.append(Path(command))

    candidates.extend(
        [
            Path.home() / "Dokumente" / "Python" / "bin" / "PyGit" / "PyGitIndex.py",
            Path.home() / "Documents" / "Python" / "bin" / "PyGit" / "PyGitIndex.py",
        ]
    )
    return candidates


def find_script(explicit: str | None = None) -> Path | None:
    if explicit:
        candidate = Path(explicit).expanduser()
        return candidate.resolve() if candidate.is_file() else None
    for candidate in _script_candidates(explicit):
        if candidate.is_file():
            return candidate.resolve()
    return None


def _translate_generated_block(text: str) -> str:
    pattern = re.compile(re.escape(INDEX_START) + r".*?" + re.escape(INDEX_END), re.DOTALL)

    def translate(match: re.Match[str]) -> str:
        block = match.group(0)
        for source, target in ENGLISH_REPLACEMENTS.items():
            block = block.replace(source, target)
        return block

    return pattern.sub(translate, text)


def normalize_generated_english(project_root: Path = ROOT) -> int:
    candidates = [project_root / "README.md", *(project_root / "docs").rglob("*.md")]
    changed = 0
    for path in candidates:
        if not path.is_file():
            continue
        original = path.read_text(encoding="utf-8")
        updated = _translate_generated_block(original)
        if updated == original:
            continue
        path.write_text(updated, encoding="utf-8", newline="\n")
        changed += 1
    return changed


def _generated_block(path: Path, start: str, end: str, issues: list[str]) -> str | None:
    text = path.read_text(encoding="utf-8")
    start_count = text.count(start)
    end_count = text.count(end)
    if start_count != 1 or end_count != 1:
        issues.append(
            f"{path}: expected exactly one '{start}' and one '{end}' marker (found {start_count} and {end_count})"
        )
        return None
    before, remainder = text.split(start, maxsplit=1)
    block, after = remainder.split(end, maxsplit=1)
    if end in before or start in after:
        issues.append(f"{path}: generated navigation markers are out of order")
        return None
    return block


def _resolve_markdown_target(path: Path, target: str, project_root: Path) -> Path | None:
    clean_target = unquote(target.split("#", maxsplit=1)[0].split("?", maxsplit=1)[0])
    if not clean_target or "://" in clean_target or clean_target.startswith(("mailto:", "#")):
        return None
    candidate = (path.parent / clean_target).resolve()
    try:
        candidate.relative_to(project_root.resolve())
    except ValueError:
        return candidate
    return candidate


def _block_targets(path: Path, block: str, project_root: Path, issues: list[str]) -> list[Path]:
    targets: list[Path] = []
    for raw_target in MARKDOWN_LINK.findall(block):
        try:
            target = _resolve_markdown_target(path, raw_target, project_root)
        except (OSError, ValueError):
            issues.append(f"{path}: generated link target is invalid: {raw_target}")
            continue
        if target is None:
            continue
        try:
            target.relative_to(project_root.resolve())
        except ValueError:
            issues.append(f"{path}: generated link escapes the project root: {raw_target}")
            continue
        targets.append(target)
        if not target.is_file():
            issues.append(f"{path}: generated link target does not exist: {raw_target}")
    if len(targets) != len(set(targets)):
        issues.append(f"{path}: generated navigation contains a duplicate link target")
    return targets


def _directory_overview(directory: Path, docs_root: Path) -> Path:
    if directory == docs_root:
        return docs_root / "index.md"
    return directory / f"{directory.name}.md"


def _expected_index_targets(overview: Path, docs_root: Path) -> set[Path]:
    directory = overview.parent
    expected = {path.resolve() for path in directory.glob("*.md") if path.name != "README.md" and path != overview}
    child_directories = sorted(path for path in directory.iterdir() if path.is_dir())
    for child in child_directories:
        child_overview = _directory_overview(child, docs_root)
        if child_overview.is_file():
            expected.add(child_overview.resolve())
        expected.update(
            path.resolve() for path in child.glob("*.md") if path.name != "README.md" and path != child_overview
        )
    return expected


def _expected_backlink(path: Path, docs_root: Path, project_root: Path) -> Path:
    if path == docs_root / "index.md":
        return project_root / "README.md"
    own_overview = _directory_overview(path.parent, docs_root)
    if path != own_overview:
        return own_overview
    parent_directory = path.parent.parent
    return _directory_overview(parent_directory, docs_root)


def _describe_targets(paths: set[Path], project_root: Path) -> str:
    return ", ".join(sorted(path.relative_to(project_root).as_posix() for path in paths))


def _check_backlinks(
    docs_files: list[Path],
    root_pages: list[Path],
    docs_root: Path,
    project_root: Path,
    readme: Path,
) -> list[str]:
    issues: list[str] = []
    for path in docs_files:
        block = _generated_block(path, BACKLINK_START, BACKLINK_END, issues)
        if block is None:
            continue
        actual = set(_block_targets(path, block, project_root, issues))
        expected = _expected_backlink(path, docs_root, project_root).resolve()
        if actual != {expected}:
            issues.append(f"{path}: backlink must target {expected.relative_to(project_root).as_posix()}")

    for path in root_pages:
        block = _generated_block(path, BACKLINK_START, BACKLINK_END, issues)
        if block is None:
            continue
        actual = set(_block_targets(path, block, project_root, issues))
        if actual != {readme.resolve()}:
            issues.append(f"{path}: backlink must target README.md")
    return issues


def _compare_index_targets(
    path: Path,
    actual: set[Path],
    expected: set[Path],
    project_root: Path,
) -> list[str]:
    issues: list[str] = []
    missing = expected - actual
    stale = actual - expected
    if missing:
        issues.append(f"{path}: index is missing {_describe_targets(missing, project_root)}")
    if stale:
        issues.append(f"{path}: index has stale entries {_describe_targets(stale, project_root)}")
    return issues


def _check_overview_indices(
    docs_files: list[Path],
    docs_root: Path,
    project_root: Path,
) -> list[str]:
    issues: list[str] = []
    overview_files = [path for path in docs_files if path == _directory_overview(path.parent, docs_root)]
    for overview in overview_files:
        block = _generated_block(overview, INDEX_START, INDEX_END, issues)
        if block is None:
            continue
        actual = set(_block_targets(overview, block, project_root, issues))
        expected = _expected_index_targets(overview, docs_root)
        if actual != expected:
            issues.extend(_compare_index_targets(overview, actual, expected, project_root))
    return issues


def _check_root_index(
    readme: Path,
    root_pages: list[Path],
    docs_root: Path,
    project_root: Path,
) -> list[str]:
    issues: list[str] = []
    root_block = _generated_block(readme, INDEX_START, INDEX_END, issues)
    docs_index = docs_root / "index.md"
    if root_block is None or not docs_index.is_file():
        return issues
    actual = set(_block_targets(readme, root_block, project_root, issues))
    expected = _expected_index_targets(docs_index, docs_root) | {
        docs_index.resolve(),
        *(path.resolve() for path in root_pages),
    }
    if actual != expected:
        issues.extend(_compare_index_targets(readme, actual, expected, project_root))
    return issues


def _documentation_root(project_root: Path, docs_dir: str) -> Path | None:
    docs_root = (project_root / docs_dir).resolve()
    try:
        docs_root.relative_to(project_root)
    except ValueError:
        logger.fail(f"Documentation directory escapes the project root: {docs_dir}")
        return None
    if not docs_root.is_dir():
        logger.fail(f"Documentation directory does not exist: {docs_dir}")
        return None
    return docs_root


def check(args: argparse.Namespace) -> int:
    project_root = ROOT.resolve()
    docs_root = _documentation_root(project_root, args.docs_dir)
    if docs_root is None:
        return 1
    readme = project_root / "README.md"
    root_pages = sorted(path for path in project_root.glob("*.md") if path != readme)
    docs_files = sorted(docs_root.rglob("*.md"))
    issues = _check_backlinks(docs_files, root_pages, docs_root, project_root, readme)
    issues.extend(_check_overview_indices(docs_files, docs_root, project_root))
    issues.extend(_check_root_index(readme, root_pages, docs_root, project_root))

    if issues:
        for issue in issues:
            logger.fail(issue)
        logger.fail(f"Documentation navigation check failed with {len(issues)} issue(s)")
        return 1
    logger.ok(f"Documentation navigation is consistent across {len(docs_files)} page(s)")
    return 0


def _command_for(script: Path, args: argparse.Namespace) -> list[str]:
    command = [sys.executable, str(script), "--docs-dir", args.docs_dir]
    if args.dry_run:
        command.append("--dry-run")
    if args.force:
        command.append("--force")
    if args.compact:
        command.append("--readme-compact-docs")
    if args.no_backlinks:
        command.extend(["--no-docs-backlinks", "--no-root-backlinks"])
    if args.no_readme:
        command.append("--no-readme")
    return command


def main(args: argparse.Namespace) -> int:
    script = find_script(args.script)
    if script is None:
        logger.fail("PyGitIndex was not found.")
        logger.info("Set PYGITINDEX_PATH or pass '--script /path/to/PyGitIndex.py', then run this command again.")
        return 1

    command = _command_for(script, args)
    logger.info(f"Using PyGitIndex: {script}")
    logger.info(f"Project root: {ROOT}")
    sys.stdout.flush()
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode != 0:
        logger.fail(f"PyGitIndex failed with exit code {completed.returncode}")
        return completed.returncode

    if args.dry_run:
        logger.ok("Documentation index preview completed; no project files were changed")
        return 0

    translated = normalize_generated_english(ROOT)
    if translated:
        logger.info(f"Normalized generated English labels in {translated} file(s)")
    logger.ok("Documentation indices and backlinks are up to date")
    return 0
