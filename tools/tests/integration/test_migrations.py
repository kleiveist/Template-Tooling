from __future__ import annotations

import pytest

from tools.integration.migrations import (
    ConditionKind,
    Migration,
    MigrationApplicability,
    MigrationCondition,
    MigrationRegistry,
    StructuredKeyAllowlist,
    build_migration_run,
    validate_postconditions,
    validate_preconditions,
)
from tools.integration.model import (
    MigrationError,
    Operation,
    OperationKind,
    Ownership,
    StructuredChange,
)
from tools.integration.planner import ObservedResource

DIGEST = "a" * 64


def _migration(
    identifier: str,
    order: int = 10,
    *,
    operation: Operation | None = None,
    structured_key_allowlist: tuple[StructuredKeyAllowlist, ...] = (),
) -> Migration:
    path = "tools/resources/config/policy.toml"
    return Migration(
        migration_id=identifier,
        description="Update portable tooling policy",
        order=order,
        applies=MigrationApplicability(
            source_tooling_versions=("0.1.0",),
            target_tooling_version="0.2.0",
            source_state_schemas=(1,),
            target_state_schema=2,
        ),
        operations=(
            operation
            or Operation(
                OperationKind.UPDATE,
                path,
                Ownership.TOOLING,
                b"updated\n",
                expected_sha256=DIGEST,
            ),
        ),
        preconditions=(
            MigrationCondition(ConditionKind.PATH_EXISTS, path, Ownership.TOOLING),
        ),
        postconditions=(
            MigrationCondition(ConditionKind.PATH_EXISTS, path, Ownership.TOOLING),
        ),
        structured_key_allowlist=structured_key_allowlist,
    )


def test_registry_order_and_applied_idempotency() -> None:
    registry = MigrationRegistry((_migration("later", 20), _migration("earlier", 10)))

    selected = registry.select(
        source_tooling_version="0.1.0",
        target_tooling_version="0.2.0",
        source_state_schema=1,
        target_state_schema=2,
        applied=("earlier",),
    )
    run = build_migration_run(
        registry,
        source_tooling_version="0.1.0",
        target_tooling_version="0.2.0",
        source_state_schema=1,
        target_state_schema=2,
        applied=("earlier",),
    )

    assert [item.migration_id for item in selected] == ["later"]
    assert run.applied_ids == ("earlier",)
    assert run.resulting_applied_ids == ("earlier", "later")
    assert build_migration_run(
        registry,
        source_tooling_version="0.1.0",
        target_tooling_version="0.2.0",
        source_state_schema=1,
        target_state_schema=2,
        applied=run.resulting_applied_ids,
    ).is_noop


def test_registry_rejects_duplicate_ids_and_project_writes() -> None:
    with pytest.raises(MigrationError, match="Duplicate"):
        MigrationRegistry((_migration("same", 1), _migration("same", 2)))

    unsafe = Operation(
        OperationKind.UPDATE, "frontend/src/main.ts", Ownership.PROJECT, b"x", DIGEST
    )
    with pytest.raises(MigrationError, match="project-owned"):
        MigrationRegistry((_migration("unsafe-project", operation=unsafe),))


def test_registry_rejects_shell_or_structured_replacement_operations() -> None:
    shell = Operation("SHELL", "tools/script", Ownership.TOOLING)
    replacement = Operation(
        OperationKind.UPDATE, "package.json", Ownership.STRUCTURED, b"{}", DIGEST
    )

    with pytest.raises(MigrationError, match="unsupported operation"):
        MigrationRegistry((_migration("no-shell", operation=shell),))
    with pytest.raises(MigrationError, match="only use UPDATE for tooling-owned"):
        MigrationRegistry((_migration("no-replacement", operation=replacement),))


def test_structured_patch_requires_known_keys_and_preimage() -> None:
    patch = Operation(
        OperationKind.PATCH,
        "package.json",
        Ownership.STRUCTURED,
        expected_sha256=DIGEST,
        structured_changes=(StructuredChange("scripts.quality", "quality"),),
    )

    policy = (StructuredKeyAllowlist("package.json", ("scripts.quality",)),)
    assert MigrationRegistry(
        (
            _migration(
                "structured-patch", operation=patch, structured_key_allowlist=policy
            ),
        )
    ).ids == ("structured-patch",)

    missing_preimage = Operation(
        OperationKind.PATCH,
        "package.json",
        Ownership.STRUCTURED,
        structured_changes=(StructuredChange("scripts.quality", "quality"),),
    )
    with pytest.raises(MigrationError, match="preimage"):
        MigrationRegistry(
            (
                _migration(
                    "missing-preimage",
                    operation=missing_preimage,
                    structured_key_allowlist=policy,
                ),
            )
        )


def test_structured_patch_rejects_undeclared_keys() -> None:
    patch = Operation(
        OperationKind.PATCH,
        "package.json",
        Ownership.STRUCTURED,
        expected_sha256=DIGEST,
        structured_changes=(StructuredChange("dependencies.foreign", "1.0.0"),),
    )

    with pytest.raises(MigrationError, match="undeclared structured keys"):
        MigrationRegistry(
            (
                _migration(
                    "structured-allowlist",
                    operation=patch,
                    structured_key_allowlist=(
                        StructuredKeyAllowlist("package.json", ("scripts.quality",)),
                    ),
                ),
            )
        )


def test_preconditions_and_postconditions_are_evaluated_without_shells() -> None:
    path = "tools/resources/config/policy.toml"
    migration = _migration("condition-check")
    observed = (ObservedResource(path, Ownership.TOOLING, sha256=DIGEST),)

    validate_preconditions(migration, observed)
    validate_postconditions(migration, observed)
    with pytest.raises(MigrationError, match="precondition failed"):
        validate_preconditions(migration, ())
    with pytest.raises(MigrationError, match="precondition failed"):
        validate_preconditions(
            migration,
            (ObservedResource(path, Ownership.PROJECT, sha256=DIGEST),),
        )


@pytest.mark.parametrize(
    "operation",
    (
        Operation(
            OperationKind.ADD,
            "tools/new.txt",
            Ownership.TOOLING,
            b"new",
            DIGEST,
        ),
        Operation(
            OperationKind.UPDATE,
            "tools/item.txt",
            Ownership.TOOLING,
            b"new",
            DIGEST,
            structured_changes=(StructuredChange("unsafe", True),),
        ),
        Operation(
            OperationKind.DELETE,
            "tools/item.txt",
            Ownership.TOOLING,
            expected_sha256=DIGEST,
            source_path="tools/source.txt",
        ),
    ),
)
def test_registry_rejects_operations_the_transaction_would_reject(
    operation: Operation,
) -> None:
    with pytest.raises(MigrationError):
        MigrationRegistry((_migration("contract-parity", operation=operation),))


def test_structured_condition_reads_only_declared_key() -> None:
    path = "package.json"
    condition = MigrationCondition(
        ConditionKind.STRUCTURED_EQUALS,
        path,
        Ownership.STRUCTURED,
        key="scripts.quality",
        value="quality",
    )
    migration = Migration(
        "structured-condition",
        "Check a known key",
        1,
        MigrationApplicability(source_state_schemas=(1,), target_state_schema=2),
        (
            Operation(
                OperationKind.PATCH,
                path,
                Ownership.STRUCTURED,
                expected_sha256=DIGEST,
                structured_changes=(StructuredChange("scripts.quality", "new"),),
            ),
        ),
        (condition,),
        (condition,),
        (StructuredKeyAllowlist(path, ("scripts.quality",)),),
    )

    validate_preconditions(
        migration,
        (
            ObservedResource(
                path,
                Ownership.STRUCTURED,
                sha256=DIGEST,
                structured_values={"scripts": {"quality": "quality"}},
            ),
        ),
    )
