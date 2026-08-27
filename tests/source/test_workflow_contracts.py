"""Source-only contracts for the hosted portable-CI topology.

These tests deliberately validate the repository's own workflows.  They live
outside ``tools/`` so an exported customer payload never mistakes a source
repository policy check for customer-facing evidence.
"""

from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = REPOSITORY_ROOT / ".github" / "workflows"
SETUP_ACTION = (
    REPOSITORY_ROOT / ".github" / "actions" / "setup-tooling-environment" / "action.yml"
)
REQUIRED_WORKFLOWS = frozenset(
    {
        "ci-quality.yml",
        "ci-core.yml",
        "ci-system.yml",
        "ci-acceptance.yml",
        "ci-upgrade.yml",
        "ci-documentation.yml",
        "ci-nightly.yml",
        "release.yml",
        "_portable-acceptance.yml",
    }
)
_PINNED_ACTION = re.compile(r"^actions/[A-Za-z0-9_-]+@[0-9a-f]{40}$")
_USES = re.compile(r"^\s*uses:\s*([^\s#]+)", re.MULTILINE)
_NODE24_UPLOAD_ARTIFACT = (
    "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
)


def _workflows() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(WORKFLOW_ROOT.glob("*.y*ml"))
    }


def test_required_portable_ci_workflows_are_present() -> None:
    workflows = _workflows()

    assert set(workflows) == REQUIRED_WORKFLOWS
    assert "workflow_call:" in workflows["_portable-acceptance.yml"]
    assert (
        "uses: ./.github/workflows/_portable-acceptance.yml"
        in workflows["ci-acceptance.yml"]
    )
    assert "final-ci-gate" in workflows["ci-acceptance.yml"]
    assert "real-version-upgrade" in workflows["ci-upgrade.yml"]
    assert "documentation-build" in workflows["ci-documentation.yml"]
    assert "schedule:" in workflows["ci-nightly.yml"]
    assert 'tags: ["tooling-v*"]' in workflows["release.yml"]
    skip_audit = _job_block(workflows["ci-quality.yml"], "skip-audit")
    assert "tests/source/test_skip_policy.py" in skip_audit
    assert "skip-audit.junit.xml" in skip_audit


def test_reusable_acceptance_pins_build_runtimes_and_separates_profile_matrix() -> None:
    reusable = _workflows()["_portable-acceptance.yml"]

    for workflow_input in (
        "python-version:",
        "node-version:",
        "rust-version:",
        "fixture-selection:",
        "profile-selection:",
    ):
        assert workflow_input in reusable
    assert "node-version: ${{ inputs.node-version }}" in reusable
    assert "rust-version: ${{ inputs.rust-version }}" in reusable
    assert "PROFILE_SELECTION: ${{ inputs.profile-selection }}" in reusable

    profile_step = _step_block(
        reusable,
        "Run independent structured profile integration acceptance",
    )
    assert "tools/tests/acceptance/test_profile_integration.py" in profile_step
    assert "portable-profile-integration.junit.xml" in profile_step
    assert "$env:PROFILE_SELECTION" in profile_step
    assert "FIXTURE_SELECTION" not in profile_step
    assert "portable-profile-integration.junit.xml" in reusable


def test_reusable_acceptance_callers_pass_central_node_and_rust_versions() -> None:
    for name, content in _workflows().items():
        if "uses: ./.github/workflows/_portable-acceptance.yml" not in content:
            continue
        for job in _job_blocks(content):
            if "uses: ./.github/workflows/_portable-acceptance.yml" not in job:
                continue
            assert (
                "node-version: ${{ needs.support-matrix.outputs.node_primary }}" in job
            ), (name, job)
            assert (
                "rust-version: ${{ needs.support-matrix.outputs.rust_channel }}" in job
            ), (name, job)
            assert "profile-selection:" in job, (name, job)

    acceptance = _workflows()["ci-acceptance.yml"]
    assert "node_primary: ${{ steps.matrix.outputs.node_primary }}" in acceptance
    assert "rust_channel: ${{ steps.matrix.outputs.rust_channel }}" in acceptance
    copy_matrix = _job_block(acceptance, "acceptance-copy-matrix")
    assert "profile-selection: web-only or desktop-local" in copy_matrix
    assert "github.event_name == 'pull_request'" in copy_matrix
    profile_matrix = _job_block(acceptance, "acceptance-profile-integration")
    assert (
        "profile-selection: ${{ github.event_name == 'pull_request'" in profile_matrix
    )
    windows = _job_block(acceptance, "acceptance-windows")
    assert (
        "profile-selection: desktop-local or desktop-cloud or full-platform" in windows
    )
    macos = _job_block(acceptance, "acceptance-macos")
    assert "profile-selection: web-only" in macos


def test_gate_zero_blocks_every_reusable_acceptance_caller_and_release_finale() -> None:
    for name, content in _workflows().items():
        if "uses: ./.github/workflows/_portable-acceptance.yml" not in content:
            continue
        for job in _job_blocks(content):
            if "uses: ./.github/workflows/_portable-acceptance.yml" not in job:
                continue
            assert "needs:" in job, (name, job)
            assert "gate-zero" in job, (name, job)

    acceptance = _workflows()["ci-acceptance.yml"]
    nightly = _workflows()["ci-nightly.yml"]
    release = _workflows()["release.yml"]
    for name, content in (
        ("ci-acceptance.yml", acceptance),
        ("ci-nightly.yml", nightly),
        ("release.yml", release),
    ):
        gate = _job_block(content, "gate-zero")
        assert "Run concrete Gate 0 capability tests" in gate, name
        for test_path in (
            "tools/tests/adapters/test_builtin_adapters.py",
            "tools/tests/adapters/test_profile_structured_mutations.py",
            "tools/tests/integration/test_actions.py",
            "tools/tests/integration/test_transaction.py",
            "tools/tests/integration/test_transaction_project_config.py",
        ):
            assert test_path in gate, (name, test_path)
        assert "tools.ci_gate --require-ready" in gate, name
        assert "actions/upload-artifact@" in gate, name
        assert "gate-zero.junit.xml" in gate, name
        assert "node-version: ${{ needs.support-matrix.outputs.node_primary }}" in gate
        assert "rust-version: ${{ needs.support-matrix.outputs.rust_channel }}" in gate
    assert "- gate-zero" in _job_block(release, "release-final")


def test_workflow_security_contract_is_minimal_and_pinned() -> None:
    for name, content in _workflows().items():
        assert "pull_request_target" not in content, name
        assert "contents: read" in content, name
        assert "timeout-minutes:" in content, name
        assert "ubuntu-latest" not in content, name
        assert "windows-latest" not in content, name
        assert "macos-latest" not in content, name
        if name != "_portable-acceptance.yml":
            assert (
                "cancel-in-progress: false" in content
                or "cancel-in-progress:" in content
            ), name
        assert "shell: bash" not in content, name
        for action in _USES.findall(content):
            if action.startswith("actions/"):
                assert _PINNED_ACTION.fullmatch(action), (name, action)
            else:
                assert action.startswith("./.github/"), (name, action)


def test_workflows_use_the_pinned_node24_artifact_action() -> None:
    upload_actions = [
        action
        for content in _workflows().values()
        for action in _USES.findall(content)
        if action.startswith("actions/upload-artifact@")
    ]

    assert upload_actions
    assert set(upload_actions) == {_NODE24_UPLOAD_ARTIFACT}


def test_support_matrix_is_the_only_workflow_runtime_version_source() -> None:
    contents = "\n".join(_workflows().values())

    for value in ("3.11", "3.13", "24.19.0", "1.97.1", "2026"):
        assert value not in contents
    assert "python tools/ci_support.py --github-output" in contents
    assert "fromJSON(needs.support-matrix.outputs.python_versions)" in contents
    assert "fromJSON(needs.support-matrix.outputs.os_matrix)" in contents


def test_setup_action_creates_only_external_isolated_environments() -> None:
    content = SETUP_ACTION.read_text(encoding="utf-8")

    assert "runs:\n  using: composite" in content
    assert "actions/setup-python@" in content
    assert "actions/setup-node@" in content
    assert "RUSTUP_TOOLCHAIN=" in content
    assert "RUNNER_TEMP" in content
    assert "tools/.venv" not in content
    assert "TOOLING_PYTHON=" in content
    for action in _USES.findall(content):
        assert _PINNED_ACTION.fullmatch(action), action


def _job_blocks(content: str) -> tuple[str, ...]:
    return tuple(
        match.group(0)
        for match in re.finditer(
            r"^  [A-Za-z0-9_-]+:\n.*?(?=^  [A-Za-z0-9_-]+:\n|\Z)",
            content,
            re.MULTILINE | re.DOTALL,
        )
    )


def _job_block(content: str, job_name: str) -> str:
    prefix = f"  {job_name}:\n"
    for block in _job_blocks(content):
        if block.startswith(prefix):
            return block
    raise AssertionError(f"Workflow job is missing: {job_name}")


def _step_block(content: str, step_name: str) -> str:
    match = re.search(
        rf"^      - name: {re.escape(step_name)}\n.*?(?=^      - name:|\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"Workflow step is missing: {step_name}")
    return match.group(0)
