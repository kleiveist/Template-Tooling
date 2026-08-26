from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.template_lifecycle.model import TEMPLATE_URL, LifecycleError
from tools.template_lifecycle.source import (
    assert_ancestor,
    normalize_origin,
    resolve_ref,
    resolve_source,
    temporary_worktree,
    working_tree_status,
)


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _template_repository(tmp_path: Path, *, origin: str = TEMPLATE_URL) -> tuple[Path, str, str]:
    root = tmp_path / "template repo ütf8"
    root.mkdir(parents=True)
    _git(root, "init", "--quiet", "--initial-branch=main")
    _git(root, "config", "user.name", "Lifecycle Tests")
    _git(root, "config", "user.email", "lifecycle@example.invalid")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "remote", "add", "origin", origin)
    (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    (root / "fixture.txt").write_text("first\n", encoding="utf-8")
    first = _commit(root, "template v1")
    (root / "VERSION").write_text("1.1.0\n", encoding="utf-8")
    (root / "fixture.txt").write_text("second\n", encoding="utf-8")
    second = _commit(root, "template v2")
    return root, first, second


@pytest.mark.parametrize(
    "origin",
    [
        "https://github.com/kleiveist/Template-Projekte.git",
        "ssh://git@github.com/kleiveist/Template-Projekte.git",
        "git@github.com:kleiveist/Template-Projekte.git",
    ],
)
def test_supported_origins_normalize_to_canonical_identity(origin: str) -> None:
    template_id, canonical = normalize_origin(origin)

    assert template_id == "kleiveist/template-projekte"
    assert canonical == TEMPLATE_URL


def test_source_resolves_refs_versions_and_exact_status(tmp_path: Path) -> None:
    root, first, second = _template_repository(tmp_path)

    source = resolve_source(root)
    old = resolve_ref(source, first)
    current = resolve_ref(source, "main")

    assert source.root == root.resolve()
    assert source.origin == TEMPLATE_URL
    assert source.head_commit == second
    assert source.version == "1.1.0"
    assert source.dirty is False
    assert source.status == ""
    assert old.commit == first
    assert old.version == "1.0.0"
    assert current.requested == "main"
    assert current.commit == second
    assert current.version == "1.1.0"

    (root / "untracked file ü.txt").write_text("local\n", encoding="utf-8")
    dirty = resolve_source(root)
    assert dirty.dirty is True
    assert "??" in dirty.status
    assert working_tree_status(dirty) == dirty.status


def test_source_rejects_wrong_or_missing_origin_and_nested_path(tmp_path: Path) -> None:
    wrong_root, _first, _second = _template_repository(
        tmp_path / "wrong",
        origin="https://github.com/example/other.git",
    )
    with pytest.raises(LifecycleError, match="expected 'kleiveist/template-projekte'"):
        resolve_source(wrong_root)

    missing_root, _first, _second = _template_repository(tmp_path / "missing")
    _git(missing_root, "remote", "remove", "origin")
    with pytest.raises(LifecycleError, match="no remote.origin.url"):
        resolve_source(missing_root)

    valid_root, _first, _second = _template_repository(tmp_path / "nested")
    nested = valid_root / "nested"
    nested.mkdir()
    with pytest.raises(LifecycleError, match="repository root"):
        resolve_source(nested)


def test_missing_ref_and_non_descendant_target_are_rejected(tmp_path: Path) -> None:
    root, first, second = _template_repository(tmp_path)
    source = resolve_source(root)

    with pytest.raises(LifecycleError, match="unavailable locally"):
        resolve_ref(source, "does-not-exist")
    with pytest.raises(LifecycleError, match="empty or unsafe"):
        resolve_ref(source, "--help")

    assert_ancestor(source, first, second)
    with pytest.raises(LifecycleError, match="not a descendant"):
        assert_ancestor(source, second, first)


def test_temporary_worktree_is_detached_and_removed_after_failure(
    tmp_path: Path,
) -> None:
    root, first, _second = _template_repository(tmp_path)
    source = resolve_source(root)
    checkout_path: Path | None = None

    with (
        pytest.raises(RuntimeError, match="fixture failure"),
        temporary_worktree(source, first) as checkout,
    ):
        checkout_path = checkout
        assert (checkout / "VERSION").read_text(encoding="utf-8") == "1.0.0\n"
        assert _git(checkout, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
        raise RuntimeError("fixture failure")

    assert checkout_path is not None and not checkout_path.exists()
    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    worktrees = _git(root, "worktree", "list", "--porcelain")
    assert worktrees.count("worktree ") == 1
    assert working_tree_status(source) == ""


def test_git_subprocesses_never_use_shell_or_network_commands(monkeypatch, tmp_path: Path) -> None:
    root, first, _second = _template_repository(tmp_path)
    from tools.template_lifecycle import source as source_module

    original_run = source_module.subprocess.run
    observed: list[list[str]] = []

    def guarded_run(command: list[str], **kwargs):
        observed.append(command)
        assert kwargs.get("shell") is not True
        assert command[1] not in {"fetch", "pull", "push", "clone"}
        return original_run(command, **kwargs)

    monkeypatch.setattr(source_module.subprocess, "run", guarded_run)

    source = resolve_source(root)
    resolve_ref(source, first)
    with temporary_worktree(source, first) as checkout:
        assert checkout.is_dir()

    assert observed
