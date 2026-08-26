from __future__ import annotations

import argparse
from pathlib import Path

from tools.profiles import cli as profile_cli
from tools.template_lifecycle.model import LifecycleError


def _init_args(target: Path, *, dry_run: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        profile="web-only",
        optional_features=[],
        project_name=None,
        project_slug=None,
        identifier=None,
        target_dir=str(target),
        dry_run=dry_run,
    )


def test_init_finalizes_lifecycle_state_after_scaffolding(
    monkeypatch,
    tmp_path: Path,
) -> None:
    events: list[tuple[str, object]] = []

    def scaffold(plan, *, dry_run: bool) -> None:
        events.append(("scaffold", dry_run))

    def finalize(plan) -> None:
        events.append(("finalize", plan))

    monkeypatch.setattr(profile_cli, "scaffold_project", scaffold)
    monkeypatch.setattr(profile_cli, "finalize_generated_project", finalize)

    assert profile_cli.main(_init_args(tmp_path / "product")) == 0

    assert [event[0] for event in events] == ["scaffold", "finalize"]
    assert events[0][1] is False
    assert events[1][1].target_dir == (tmp_path / "product").resolve()


def test_init_dry_run_never_calls_lifecycle_finalizer(
    monkeypatch,
    tmp_path: Path,
) -> None:
    scaffold_calls: list[bool] = []
    finalize_calls: list[object] = []
    monkeypatch.setattr(
        profile_cli,
        "scaffold_project",
        lambda _plan, *, dry_run: scaffold_calls.append(dry_run),
    )
    monkeypatch.setattr(
        profile_cli,
        "finalize_generated_project",
        finalize_calls.append,
    )

    assert profile_cli.main(_init_args(tmp_path / "product", dry_run=True)) == 0

    assert scaffold_calls == [True]
    assert finalize_calls == []
    assert not (tmp_path / "product").exists()


def test_init_reports_lifecycle_failures_without_traceback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    failures: list[str] = []
    monkeypatch.setattr(
        profile_cli,
        "scaffold_project",
        lambda _plan, *, dry_run: None,
    )
    monkeypatch.setattr(profile_cli.logger, "fail", failures.append)

    def fail_finalization(_plan) -> None:
        raise LifecycleError("Could not record generated template provenance.")

    monkeypatch.setattr(profile_cli, "finalize_generated_project", fail_finalization)

    assert profile_cli.main(_init_args(tmp_path / "product")) == 1

    assert failures == ["Could not record generated template provenance."]
    assert "Unhandled error" not in failures[0]
    assert "Traceback" not in failures[0]
