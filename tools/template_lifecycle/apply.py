from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tools.template_lifecycle.manifest import (
    create_manifest,
    inspect_relative,
    safe_relative_path,
    write_manifest,
)
from tools.template_lifecycle.migrations import (
    Migration,
    MigrationRun,
    run_migrations,
    validate_migration_postconditions,
)
from tools.template_lifecycle.model import (
    BaselineManifest,
    LifecycleError,
    LifecycleState,
    PlanOperation,
    UpdatePlan,
    VerificationResult,
)
from tools.template_lifecycle.state import (
    BASELINE_RELATIVE_PATH,
    STATE_RELATIVE_PATH,
    state_digest,
    write_state,
)
from tools.template_lifecycle.verify import verify_project

RUNTIME_DIRECTORIES = {
    ".git",
    ".venv",
    "node_modules",
    "target",
    "dist",
    "coverage",
    "playwright-report",
    "test-results",
    ".generated",
    ".dist",
    ".report",
    ".runtime",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
}


@dataclass(frozen=True, slots=True)
class ApplyResult:
    verification: VerificationResult
    migration_run: MigrationRun


ReportFinalizer = Callable[[VerificationResult, str, tuple[str, ...]], None]


@dataclass(frozen=True, slots=True)
class ApplyRequest:
    project_root: Path
    plan: UpdatePlan
    new_state: LifecycleState
    new_manifest: BaselineManifest
    migrations: tuple[Migration, ...]
    report_directory: Path
    expected_head: str | None = None
    verifier: Callable[[Path], VerificationResult] = verify_project
    report_finalizer: ReportFinalizer | None = None


@dataclass(frozen=True, slots=True)
class TransactionContext:
    root: Path
    staging: Path
    backup: Path
    affected: tuple[str, ...]
    verifier: Callable[[Path], VerificationResult]
    finalizer: ReportFinalizer | None
    notices: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class JournalContext:
    report_directory: Path
    root: Path
    staging: Path
    affected: tuple[str, ...]
    plan: UpdatePlan
    state: LifecycleState
    migrations: tuple[Migration, ...]


@dataclass(frozen=True, slots=True)
class StagedUpdate:
    migration_run: MigrationRun
    affected: tuple[str, ...]


def apply_adoption_metadata(
    project_root: Path,
    state: LifecycleState,
    manifest: BaselineManifest,
    *,
    verifier: Callable[[Path], VerificationResult] = verify_project,
) -> VerificationResult:
    """Write only lifecycle metadata and restore the prior metadata on failure."""
    root = project_root.resolve()
    require_clean_product_repository(root)
    with tempfile.TemporaryDirectory(prefix=f".{root.name}.template-adopt-", dir=root.parent) as temporary:
        scratch = Path(temporary)
        staged = scratch / "staged"
        backup = scratch / "backup"
        staged.mkdir()
        lifecycle = root / ".template"
        if lifecycle.exists() or lifecycle.is_symlink():
            _copy_path(lifecycle, backup / ".template")
        write_manifest(staged / BASELINE_RELATIVE_PATH, manifest)
        write_state(staged, state)
        try:
            _replace_from_stage(root, staged, BASELINE_RELATIVE_PATH)
            _replace_from_stage(root, staged, STATE_RELATIVE_PATH)
            result = verifier(root)
            if not result.ok:
                raise LifecycleError("Adopted lifecycle metadata did not pass verification.")
            return result
        except BaseException as exc:
            try:
                _delete_path(lifecycle)
                saved = backup / ".template"
                if saved.exists() or saved.is_symlink():
                    _copy_path(saved, lifecycle)
            except (OSError, LifecycleError) as rollback_exc:
                raise LifecycleError(
                    f"Adoption failed and metadata rollback could not complete: {rollback_exc}."
                ) from exc
            if isinstance(exc, KeyboardInterrupt):
                raise
            if isinstance(exc, LifecycleError):
                raise
            raise LifecycleError(f"Adoption failed and metadata was rolled back: {exc}.") from exc


def require_clean_product_repository(project_root: Path, *, tool_report_directory: Path | None = None) -> str:
    root = project_root.resolve()
    top_level = _run_git(root, ["rev-parse", "--show-toplevel"])
    if top_level.returncode != 0:
        raise LifecycleError("Applying lifecycle metadata or updates requires the target to be a Git repository.")
    try:
        repository_root = Path(top_level.stdout.strip()).resolve()
    except OSError as exc:
        raise LifecycleError("Could not resolve the target Git repository root.") from exc
    if repository_root != root:
        raise LifecycleError("The lifecycle target must be the root of its own Git repository.")
    status = _run_git(
        root,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
    )
    if status.returncode != 0:
        raise LifecycleError("Could not inspect the complete target Git working tree status.")
    dirty_entries = _unallowed_status_entries(root, status.stdout, tool_report_directory)
    if dirty_entries:
        first = dirty_entries[0]
        raise LifecycleError(f"Update apply requires a completely clean Git working tree; found {first}.")
    head = _run_git(root, ["rev-parse", "HEAD"])
    if head.returncode != 0:
        raise LifecycleError("Target Git repository has no readable HEAD commit.")
    return head.stdout.strip()


def _unallowed_status_entries(root: Path, status: str, tool_report_directory: Path | None) -> tuple[str, ...]:
    allowed_prefix: str | None = None
    if tool_report_directory is not None:
        try:
            allowed_prefix = tool_report_directory.resolve().relative_to(root).as_posix()
        except ValueError:
            allowed_prefix = None
        if allowed_prefix is not None and not allowed_prefix.startswith(".report/"):
            allowed_prefix = None
    entries = tuple(entry for entry in status.split("\0") if entry)
    return tuple(
        entry
        for entry in entries
        if not (
            allowed_prefix is not None
            and entry.startswith("?? ")
            and (entry[3:] == allowed_prefix or entry[3:].startswith(f"{allowed_prefix}/"))
        )
    )


def apply_update(request: ApplyRequest) -> ApplyResult:
    root = request.project_root.resolve()
    if request.plan.conflicts:
        raise LifecycleError("Update plan contains conflicts; no product files were changed.")
    current_head = require_clean_product_repository(root, tool_report_directory=request.report_directory)
    expected_head = request.expected_head or current_head
    if current_head != expected_head:
        raise LifecycleError("Product HEAD changed after update planning; recalculate the plan.")
    _validate_plan_preimages(root, request.plan)
    if request.plan.is_noop:
        return _verify_noop(request, root)
    temporary_parent = root.parent
    prefix = f".{root.name}.template-update-"
    with tempfile.TemporaryDirectory(prefix=prefix, dir=temporary_parent) as temporary:
        scratch = Path(temporary)
        staging = scratch / "staging"
        backup = scratch / "backup"
        staged = _prepare_staged_update(request, root, staging)
        repeated_head = require_clean_product_repository(root, tool_report_directory=request.report_directory)
        if repeated_head != expected_head:
            raise LifecycleError("Product HEAD changed during update planning; target remains unchanged.")
        _validate_plan_preimages(root, request.plan)
        transaction = TransactionContext(
            root,
            staging,
            backup,
            staged.affected,
            request.verifier,
            request.report_finalizer,
            staged.migration_run.notices,
        )
        final_verification = _apply_with_rollback(transaction)
        return ApplyResult(final_verification, staged.migration_run)


def _verify_noop(request: ApplyRequest, root: Path) -> ApplyResult:
    result = request.verifier(root)
    if not result.ok:
        raise LifecycleError("No-op update verification failed.")
    if request.report_finalizer is not None:
        request.report_finalizer(result, "UPDATED", ())
    migration_run = MigrationRun(request.new_state.baseline.applied_migrations, (), (), ())
    return ApplyResult(result, migration_run)


def _prepare_staged_update(request: ApplyRequest, root: Path, staging: Path) -> StagedUpdate:
    _copy_staging_tree(root, staging)
    owned_paths = _migration_owned_paths(request.plan, request.new_manifest)
    prior_migration_ids = tuple(
        migration_id
        for migration_id in request.new_state.baseline.applied_migrations
        if migration_id not in request.plan.migrations
    )
    migration_run = run_migrations(
        staging,
        request.migrations,
        owned_paths=owned_paths,
        already_applied=prior_migration_ids,
    )
    if migration_run.applied_ids != request.new_state.baseline.applied_migrations:
        raise LifecycleError("Applied migration ids do not match the planned lifecycle state.")
    _apply_plan_to_tree(staging, request.plan)
    validate_migration_postconditions(staging, request.migrations, owned_paths=owned_paths)
    write_manifest(staging / BASELINE_RELATIVE_PATH, request.new_manifest)
    write_state(staging, request.new_state)
    _verify_staged_update(request, staging, migration_run)
    affected = _affected_paths(request.plan, migration_run)
    _write_journal(
        JournalContext(
            request.report_directory,
            root,
            staging,
            affected,
            request.plan,
            request.new_state,
            request.migrations,
        )
    )
    return StagedUpdate(migration_run, affected)


def _verify_staged_update(request: ApplyRequest, staging: Path, migration_run: MigrationRun) -> None:
    result = request.verifier(staging)
    if result.ok:
        return
    if request.report_finalizer is not None:
        request.report_finalizer(result, "FAILED", migration_run.notices)
    raise LifecycleError("Staged lifecycle verification failed; target remains unchanged.")


def _apply_with_rollback(context: TransactionContext) -> VerificationResult:
    _backup_paths(context.root, context.backup, context.affected)
    try:
        product_paths = tuple(path for path in context.affected if not path.startswith(".template/"))
        for relative in product_paths:
            _replace_from_stage(context.root, context.staging, relative)
        _replace_from_stage(context.root, context.staging, BASELINE_RELATIVE_PATH)
        _replace_from_stage(context.root, context.staging, STATE_RELATIVE_PATH)
        result = context.verifier(context.root)
        if not result.ok:
            if context.finalizer is not None:
                context.finalizer(result, "FAILED", context.notices)
            raise LifecycleError("Post-update verification failed.")
        if context.finalizer is not None:
            context.finalizer(result, "UPDATED", context.notices)
        return result
    except BaseException as exc:
        try:
            _restore_paths(context.root, context.backup, context.affected)
        except (OSError, LifecycleError) as rollback_exc:
            raise LifecycleError(f"Update failed and rollback could not complete: {rollback_exc}.") from exc
        if isinstance(exc, KeyboardInterrupt):
            raise
        if isinstance(exc, LifecycleError):
            raise
        raise LifecycleError(f"Update failed and was rolled back: {exc}.") from exc


def _copy_staging_tree(root: Path, staging: Path) -> None:
    def ignored(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in RUNTIME_DIRECTORIES or name.endswith((".pyc", ".pyo"))}

    try:
        shutil.copytree(root, staging, symlinks=True, ignore=ignored)
    except OSError as exc:
        raise LifecycleError(f"Could not create the isolated update staging tree: {exc}.") from exc


def copy_product_tree(root: Path, destination: Path) -> None:
    """Copy a product into isolated staging while excluding runtime artifacts."""

    _copy_staging_tree(root.resolve(), destination.resolve())


def _migration_owned_paths(plan: UpdatePlan, manifest: BaselineManifest) -> tuple[str, ...]:
    paths = set(manifest.by_path())
    for operation in plan.operations:
        if operation.changes_product:
            paths.add(operation.path)
            if operation.source_path is not None:
                paths.add(operation.source_path)
    return tuple(sorted(paths))


def _validate_plan_preimages(root: Path, plan: UpdatePlan) -> None:
    for operation in plan.operations:
        if not operation.changes_product:
            continue
        if operation.action == "ADD":
            if inspect_relative(root, operation.path) is not None:
                raise LifecycleError(f"Product path appeared after planning: {operation.path}.")
            continue
        relative = operation.source_path or operation.path
        actual = inspect_relative(root, relative)
        if actual is None:
            raise LifecycleError(f"Planned product path is now missing: {relative}.")
        if operation.local_sha256 and actual.sha256 != operation.local_sha256:
            raise LifecycleError(f"Product path changed after planning: {relative}.")
        if operation.action == "MOVE" and inspect_relative(root, operation.path) is not None:
            raise LifecycleError(f"Move destination appeared after planning: {operation.path}.")


def _apply_plan_to_tree(staging: Path, plan: UpdatePlan) -> None:
    for operation in plan.operations:
        if not operation.changes_product:
            continue
        relative = safe_relative_path(operation.path)
        path = _safe_target(staging, relative)
        if operation.action == "DELETE":
            _delete_path(path)
        elif operation.action == "MOVE":
            if operation.source_path is None:
                raise LifecycleError(f"MOVE operation lacks a source path: {relative}.")
            source = _safe_target(staging, safe_relative_path(operation.source_path))
            _apply_move_to_tree(staging, source, path, operation)
        elif operation.action in {"ADD", "UPDATE", "MERGE"}:
            if operation.result is None or operation.kind is None:
                raise LifecycleError(f"{operation.action} operation lacks staged content: {relative}.")
            _write_result(
                path,
                operation.result,
                operation.kind,
                bool(operation.executable),
                staging,
            )
        else:
            raise LifecycleError(f"Unsupported product-changing operation: {operation.action}.")


def _apply_move_to_tree(
    staging: Path,
    source: Path,
    destination: Path,
    operation: PlanOperation,
) -> None:
    source_exists = source.exists() or source.is_symlink()
    destination_exists = destination.exists() or destination.is_symlink()
    if source_exists:
        if destination_exists:
            raise LifecycleError(f"MOVE destination already exists: {operation.path}.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
    elif not destination_exists:
        raise LifecycleError(f"MOVE source and destination are both missing: {operation.source_path}.")
    if operation.result is not None:
        if operation.kind is None:
            raise LifecycleError(f"MOVE operation lacks a staged content kind: {operation.path}.")
        _write_result(
            destination,
            operation.result,
            operation.kind,
            bool(operation.executable),
            staging,
        )


def _write_result(path: Path, content: bytes, kind: str, executable: bool, root: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "symlink":
        _delete_path(path)
        try:
            target = content.decode("utf-8")
            os.symlink(target, path)
            if not path.resolve(strict=False).is_relative_to(root):
                raise LifecycleError(f"Staged symbolic link escapes the product root: {path.relative_to(root)}.")
        except (OSError, UnicodeDecodeError) as exc:
            path.unlink(missing_ok=True)
            raise LifecycleError(f"Could not stage symbolic link {path.name}.") from exc
        return
    if kind not in {"text", "binary"}:
        raise LifecycleError(f"Unsupported staged file kind: {kind}.")
    from tools.template_lifecycle.state import _atomic_write

    _atomic_write(path, content, mode=0o755 if executable else 0o644)


def _affected_paths(plan: UpdatePlan, migration_run: MigrationRun) -> tuple[str, ...]:
    paths = {operation.path for operation in plan.operations if operation.changes_product}
    paths.update(
        operation.source_path
        for operation in plan.operations
        if operation.changes_product and operation.source_path is not None
    )
    paths.update(migration_run.affected_paths)
    paths.update((BASELINE_RELATIVE_PATH, STATE_RELATIVE_PATH))
    product = sorted(path for path in paths if not path.startswith(".template/"))
    return (*product, BASELINE_RELATIVE_PATH, STATE_RELATIVE_PATH)


def _backup_paths(root: Path, backup: Path, affected: tuple[str, ...]) -> None:
    backup.mkdir(parents=True, exist_ok=True)
    existing: list[str] = []
    for relative in affected:
        source = _safe_target(root, _transaction_path(relative))
        if not source.exists() and not source.is_symlink():
            continue
        existing.append(relative)
        _copy_path(source, backup / Path(relative))
    (backup / "existing.json").write_text(json.dumps(existing, sort_keys=True), encoding="utf-8")


def _restore_paths(root: Path, backup: Path, affected: tuple[str, ...]) -> None:
    try:
        existing = set(json.loads((backup / "existing.json").read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleError("Rollback inventory is unreadable.") from exc
    for relative in reversed(affected):
        target = _safe_target(root, _transaction_path(relative))
        _delete_path(target)
        if relative in existing:
            _copy_path(backup / Path(relative), target)


def _replace_from_stage(root: Path, staging: Path, relative: str) -> None:
    validated = _transaction_path(relative)
    target = _safe_target(root, validated)
    source = _safe_target(staging, validated)
    if not source.exists() and not source.is_symlink():
        _delete_path(target)
        return
    if source.is_file() and not source.is_symlink() and not target.is_dir():
        _copy_path(source, target, atomic=True)
        return
    _delete_path(target)
    _copy_path(source, target, atomic=True)


def _copy_path(source: Path, destination: Path, *, atomic: bool = False) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        if not atomic:
            os.symlink(os.readlink(source), destination)
            return
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.lifecycle-link-",
            dir=destination.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        temporary.unlink()
        try:
            os.symlink(os.readlink(source), temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    elif source.is_dir():
        if atomic:
            temporary = Path(
                tempfile.mkdtemp(
                    prefix=f".{destination.name}.lifecycle-dir-",
                    dir=destination.parent,
                )
            )
            temporary.rmdir()
            try:
                shutil.copytree(source, temporary, symlinks=True)
                os.replace(temporary, destination)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        else:
            shutil.copytree(source, destination, symlinks=True)
    else:
        content = source.read_bytes()
        mode = source.stat().st_mode & 0o777
        if atomic:
            from tools.template_lifecycle.state import _atomic_write

            _atomic_write(destination, content, mode=mode)
        else:
            shutil.copy2(source, destination)


def _delete_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _safe_target(root: Path, relative: str) -> Path:
    candidate = root / Path(relative)
    ancestor = candidate if candidate.exists() or candidate.is_symlink() else candidate.parent
    while not ancestor.exists() and ancestor != root:
        ancestor = ancestor.parent
    try:
        if not ancestor.resolve(strict=True).is_relative_to(root.resolve()):
            raise LifecycleError(f"Lifecycle path escapes the target root: {relative}.")
    except OSError as exc:
        raise LifecycleError(f"Could not resolve lifecycle path: {relative}.") from exc
    return candidate


def _transaction_path(relative: str) -> str:
    if relative in {BASELINE_RELATIVE_PATH, STATE_RELATIVE_PATH}:
        return relative
    return safe_relative_path(relative)


def _write_journal(context: JournalContext) -> None:
    entries = []
    operation_by_path = _journal_operations(context.plan, context.migrations)
    for relative in context.affected:
        before_sha, before_kind = _journal_snapshot(context.root, relative)
        after_sha, after_kind = _journal_snapshot(context.staging, relative)
        entries.append(
            {
                "path": relative,
                "operation": operation_by_path.get(relative, "STATE_UPDATE"),
                "before_sha256": before_sha,
                "after_sha256": after_sha,
                "before_kind": before_kind,
                "after_kind": after_kind,
                "rollback": "backup" if before_kind else "remove",
            }
        )
    payload = {
        "schema_version": 1,
        "target_template_commit": context.plan.target_commit,
        "planned_state_digest": state_digest(context.state),
        "files": entries,
    }
    context.report_directory.mkdir(parents=True, exist_ok=True)
    journal = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    from tools.template_lifecycle.state import _atomic_write

    _atomic_write(
        context.report_directory / "journal.json",
        journal.encode("utf-8"),
        mode=0o644,
    )


def _journal_operations(plan: UpdatePlan, migrations: tuple[Migration, ...]) -> dict[str, str]:
    operations: dict[str, str] = {}
    for migration in migrations:
        for operation in migration.operations:
            action = f"MIGRATION_{operation.kind.upper()}"
            if operation.path is not None:
                operations[operation.path] = action
            if operation.source is not None:
                operations[operation.source] = action
            if operation.destination is not None:
                operations[operation.destination] = action
    for operation in plan.operations:
        operations[operation.path] = operation.action
        if operation.source_path is not None:
            operations.setdefault(operation.source_path, operation.action)
    return operations


def _journal_snapshot(root: Path, relative: str) -> tuple[str | None, str | None]:
    path = _safe_target(root, _transaction_path(relative))
    if not path.exists() and not path.is_symlink():
        return None, None
    if path.is_dir() and not path.is_symlink():
        return create_manifest(path).digest, "directory"
    entry = inspect_relative(root, relative)
    if entry is None:
        return None, None
    return entry.sha256, entry.kind


def _run_git(root: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise LifecycleError(f"Could not execute Git: {exc}.") from exc
