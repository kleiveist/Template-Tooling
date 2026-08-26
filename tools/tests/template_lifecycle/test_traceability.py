"""Executable LC-001 through LC-020 acceptance-to-test traceability map."""

from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]

TRACEABILITY: dict[str, tuple[str, ...]] = {
    "LC-001": (
        "tools/tests/template_lifecycle/test_generation.py::test_every_profile_gets_valid_deterministic_lifecycle_metadata",
        "tools/tests/template_lifecycle/test_status.py::test_status_with_local_source_and_verify_are_offline",
    ),
    "LC-002": (
        "tools/tests/template_lifecycle/test_state.py::test_state_render_write_and_digest_are_deterministic",
        "tools/tests/template_lifecycle/test_manifest.py::test_manifest_is_deterministic_sorted_and_content_free",
    ),
    "LC-003": (
        "tools/tests/template_lifecycle/test_scaffold.py::test_reconstruction_uses_exact_commit_and_preserves_product_metadata",
        "tools/tests/template_lifecycle/test_integration.py::test_end_to_end_plan_update_verify_and_repeat_noop",
    ),
    "LC-004": (
        "tools/tests/template_lifecycle/test_scaffold.py::test_reconstruction_uses_exact_commit_and_preserves_product_metadata",
        "tools/tests/template_lifecycle/test_verify.py::test_verify_reports_profile_capability_identity_and_version_drift",
    ),
    "LC-005": ("tools/tests/template_lifecycle/test_audit.py::test_audit_works_without_state_and_is_read_only",),
    "LC-006": (
        "tools/tests/template_lifecycle/test_adopt.py::test_adoption_writes_only_deterministic_lifecycle_metadata",
    ),
    "LC-007": (
        "tools/tests/template_lifecycle/test_planner.py::test_case_e_non_overlapping_changes_produce_a_real_merge",
        "tools/tests/template_lifecycle/test_integration.py::test_end_to_end_plan_update_verify_and_repeat_noop",
    ),
    "LC-008": (
        "tools/tests/template_lifecycle/test_planner.py::test_product_owned_paths_outside_base_and_incoming_are_ignored",
        "tools/tests/template_lifecycle/test_integration.py::test_end_to_end_plan_update_verify_and_repeat_noop",
    ),
    "LC-009": (
        "tools/tests/template_lifecycle/test_integration.py::test_conflict_blocks_all_product_and_state_changes",
        "tools/tests/template_lifecycle/test_apply.py::test_conflicting_plan_performs_no_writes",
    ),
    "LC-010": (
        "tools/tests/template_lifecycle/test_planner.py::test_case_i_binary_files_are_never_text_merged",
        "tools/tests/template_lifecycle/test_planner.py::test_case_j_template_executable_mode_change_is_planned",
    ),
    "LC-011": (
        "tools/tests/template_lifecycle/test_migrations.py::test_registry_rejects_duplicates_and_selects_in_deterministic_order",
        "tools/tests/template_lifecycle/test_migrations.py::test_applied_migration_is_skipped_idempotently",
    ),
    "LC-012": (
        "tools/tests/template_lifecycle/test_apply.py::test_verifier_failure_rolls_back_migration_move_source_and_destination",
        "tools/tests/template_lifecycle/test_report.py::test_successful_update_report_preserves_pre_apply_update_and_delete_diff",
    ),
    "LC-013": (
        "tools/tests/template_lifecycle/test_apply.py::test_successful_apply_writes_state_last_and_verifies_once_per_tree",
    ),
    "LC-014": (
        "tools/tests/template_lifecycle/test_integration.py::test_end_to_end_plan_update_verify_and_repeat_noop",
        "tools/tests/template_lifecycle/test_apply.py::test_noop_apply_only_verifies_and_does_not_write",
    ),
    "LC-015": (
        "tools/tests/template_lifecycle/test_service.py::test_plan_marks_changed_profile_meaning_without_migration_as_conflict",
        "tools/tests/template_lifecycle/test_service.py::test_architecture_update_requires_flag_then_accepts_explicit_migration",
    ),
    "LC-016": (
        "tools/tests/template_lifecycle/test_generation.py::test_every_profile_gets_valid_deterministic_lifecycle_metadata",
        "tools/tests/test_ci_workflows.py::test_profile_matrix_generates_and_tests_every_profile",
    ),
    "LC-017": (
        "tools/tests/template_lifecycle/test_status.py::test_status_with_local_source_and_verify_are_offline",
        "tools/tests/template_lifecycle/test_source.py::test_git_subprocesses_never_use_shell_or_network_commands",
    ),
    "LC-018": (
        "tools/tests/template_lifecycle/test_manifest.py::test_manifest_rejects_direct_and_parent_external_symlinks",
        "tools/tests/template_lifecycle/test_report.py::test_report_redacts_json_secrets_and_absolute_developer_paths",
        "tools/tests/template_lifecycle/test_service.py::test_update_rejects_branch_that_moves_between_plan_and_apply",
    ),
    "LC-019": (
        "tools/tests/template_lifecycle/test_cli.py::test_every_lifecycle_subcommand_has_help",
        "tools/tests/template_lifecycle/test_report.py::test_report_writes_five_required_files_with_pre_apply_diff",
        "tools/tests/test_docs_index.py::test_navigation_check_accepts_complete_indices_and_backlinks",
        "tools/tests/test_readme_onboarding.py::test_all_local_readme_links_resolve",
    ),
    "LC-020": (
        "tools/tests/test_ci_workflows.py::test_core_ci_uses_supported_runtimes_and_public_tooling",
        "tools/tests/test_ci_workflows.py::test_profile_matrix_generates_and_tests_every_profile",
        "tools/tests/test_ci_workflows.py::test_release_validation_is_explicit_and_never_publishes",
        "tools/tests/test_container_release.py::test_release_check_accepts_canonical_master_template_identity",
    ),
}


def test_every_lifecycle_acceptance_id_references_existing_tests() -> None:
    expected_ids = {f"LC-{number:03d}" for number in range(1, 21)}
    assert set(TRACEABILITY) == expected_ids

    for requirement_id, references in TRACEABILITY.items():
        assert references, f"{requirement_id} has no test evidence"
        for reference in references:
            relative_path, test_name = reference.split("::", maxsplit=1)
            test_path = REPOSITORY_ROOT / relative_path
            assert test_path.is_file(), f"{requirement_id}: missing {relative_path}"
            source = test_path.read_text(encoding="utf-8")
            assert f"def {test_name}(" in source, f"{requirement_id}: missing {reference}"
