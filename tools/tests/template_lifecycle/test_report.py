"""Report evidence for LC-012, LC-018, and LC-019."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import control
from tools.template_lifecycle.model import (
    LifecycleError,
    PlanOperation,
    UpdatePlan,
    VerificationFinding,
    VerificationResult,
)
from tools.template_lifecycle.report import create_report_directory, write_report

OLD_COMMIT = "a" * 40
NEW_COMMIT = "b" * 40
REQUIRED_REPORT_FILES = {
    "changes.patch",
    "conflicts.json",
    "plan.json",
    "summary.md",
    "verification.json",
}


def _update_plan(path: str, result: bytes) -> UpdatePlan:
    return UpdatePlan(
        baseline_commit=OLD_COMMIT,
        target_commit=NEW_COMMIT,
        target_version="1.1.0",
        operations=(
            PlanOperation(
                action="UPDATE",
                path=path,
                reason="template changed",
                kind="text",
                executable=False,
                result=result,
            ),
        ),
    )


def _passing_verification() -> VerificationResult:
    return VerificationResult((VerificationFinding("report-fixture", "PASS", "Report fixture passed."),))


def test_report_writes_five_required_files_with_pre_apply_diff(tmp_path: Path) -> None:
    target = tmp_path / "product"
    target.mkdir()
    managed = target / "managed.txt"
    managed.write_text("before\n", encoding="utf-8")
    report = tmp_path / "report"

    write_report(
        report,
        plan=_update_plan("managed.txt", b"after\n"),
        target_root=target,
        verification=_passing_verification(),
        outcome="PREVIEW",
    )

    assert REQUIRED_REPORT_FILES <= {path.name for path in report.iterdir()}
    patch = (report / "changes.patch").read_text(encoding="utf-8")
    assert "--- a/managed.txt" in patch
    assert "+++ b/managed.txt" in patch
    assert "-before\n" in patch
    assert "+after\n" in patch
    assert json.loads((report / "plan.json").read_text(encoding="utf-8"))["schema_version"] == 1
    assert json.loads((report / "conflicts.json").read_text(encoding="utf-8"))["schema_version"] == 1
    verification = json.loads((report / "verification.json").read_text(encoding="utf-8"))
    assert verification["schema_version"] == 1
    assert verification["status"] == "PASS"


def test_successful_update_report_preserves_pre_apply_update_and_delete_diff(
    lifecycle_fixture,
    tmp_path: Path,
    capsys,
) -> None:
    target = lifecycle_fixture.managed_product(tmp_path / "managed product")
    reports = tmp_path / "lifecycle reports"

    code = control.main(
        [
            "template",
            "update",
            "--target-dir",
            str(target),
            "--source-dir",
            str(lifecycle_fixture.source_root),
            "--to-ref",
            lifecycle_fixture.v2,
            "--report-dir",
            str(reports),
            "--apply",
        ]
    )
    capsys.readouterr()

    assert code == 0
    run_directories = tuple(path for path in reports.iterdir() if path.is_dir())
    assert len(run_directories) == 1
    patch = (run_directories[0] / "changes.patch").read_text(encoding="utf-8")
    assert "-omega\n" in patch
    assert "+omega-template\n" in patch
    assert "-remove in v2\n" in patch


def test_report_redacts_json_secrets_and_absolute_developer_paths(
    tmp_path: Path,
) -> None:
    target = tmp_path / "developer workspace" / "product"
    target.mkdir(parents=True)
    before_secret = "before-secret-value"
    after_secret = "after-secret-value"
    verification_secret = "verification-secret-value"
    notice_secret = "notice-secret-value"
    before = f'{{\n  "api_key": "{before_secret}"\n}}\n'.encode()
    after = f'{{\n  "api_key": "{after_secret}"\n}}\n'.encode()
    (target / "config.json").write_bytes(before)
    report = tmp_path / "redacted report"
    verification = VerificationResult(
        (
            VerificationFinding(
                "profile",
                "FAIL",
                f"Could not read {target / 'project-profile.toml'}; token={verification_secret}",
            ),
        )
    )

    write_report(
        report,
        plan=_update_plan("config.json", after),
        target_root=target,
        verification=verification,
        outcome="FAILED",
        notices=(f'"password": "{notice_secret}"',),
    )

    rendered = "\n".join(path.read_text(encoding="utf-8") for path in sorted(report.iterdir()))
    for secret in (before_secret, after_secret, verification_secret, notice_secret):
        assert secret not in rendered
    assert str(tmp_path) not in rendered
    for name in ("plan.json", "conflicts.json", "verification.json"):
        json.loads((report / name).read_text(encoding="utf-8"))


def test_default_report_directory_rejects_external_symlink(tmp_path: Path) -> None:
    target = tmp_path / "product"
    external = tmp_path / "external reports"
    target.mkdir()
    external.mkdir()
    try:
        (target / ".report").symlink_to(external, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")

    with pytest.raises(LifecycleError, match="report|symbolic|outside"):
        create_report_directory(target)

    assert tuple(external.iterdir()) == ()
