from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import signal
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from tools import logger
from tools.config import (
    ConfigLoadError,
    ConfigValidationError,
    RuntimeConfig,
    is_server_only_name,
    load_runtime_config,
)
from tools.process import prepare_command, process_start_token
from tools.profiles import runtime as profile_runtime

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = ROOT / "tools" / ".runtime"
LOG_DIR = RUNTIME_DIR / "logs"
STATE_FILE = RUNTIME_DIR / "run_state.json"


def _venv_python(backend_dir: Path) -> Path:
    candidates = [
        backend_dir / ".venv" / "Scripts" / "python.exe",
        backend_dir / ".venv" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if os.name == "nt" else candidates[1]


@dataclass(slots=True)
class ServiceDef:
    name: str
    command: list[str]
    cwd: Path
    port: int
    host: str
    env: dict[str, str]


def _port_is_free(host: str, port: int) -> bool:
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for family, socktype, protocol, _, address in addresses:
        with socket.socket(family, socktype, protocol) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(address)
                return True
            except OSError:
                continue
    return False


def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_state() -> dict | None:
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_state(payload: dict) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _clear_state() -> None:
    try:
        STATE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _frontend_process_environment(config: RuntimeConfig) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if not is_server_only_name(key)}
    environment.update(config.frontend_environment())
    return environment


def _backend_process_environment(config: RuntimeConfig) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(config.backend_environment())
    return environment


def _frontend_service(config: RuntimeConfig) -> tuple[ServiceDef | None, list[str]]:
    errors: list[str] = []
    frontend_dir = ROOT / "frontend"
    assert config.frontend_host is not None
    assert config.frontend_port is not None
    if not (frontend_dir / "package.json").exists():
        errors.append("Missing frontend/package.json")

    npm = shutil.which("npm")
    if npm is None:
        errors.append("npm not found. Action: install Node.js and npm.")
        return None, errors

    service = ServiceDef(
        name="frontend",
        command=[
            npm,
            "run",
            "dev",
            "--",
            "--host",
            config.frontend_host,
            "--port",
            str(config.frontend_port),
        ],
        cwd=frontend_dir,
        port=config.frontend_port,
        host=config.frontend_host,
        env=_frontend_process_environment(config),
    )
    return service, errors


def _backend_executable(backend_dir: Path) -> Path | None:
    backend_python = _venv_python(backend_dir)
    if backend_python.exists():
        return backend_python
    fallback = shutil.which("python3") or shutil.which("python")
    return Path(fallback) if fallback else None


def _backend_runtime_error(backend_python: Path) -> str | None:
    try:
        probe = subprocess.run(
            [str(backend_python), "-c", "import pydantic_settings, uvicorn"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return str(exc)
    if probe.returncode == 0:
        return None
    details = ((probe.stderr or "") + "\n" + (probe.stdout or "")).strip()
    return details or f"exit code {probe.returncode}"


def _backend_service(config: RuntimeConfig) -> tuple[ServiceDef | None, list[str]]:
    errors: list[str] = []
    backend_dir = ROOT / "backend"
    assert config.backend_host is not None
    assert config.backend_port is not None
    if not (backend_dir / "app" / "main.py").exists():
        errors.append("Missing backend/app/main.py")

    backend_python = _backend_executable(backend_dir)
    if backend_python is None:
        errors.append("Python executable not found for backend service.")
        return None, errors

    runtime_error = _backend_runtime_error(backend_python)
    if runtime_error is not None:
        errors.append(
            "Backend runtime is not executable. Action: run "
            "'python tools/control.py install --skip-frontend --skip-playwright'. "
            f"Details: {runtime_error}"
        )
        return None, errors

    service = ServiceDef(
        name="backend",
        command=[
            str(backend_python),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            config.backend_host,
            "--port",
            str(config.backend_port),
        ],
        cwd=backend_dir,
        port=config.backend_port,
        host=config.backend_host,
        env=_backend_process_environment(config),
    )
    return service, errors


def _build_service_defs(config: RuntimeConfig) -> tuple[list[ServiceDef], list[str]]:
    errors: list[str] = []
    services: list[ServiceDef] = []
    profile = profile_runtime.active_profile(ROOT)

    builders = []
    if profile.has_feature("frontend"):
        builders.append(_frontend_service)
    if profile.has_feature("backend"):
        builders.append(_backend_service)

    for builder in builders:
        service, service_errors = builder(config)
        errors.extend(service_errors)
        if service is not None:
            services.append(service)

    if errors:
        return services, errors

    if not services:
        errors.append("Active profile does not enable a runnable development service.")

    return services, errors


def _terminate_pid(pid: int, timeout_seconds: int = 8) -> bool:
    if not _is_process_alive(pid):
        return True

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return True

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not _is_process_alive(pid):
            return True
        time.sleep(0.2)

    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return True

    return not _is_process_alive(pid)


def _stop_from_state(print_output: bool = True) -> int:
    state = _read_state()
    if not state or "services" not in state:
        if print_output:
            logger.ok("No tracked services are running")
        _clear_state()
        return 0

    failures = 0
    for service in state.get("services", []):
        pid = int(service.get("pid", -1))
        name = str(service.get("name", "unknown"))
        if pid <= 0:
            continue
        ok = _terminate_pid(pid)
        if print_output:
            status = "OK" if ok else "FAIL"
            logger.status(status, f"stop:{name:<10} pid={pid}")
        if not ok:
            failures += 1

    _clear_state()
    return 1 if failures else 0


def _state_has_live_processes() -> bool:
    state = _read_state()
    if not state:
        return False

    for service in state.get("services", []):
        pid = int(service.get("pid", -1))
        if pid > 0 and _is_process_alive(pid):
            return True

    _clear_state()
    return False


def _preflight(config: RuntimeConfig) -> list[str]:
    errors: list[str] = []
    profile = profile_runtime.active_profile(ROOT)

    if _state_has_live_processes():
        errors.append("Tracked services are already running. Use 'python tools/control.py stop' first.")

    endpoints: list[tuple[str, int]] = []
    if profile.has_feature("frontend"):
        assert config.frontend_host is not None
        assert config.frontend_port is not None
        endpoints.append((config.frontend_host, config.frontend_port))
    if profile.has_feature("backend"):
        assert config.backend_host is not None
        assert config.backend_port is not None
        endpoints.append((config.backend_host, config.backend_port))

    for host, port in endpoints:
        if not _port_is_free(host, port):
            errors.append(f"Port {host}:{port} is already occupied.")

    return errors


def _service_state(service: ServiceDef, process: subprocess.Popen, log_file: str | None) -> dict:
    return {
        "name": service.name,
        "pid": process.pid,
        "port": service.port,
        "command": service.command,
        "process_start_token": process_start_token(process.pid),
        "process_group_id": process.pid,
        "log_file": log_file,
    }


def _start_detached(services: list[ServiceDef]) -> tuple[list[subprocess.Popen], dict]:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    payload = {"created_at": int(time.time()), "services": []}
    processes: list[subprocess.Popen] = []

    for service in services:
        log_path = LOG_DIR / f"{service.name}.log"
        log_file = log_path.open("a", encoding="utf-8")
        process = subprocess.Popen(
            prepare_command(service.command),
            cwd=service.cwd,
            env=service.env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        log_file.close()
        processes.append(process)
        payload["services"].append(_service_state(service, process, str(log_path.relative_to(ROOT))))

    _write_state(payload)
    return processes, payload


def _start_foreground(services: list[ServiceDef]) -> tuple[list[subprocess.Popen], dict]:
    payload = {"created_at": int(time.time()), "services": []}
    processes: list[subprocess.Popen] = []

    for service in services:
        process = subprocess.Popen(
            prepare_command(service.command),
            cwd=service.cwd,
            env=service.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        processes.append(process)
        payload["services"].append(_service_state(service, process, None))

    _write_state(payload)
    return processes, payload


def _stream_foreground(payload: dict, processes: list[subprocess.Popen]) -> int:
    q: queue.Queue[tuple[str, str]] = queue.Queue()

    def reader(name: str, process: subprocess.Popen) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            q.put((name, line.rstrip()))

    threads = []
    for item, process in zip(payload["services"], processes, strict=True):
        t = threading.Thread(target=reader, args=(item["name"], process), daemon=True)
        t.start()
        threads.append(t)

    logger.info("Services started. Press Ctrl+C to stop.")

    while True:
        try:
            name, line = q.get(timeout=0.2)
            logger.info(f"{name}: {line}")
        except queue.Empty:
            pass

        for item, process in zip(payload["services"], processes, strict=True):
            code = process.poll()
            if code is not None:
                if code == 0:
                    logger.warn(f"Service exited: {item['name']} (code={code})")
                else:
                    logger.fail(f"Service crashed: {item['name']} (code={code})")
                return 1


def run_command(args: argparse.Namespace) -> int:
    profile = profile_runtime.active_profile(ROOT)
    try:
        config = load_runtime_config(
            profile,
            project_root=ROOT,
            cli_overrides={
                "FRONTEND_HOST": getattr(args, "frontend_host", None),
                "FRONTEND_PORT": getattr(args, "frontend_port", None),
                "BACKEND_HOST": getattr(args, "backend_host", None),
                "BACKEND_PORT": getattr(args, "backend_port", None),
            },
        )
    except (ConfigLoadError, ConfigValidationError) as exc:
        logger.fail(f"Invalid runtime configuration: {exc}")
        return 1

    preflight_errors = _preflight(config)
    if preflight_errors:
        for err in preflight_errors:
            logger.fail(err)
        return 1

    services, errors = _build_service_defs(config)
    if errors:
        for err in errors:
            logger.fail(err)
        return 1

    if args.detach:
        processes, payload = _start_detached(services)
        time.sleep(2)

        for item, process in zip(payload["services"], processes, strict=True):
            code = process.poll()
            if code is not None:
                logger.fail(f"Service failed early: {item['name']} (code={code})")
                _stop_from_state(print_output=False)
                return 1

        logger.ok("Services started in detached mode")
        for item in payload["services"]:
            logger.ok(f"service:{item['name']:<9} pid={item['pid']} port={item['port']} log={item['log_file']}")
        return 0

    processes, payload = _start_foreground(services)
    try:
        return _stream_foreground(payload, processes)
    except KeyboardInterrupt:
        logger.warn("Interrupted by user")
        return 0
    finally:
        _stop_from_state(print_output=True)


def stop_command(args: argparse.Namespace) -> int:
    _ = args
    return _stop_from_state(print_output=True)
