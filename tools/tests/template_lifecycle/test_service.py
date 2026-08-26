"""Service orchestration evidence for LC-009, LC-012, LC-015, and LC-018."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.template_lifecycle import service
from tools.template_lifecycle.apply import ApplyResult
from tools.template_lifecycle.migrations import (
    Migration,
    MigrationCondition,
    MigrationOperation,
    MigrationRange,
    MigrationRegistry,
    MigrationRun,
)
from tools.template_lifecycle.model import (
    LifecycleError,
    SelectionState,
    VerificationFinding,
    VerificationResult,
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


def _options(lifecycle_fixture, target: Path, report_dir: Path) -> service.CommonOptions:
    return service.CommonOptions(
        executor_root=Path.cwd(),
        target_dir=target,
        source_dir=lifecycle_fixture.source_root,
        report_dir=str(report_dir),
    )


def _architecture_migration() -> Migration:
    return Migration(
        migration_id="synthetic-architecture-change",
        description="Exercise explicit architecture-change confirmation",
        order=10,
        applies=MigrationRange(source_versions=("1.0.0",), target_version="1.1.0"),
        operations=(MigrationOperation(kind="record_notice", notice="Synthetic architecture change."),),
        preconditions=(MigrationCondition(kind="path_exists", path="VERSION"),),
        postconditions=(MigrationCondition(kind="path_exists", path="VERSION"),),
        architecture_change=True,
    )


def _passing_apply_result(migration_id: str) -> ApplyResult:
    verification = VerificationResult((VerificationFinding("service-fixture", "PASS", "Service fixture passed."),))
    migration_run = MigrationRun((migration_id,), (), (), ())
    return ApplyResult(verification, migration_run)


def test_update_rejects_branch_that_moves_between_plan_and_apply(
    lifecycle_fixture,
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = lifecycle_fixture.managed_product(tmp_path / "branch drift product")
    source = lifecycle_fixture.source_root
    _git(source, "branch", "moving-target", lifecycle_fixture.v2)
    original_resolve_ref = service.resolve_ref
    target_resolutions = 0

    def moving_resolve_ref(template_source, requested_ref: str):
        nonlocal target_resolutions
        if requested_ref == "moving-target":
            target_resolutions += 1
            if target_resolutions == 2:
                _git(source, "branch", "-f", "moving-target", lifecycle_fixture.v1)
        return original_resolve_ref(template_source, requested_ref)

    monkeypatch.setattr(service, "resolve_ref", moving_resolve_ref)
    managed_before = (target / "managed.txt").read_bytes()
    state_before = (target / ".template/state.toml").read_bytes()
    baseline_before = (target / ".template/baseline.json").read_bytes()

    with pytest.raises(LifecycleError, match="moved between planning and apply"):
        service.update(
            _options(lifecycle_fixture, target, tmp_path / "branch reports"),
            to_ref="moving-target",
            apply=True,
            allow_architecture_change=False,
        )

    assert target_resolutions == 2
    assert (target / "managed.txt").read_bytes() == managed_before
    assert (target / ".template/state.toml").read_bytes() == state_before
    assert (target / ".template/baseline.json").read_bytes() == baseline_before
    assert not (target / "template-added.txt").exists()


def test_plan_marks_changed_profile_meaning_without_migration_as_conflict(
    lifecycle_fixture,
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = lifecycle_fixture.managed_product(tmp_path / "architecture conflict product")
    changed_selection = SelectionState("web-only", (), ("frontend", "backend"))
    monkeypatch.setattr(service, "_selection", lambda _scaffold: changed_selection)

    bundle = service._build_plan(
        _options(lifecycle_fixture, target, tmp_path / "architecture conflict reports"),
        target,
        lifecycle_fixture.v2,
    )

    assert bundle.plan.architecture_change is True
    assert any(
        operation.is_conflict and "architecture migration" in operation.reason for operation in bundle.plan.operations
    )


def test_architecture_update_requires_flag_then_accepts_explicit_migration(
    lifecycle_fixture,
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = lifecycle_fixture.managed_product(tmp_path / "architecture approval product")
    migration = _architecture_migration()
    changed_selection = SelectionState("web-only", (), ("frontend", "backend"))
    monkeypatch.setattr(service, "REGISTRY", MigrationRegistry((migration,)))
    monkeypatch.setattr(service, "_selection", lambda _scaffold: changed_selection)
    options = _options(lifecycle_fixture, target, tmp_path / "architecture reports")
    bundle = service._build_plan(options, target, lifecycle_fixture.v2)
    assert bundle.plan.architecture_change is True
    assert bundle.plan.migrations == (migration.migration_id,)
    assert bundle.plan.conflicts == ()

    monkeypatch.setattr(service, "_build_plan", lambda *_args, **_kwargs: bundle)
    monkeypatch.setattr(service, "resolve_ref", lambda *_args, **_kwargs: bundle.target)
    apply_calls: list[object] = []

    def fake_apply_update(*args, **kwargs):
        apply_calls.append((args, kwargs))
        return _passing_apply_result(migration.migration_id)

    monkeypatch.setattr(service, "apply_update", fake_apply_update)

    with pytest.raises(LifecycleError, match="--allow-architecture-change"):
        service.update(
            options,
            to_ref=lifecycle_fixture.v2,
            apply=True,
            allow_architecture_change=False,
        )
    assert apply_calls == []

    output = service.update(
        options,
        to_ref=lifecycle_fixture.v2,
        apply=True,
        allow_architecture_change=True,
    )

    assert output.exit_code == 0
    assert output.payload["applied"] is True
    assert len(apply_calls) == 1


def test_architecture_migration_requires_preconditions_and_postconditions() -> None:
    without_conditions = Migration(
        migration_id="unsafe-architecture-change",
        description="Architecture changes require explicit guards",
        order=10,
        applies=MigrationRange(source_versions=("1.0.0",), target_version="1.1.0"),
        operations=(MigrationOperation(kind="record_notice", notice="Unsafe fixture."),),
        architecture_change=True,
    )

    with pytest.raises(LifecycleError, match="precondition|postcondition"):
        MigrationRegistry((without_conditions,))
