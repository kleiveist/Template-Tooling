from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
import tomllib

from tools.template_lifecycle.migrations import (
    Migration,
    MigrationCondition,
    MigrationOperation,
    MigrationRange,
    MigrationRegistry,
    run_migrations,
    validate_migration_postconditions,
)
from tools.template_lifecycle.model import LifecycleError

SENTINEL = "migration-contract-sentinel"
SAFE_CONDITIONS = (MigrationCondition(kind="path_missing", path=SENTINEL),)


def _migration(
    migration_id: str,
    order: int,
    operations: tuple[MigrationOperation, ...],
    *,
    preconditions: tuple[MigrationCondition, ...] = SAFE_CONDITIONS,
    postconditions: tuple[MigrationCondition, ...] = SAFE_CONDITIONS,
    architecture_change: bool = False,
) -> Migration:
    return Migration(
        migration_id=migration_id,
        description=f"Exercise {migration_id}",
        order=order,
        applies=MigrationRange(
            source_versions=("1.0.0",),
            target_version="1.1.0",
        ),
        operations=operations,
        preconditions=preconditions,
        postconditions=postconditions,
        architecture_change=architecture_change,
    )


def _owned(*paths: str) -> tuple[str, ...]:
    return (SENTINEL, *paths)


def _notice_migration(migration_id: str, order: int) -> Migration:
    return _migration(
        migration_id,
        order,
        (MigrationOperation(kind="record_notice", notice=migration_id),),
    )


def _write_operation_fixture(root: Path) -> None:
    root.mkdir()
    (root / "old.txt").write_text("move me\n", encoding="utf-8")
    (root / "seed.txt").write_text("copy me\n", encoding="utf-8")
    (root / "obsolete.txt").write_text("remove me\n", encoding="utf-8")
    (root / "config.json").write_text(json.dumps({"legacy": {"name": "demo"}}), encoding="utf-8")
    (root / "settings.toml").write_text("[legacy]\nenabled = true\n", encoding="utf-8")
    (root / "message.txt").write_text("old marker\n", encoding="utf-8")


def _supported_operations() -> tuple[MigrationOperation, ...]:
    return (
        MigrationOperation(kind="move_path", source="old.txt", destination="moved/new.txt"),
        MigrationOperation(kind="copy_path", source="seed.txt", destination="copied.txt"),
        MigrationOperation(kind="delete_path", path="obsolete.txt"),
        MigrationOperation(
            kind="rename_key",
            path="config.json",
            key="legacy.name",
            new_key="app.name",
        ),
        MigrationOperation(kind="set_default", path="config.json", key="app.mode", value="safe"),
        MigrationOperation(
            kind="transform_json",
            path="config.json",
            value={"app.enabled": True},
        ),
        MigrationOperation(
            kind="transform_toml",
            path="settings.toml",
            value={"app.mode": "safe"},
        ),
        MigrationOperation(
            kind="transform_text",
            path="message.txt",
            old="old marker",
            new="new marker",
        ),
        MigrationOperation(kind="record_notice", notice="Review the new layout."),
    )


def _supported_preconditions() -> tuple[MigrationCondition, ...]:
    return (
        MigrationCondition(kind="path_exists", path="old.txt"),
        MigrationCondition(
            kind="json_key_equals",
            path="config.json",
            key="legacy.name",
            value="demo",
        ),
        MigrationCondition(
            kind="toml_key_equals",
            path="settings.toml",
            key="legacy.enabled",
            value=True,
        ),
        MigrationCondition(kind="text_contains", path="message.txt", value="old marker"),
    )


def _supported_postconditions() -> tuple[MigrationCondition, ...]:
    return (
        MigrationCondition(kind="path_missing", path="old.txt"),
        MigrationCondition(kind="path_exists", path="moved/new.txt"),
        MigrationCondition(
            kind="json_key_equals",
            path="config.json",
            key="app.enabled",
            value=True,
        ),
        MigrationCondition(
            kind="toml_key_equals",
            path="settings.toml",
            key="app.mode",
            value="safe",
        ),
        MigrationCondition(kind="text_contains", path="message.txt", value="new marker"),
    )


def _assert_supported_operation_results(root: Path) -> None:
    assert (root / "moved/new.txt").read_text(encoding="utf-8") == "move me\n"
    assert (root / "copied.txt").read_text(encoding="utf-8") == "copy me\n"
    assert not (root / "obsolete.txt").exists()
    assert json.loads((root / "config.json").read_text(encoding="utf-8")) == {
        "app": {"enabled": True, "mode": "safe", "name": "demo"},
        "legacy": {},
    }
    settings = tomllib.loads((root / "settings.toml").read_text(encoding="utf-8"))
    assert settings == {"app": {"mode": "safe"}, "legacy": {"enabled": True}}
    assert (root / "message.txt").read_text(encoding="utf-8") == "new marker\n"


def test_registry_rejects_duplicates_and_selects_in_deterministic_order() -> None:
    first = _notice_migration("alpha", 10)
    second = _notice_migration("beta", 10)
    later = _notice_migration("later", 20)

    registry = MigrationRegistry((later, second, first))

    assert registry.ids == ("alpha", "beta", "later")
    selected = registry.select(
        source_version="1.0.0",
        source_commit="a" * 40,
        target_version="1.1.0",
        target_commit="b" * 40,
        applied=("beta",),
    )
    assert tuple(migration.migration_id for migration in selected) == (
        "alpha",
        "later",
    )

    with pytest.raises(LifecycleError, match="Duplicate lifecycle migration id"):
        MigrationRegistry((first, _notice_migration("alpha", 30)))


def test_registry_binds_every_declared_version_and_commit_constraint() -> None:
    migration = replace(
        _notice_migration("commit-bound", 10),
        applies=MigrationRange(
            source_versions=("1.0.0",),
            source_commits=("a" * 40,),
            target_version="1.1.0",
            target_commit="b" * 40,
        ),
    )
    registry = MigrationRegistry((migration,))

    def selected(
        source_commit: str,
        target_commit: str,
        *,
        source_version: str = "1.0.0",
        target_version: str = "1.1.0",
    ) -> tuple[Migration, ...]:
        return registry.select(
            source_version=source_version,
            source_commit=source_commit,
            target_version=target_version,
            target_commit=target_commit,
            applied=(),
        )

    assert selected("a" * 40, "b" * 40, source_version="0.9.0") == ()
    assert selected("c" * 40, "b" * 40) == ()
    assert selected("a" * 40, "b" * 40, target_version="2.0.0") == ()
    assert selected("a" * 40, "c" * 40) == ()
    assert selected("a" * 40, "b" * 40) == (migration,)


@pytest.mark.parametrize(("preconditions", "postconditions"), [((), SAFE_CONDITIONS), (SAFE_CONDITIONS, ())])
def test_registry_requires_preconditions_and_postconditions_for_every_migration(
    preconditions: tuple[MigrationCondition, ...],
    postconditions: tuple[MigrationCondition, ...],
) -> None:
    migration = _migration(
        "missing-contract",
        10,
        (MigrationOperation(kind="record_notice", notice="notice"),),
        preconditions=preconditions,
        postconditions=postconditions,
        architecture_change=True,
    )

    with pytest.raises(LifecycleError, match="non-empty preconditions and postconditions"):
        MigrationRegistry((migration,))


def test_run_migrations_applies_supported_operations_and_conditions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "staging"
    _write_operation_fixture(root)
    migration = _migration(
        "complete-operation-set",
        10,
        _supported_operations(),
        preconditions=_supported_preconditions(),
        postconditions=_supported_postconditions(),
    )

    result = run_migrations(
        root,
        (migration,),
        owned_paths=_owned(
            "old.txt",
            "moved/new.txt",
            "seed.txt",
            "copied.txt",
            "obsolete.txt",
            "config.json",
            "settings.toml",
            "message.txt",
        ),
    )

    assert result.applied_ids == ("complete-operation-set",)
    assert result.notices == ("Review the new layout.",)
    assert result.moves == (("old.txt", "moved/new.txt"),)
    assert result.affected_paths == (
        "config.json",
        "copied.txt",
        "message.txt",
        "moved/new.txt",
        "obsolete.txt",
        "old.txt",
        "settings.toml",
    )
    _assert_supported_operation_results(root)


def test_applied_migration_is_skipped_idempotently(tmp_path: Path) -> None:
    root = tmp_path / "staging"
    root.mkdir()
    (root / "old.txt").write_text("content\n", encoding="utf-8")
    migration = _migration(
        "move-once",
        10,
        (
            MigrationOperation(kind="move_path", source="old.txt", destination="new.txt"),
            MigrationOperation(kind="record_notice", notice="moved"),
        ),
    )

    ownership = _owned("old.txt", "new.txt")
    first = run_migrations(root, (migration,), owned_paths=ownership)
    second = run_migrations(
        root,
        (migration,),
        owned_paths=ownership,
        already_applied=first.applied_ids,
    )

    assert first.applied_ids == ("move-once",)
    assert second.applied_ids == first.applied_ids
    assert second.moves == ()
    assert second.notices == ()
    assert second.affected_paths == ()
    assert not (root / "old.txt").exists()
    assert (root / "new.txt").read_text(encoding="utf-8") == "content\n"


def test_registry_rejects_shell_operations_and_runner_rejects_escape_paths(
    tmp_path: Path,
) -> None:
    shell = _migration(
        "no-shell",
        10,
        (MigrationOperation(kind="shell", value="touch owned"),),
    )
    with pytest.raises(LifecycleError, match="unsupported operation 'shell'"):
        MigrationRegistry((shell,))

    root = tmp_path / "staging"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("preserved\n", encoding="utf-8")
    escape = _migration(
        "no-escape",
        10,
        (MigrationOperation(kind="delete_path", path="../outside.txt"),),
    )

    with pytest.raises(LifecycleError, match="Unsafe lifecycle path"):
        run_migrations(root, (escape,), owned_paths=_owned("outside.txt"))
    assert outside.read_text(encoding="utf-8") == "preserved\n"


def test_runner_rejects_paths_through_external_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "staging"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("preserved\n", encoding="utf-8")
    try:
        (root / "escape").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")
    migration = _migration(
        "no-symlink-escape",
        10,
        (MigrationOperation(kind="delete_path", path="escape/secret.txt"),),
    )

    with pytest.raises(LifecycleError, match="symbolic-link alias|escapes the staging root"):
        run_migrations(root, (migration,), owned_paths=_owned("escape/secret.txt"))
    assert secret.read_text(encoding="utf-8") == "preserved\n"


def test_failed_postcondition_does_not_record_migration_or_change_state(
    tmp_path: Path,
) -> None:
    root = tmp_path / "staging"
    state_path = root / ".template/state.toml"
    state_path.parent.mkdir(parents=True)
    state_path.write_text('applied_migrations = ["prior"]\n', encoding="utf-8")
    (root / "product.txt").write_text("before\n", encoding="utf-8")
    migration = _migration(
        "fails-late",
        10,
        (
            MigrationOperation(
                kind="transform_text",
                path="product.txt",
                old="before",
                new="after",
            ),
        ),
        postconditions=(MigrationCondition(kind="text_contains", path="product.txt", value="never-present"),),
    )
    prior_ids = ("prior",)
    state_before = state_path.read_bytes()

    with pytest.raises(LifecycleError, match="postcondition failed"):
        run_migrations(
            root,
            (migration,),
            owned_paths=_owned("product.txt"),
            already_applied=prior_ids,
        )

    assert prior_ids == ("prior",)
    assert "fails-late" not in prior_ids
    assert state_path.read_bytes() == state_before


def test_final_postconditions_are_revalidated_after_later_plan_changes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "staging"
    root.mkdir()
    path = root / "managed.txt"
    path.write_text("before\n", encoding="utf-8")
    migration = _migration(
        "final-contract",
        10,
        (
            MigrationOperation(
                kind="transform_text",
                path="managed.txt",
                old="before",
                new="after",
            ),
        ),
        preconditions=(MigrationCondition(kind="text_contains", path="managed.txt", value="before"),),
        postconditions=(MigrationCondition(kind="text_contains", path="managed.txt", value="after"),),
    )
    ownership = _owned("managed.txt")
    run_migrations(root, (migration,), owned_paths=ownership)
    path.write_text("later plan output\n", encoding="utf-8")

    with pytest.raises(LifecycleError, match="final postcondition failed"):
        validate_migration_postconditions(root, (migration,), owned_paths=ownership)


def test_text_condition_reports_invalid_utf8_without_leaking_an_absolute_path(tmp_path: Path) -> None:
    root = tmp_path / "staging"
    root.mkdir()
    (root / "managed.txt").write_bytes(b"\xff\xfe")
    migration = _migration(
        "unreadable-condition",
        10,
        (MigrationOperation(kind="record_notice", notice="unreachable"),),
        preconditions=(MigrationCondition(kind="text_contains", path="managed.txt", value="marker"),),
    )

    with pytest.raises(LifecycleError, match=r"^Could not read migration condition path: managed\.txt\.$") as error:
        run_migrations(root, (migration,), owned_paths=_owned("managed.txt"))

    assert str(tmp_path) not in str(error.value)


def test_runner_rejects_product_owned_and_recognized_data_paths(tmp_path: Path) -> None:
    root = tmp_path / "staging"
    root.mkdir()
    (root / "product.txt").write_text("private product code\n", encoding="utf-8")
    (root / "customer.sqlite").write_bytes(b"database")
    product_migration = _migration(
        "reject-product-file",
        10,
        (MigrationOperation(kind="delete_path", path="product.txt"),),
    )
    data_migration = _migration(
        "reject-data-file",
        20,
        (MigrationOperation(kind="delete_path", path="customer.sqlite"),),
    )

    with pytest.raises(LifecycleError, match="product-owned"):
        run_migrations(root, (product_migration,), owned_paths=_owned())
    with pytest.raises(LifecycleError, match="protected path|product or user data"):
        run_migrations(root, (data_migration,), owned_paths=_owned("customer.sqlite"))
    assert (root / "product.txt").exists()
    assert (root / "customer.sqlite").exists()


def test_runner_rejects_internal_symlink_aliases(tmp_path: Path) -> None:
    root = tmp_path / "staging"
    managed = root / "managed"
    managed.mkdir(parents=True)
    (managed / "message.txt").write_text("before\n", encoding="utf-8")
    try:
        (root / "alias").symlink_to(managed, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")
    migration = _migration(
        "reject-alias",
        10,
        (
            MigrationOperation(
                kind="transform_text",
                path="alias/message.txt",
                old="before",
                new="after",
            ),
        ),
    )

    with pytest.raises(LifecycleError, match="symbolic-link alias"):
        run_migrations(root, (migration,), owned_paths=_owned("alias/message.txt"))
    assert (managed / "message.txt").read_text(encoding="utf-8") == "before\n"


@pytest.mark.parametrize(
    ("extra_path", "is_directory", "error"),
    [
        (".env", False, "protected path"),
        ("product.txt", False, "product-owned"),
        ("empty-product-directory", True, "product-owned"),
    ],
)
def test_recursive_migration_rejects_protected_or_unowned_descendants(
    tmp_path: Path, extra_path: str, is_directory: bool, error: str
) -> None:
    root = tmp_path / "staging"
    source = root / "old"
    source.mkdir(parents=True)
    (source / "managed.txt").write_text("managed\n", encoding="utf-8")
    extra = source / extra_path
    if is_directory:
        extra.mkdir()
    else:
        extra.write_text("product data\n", encoding="utf-8")
    migration = _migration(
        "reject-recursive-secret",
        10,
        (MigrationOperation(kind="move_path", source="old", destination="new"),),
    )

    with pytest.raises(LifecycleError, match=error):
        run_migrations(
            root,
            (migration,),
            owned_paths=_owned("old/managed.txt", "new/managed.txt"),
        )
    assert source.is_dir()
    assert not (root / "new").exists()
