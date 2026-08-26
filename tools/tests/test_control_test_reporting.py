from __future__ import annotations

import subprocess

import pytest

from tools import control
from tools.inst import report
from tools.profiles.model import ProjectProfile


def _payload() -> dict[str, object]:
    return {
        "command": "python tools/control.py --test --suite schema --report",
        "suite_selection": "schema",
        "expanded_suites": ["schema"],
        "no_start": False,
        "overall": "OK",
        "bootstrap": {
            "name": "service-bootstrap",
            "status": "SKIP",
            "message": "not required",
            "duration_seconds": 0.0,
        },
        "results": [
            {
                "name": "schema",
                "status": "OK",
                "message": "schema examples validated",
                "duration_seconds": 0.1,
                "command": None,
                "cwd": None,
                "exit_code": None,
                "stdout": "full stdout content",
                "stderr": "full stderr content",
                "stdout_tail": "",
                "stderr_tail": "",
                "detail": "Schema examples",
            }
        ],
    }


def test_bare_test_alias_opens_suite_help() -> None:
    assert control._normalize_argv(["--test"]) == ["test", "--suite-help"]


def test_test_alias_with_suite_runs_requested_suite() -> None:
    assert control._normalize_argv(["--test", "--suite", "schema"]) == [
        "test",
        "--suite",
        "schema",
    ]


def test_all_suite_includes_frontend_npm_test() -> None:
    from tools.inst import run_test

    assert run_test._expand_suites("all") == [
        "tools",
        "schema",
        "api",
        "database",
        "postgres",
        "frontend",
        "e2e",
        "tauri",
    ]


def test_disabled_optional_suites_report_skip(monkeypatch) -> None:
    from tools.inst import run_test

    monkeypatch.setattr(run_test.profile_runtime, "feature_enabled", lambda _feature, _root: False)

    assert run_test._run_api_suite().status == "SKIP"
    assert run_test._run_database_suite().status == "SKIP"
    assert run_test._run_postgres_suite().status == "SKIP"
    assert run_test._run_tauri_suite().status == "SKIP"


def test_subprocess_start_failure_becomes_a_ci_compatible_result(monkeypatch) -> None:
    from tools.inst import run_test

    monkeypatch.setattr(
        run_test.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("missing runtime")),
    )

    completed = run_test._run(["missing-runtime"])

    assert completed.returncode == 127
    assert "missing runtime" in completed.stderr


def test_tooling_runtime_never_falls_back_to_backend_virtualenv(monkeypatch, tmp_path) -> None:
    from tools.inst import run_test

    tooling_python = tmp_path / "tools" / ".venv" / "bin" / "python"
    backend_python = tmp_path / "backend" / ".venv" / "bin" / "python"
    backend_python.parent.mkdir(parents=True)
    backend_python.touch()

    monkeypatch.setattr(run_test, "ROOT", tmp_path)

    assert run_test._tooling_python() == tooling_python


def test_tools_suite_includes_optional_master_case_study_tests(monkeypatch, tmp_path) -> None:
    from tools.inst import run_test

    tools_tests = tmp_path / "tools" / "tests"
    case_study_tests = tmp_path / "case-study" / "tests"
    tools_tests.mkdir(parents=True)
    case_study_tests.mkdir(parents=True)
    python = tmp_path / "python"
    captured: list[str] = []

    def fake_run(command: list[str], cwd=None) -> subprocess.CompletedProcess[str]:
        captured.extend(command)
        return subprocess.CompletedProcess(command, 0, stdout="passed", stderr="")

    monkeypatch.setattr(run_test, "ROOT", tmp_path)
    monkeypatch.setattr(run_test, "_tooling_python", lambda: python)
    monkeypatch.setattr(run_test, "_run", fake_run)

    result = run_test._run_tools_suite()

    assert result.status == "OK"
    assert captured[-2:] == [str(tools_tests), str(case_study_tests)]


DESKTOP_PROFILE_CASES = [
    (
        "desktop-local",
        {"frontend", "tauri"},
        {"api": "SKIP", "database": "SKIP", "postgres": "SKIP", "frontend": "OK", "tauri": "OK"},
    ),
    (
        "desktop-cloud",
        {"frontend", "backend", "tauri", "cloud"},
        {"api": "OK", "database": "SKIP", "postgres": "SKIP", "frontend": "OK", "tauri": "OK"},
    ),
]


@pytest.mark.parametrize(("profile_id", "features", "expected"), DESKTOP_PROFILE_CASES)
def test_desktop_profiles_select_only_enabled_feature_suites(
    monkeypatch,
    tmp_path,
    profile_id: str,
    features: set[str],
    expected: dict[str, str],
) -> None:
    from tools.inst import run_test

    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "backend" / "tests" / "api").mkdir(parents=True)
    profile = ProjectProfile(
        schema_version=1,
        profile_id=profile_id,
        name=profile_id,
        description="profile-aware test selection",
        features=tuple(features),
    )

    monkeypatch.setattr(run_test, "ROOT", tmp_path)
    monkeypatch.setattr(run_test.profile_runtime, "active_profile", lambda _root: profile)
    monkeypatch.setattr(
        run_test.profile_runtime,
        "feature_enabled",
        lambda feature, _root: feature in features,
    )
    monkeypatch.setattr(run_test.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        run_test,
        "_run",
        lambda command, cwd=None: subprocess.CompletedProcess(command, 0, stdout="passed", stderr=""),
    )

    actual = {
        "api": run_test._run_api_suite().status,
        "database": run_test._run_database_suite().status,
        "postgres": run_test._run_postgres_suite().status,
        "frontend": run_test._run_frontend_suite().status,
        "tauri": run_test._run_tauri_suite().status,
    }

    assert actual == expected


def test_configured_postgres_suite_invokes_integration_tests(monkeypatch, tmp_path) -> None:
    from tools.inst import run_test

    tests_dir = tmp_path / "backend" / "tests" / "integration"
    tests_dir.mkdir(parents=True)
    monkeypatch.setattr(run_test, "ROOT", tmp_path)
    monkeypatch.setattr(
        run_test.profile_runtime,
        "feature_enabled",
        lambda feature, _root: feature == "postgres",
    )
    monkeypatch.setenv("DATABASE_URL_TEST", "postgresql+psycopg://test:test@127.0.0.1:5432/test")
    monkeypatch.setattr(run_test, "_backend_python", lambda: tmp_path / "python")
    monkeypatch.setattr(
        run_test,
        "_run",
        lambda command, cwd=None: subprocess.CompletedProcess(command, 0, stdout="1 passed", stderr=""),
    )

    result = run_test._run_postgres_suite()

    assert result.status == "OK"
    assert result.command is not None
    assert result.command[-1] == str(tests_dir)


def test_e2e_bootstrap_runs_cleanup_before_service_start(monkeypatch) -> None:
    from tools.inst import run_test

    monkeypatch.setattr(run_test, "_e2e_configured", lambda: True)

    calls: list[tuple[str, object]] = []

    def fake_cleanup(args) -> int:
        calls.append(("cleanup", (args.frontend_port, args.backend_port, args.tracked_only)))
        return 0

    def fake_run(cmd: list[str], cwd=None) -> subprocess.CompletedProcess[str]:
        calls.append(("run", cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="Services started", stderr="")

    monkeypatch.setattr(run_test.service_cleanup, "main", fake_cleanup)
    monkeypatch.setattr(run_test, "_run", fake_run)

    started_by_runner, bootstrap = run_test._start_services_if_needed(["e2e"], no_start=False)

    assert started_by_runner is True
    assert bootstrap.status == "OK"
    assert bootstrap.message == "services started by test runner"
    backend_port = 8000 if run_test.profile_runtime.feature_enabled("backend", run_test.ROOT) else 0
    assert calls == [
        ("cleanup", (5173, backend_port, True)),
        (
            "run",
            [
                run_test.sys.executable,
                str(run_test.ROOT / "tools" / "control.py"),
                "run",
                "--detach",
            ],
        ),
    ]


def test_e2e_bootstrap_cleanup_failure_skips_service_start(monkeypatch) -> None:
    from tools.inst import run_test

    monkeypatch.setattr(run_test, "_e2e_configured", lambda: True)

    def fake_run(cmd: list[str], cwd=None) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"service start should not run after cleanup failure: {cmd}")

    monkeypatch.setattr(run_test.service_cleanup, "main", lambda args: 1)
    monkeypatch.setattr(run_test, "_run", fake_run)

    started_by_runner, bootstrap = run_test._start_services_if_needed(["e2e"], no_start=False)

    assert started_by_runner is False
    assert bootstrap.status == "FAIL"
    assert bootstrap.message == "cleanup failed before e2e service bootstrap"
    assert bootstrap.exit_code == 1


def test_e2e_bootstrap_preserves_untracked_listener_and_reports_conflict(monkeypatch) -> None:
    from tools.inst import run_test

    monkeypatch.setattr(run_test, "_e2e_configured", lambda: True)
    cleanup_modes: list[bool] = []

    def fake_cleanup(args) -> int:
        cleanup_modes.append(args.tracked_only)
        return 0

    def occupied_port_failure(cmd: list[str], cwd=None) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Port 127.0.0.1:5173 is already occupied.")

    monkeypatch.setattr(run_test.service_cleanup, "main", fake_cleanup)
    monkeypatch.setattr(run_test, "_run", occupied_port_failure)

    started_by_runner, bootstrap = run_test._start_services_if_needed(["e2e"], no_start=False)

    assert cleanup_modes == [True]
    assert started_by_runner is False
    assert bootstrap.status == "FAIL"
    assert bootstrap.stderr == "Port 127.0.0.1:5173 is already occupied."


def test_e2e_teardown_stops_only_tracked_services(monkeypatch) -> None:
    from tools.inst import run_test

    commands: list[list[str]] = []

    def fake_run(cmd: list[str], cwd=None) -> subprocess.CompletedProcess[str]:
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(run_test, "_run", fake_run)

    result = run_test._stop_services_if_started(True)

    assert result.status == "OK"
    assert commands == [
        [
            run_test.sys.executable,
            str(run_test.ROOT / "tools" / "control.py"),
            "stop",
            "--tracked-only",
        ]
    ]


def test_e2e_teardown_failure_marks_overall_and_report_failed(monkeypatch) -> None:
    from tools.inst import run_test

    args = control._build_parser().parse_args(["test", "--suite", "e2e", "--report", "json"])
    bootstrap = run_test.SuiteResult("service-bootstrap", "OK", "started", 0.1)
    suite = run_test.SuiteResult("e2e", "OK", "passed", 0.2)
    captured_report: dict[str, object] = {}

    monkeypatch.setattr(run_test, "_ensure_backend_runtime", lambda _suites: None)
    monkeypatch.setattr(run_test, "_start_services_if_needed", lambda _suites, _no_start: (True, bootstrap))
    monkeypatch.setattr(run_test, "_run_selected_suite", lambda _suite, bootstrap_failed: suite)
    monkeypatch.setattr(
        run_test,
        "_run",
        lambda command, cwd=None: subprocess.CompletedProcess(command, 1, stdout="", stderr="still running"),
    )

    def capture_report(**kwargs) -> bool:
        captured_report.update(run_test._build_report_payload(**kwargs))
        return True

    monkeypatch.setattr(run_test, "_write_report_if_requested", capture_report)

    assert run_test.main(args) == 1
    assert captured_report["overall"] == "FAIL"
    report_results = captured_report["results"]
    assert isinstance(report_results, list)
    assert report_results[-1]["name"] == "service-teardown"
    assert report_results[-1]["status"] == "FAIL"


def test_e2e_bootstrap_no_start_skips_cleanup(monkeypatch) -> None:
    from tools.inst import run_test

    monkeypatch.setattr(run_test, "_e2e_configured", lambda: True)

    monkeypatch.setattr(
        run_test.service_cleanup,
        "main",
        lambda args: (_ for _ in ()).throw(AssertionError("cleanup should not run with --no-start")),
    )
    monkeypatch.setattr(
        run_test,
        "_run",
        lambda cmd, cwd=None: (_ for _ in ()).throw(AssertionError("service start should not run")),
    )

    started_by_runner, bootstrap = run_test._start_services_if_needed(["e2e"], no_start=True)

    assert started_by_runner is False
    assert bootstrap.status == "SKIP"
    assert bootstrap.message == "disabled by --no-start"


def test_all_suite_e2e_bootstrap_runs_cleanup(monkeypatch) -> None:
    from tools.inst import run_test

    monkeypatch.setattr(run_test, "_e2e_configured", lambda: True)

    cleanup_calls = 0

    def fake_cleanup(args) -> int:
        nonlocal cleanup_calls
        cleanup_calls += 1
        return 0

    def fake_run(cmd: list[str], cwd=None) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="Services started", stderr="")

    monkeypatch.setattr(run_test.service_cleanup, "main", fake_cleanup)
    monkeypatch.setattr(run_test, "_run", fake_run)

    started_by_runner, bootstrap = run_test._start_services_if_needed(
        ["schema", "api", "frontend", "e2e"],
        no_start=False,
    )

    assert started_by_runner is True
    assert bootstrap.status == "OK"
    assert cleanup_calls == 1


def test_e2e_suite_uses_the_frontend_package_script(monkeypatch, tmp_path) -> None:
    from tools.inst import run_test

    e2e_tests = tmp_path / "frontend" / "tests" / "e2e"
    e2e_tests.mkdir(parents=True)
    commands: list[tuple[list[str], object, dict[str, str] | None]] = []
    environment = {"PLAYWRIGHT_BASE_URL": "http://localhost:6123"}

    monkeypatch.setattr(run_test, "ROOT", tmp_path)
    monkeypatch.setattr(run_test.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    monkeypatch.setattr(run_test.e2e_runtime, "playwright_environment", lambda _root: environment)
    monkeypatch.setattr(
        run_test,
        "_run",
        lambda command, cwd=None, env=None: (
            commands.append((command, cwd, env))
            or subprocess.CompletedProcess(command, 0, stdout="2 passed", stderr="")
        ),
    )

    result = run_test._run_e2e_suite()

    assert result.status == "OK"
    assert commands == [(["/usr/bin/npm", "run", "test:e2e"], tmp_path / "frontend", environment)]


def test_playwright_base_url_uses_runtime_frontend_override() -> None:
    from tools.inst import e2e as e2e_runtime
    from tools.inst import run_test

    environment = e2e_runtime.playwright_environment(
        run_test.ROOT,
        environ={
            "FRONTEND_HOST": "0.0.0.0",
            "FRONTEND_PORT": "6123",
            "PLAYWRIGHT_BASE_URL": "http://stale.example:9999",
            "DATABASE_URL": "postgresql://private.example/database",
        },
    )

    assert environment["PLAYWRIGHT_BASE_URL"] == "http://127.0.0.1:6123"
    assert "DATABASE_URL" not in environment


def test_report_writer_creates_markdown_and_json(tmp_path) -> None:
    paths = report.write_test_report(tmp_path, _payload(), "all")

    assert len(paths) == 2
    assert {path.suffix for path in paths} == {".md", ".json"}
    assert all(path.parent == tmp_path / ".report" for path in paths)
    markdown = next(path for path in paths if path.suffix == ".md").read_text(encoding="utf-8")
    json_report = next(path for path in paths if path.suffix == ".json").read_text(encoding="utf-8")

    assert "# 🧪 Template Project Test Report" in markdown
    assert "## 📋 Summary" in markdown
    assert "full stdout content" in markdown
    assert "full stderr content" in markdown
    assert '"stdout": "full stdout content"' in json_report
    assert '"stderr": "full stderr content"' in json_report


def test_report_cleanup_removes_report_directory(tmp_path) -> None:
    report.write_test_report(tmp_path, _payload(), "md")

    assert report.clean_reports(tmp_path) is True
    assert not (tmp_path / ".report").exists()
    assert report.clean_reports(tmp_path) is False
