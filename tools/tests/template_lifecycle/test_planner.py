from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tools.template_lifecycle.manifest import create_manifest
from tools.template_lifecycle.model import LifecycleError, PlanOperation, UpdatePlan
from tools.template_lifecycle.planner import PlanRequest, create_update_plan


@dataclass(frozen=True, slots=True)
class TreeCase:
    base: dict[str, bytes]
    local: dict[str, bytes]
    incoming: dict[str, bytes]
    base_executable: frozenset[str] = field(default_factory=frozenset)
    local_executable: frozenset[str] = field(default_factory=frozenset)
    incoming_executable: frozenset[str] = field(default_factory=frozenset)


def _write_tree(root: Path, files: dict[str, bytes], executable: frozenset[str]) -> None:
    root.mkdir(parents=True)
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o755 if relative in executable else 0o644)


def _create_plan(
    tmp_path: Path,
    case: TreeCase,
    *,
    name: str = "case",
    moves: tuple[tuple[str, str], ...] = (),
) -> tuple[UpdatePlan, Path, Path, Path]:
    base = tmp_path / name / "base"
    local = tmp_path / name / "local"
    incoming = tmp_path / name / "incoming"
    _write_tree(base, case.base, case.base_executable)
    _write_tree(local, case.local, case.local_executable)
    _write_tree(incoming, case.incoming, case.incoming_executable)
    plan, _manifest = create_update_plan(
        PlanRequest(
            base_root=base,
            local_root=local,
            incoming_root=incoming,
            baseline_manifest=create_manifest(base),
            baseline_commit="a" * 40,
            target_commit="b" * 40,
            target_version="1.1.0",
            moves=moves,
        )
    )
    return plan, base, local, incoming


def _product_operations(plan: UpdatePlan) -> tuple[PlanOperation, ...]:
    return tuple(operation for operation in plan.operations if operation.action != "STATE_UPDATE")


def _single_operation(plan: UpdatePlan) -> PlanOperation:
    operations = _product_operations(plan)
    assert len(operations) == 1
    return operations[0]


def _snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(root).as_posix(): (
            path.read_bytes(),
            path.stat().st_mode & 0o777,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_case_a_unchanged_template_produces_no_file_operation(tmp_path: Path) -> None:
    content = b"template baseline\n"
    plan, *_roots = _create_plan(
        tmp_path,
        TreeCase(
            base={"managed.txt": content},
            local={"managed.txt": content},
            incoming={"managed.txt": content},
        ),
    )

    assert _product_operations(plan) == ()


def test_case_b_only_template_change_updates_unchanged_product(tmp_path: Path) -> None:
    plan, *_roots = _create_plan(
        tmp_path,
        TreeCase(
            base={"managed.txt": b"old\n"},
            local={"managed.txt": b"old\n"},
            incoming={"managed.txt": b"new\n"},
        ),
    )

    operation = _single_operation(plan)
    assert operation.action == "UPDATE"
    assert operation.result == b"new\n"


def test_case_c_local_already_equals_incoming_is_a_noop(tmp_path: Path) -> None:
    plan, *_roots = _create_plan(
        tmp_path,
        TreeCase(
            base={"managed.txt": b"old\n"},
            local={"managed.txt": b"new\n"},
            incoming={"managed.txt": b"new\n"},
        ),
    )

    assert _product_operations(plan) == ()


def test_case_d_only_product_change_is_preserved(tmp_path: Path) -> None:
    plan, *_roots = _create_plan(
        tmp_path,
        TreeCase(
            base={"managed.txt": b"old\n"},
            local={"managed.txt": b"product\n"},
            incoming={"managed.txt": b"old\n"},
        ),
    )

    assert _product_operations(plan) == ()


def test_case_e_non_overlapping_changes_produce_a_real_merge(tmp_path: Path) -> None:
    plan, *_roots = _create_plan(
        tmp_path,
        TreeCase(
            base={"managed.txt": b"local=old\nkeep-a\nkeep-b\nincoming=old\n"},
            local={"managed.txt": b"local=new\nkeep-a\nkeep-b\nincoming=old\n"},
            incoming={"managed.txt": b"local=old\nkeep-a\nkeep-b\nincoming=new\n"},
        ),
    )

    operation = _single_operation(plan)
    assert operation.action == "MERGE"
    assert operation.result == b"local=new\nkeep-a\nkeep-b\nincoming=new\n"
    assert not plan.conflicts


def test_case_f_conflict_planning_never_writes_product_files(tmp_path: Path) -> None:
    plan, _base, local, _incoming = _create_plan(
        tmp_path,
        TreeCase(
            base={"managed.txt": b"value=old\n"},
            local={"managed.txt": b"value=product\n", "product-only.txt": b"keep\n"},
            incoming={"managed.txt": b"value=template\n"},
        ),
    )
    before = {
        "managed.txt": (b"value=product\n", 0o644),
        "product-only.txt": (b"keep\n", 0o644),
    }

    operation = _single_operation(plan)

    assert operation.action == "CONFLICT"
    assert operation.conflict_result is not None
    assert b"<<<<<<< LOCAL" in operation.conflict_result
    assert _snapshot(local) == before


def test_case_g_template_deletion_deletes_unchanged_file(tmp_path: Path) -> None:
    plan, *_roots = _create_plan(
        tmp_path,
        TreeCase(
            base={"obsolete.txt": b"old\n"},
            local={"obsolete.txt": b"old\n"},
            incoming={},
        ),
    )

    assert _single_operation(plan).action == "DELETE"


def test_case_g_template_deletion_conflicts_with_product_change(tmp_path: Path) -> None:
    plan, *_roots = _create_plan(
        tmp_path,
        TreeCase(
            base={"obsolete.txt": b"old\n"},
            local={"obsolete.txt": b"product change\n"},
            incoming={},
        ),
    )

    operation = _single_operation(plan)
    assert operation.action == "CONFLICT"
    assert "deleted" in operation.reason


def test_case_h_template_addition_adds_free_path(tmp_path: Path) -> None:
    plan, *_roots = _create_plan(
        tmp_path,
        TreeCase(base={}, local={}, incoming={"new.txt": b"template\n"}),
    )

    operation = _single_operation(plan)
    assert operation.action == "ADD"
    assert operation.result == b"template\n"


def test_case_h_template_addition_conflicts_with_product_owned_path(
    tmp_path: Path,
) -> None:
    plan, *_roots = _create_plan(
        tmp_path,
        TreeCase(
            base={},
            local={"new.txt": b"product\n"},
            incoming={"new.txt": b"template\n"},
        ),
    )

    operation = _single_operation(plan)
    assert operation.action == "CONFLICT"
    assert "product-owned" in operation.reason


def test_product_owned_paths_outside_base_and_incoming_are_ignored(
    tmp_path: Path,
) -> None:
    plan, _base, local, _incoming = _create_plan(
        tmp_path,
        TreeCase(
            base={"managed.txt": b"same\n"},
            local={"managed.txt": b"same\n", "custom/data.txt": b"product data\n"},
            incoming={"managed.txt": b"same\n"},
        ),
    )

    assert _product_operations(plan) == ()
    assert (local / "custom" / "data.txt").read_bytes() == b"product data\n"


@pytest.mark.parametrize(
    ("local", "incoming", "expected"),
    [
        (b"\x00base", b"\x00template", "UPDATE"),
        (b"\x00product", b"\x00base", None),
        (b"\x00template", b"\x00template", None),
        (b"\x00product", b"\x00template", "CONFLICT"),
    ],
)
def test_case_i_binary_files_are_never_text_merged(
    tmp_path: Path,
    local: bytes,
    incoming: bytes,
    expected: str | None,
) -> None:
    plan, *_roots = _create_plan(
        tmp_path,
        TreeCase(
            base={"asset.bin": b"\x00base"},
            local={"asset.bin": local},
            incoming={"asset.bin": incoming},
        ),
    )

    operations = _product_operations(plan)
    if expected is None:
        assert operations == ()
    else:
        assert _single_operation(plan).action == expected
    assert all(operation.action != "MERGE" for operation in operations)


@pytest.mark.skipif(os.name == "nt", reason="Windows does not expose POSIX executable bits")
def test_case_j_template_executable_mode_change_is_planned(tmp_path: Path) -> None:
    case = TreeCase(
        base={"script.sh": b"#!/bin/sh\nexit 0\n"},
        local={"script.sh": b"#!/bin/sh\nexit 0\n"},
        incoming={"script.sh": b"#!/bin/sh\nexit 0\n"},
        incoming_executable=frozenset({"script.sh"}),
    )
    plan, *_roots = _create_plan(tmp_path, case)

    operation = _single_operation(plan)
    assert operation.action == "UPDATE"
    assert operation.executable is True


@pytest.mark.skipif(os.name == "nt", reason="Windows does not expose POSIX executable bits")
def test_case_j_product_executable_mode_change_is_preserved(tmp_path: Path) -> None:
    case = TreeCase(
        base={"script.sh": b"#!/bin/sh\nexit 0\n"},
        local={"script.sh": b"#!/bin/sh\nexit 0\n"},
        incoming={"script.sh": b"#!/bin/sh\nexit 0\n"},
        local_executable=frozenset({"script.sh"}),
    )
    plan, *_roots = _create_plan(tmp_path, case)

    assert _product_operations(plan) == ()


def test_crlf_only_local_difference_does_not_trigger_uncontrolled_rewrite(
    tmp_path: Path,
) -> None:
    plan, *_roots = _create_plan(
        tmp_path,
        TreeCase(
            base={"managed.txt": b"value=old\n"},
            local={"managed.txt": b"value=old\r\n"},
            incoming={"managed.txt": b"value=new\n"},
        ),
    )

    operation = _single_operation(plan)
    assert operation.action == "UPDATE"
    assert operation.result == b"value=new\r\n"


def test_reconstructed_baseline_accepts_semantically_equal_line_endings(
    tmp_path: Path,
) -> None:
    recorded = tmp_path / "recorded"
    base = tmp_path / "base"
    local = tmp_path / "local"
    incoming = tmp_path / "incoming"
    _write_tree(recorded, {"managed.txt": b"same\r\n"}, frozenset())
    _write_tree(base, {"managed.txt": b"same\n"}, frozenset())
    _write_tree(local, {"managed.txt": b"same\r\n"}, frozenset())
    _write_tree(incoming, {"managed.txt": b"same\n"}, frozenset())

    plan, _manifest = create_update_plan(
        PlanRequest(
            base_root=base,
            local_root=local,
            incoming_root=incoming,
            baseline_manifest=create_manifest(recorded),
            baseline_commit="a" * 40,
            target_commit="b" * 40,
            target_version="1.1.0",
        )
    )

    assert _product_operations(plan) == ()


def test_reconstructed_product_version_is_authoritative_over_recorded_hash(
    tmp_path: Path,
) -> None:
    recorded = tmp_path / "recorded"
    base = tmp_path / "base"
    local = tmp_path / "local"
    incoming = tmp_path / "incoming"
    _write_tree(recorded, {"VERSION": b"1.0.0\n"}, frozenset())
    for root in (base, local, incoming):
        _write_tree(root, {"VERSION": b"0.7.3\n"}, frozenset())

    plan, _manifest = create_update_plan(
        PlanRequest(
            base_root=base,
            local_root=local,
            incoming_root=incoming,
            baseline_manifest=create_manifest(recorded),
            baseline_commit="a" * 40,
            target_commit="b" * 40,
            target_version="1.1.0",
        )
    )

    assert _product_operations(plan) == ()


def test_declared_rename_produces_one_move_instead_of_add_and_delete(
    tmp_path: Path,
) -> None:
    plan, *_roots = _create_plan(
        tmp_path,
        TreeCase(
            base={"old.txt": b"same\n"},
            local={"old.txt": b"same\n"},
            incoming={"new.txt": b"same\n"},
        ),
        moves=(("old.txt", "new.txt"),),
    )

    operation = _single_operation(plan)
    assert operation.action == "MOVE"
    assert operation.source_path == "old.txt"
    assert operation.path == "new.txt"
    assert operation.result == b"same\n"


def test_declared_rename_accepts_a_migration_transformed_local_tree(
    tmp_path: Path,
) -> None:
    plan, *_roots = _create_plan(
        tmp_path,
        TreeCase(
            base={"old.txt": b"baseline\n"},
            local={"new.txt": b"product edit\n"},
            incoming={"new.txt": b"baseline\n"},
        ),
        moves=(("old.txt", "new.txt"),),
    )

    operation = _single_operation(plan)
    assert operation.action == "MOVE"
    assert operation.source_path == "old.txt"
    assert operation.path == "new.txt"
    assert operation.result == b"product edit\n"


def test_declared_directory_rename_expands_to_file_moves_and_additions(
    tmp_path: Path,
) -> None:
    plan, *_roots = _create_plan(
        tmp_path,
        TreeCase(
            base={
                "old/one.txt": b"same\n",
                "old/nested/two.txt": b"baseline\n",
            },
            local={
                "old/one.txt": b"same\n",
                "old/nested/two.txt": b"product edit\n",
            },
            incoming={
                "new/one.txt": b"same\n",
                "new/nested/two.txt": b"baseline\n",
                "new/template-added.txt": b"added\n",
            },
        ),
        moves=(("old", "new"),),
    )

    operations = _product_operations(plan)
    assert [(operation.action, operation.source_path, operation.path) for operation in operations] == [
        ("MOVE", "old/nested/two.txt", "new/nested/two.txt"),
        ("MOVE", "old/one.txt", "new/one.txt"),
        ("ADD", None, "new/template-added.txt"),
    ]
    assert operations[0].result == b"product edit\n"


def test_declared_directory_rename_requires_every_owned_source_in_incoming(
    tmp_path: Path,
) -> None:
    case = TreeCase(
        base={"old/kept.txt": b"keep\n", "old/missing.txt": b"missing\n"},
        local={"old/kept.txt": b"keep\n", "old/missing.txt": b"missing\n"},
        incoming={"new/kept.txt": b"keep\n"},
    )

    with pytest.raises(LifecycleError, match="destination is absent from incoming"):
        _create_plan(tmp_path, case, moves=(("old", "new"),))


def test_declared_directory_rename_reports_file_destination_collision(
    tmp_path: Path,
) -> None:
    plan, *_roots = _create_plan(
        tmp_path,
        TreeCase(
            base={"old/managed.txt": b"baseline\n"},
            local={
                "old/managed.txt": b"baseline\n",
                "new/managed.txt": b"product-owned\n",
            },
            incoming={"new/managed.txt": b"baseline\n"},
        ),
        moves=(("old", "new"),),
    )

    operation = _single_operation(plan)
    assert operation.action == "CONFLICT"
    assert operation.source_path == "old/managed.txt"
    assert operation.path == "new/managed.txt"


def test_declared_rename_preserves_a_product_only_edit(tmp_path: Path) -> None:
    plan, *_roots = _create_plan(
        tmp_path,
        TreeCase(
            base={"old.txt": b"baseline\n"},
            local={"old.txt": b"product edit\n"},
            incoming={"new.txt": b"baseline\n"},
        ),
        moves=(("old.txt", "new.txt"),),
    )

    operation = _single_operation(plan)
    assert operation.action == "MOVE"
    assert operation.result == b"product edit\n"


def test_declared_rename_merges_non_overlapping_changes(tmp_path: Path) -> None:
    plan, *_roots = _create_plan(
        tmp_path,
        TreeCase(
            base={"old.txt": b"local=old\nkeep-a\nkeep-b\nincoming=old\n"},
            local={"old.txt": b"local=new\nkeep-a\nkeep-b\nincoming=old\n"},
            incoming={"new.txt": b"local=old\nkeep-a\nkeep-b\nincoming=new\n"},
        ),
        moves=(("old.txt", "new.txt"),),
    )

    operation = _single_operation(plan)
    assert operation.action == "MOVE"
    assert operation.source_path == "old.txt"
    assert operation.result == b"local=new\nkeep-a\nkeep-b\nincoming=new\n"


def test_declared_rename_destination_collision_is_read_only(tmp_path: Path) -> None:
    plan, _base, local, _incoming = _create_plan(
        tmp_path,
        TreeCase(
            base={"old.txt": b"baseline\n"},
            local={"old.txt": b"baseline\n", "new.txt": b"product owned\n"},
            incoming={"new.txt": b"baseline\n"},
        ),
        moves=(("old.txt", "new.txt"),),
    )
    before = _snapshot(local)

    operation = _single_operation(plan)

    assert operation.action == "CONFLICT"
    assert operation.source_path == "old.txt"
    assert "collides" in operation.reason
    assert _snapshot(local) == before


def test_declared_rename_overlapping_changes_stage_conflict_only(
    tmp_path: Path,
) -> None:
    plan, _base, local, _incoming = _create_plan(
        tmp_path,
        TreeCase(
            base={"old.txt": b"value=old\n"},
            local={"old.txt": b"value=product\n"},
            incoming={"new.txt": b"value=template\n"},
        ),
        moves=(("old.txt", "new.txt"),),
    )
    before = _snapshot(local)

    operation = _single_operation(plan)

    assert operation.action == "CONFLICT"
    assert operation.source_path == "old.txt"
    assert operation.conflict_result is not None
    assert _snapshot(local) == before


def test_operations_have_deterministic_path_order_with_state_last(
    tmp_path: Path,
) -> None:
    old = {path: b"old\n" for path in ("z.txt", "a.txt", "nested/m.txt")}
    new = {path: b"new\n" for path in reversed(tuple(old))}
    plan, *_roots = _create_plan(
        tmp_path,
        TreeCase(base=old, local=dict(reversed(tuple(old.items()))), incoming=new),
    )

    assert [(operation.action, operation.path) for operation in plan.operations] == [
        ("UPDATE", "a.txt"),
        ("UPDATE", "nested/m.txt"),
        ("UPDATE", "z.txt"),
        ("STATE_UPDATE", ".template/state.toml"),
    ]
