from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from tools import logger
from tools.config import (
    ConfigLoadError,
    is_server_only_name,
    resolve_configuration,
    validate_configuration,
)
from tools.profiles import runtime as profile_runtime
from tools.tauri import cache, common, paths

RUNTIME_DIR = paths.ROOT / "tools" / ".runtime"
LOG_DIR = RUNTIME_DIR / "logs"
STATE_FILE = RUNTIME_DIR / "tauri_run_state.json"


@dataclass(frozen=True, slots=True)
class _DevSettings:
    frontend_host: str
    frontend_port: int
    frontend_environment: dict[str, str]
    secret_environment: set[str]


def main(args: argparse.Namespace) -> int:
    settings = _resolve_dev_settings(args)
    if settings is None:
        return 1
    if not cache.prepare_dev_cache():
        return 1
    command = common.tauri_cli_command(
        "dev",
        "--config",
        _dev_config_override(settings.frontend_port, settings.frontend_host),
    )

    if getattr(args, "foreground", False):
        return _run_foreground(command, settings.frontend_environment, settings.secret_environment)

    return _run_detached(
        command,
        follow=not bool(getattr(args, "no_follow", False)),
        env=settings.frontend_environment,
        remove_env=settings.secret_environment,
    )


def _resolve_dev_settings(args: argparse.Namespace) -> _DevSettings | None:
    profile = profile_runtime.active_profile(paths.ROOT)
    try:
        resolved = resolve_configuration(
            profile,
            project_root=paths.ROOT,
            cli_overrides={
                "FRONTEND_HOST": getattr(args, "frontend_host", None),
                "FRONTEND_PORT": getattr(args, "frontend_port", None),
            },
        )
    except ConfigLoadError as exc:
        logger.fail(f"Could not load frontend configuration: {exc}")
        return None
    relevant = {"FRONTEND_HOST", "FRONTEND_PORT"}
    issues = [issue for issue in validate_configuration(resolved) if issue.name in relevant]
    if issues:
        for issue in issues:
            logger.fail(f"{issue.name}: {issue.message}")
        return None
    frontend_host = resolved.value("FRONTEND_HOST")
    frontend_port_value = resolved.value("FRONTEND_PORT")
    assert frontend_host is not None and frontend_port_value is not None
    frontend_names = {
        "FRONTEND_HOST",
        "FRONTEND_PORT",
        "BACKEND_HOST",
        "BACKEND_PORT",
        "VITE_API_BASE_URL",
    }
    frontend_environment = {
        name: value for name, value in resolved.values.items() if value is not None and name in frontend_names
    }
    secret_environment = {name for name in os.environ if is_server_only_name(name)}
    return _DevSettings(
        frontend_host=frontend_host,
        frontend_port=int(frontend_port_value),
        frontend_environment=frontend_environment,
        secret_environment=secret_environment,
    )


def _run_foreground(
    command: list[str],
    environment: dict[str, str],
    secret_environment: set[str],
) -> int:
    result = common.run_command(
        command,
        cwd=paths.ROOT,
        env=environment,
        remove_env=secret_environment,
    )
    if result.returncode != 0 and _recover_failed_dev_session(result.stdout + "\n" + result.stderr):
        logger.info("Retrying Tauri development once after cache recovery.")
        result = common.run_command(
            command,
            cwd=paths.ROOT,
            env=environment,
            remove_env=secret_environment,
        )
    return common.print_result(result, "Tauri dev session finished", "Tauri dev failed")


def _dev_config_override(frontend_port: int, frontend_host: str = "127.0.0.1") -> str:
    client_host = "127.0.0.1" if frontend_host in {"0.0.0.0", "::", "[::]"} else frontend_host
    return json.dumps(
        {
            "build": {
                "beforeDevCommand": (f"cd ../frontend && npm run dev -- --host {frontend_host} --port {frontend_port}"),
                "devUrl": f"http://{client_host}:{frontend_port}",
            }
        }
    )


def _run_detached(
    command: list[str],
    *,
    follow: bool = True,
    env: dict[str, str] | None = None,
    remove_env: set[str] | None = None,
    recovery_attempted: bool = False,
) -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "tauri.log"
    log_mode = "a" if recovery_attempted else "w"
    log_file = log_path.open(log_mode, encoding="utf-8")
    if recovery_attempted:
        log_file.write("\n--- retry after Tauri dev-cache recovery ---\n")
    environment = {name: value for name, value in os.environ.items() if not is_server_only_name(name)}
    for name in remove_env or set():
        environment.pop(name, None)
    environment.update(env or {})
    process = subprocess.Popen(
        command,
        cwd=paths.ROOT,
        env=environment,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_file.close()
    _write_detached_state(process.pid, log_path)
    logger.ok(f"Tauri dev started in background pid={process.pid} log={log_path}")
    if not follow:
        logger.info(f"Follow logs with: tail -f {log_path.relative_to(paths.ROOT)}")
        return 0

    returncode = _follow_log(log_path, process)
    if returncode == 0 or recovery_attempted or not _recover_failed_log(log_path):
        return returncode

    logger.info("Retrying Tauri development once after cache recovery.")
    return _run_detached(
        command,
        follow=True,
        env=env,
        remove_env=remove_env,
        recovery_attempted=True,
    )


def _follow_log(log_path: Path, process: subprocess.Popen) -> int:
    logger.info("Streaming Tauri log. Press Ctrl+C to stop Tauri.")
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
            return _stream_log(handle, process)
    except KeyboardInterrupt:
        return _stop_interrupted_process(process)


def _stream_log(handle: TextIO, process: subprocess.Popen) -> int:
    while True:
        line = handle.readline()
        if line:
            print(line, end="", flush=True)
            continue

        returncode = process.poll()
        if returncode is None:
            time.sleep(0.2)
            continue

        _print_remaining_log(handle)
        return _report_process_exit(int(returncode))


def _print_remaining_log(handle: TextIO) -> None:
    rest = handle.read()
    if rest:
        print(rest, end="", flush=True)


def _report_process_exit(returncode: int) -> int:
    if returncode == 0:
        logger.ok("Tauri dev process exited")
        return 0
    logger.fail(f"Tauri dev process exited with code {returncode}")
    return returncode


def _recover_failed_log(log_path: Path) -> bool:
    try:
        output = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return _recover_failed_dev_session(output)


def _recover_failed_dev_session(output: str) -> bool:
    if not cache.is_plugin_permission_cache_failure(output):
        return False
    return cache.recover_dev_cache("Tauri reported stale generated plugin permissions.")


def _stop_interrupted_process(process: subprocess.Popen) -> int:
    print()
    stopped = _terminate_process_group(process)
    _clear_state()
    if stopped:
        logger.ok(f"Tauri dev stopped pid={process.pid}")
    else:
        logger.fail(f"Tauri dev did not stop cleanly pid={process.pid}")
    return 0


def _write_detached_state(pid: int, log_path: Path) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        f'{{"pid": {pid}, "log": "{log_path}", "command": "tauri dev"}}\n',
        encoding="utf-8",
    )


def _clear_state() -> None:
    try:
        STATE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _terminate_process_group(process: subprocess.Popen, *, timeout_seconds: float = 8.0) -> bool:
    if process.poll() is not None:
        return True

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        try:
            process.terminate()
        except OSError:
            return True

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return True
        time.sleep(0.2)

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except OSError:
        try:
            process.kill()
        except OSError:
            return True

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return True
        time.sleep(0.1)
    return process.poll() is not None
