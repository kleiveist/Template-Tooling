"""Repository-wide pytest classification for CI selection.

The portable payload deliberately does not depend on this source-repository
configuration.  CI collects from the repository root, where paths make the
portable/source ownership boundary explicit without changing individual tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parent
PORTABLE_TEST_ROOT = ("tools", "tests")
SOURCE_TEST_ROOT = ("tests", "source")
CASE_STUDY_TEST_ROOT = ("docs", "toolingdocs", "case-study", "tests")
UNIT_DIRECTORIES = frozenset({"adapters", "core", "quality"})
SYSTEM_TEST_FILES = frozenset(
    {
        "tools/tests/test_process.py",
        "tools/tests/test_run_services.py",
        "tools/tests/test_stop_safety.py",
        "tools/tests/test_tauri_detached_safety.py",
    }
)
SOURCE_INTEGRATION_FILES = frozenset(
    {
        "tests/source/test_historical_tooling_migration.py",
        "tests/source/test_real_quality_integrations.py",
        "tests/source/test_typescript_ast.py",
    }
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Add CI markers from stable repository test locations."""

    for item in items:
        relative = _repository_relative_path(item)
        if relative is None:
            continue
        _add_markers(item, _markers_for(relative))


def _repository_relative_path(item: pytest.Item) -> str | None:
    try:
        return item.path.resolve().relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return None


def _markers_for(relative: str) -> tuple[str, ...]:
    parts = tuple(Path(relative).parts)
    if parts[:2] == PORTABLE_TEST_ROOT:
        return _portable_markers(relative, parts)
    if parts[:2] == SOURCE_TEST_ROOT:
        return _source_markers(relative)
    if parts[:4] == CASE_STUDY_TEST_ROOT:
        return ("portable", "latex")
    return ()


def _portable_markers(relative: str, parts: tuple[str, ...]) -> tuple[str, ...]:
    if parts[2:3] == ("acceptance",):
        return ("portable", "integration", "acceptance")
    if parts[2:3] == ("integration",):
        return ("portable", "integration")
    if parts[2:3] == ("system",) or relative in SYSTEM_TEST_FILES:
        return ("portable", "system")
    if relative == "tools/tests/test_portable_documentation.py":
        return ("portable", "latex")
    if parts[2:3] and parts[2] in UNIT_DIRECTORIES:
        return ("portable", "unit")
    return ("portable", "unit")


def _source_markers(relative: str) -> tuple[str, ...]:
    if relative == "tests/source/test_historical_tooling_migration.py":
        return ("source_repository", "integration", "upgrade")
    if relative in SOURCE_INTEGRATION_FILES:
        return ("source_repository", "integration")
    return ("source_repository", "unit")


def _add_markers(item: pytest.Item, markers: tuple[str, ...]) -> None:
    existing = {marker.name for marker in item.iter_markers()}
    for marker in markers:
        if marker not in existing:
            item.add_marker(marker)
