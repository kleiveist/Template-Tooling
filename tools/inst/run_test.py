from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools import logger
from tools.config import ConfigLoadError, resolve_configuration, validate_configuration
from tools.inst import report as report_writer
from tools.inst import e2e as e2e_runtime
from tools.inst import stop as service_cleanup
from tools.process import prepare_command
from tools.profiles import runtime as profile_runtime

ROOT = Path(__file__).resolve().parents[2]
CONSOLE_TAIL_LINES = 12
REPORT_TAIL_LINES = 80


@dataclass(slots=True)
class SuiteResult:
    name: str
    status: str
    message: str
    duration_seconds: float
    command: list[str] | None = None
    cwd: str | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        if self.stdout and not self.stdout_tail:
            self.stdout_tail = _tail_text(self.stdout)
        if self.stderr and not self.stderr_tail:
            self.stderr_tail = _tail_text(self.stderr)

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "duration_seconds": round(self.duration_seconds, 3),
            "command": _format_command(self.command) if self.command else None,
            "cwd": self.cwd,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
            "detail": self.detail,
        }


def _tail_lines(text: str, limit: int) -> list[str]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    return lines[-limit:]


def _tail_text(text: str, limit: int = REPORT_TAIL_LINES) -> str:
    lines = _tail_lines(text, limit)
    if not lines:
        return ""
    return "\n".join(lines)


def _format_command(command: list[str] | None, *, max_chars: int | None = None) -> str:
    if not command:
        return ""
    formatted = shlex.join(command)
    if max_chars is not None and len(formatted) > max_chars:
        return f"{formatted[: max_chars - 3]}..."
    return formatted


def _run(
    cmd: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(prepare_command(cmd), cwd=cwd, env=env, text=True, capture_output=True, check=False)
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 127, stdout="", stderr=str(exc))


def _expand_suites(value: str) -> list[str]:
    if value == "all":
        return [
            "tools",
            "schema",
            "api",
            "database",
            "postgres",
            "frontend",
            "e2e",
            "tauri",
        ]
    return [value]


def _backend_python() -> Path:
    windows_python = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
    unix_python = ROOT / "backend" / ".venv" / "bin" / "python"
    return windows_python if windows_python.exists() else unix_python


def _tooling_python() -> Path:
    windows_python = ROOT / "tools" / ".venv" / "Scripts" / "python.exe"
    unix_python = ROOT / "tools" / ".venv" / "bin" / "python"
    if windows_python.exists():
        return windows_python
    return unix_python


def _needs_backend_runtime(selected_suites: list[str]) -> bool:
    if not profile_runtime.feature_enabled("backend", ROOT):
        return False
    return bool({"api", "database", "postgres"}.intersection(selected_suites))


def _backend_runtime_imports(selected_suites: list[str]) -> str:
    modules = ["jsonschema", "pydantic_settings", "pytest", "uvicorn"]
    profile = profile_runtime.active_profile(ROOT)
    if profile.has_feature("database") and {"database", "postgres"}.intersection(selected_suites):
        modules.extend(["sqlalchemy", "alembic"])
    if profile.has_feature("postgres") and "postgres" in selected_suites:
        modules.append("psycopg")
    return "import " + ", ".join(dict.fromkeys(modules))


def _probe_backend_runtime(
    selected_suites: list[str],
) -> tuple[bool, str, list[str] | None]:
    backend_python = _backend_python()
    if not backend_python.exists():
        return False, f"backend venv python missing at {backend_python}", None

    command = [str(backend_python), "-c", _backend_runtime_imports(selected_suites)]
    completed = _run(command, cwd=ROOT)
    if completed.returncode == 0:
        return True, "backend runtime imports succeeded", command

    details = _tail_text(
        ((completed.stderr or "") + "\n" + (completed.stdout or "")).strip(),
        CONSOLE_TAIL_LINES,
    )
    if not details:
        details = f"exit code {completed.returncode}"
    return False, details, command


def _ensure_backend_runtime(selected_suites: list[str]) -> SuiteResult | None:
    if not _needs_backend_runtime(selected_suites):
        return None

    probe_ok, probe_detail, probe_command = _probe_backend_runtime(selected_suites)
    if probe_ok:
        return None

    started = time.monotonic()
    command = [
        sys.executable,
        str(ROOT / "tools" / "control.py"),
        "install",
        "--skip-frontend",
        "--skip-playwright",
    ]
    completed = _run(command, cwd=ROOT)
    if completed.returncode != 0:
        return SuiteResult(
            "backend-preflight",
            "FAIL",
            "backend runtime install failed",
            time.monotonic() - started,
            command=command,
            cwd=str(ROOT),
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            stdout_tail=_tail_text(completed.stdout),
            stderr_tail=_tail_text(completed.stderr),
            detail=f"Initial probe failed: {probe_detail}",
        )

    repaired_ok, repaired_detail, repaired_command = _probe_backend_runtime(selected_suites)
    if not repaired_ok:
        return SuiteResult(
            "backend-preflight",
            "FAIL",
            "backend runtime still not executable after install",
            time.monotonic() - started,
            command=repaired_command or probe_command or command,
            cwd=str(ROOT),
            exit_code=1,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            stdout_tail=_tail_text(completed.stdout),
            stderr_tail=_tail_text(completed.stderr),
            detail=f"Initial probe failed: {probe_detail}; post-install probe failed: {repaired_detail}",
        )

    return SuiteResult(
        "backend-preflight",
        "OK",
        "backend runtime repaired before tests",
        time.monotonic() - started,
        command=command,
        cwd=str(ROOT),
        exit_code=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        stdout_tail=_tail_text(completed.stdout),
        stderr_tail=_tail_text(completed.stderr),
        detail=f"Initial probe failed: {probe_detail}",
    )


def _result_from_completed(
    *,
    name: str,
    completed: subprocess.CompletedProcess[str],
    started: float,
    command: list[str],
    cwd: Path,
    ok_message: str,
    fail_message: str,
) -> SuiteResult:
    status = "OK" if completed.returncode == 0 else "FAIL"
    message = ok_message if completed.returncode == 0 else fail_message
    return SuiteResult(
        name=name,
        status=status,
        message=message,
        duration_seconds=time.monotonic() - started,
        command=command,
        cwd=str(cwd),
        exit_code=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        stdout_tail=_tail_text(completed.stdout),
        stderr_tail=_tail_text(completed.stderr),
    )


def _run_schema_suite() -> SuiteResult:
    started = time.monotonic()
    schema_path = ROOT / "shared" / "schema" / "input.schema.json"
    valid_path = ROOT / "shared" / "examples" / "valid.json"
    invalid_path = ROOT / "shared" / "examples" / "invalid.json"
    detail = (
        "Schema: shared/schema/input.schema.json; examples: shared/examples/valid.json, shared/examples/invalid.json"
    )

    if not schema_path.exists() or not valid_path.exists() or not invalid_path.exists():
        return SuiteResult(
            "schema",
            "FAIL",
            "required schema fixtures are missing",
            time.monotonic() - started,
            detail=detail,
        )

    try:
        import jsonschema  # type: ignore

        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        valid_data = json.loads(valid_path.read_text(encoding="utf-8"))
        invalid_data = json.loads(invalid_path.read_text(encoding="utf-8"))

        validator = jsonschema.Draft202012Validator(schema)

        valid_errors = list(validator.iter_errors(valid_data))
        if valid_errors:
            return SuiteResult(
                "schema",
                "FAIL",
                "valid example failed validation",
                time.monotonic() - started,
                detail=f"{detail}; error: {valid_errors[0].message}",
            )

        invalid_errors = list(validator.iter_errors(invalid_data))
        if not invalid_errors:
            return SuiteResult(
                "schema",
                "FAIL",
                "invalid example passed unexpectedly",
                time.monotonic() - started,
                detail=detail,
            )

        return SuiteResult(
            "schema",
            "OK",
            "schema examples validated",
            time.monotonic() - started,
            detail=detail,
        )
    except ImportError:
        schema_python = _backend_python() if profile_runtime.feature_enabled("backend", ROOT) else _tooling_python()
        if not schema_python.exists():
            return SuiteResult(
                "schema",
                "FAIL",
                "jsonschema runtime is missing; run 'python tools/control.py install'",
                time.monotonic() - started,
                detail=detail,
            )

        script = (
            "import json, pathlib, jsonschema; "
            "root=pathlib.Path.cwd(); "
            "schema=json.loads((root/'shared/schema/input.schema.json').read_text()); "
            "valid=json.loads((root/'shared/examples/valid.json').read_text()); "
            "invalid=json.loads((root/'shared/examples/invalid.json').read_text()); "
            "v=jsonschema.Draft202012Validator(schema); "
            "assert not list(v.iter_errors(valid)); "
            "assert list(v.iter_errors(invalid)); "
        )
        command = [str(schema_python), "-c", script]
        completed = _run(command, cwd=ROOT)
        result = _result_from_completed(
            name="schema",
            completed=completed,
            started=started,
            command=command,
            cwd=ROOT,
            ok_message="schema examples validated via project runtime",
            fail_message="schema validation failed via project runtime",
        )
        result.detail = detail
        return result


def _run_api_suite() -> SuiteResult:
    started = time.monotonic()
    if not profile_runtime.feature_enabled("backend", ROOT):
        return SuiteResult("api", "SKIP", "backend feature disabled", time.monotonic() - started)

    api_tests = ROOT / "backend" / "tests" / "api"
    if not api_tests.exists():
        return SuiteResult("api", "FAIL", "backend/tests/api missing", time.monotonic() - started)

    backend_python = _backend_python()
    if not backend_python.exists():
        fallback_python = shutil.which("python3") or shutil.which("python")
        if fallback_python is None:
            return SuiteResult("api", "FAIL", "python runtime missing", time.monotonic() - started)
        backend_python = Path(fallback_python)

    command = [str(backend_python), "-m", "pytest", "-q", str(api_tests)]
    completed = _run(command, cwd=ROOT)
    return _result_from_completed(
        name="api",
        completed=completed,
        started=started,
        command=command,
        cwd=ROOT,
        ok_message="pytest api suite passed",
        fail_message="pytest api suite failed",
    )


def _run_database_suite() -> SuiteResult:
    started = time.monotonic()
    if not profile_runtime.feature_enabled("database", ROOT):
        return SuiteResult(
            "database",
            "SKIP",
            "database feature disabled",
            time.monotonic() - started,
        )

    tests_dir = ROOT / "backend" / "tests" / "db"
    if not tests_dir.exists():
        return SuiteResult("database", "FAIL", "backend/tests/db missing", time.monotonic() - started)
    command = [str(_backend_python()), "-m", "pytest", "-q", str(tests_dir)]
    completed = _run(command, cwd=ROOT)
    return _result_from_completed(
        name="database",
        completed=completed,
        started=started,
        command=command,
        cwd=ROOT,
        ok_message="SQLAlchemy database unit tests passed",
        fail_message="SQLAlchemy database unit tests failed",
    )


def _run_postgres_suite() -> SuiteResult:
    started = time.monotonic()
    if not profile_runtime.feature_enabled("postgres", ROOT):
        return SuiteResult(
            "postgres",
            "SKIP",
            "postgres feature disabled",
            time.monotonic() - started,
        )

    if not os.environ.get("DATABASE_URL_TEST", "").strip():
        return SuiteResult(
            "postgres",
            "SKIP",
            "DATABASE_URL_TEST is not configured",
            time.monotonic() - started,
        )

    tests_dir = ROOT / "backend" / "tests" / "integration"
    if not tests_dir.exists():
        return SuiteResult(
            "postgres",
            "FAIL",
            "backend/tests/integration missing",
            time.monotonic() - started,
        )
    command = [str(_backend_python()), "-m", "pytest", "-q", str(tests_dir)]
    completed = _run(command, cwd=ROOT)
    return _result_from_completed(
        name="postgres",
        completed=completed,
        started=started,
        command=command,
        cwd=ROOT,
        ok_message="PostgreSQL integration suite passed or skipped cleanly",
        fail_message="PostgreSQL integration suite failed",
    )


def _run_frontend_suite() -> SuiteResult:
    started = time.monotonic()
    if not profile_runtime.feature_enabled("frontend", ROOT):
        return SuiteResult("frontend", "SKIP", "frontend feature disabled", time.monotonic() - started)

    frontend_dir = ROOT / "frontend"
    package_json = frontend_dir / "package.json"
    if not package_json.exists():
        return SuiteResult(
            "frontend",
            "FAIL",
            "frontend/package.json missing",
            time.monotonic() - started,
        )

    npm = shutil.which("npm")
    if npm is None:
        return SuiteResult("frontend", "FAIL", "npm not found", time.monotonic() - started)

    command = [npm, "test"]
    completed = _run(command, cwd=frontend_dir)
    return _result_from_completed(
        name="frontend",
        completed=completed,
        started=started,
        command=command,
        cwd=frontend_dir,
        ok_message="npm test frontend suite passed",
        fail_message="npm test frontend suite failed",
    )


def _run_tools_suite() -> SuiteResult:
    started = time.monotonic()
    tests_dir = ROOT / "tools" / "tests"
    if not tests_dir.exists():
        return SuiteResult("tools", "FAIL", "tools/tests missing", time.monotonic() - started)

    python = str(_tooling_python())
    test_paths = [tests_dir]
    case_study_tests = ROOT / "case-study" / "tests"
    if case_study_tests.exists():
        test_paths.append(case_study_tests)
    command = [python, "-m", "pytest", "-q", *(str(path) for path in test_paths)]
    completed = _run(command, cwd=ROOT)
    return _result_from_completed(
        name="tools",
        completed=completed,
        started=started,
        command=command,
        cwd=ROOT,
        ok_message="restored tooling tests passed",
        fail_message="restored tooling tests failed",
    )


def _run_e2e_suite() -> SuiteResult:
    started = time.monotonic()
    e2e_tests = ROOT / "frontend" / "tests" / "e2e"
    if not e2e_tests.exists():
        return SuiteResult(
            "e2e",
            "SKIP",
            "Playwright E2E is not configured",
            time.monotonic() - started,
        )

    npm = shutil.which("npm")
    if npm is None:
        return SuiteResult("e2e", "FAIL", "npm not found", time.monotonic() - started)

    try:
        environment = e2e_runtime.playwright_environment(ROOT)
    except e2e_runtime.E2EConfigurationError as exc:
        return SuiteResult(
            "e2e",
            "FAIL",
            "frontend endpoint is unavailable for Playwright",
            time.monotonic() - started,
            detail=str(exc),
        )

    command = [npm, "run", "test:e2e"]
    completed = _run(command, cwd=ROOT / "frontend", env=environment)
    return _result_from_completed(
        name="e2e",
        completed=completed,
        started=started,
        command=command,
        cwd=ROOT / "frontend",
        ok_message="playwright e2e suite passed",
        fail_message="playwright e2e suite failed",
    )


def _run_tauri_suite() -> SuiteResult:
    started = time.monotonic()
    if not profile_runtime.feature_enabled("tauri", ROOT):
        return SuiteResult("tauri", "SKIP", "tauri feature disabled", time.monotonic() - started)

    command = [
        sys.executable,
        str(ROOT / "tools" / "control.py"),
        "tauri",
        "test",
        "--cargo",
    ]
    completed = _run(command, cwd=ROOT)
    return _result_from_completed(
        name="tauri",
        completed=completed,
        started=started,
        command=command,
        cwd=ROOT,
        ok_message="Tauri structure, cargo check, and Rust tests passed",
        fail_message="Tauri or Rust checks failed",
    )


def _run_e2e_cleanup(started: float) -> SuiteResult | None:
    logger.info("Running cleanup before E2E tests")
    profile = profile_runtime.active_profile(ROOT)
    try:
        resolved = resolve_configuration(profile, project_root=ROOT)
    except ConfigLoadError as exc:
        return SuiteResult(
            "service-bootstrap",
            "FAIL",
            "configuration could not be loaded before E2E cleanup",
            time.monotonic() - started,
            detail=str(exc),
        )
    relevant = {"FRONTEND_PORT", "BACKEND_PORT"}
    issues = [issue for issue in validate_configuration(resolved) if issue.name in relevant]
    if issues:
        return SuiteResult(
            "service-bootstrap",
            "FAIL",
            "development ports are invalid",
            time.monotonic() - started,
            detail="; ".join(f"{issue.name}: {issue.message}" for issue in issues),
        )
    cleanup_args = argparse.Namespace(
        frontend_port=int(resolved.value("FRONTEND_PORT") or 0),
        backend_port=int(resolved.value("BACKEND_PORT") or 0),
        tracked_only=True,
    )
    cleanup_code = service_cleanup.main(cleanup_args)
    if cleanup_code == 0:
        return None

    return SuiteResult(
        "service-bootstrap",
        "FAIL",
        "cleanup failed before e2e service bootstrap",
        time.monotonic() - started,
        command=["python", "tools/control.py", "stop", "--tracked-only"],
        cwd=str(ROOT),
        exit_code=cleanup_code,
        detail="E2E service startup was skipped because cleanup did not complete successfully.",
    )


def _e2e_configured() -> bool:
    frontend_dir = ROOT / "frontend"
    return (frontend_dir / "tests" / "e2e").exists() or any(frontend_dir.glob("playwright.config.*"))


def _start_services_if_needed(selected_suites: list[str], no_start: bool) -> tuple[bool, SuiteResult]:
    requires_services = "e2e" in selected_suites and _e2e_configured()
    if not requires_services:
        return False, SuiteResult("service-bootstrap", "SKIP", "not required", 0.0)
    if no_start:
        return False, SuiteResult("service-bootstrap", "SKIP", "disabled by --no-start", 0.0)

    started = time.monotonic()
    cleanup_failure = _run_e2e_cleanup(started)
    if cleanup_failure is not None:
        return False, cleanup_failure

    command = [sys.executable, str(ROOT / "tools" / "control.py"), "run", "--detach"]
    completed = _run(command, cwd=ROOT)
    if completed.returncode == 0:
        return True, SuiteResult(
            "service-bootstrap",
            "OK",
            "services started by test runner",
            time.monotonic() - started,
            command=command,
            cwd=str(ROOT),
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            stdout_tail=_tail_text(completed.stdout),
            stderr_tail=_tail_text(completed.stderr),
        )

    return False, SuiteResult(
        "service-bootstrap",
        "FAIL",
        "service bootstrap failed",
        time.monotonic() - started,
        command=command,
        cwd=str(ROOT),
        exit_code=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        stdout_tail=_tail_text(completed.stdout),
        stderr_tail=_tail_text(completed.stderr),
    )


def _stop_services_if_started(started: bool) -> SuiteResult:
    teardown_started = time.monotonic()
    if not started:
        return SuiteResult("service-teardown", "SKIP", "not required", 0.0)
    command = [sys.executable, str(ROOT / "tools" / "control.py"), "stop", "--tracked-only"]
    completed = _run(command, cwd=ROOT)
    return _result_from_completed(
        name="service-teardown",
        completed=completed,
        started=teardown_started,
        command=command,
        cwd=ROOT,
        ok_message="services started by test runner were stopped",
        fail_message="tracked service teardown failed",
    )


def _print_suite_guide() -> None:
    logger.info("Template project test suites")
    print()
    print("Use an explicit suite command:")
    print("  python tools/control.py test --suite api      # Backend API only")
    print("  python tools/control.py test --suite schema   # Schema validation only")
    print("  python tools/control.py test --suite database # SQLAlchemy database unit tests")
    print("  python tools/control.py test --suite postgres # Optional PostgreSQL integration test")
    print("  python tools/control.py test --suite frontend # Frontend unit tests with npm test")
    print("  python tools/control.py test --suite e2e      # Frontend E2E with Playwright")
    print("  python tools/control.py test --suite tools    # Restored tooling tests")
    print("  python tools/control.py test --suite tauri    # Tauri structure, cargo check, and Rust tests")
    print("  python tools/control.py test --suite all      # Complete configured test run")
    print()
    print("Useful options:")
    print("  --no-start       Do not start frontend/backend automatically for E2E")
    print("  --report         Write a Markdown report to .report")
    print("  --report json    Write a JSON report to .report")
    print("  --report all     Write Markdown and JSON reports to .report")
    print("  --report done    Remove the .report folder")


def _print_tail(label: str, text: str) -> None:
    lines = _tail_lines(text, CONSOLE_TAIL_LINES)
    if not lines:
        return
    logger.info(f"  {label}:")
    for line in lines:
        print(f"    {line}")


def _print_result_details(item: SuiteResult) -> None:
    if item.command:
        logger.info(f"  command: {_format_command(item.command, max_chars=180)}")
    if item.cwd:
        logger.info(f"  cwd: {item.cwd}")
    if item.exit_code is not None:
        logger.info(f"  exit code: {item.exit_code}")
    if item.detail:
        logger.info(f"  detail: {item.detail}")
    if item.status == "FAIL":
        _print_tail("stdout tail", item.stdout_tail)
        _print_tail("stderr tail", item.stderr_tail)


def _print_results(results: list[SuiteResult], bootstrap: SuiteResult) -> str:
    overall = "OK"
    if bootstrap.status != "SKIP":
        logger.status(
            bootstrap.status,
            f"step:{bootstrap.name:<17} {bootstrap.message} ({bootstrap.duration_seconds:.2f}s)",
        )
        _print_result_details(bootstrap)

    logger.info("Test suite summary")
    for item in results:
        logger.status(
            item.status,
            f"suite:{item.name:<7} {item.message} ({item.duration_seconds:.2f}s)",
        )
        _print_result_details(item)
        if item.status == "FAIL":
            overall = "FAIL"
        elif item.status == "WARN" and overall != "FAIL":
            overall = "WARN"
    logger.status(overall, f"Overall test status: {overall}")
    return overall


def _build_report_payload(
    *,
    args: argparse.Namespace,
    selected_suites: list[str],
    bootstrap: SuiteResult,
    results: list[SuiteResult],
    overall: str,
) -> dict[str, Any]:
    display_argv = getattr(args, "display_argv", None) or sys.argv[1:]
    return {
        "command": _format_command(["python", "tools/control.py", *display_argv]),
        "suite_selection": args.suite,
        "expanded_suites": selected_suites,
        "no_start": args.no_start,
        "overall": overall,
        "bootstrap": bootstrap.to_report_dict(),
        "results": [item.to_report_dict() for item in results],
    }


def _write_report_if_requested(
    *,
    args: argparse.Namespace,
    selected_suites: list[str],
    bootstrap: SuiteResult,
    results: list[SuiteResult],
    overall: str,
) -> bool:
    report_mode = getattr(args, "report", None)
    if not report_mode:
        return True

    payload = _build_report_payload(
        args=args,
        selected_suites=selected_suites,
        bootstrap=bootstrap,
        results=results,
        overall=overall,
    )
    try:
        written_paths = report_writer.write_test_report(ROOT, payload, report_mode)
    except (OSError, ValueError) as exc:
        logger.fail(f"failed to write test report: {exc}")
        return False

    for path in written_paths:
        logger.ok(f"test report written: {path.relative_to(ROOT)}")
    return True


def _bootstrap_failure_result(suite: str) -> SuiteResult:
    return SuiteResult(
        suite,
        "FAIL",
        "service bootstrap failed before e2e could run",
        0.0,
        detail="See service-bootstrap failure details above.",
    )


def _run_selected_suite(suite: str, *, bootstrap_failed: bool) -> SuiteResult:
    if suite == "e2e" and bootstrap_failed:
        return _bootstrap_failure_result(suite)
    runners = {
        "schema": _run_schema_suite,
        "api": _run_api_suite,
        "database": _run_database_suite,
        "postgres": _run_postgres_suite,
        "frontend": _run_frontend_suite,
        "e2e": _run_e2e_suite,
        "tools": _run_tools_suite,
        "tauri": _run_tauri_suite,
    }
    return runners[suite]()


def main(args: argparse.Namespace) -> int:
    if getattr(args, "report", None) == "done":
        removed = report_writer.clean_reports(ROOT)
        if removed:
            logger.ok("removed .report")
        else:
            logger.info(".report does not exist")
        return 0

    if getattr(args, "suite_help", False):
        _print_suite_guide()
        return 0

    selected_suites = _expand_suites(args.suite)

    preflight = _ensure_backend_runtime(selected_suites)
    started_by_runner, bootstrap = _start_services_if_needed(selected_suites, args.no_start)

    results: list[SuiteResult] = []
    if preflight is not None:
        results.append(preflight)

    try:
        results.extend(
            _run_selected_suite(suite, bootstrap_failed=bootstrap.status == "FAIL") for suite in selected_suites
        )
    finally:
        teardown = _stop_services_if_started(started_by_runner)
    if teardown.status != "SKIP":
        results.append(teardown)

    overall = _print_results(results, bootstrap)
    report_ok = _write_report_if_requested(
        args=args,
        selected_suites=selected_suites,
        bootstrap=bootstrap,
        results=results,
        overall=overall,
    )
    return 1 if overall == "FAIL" or not report_ok else 0
