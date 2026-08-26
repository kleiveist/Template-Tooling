from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from tools.template_lifecycle import apply as apply_module
from tools.template_lifecycle.apply import (
    ApplyRequest,
    ApplyResult,
    apply_update,
    require_clean_product_repository,
)
from tools.template_lifecycle.manifest import (
    create_manifest,
    load_manifest,
    write_manifest,
)
from tools.template_lifecycle.migrations import (
    Migration,
    MigrationCondition,
    MigrationOperation,
    MigrationRange,
)
from tools.template_lifecycle.model import (
    STATE_SCHEMA_VERSION,
    TEMPLATE_ID,
    TEMPLATE_URL,
    BaselineManifest,
    BaselineState,
    LifecycleError,
    LifecycleState,
    PlanOperation,
    ProductIdentity,
    SelectionState,
    SourceState,
    UpdatePlan,
    VerificationFinding,
    VerificationResult,
)
from tools.template_lifecycle.state import (
    BASELINE_RELATIVE_PATH,
    STATE_RELATIVE_PATH,
    load_state,
    state_digest,
    write_state,
)

OLD_COMMIT = "a" * 40
NEW_COMMIT = "b" * 40


def test_playwright_outputs_are_runtime_only() -> None:
    assert {"playwright-report", "test-results"} <= apply_module.RUNTIME_DIRECTORIES


@dataclass(frozen=True, slots=True)
class RepositoryFixture:
    root: Path
    state: LifecycleState
    manifest: BaselineManifest


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _write_files(root: Path, files: dict[str, bytes]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _state(
    commit: str,
    digest: str,
    *,
    version: str,
    migrations: tuple[str, ...] = (),
) -> LifecycleState:
    return LifecycleState(
        schema_version=STATE_SCHEMA_VERSION,
        repository_kind="product",
        template_id=TEMPLATE_ID,
        provenance="generated",
        source_dirty=False,
        source=SourceState(
            url=TEMPLATE_URL,
            version=version,
            ref=commit,
            commit=commit,
            tree_digest=digest,
        ),
        selection=SelectionState(
            profile="base",
            optional_features=(),
            resolved_features=("base",),
        ),
        identity=ProductIdentity(
            name="Fixture",
            slug="fixture",
            identifier="com.example.fixture",
            binary="fixture",
        ),
        baseline=BaselineState(
            manifest=BASELINE_RELATIVE_PATH,
            digest=digest,
            applied_migrations=migrations,
        ),
    )


def _repository(tmp_path: Path, files: dict[str, bytes]) -> RepositoryFixture:
    root = tmp_path / "product"
    root.mkdir()
    _write_files(root, files)
    manifest = create_manifest(root)
    state = _state(OLD_COMMIT, manifest.digest, version="1.0.0")
    write_manifest(root / BASELINE_RELATIVE_PATH, manifest)
    write_state(root, state)
    _git(root, "init", "--quiet")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Lifecycle Test")
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "-m", "fixture")
    return RepositoryFixture(root, state, manifest)


def _incoming_manifest(
    tmp_path: Path,
    files: dict[str, bytes],
    *,
    executable: tuple[str, ...] = (),
) -> BaselineManifest:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    _write_files(incoming, files)
    for relative in executable:
        os.chmod(incoming / relative, 0o755)
    return create_manifest(incoming)


def _verification(*, passing: bool) -> VerificationResult:
    status = "PASS" if passing else "FAIL"
    return VerificationResult((VerificationFinding("test-verifier", status, f"fixture {status.lower()}"),))


def _updated_state(
    manifest: BaselineManifest,
    *,
    migrations: tuple[str, ...] = (),
) -> LifecycleState:
    return _state(
        NEW_COMMIT,
        manifest.digest,
        version="1.1.0",
        migrations=migrations,
    )


def _update_plan(
    *operations: PlanOperation,
    migrations: tuple[str, ...] = (),
) -> UpdatePlan:
    return UpdatePlan(
        baseline_commit=OLD_COMMIT,
        target_commit=NEW_COMMIT,
        target_version="1.1.0",
        operations=operations,
        migrations=migrations,
    )


def _notice_migration(migration_id: str) -> Migration:
    return Migration(
        migration_id=migration_id,
        description="Record a successful synthetic migration",
        order=10,
        applies=MigrationRange(source_versions=("1.0.0",), target_version="1.1.0"),
        operations=(MigrationOperation(kind="record_notice", notice="Synthetic migration applied."),),
        preconditions=(MigrationCondition(kind="path_exists", path="product.txt"),),
        postconditions=(MigrationCondition(kind="path_exists", path="product.txt"),),
    )


def _move_migration(migration_id: str) -> Migration:
    return Migration(
        migration_id=migration_id,
        description="Rename the legacy path",
        order=10,
        applies=MigrationRange(source_versions=("1.0.0",), target_version="1.1.0"),
        operations=(
            MigrationOperation(
                kind="move_path",
                source="legacy.txt",
                destination="modern.txt",
            ),
        ),
        preconditions=(MigrationCondition(kind="path_exists", path="legacy.txt"),),
        postconditions=(MigrationCondition(kind="path_exists", path="modern.txt"),),
    )


def _assert_state_was_replaced_last(replacements: list[str]) -> None:
    assert replacements == ["product.txt", BASELINE_RELATIVE_PATH, STATE_RELATIVE_PATH]


def _apply(
    *,
    project_root: Path,
    plan: UpdatePlan,
    new_state: LifecycleState,
    new_manifest: BaselineManifest,
    migrations: tuple[Migration, ...],
    report_directory: Path,
    verifier: Callable[[Path], VerificationResult],
    report_finalizer: Callable[[VerificationResult, str, tuple[str, ...]], None] | None = None,
) -> ApplyResult:
    return apply_update(
        ApplyRequest(
            project_root=project_root,
            plan=plan,
            new_state=new_state,
            new_manifest=new_manifest,
            migrations=migrations,
            report_directory=report_directory,
            verifier=verifier,
            report_finalizer=report_finalizer,
        )
    )


def test_clean_repository_guard_accepts_head_and_rejects_untracked_files(
    tmp_path: Path,
) -> None:
    fixture = _repository(tmp_path, {"product.txt": b"old\n"})

    assert require_clean_product_repository(fixture.root) == _git(fixture.root, "rev-parse", "HEAD")

    (fixture.root / "untracked.txt").write_text("not ignored\n", encoding="utf-8")
    with pytest.raises(LifecycleError, match="completely clean Git working tree"):
        require_clean_product_repository(fixture.root)


def test_clean_repository_guard_rejects_tracked_modifications(tmp_path: Path) -> None:
    fixture = _repository(tmp_path, {"product.txt": b"old\n"})

    (fixture.root / "product.txt").write_text("locally modified\n", encoding="utf-8")

    with pytest.raises(LifecycleError, match="completely clean Git working tree"):
        require_clean_product_repository(fixture.root)


def test_conflicting_plan_performs_no_writes(tmp_path: Path) -> None:
    fixture = _repository(tmp_path, {"product.txt": b"old\n"})
    incoming = _incoming_manifest(tmp_path, {"product.txt": b"new\n"})
    new_state = _updated_state(incoming)
    state_before = (fixture.root / STATE_RELATIVE_PATH).read_bytes()
    baseline_before = (fixture.root / BASELINE_RELATIVE_PATH).read_bytes()
    plan = _update_plan(
        PlanOperation(
            action="CONFLICT",
            path="product.txt",
            reason="both sides changed",
        )
    )
    report_directory = tmp_path / "reports"

    with pytest.raises(LifecycleError, match="contains conflicts"):
        _apply(
            project_root=fixture.root,
            plan=plan,
            new_state=new_state,
            new_manifest=incoming,
            migrations=(),
            report_directory=report_directory,
            verifier=lambda _root: _verification(passing=True),
        )

    assert (fixture.root / "product.txt").read_bytes() == b"old\n"
    assert (fixture.root / STATE_RELATIVE_PATH).read_bytes() == state_before
    assert (fixture.root / BASELINE_RELATIVE_PATH).read_bytes() == baseline_before
    assert not report_directory.exists()
    assert _git(fixture.root, "status", "--porcelain") == ""


def test_successful_apply_writes_state_last_and_verifies_once_per_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _repository(tmp_path, {"product.txt": b"old\n"})
    incoming = _incoming_manifest(tmp_path, {"product.txt": b"new\n"})
    migration_id = "successful-synthetic"
    migration = _notice_migration(migration_id)
    new_state = _updated_state(incoming, migrations=(migration_id,))
    plan = _update_plan(
        PlanOperation(
            action="UPDATE",
            path="product.txt",
            reason="template changed",
            kind="text",
            executable=False,
            result=b"new\n",
        ),
        migrations=(migration_id,),
    )
    replacements: list[str] = []
    verification_roots: list[Path] = []
    original_replace = apply_module._replace_from_stage

    def recording_replace(root: Path, staging: Path, relative: str) -> None:
        replacements.append(relative)
        original_replace(root, staging, relative)

    def verifier(root: Path) -> VerificationResult:
        verification_roots.append(root.resolve())
        return _verification(passing=True)

    monkeypatch.setattr(apply_module, "_replace_from_stage", recording_replace)
    result = _apply(
        project_root=fixture.root,
        plan=plan,
        new_state=new_state,
        new_manifest=incoming,
        migrations=(migration,),
        report_directory=tmp_path / "reports",
        verifier=verifier,
    )

    assert result.verification.ok
    assert result.migration_run.applied_ids == (migration_id,)
    assert result.migration_run.notices == ("Synthetic migration applied.",)
    assert (fixture.root / "product.txt").read_bytes() == b"new\n"
    assert load_state(fixture.root) == new_state
    assert load_manifest(fixture.root / BASELINE_RELATIVE_PATH) == incoming
    _assert_state_was_replaced_last(replacements)
    assert len(verification_roots) == 2
    assert verification_roots[-1] == fixture.root.resolve()
    assert (tmp_path / "reports/journal.json").is_file()


def test_noop_apply_only_verifies_and_does_not_write(tmp_path: Path) -> None:
    fixture = _repository(tmp_path, {"product.txt": b"old\n"})
    state_before = (fixture.root / STATE_RELATIVE_PATH).read_bytes()
    baseline_before = (fixture.root / BASELINE_RELATIVE_PATH).read_bytes()
    verification_roots: list[Path] = []

    def verifier(root: Path) -> VerificationResult:
        verification_roots.append(root.resolve())
        return _verification(passing=True)

    plan = UpdatePlan(
        baseline_commit=OLD_COMMIT,
        target_commit=OLD_COMMIT,
        target_version="1.0.0",
        operations=(),
    )
    result = _apply(
        project_root=fixture.root,
        plan=plan,
        new_state=fixture.state,
        new_manifest=fixture.manifest,
        migrations=(),
        report_directory=tmp_path / "reports",
        verifier=verifier,
    )

    assert result.verification.ok
    assert verification_roots == [fixture.root.resolve()]
    assert (fixture.root / STATE_RELATIVE_PATH).read_bytes() == state_before
    assert (fixture.root / BASELINE_RELATIVE_PATH).read_bytes() == baseline_before
    assert not (tmp_path / "reports").exists()
    assert _git(fixture.root, "status", "--porcelain") == ""


def test_verifier_failure_rolls_back_migration_move_source_and_destination(
    tmp_path: Path,
) -> None:
    fixture = _repository(tmp_path, {"legacy.txt": b"legacy\n"})
    incoming = _incoming_manifest(
        tmp_path,
        {"modern.txt": b"modernized\n"},
        executable=("modern.txt",),
    )
    migration_id = "rename-legacy"
    migration = _move_migration(migration_id)
    new_state = _updated_state(incoming, migrations=(migration_id,))
    plan = _update_plan(
        PlanOperation(
            action="MOVE",
            path="modern.txt",
            source_path="legacy.txt",
            reason="migration aliases the renamed path",
            kind="text",
            executable=True,
            result=b"modernized\n",
        ),
        migrations=(migration_id,),
    )
    state_before = (fixture.root / STATE_RELATIVE_PATH).read_bytes()
    baseline_before = (fixture.root / BASELINE_RELATIVE_PATH).read_bytes()

    def verifier(root: Path) -> VerificationResult:
        if root.resolve() != fixture.root.resolve():
            return _verification(passing=True)
        assert not (root / "legacy.txt").exists()
        assert (root / "modern.txt").read_bytes() == b"modernized\n"
        assert (root / "modern.txt").stat().st_mode & 0o111
        return _verification(passing=False)

    with pytest.raises(LifecycleError, match="Post-update verification failed"):
        _apply(
            project_root=fixture.root,
            plan=plan,
            new_state=new_state,
            new_manifest=incoming,
            migrations=(migration,),
            report_directory=tmp_path / "reports",
            verifier=verifier,
        )

    assert (fixture.root / "legacy.txt").read_bytes() == b"legacy\n"
    assert not (fixture.root / "modern.txt").exists()
    assert (fixture.root / STATE_RELATIVE_PATH).read_bytes() == state_before
    assert (fixture.root / BASELINE_RELATIVE_PATH).read_bytes() == baseline_before
    assert _git(fixture.root, "status", "--porcelain") == ""


def test_failed_staged_migration_does_not_change_product_or_lifecycle_state(
    tmp_path: Path,
) -> None:
    fixture = _repository(tmp_path, {"product.txt": b"before\n"})
    incoming = _incoming_manifest(tmp_path, {"product.txt": b"after\n"})
    migration_id = "fails-postcondition"
    migration = Migration(
        migration_id=migration_id,
        description="Fail after a staged edit",
        order=10,
        applies=MigrationRange(source_versions=("1.0.0",), target_version="1.1.0"),
        operations=(
            MigrationOperation(
                kind="transform_text",
                path="product.txt",
                old="before",
                new="after",
            ),
        ),
        preconditions=(MigrationCondition(kind="text_contains", path="product.txt", value="before"),),
        postconditions=(
            MigrationCondition(
                kind="text_contains",
                path="product.txt",
                value="impossible marker",
            ),
        ),
    )
    new_state = _updated_state(incoming, migrations=(migration_id,))
    plan = _update_plan(migrations=(migration_id,))
    state_before = (fixture.root / STATE_RELATIVE_PATH).read_bytes()
    baseline_before = (fixture.root / BASELINE_RELATIVE_PATH).read_bytes()

    with pytest.raises(LifecycleError, match="postcondition failed"):
        _apply(
            project_root=fixture.root,
            plan=plan,
            new_state=new_state,
            new_manifest=incoming,
            migrations=(migration,),
            report_directory=tmp_path / "reports",
            verifier=lambda _root: _verification(passing=True),
        )

    assert (fixture.root / "product.txt").read_bytes() == b"before\n"
    assert (fixture.root / STATE_RELATIVE_PATH).read_bytes() == state_before
    assert (fixture.root / BASELINE_RELATIVE_PATH).read_bytes() == baseline_before
    assert not (tmp_path / "reports").exists()
    assert _git(fixture.root, "status", "--porcelain") == ""


def test_migration_only_transform_is_applied_and_journaled(tmp_path: Path) -> None:
    fixture = _repository(tmp_path, {"product.txt": b"before\n"})
    incoming = _incoming_manifest(tmp_path, {"product.txt": b"before\n"})
    migration_id = "transform-product-text"
    migration = Migration(
        migration_id=migration_id,
        description="Transform a product-owned value",
        order=10,
        applies=MigrationRange(source_versions=("1.0.0",), target_version="1.1.0"),
        operations=(
            MigrationOperation(
                kind="transform_text",
                path="product.txt",
                old="before",
                new="after",
            ),
        ),
        preconditions=(MigrationCondition(kind="text_contains", path="product.txt", value="before"),),
        postconditions=(MigrationCondition(kind="text_contains", path="product.txt", value="after"),),
    )
    new_state = _updated_state(incoming, migrations=(migration_id,))
    plan = _update_plan(migrations=(migration_id,))
    report_directory = tmp_path / "reports"

    result = _apply(
        project_root=fixture.root,
        plan=plan,
        new_state=new_state,
        new_manifest=incoming,
        migrations=(migration,),
        report_directory=report_directory,
        verifier=lambda _root: _verification(passing=True),
    )

    assert result.migration_run.affected_paths == ("product.txt",)
    assert (fixture.root / "product.txt").read_text(encoding="utf-8") == "after\n"
    journal = json.loads((report_directory / "journal.json").read_text(encoding="utf-8"))
    product_entry = next(entry for entry in journal["files"] if entry["path"] == "product.txt")
    assert journal["target_template_commit"] == NEW_COMMIT
    assert journal["planned_state_digest"] == state_digest(new_state)
    assert product_entry["operation"] == "MIGRATION_TRANSFORM_TEXT"
    assert product_entry["before_sha256"] != product_entry["after_sha256"]
    assert product_entry["rollback"] == "backup"


def test_failure_during_product_replacement_rolls_back_every_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _repository(tmp_path, {"first.txt": b"first-old\n", "second.txt": b"second-old\n"})
    incoming = _incoming_manifest(tmp_path, {"first.txt": b"first-new\n", "second.txt": b"second-new\n"})
    plan = _update_plan(
        PlanOperation("UPDATE", "first.txt", "template changed", kind="text", result=b"first-new\n"),
        PlanOperation("UPDATE", "second.txt", "template changed", kind="text", result=b"second-new\n"),
    )
    state_before = (fixture.root / STATE_RELATIVE_PATH).read_bytes()
    baseline_before = (fixture.root / BASELINE_RELATIVE_PATH).read_bytes()
    original_replace = apply_module._replace_from_stage

    def fail_on_second(root: Path, staging: Path, relative: str) -> None:
        if relative == "second.txt":
            raise OSError("synthetic mid-apply failure")
        original_replace(root, staging, relative)

    monkeypatch.setattr(apply_module, "_replace_from_stage", fail_on_second)

    with pytest.raises(LifecycleError, match="rolled back"):
        _apply(
            project_root=fixture.root,
            plan=plan,
            new_state=_updated_state(incoming),
            new_manifest=incoming,
            migrations=(),
            report_directory=tmp_path / "reports",
            verifier=lambda _root: _verification(passing=True),
        )

    assert (fixture.root / "first.txt").read_bytes() == b"first-old\n"
    assert (fixture.root / "second.txt").read_bytes() == b"second-old\n"
    assert (fixture.root / STATE_RELATIVE_PATH).read_bytes() == state_before
    assert (fixture.root / BASELINE_RELATIVE_PATH).read_bytes() == baseline_before
    assert _git(fixture.root, "status", "--porcelain") == ""


def test_report_finalization_failure_is_part_of_the_rollback_boundary(tmp_path: Path) -> None:
    fixture = _repository(tmp_path, {"product.txt": b"old\n"})
    incoming = _incoming_manifest(tmp_path, {"product.txt": b"new\n"})
    plan = _update_plan(PlanOperation("UPDATE", "product.txt", "template changed", kind="text", result=b"new\n"))
    state_before = (fixture.root / STATE_RELATIVE_PATH).read_bytes()
    baseline_before = (fixture.root / BASELINE_RELATIVE_PATH).read_bytes()

    def fail_report(_result: VerificationResult, _outcome: str, _notices: tuple[str, ...]) -> None:
        raise OSError("synthetic report finalization failure")

    with pytest.raises(LifecycleError, match="rolled back"):
        _apply(
            project_root=fixture.root,
            plan=plan,
            new_state=_updated_state(incoming),
            new_manifest=incoming,
            migrations=(),
            report_directory=tmp_path / "reports",
            verifier=lambda _root: _verification(passing=True),
            report_finalizer=fail_report,
        )

    assert (fixture.root / "product.txt").read_bytes() == b"old\n"
    assert (fixture.root / STATE_RELATIVE_PATH).read_bytes() == state_before
    assert (fixture.root / BASELINE_RELATIVE_PATH).read_bytes() == baseline_before
    assert _git(fixture.root, "status", "--porcelain") == ""


def test_preimage_validation_detects_hidden_product_change_after_planning(tmp_path: Path) -> None:
    fixture = _repository(tmp_path, {"product.txt": b"old\n"})
    incoming = _incoming_manifest(tmp_path, {"product.txt": b"new\n"})
    baseline_entry = fixture.manifest.by_path()["product.txt"]
    plan = _update_plan(
        PlanOperation(
            "UPDATE",
            "product.txt",
            "template changed",
            local_sha256=baseline_entry.sha256,
            kind="text",
            result=b"new\n",
        )
    )
    (fixture.root / "product.txt").write_text("hidden race\n", encoding="utf-8")
    _git(fixture.root, "update-index", "--assume-unchanged", "product.txt")

    with pytest.raises(LifecycleError, match="changed after planning"):
        _apply(
            project_root=fixture.root,
            plan=plan,
            new_state=_updated_state(incoming),
            new_manifest=incoming,
            migrations=(),
            report_directory=tmp_path / "reports",
            verifier=lambda _root: _verification(passing=True),
        )

    assert (fixture.root / "product.txt").read_text(encoding="utf-8") == "hidden race\n"
    assert load_state(fixture.root) == fixture.state
