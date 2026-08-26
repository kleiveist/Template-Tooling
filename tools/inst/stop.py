from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

from tools import logger
from tools.config import ConfigLoadError, resolve_configuration, validate_configuration
from tools.process import process_start_token
from tools.profiles import runtime as profile_runtime

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = ROOT / "tools" / ".runtime"
STATE_FILE = RUNTIME_DIR / "run_state.json"


def _is_process_alive(pid: int) -> bool:
    if _is_zombie_process(pid):
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _is_zombie_process(pid: int) -> bool:
    if os.name == "nt":
        return False
    try:
        stat = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8")
    except OSError:
        return False
    parts = stat.rsplit(") ", 1)
    if len(parts) != 2:
        return False
    return parts[1].split(maxsplit=1)[0] == "Z"


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


def _read_state() -> dict | None:
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _clear_state() -> None:
    try:
        STATE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _read_proc_cmdline_tokens(pid: int) -> tuple[str, ...] | None:
    path = Path("/proc") / str(pid) / "cmdline"
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    tokens = tuple(part.decode(errors="replace") for part in raw.split(b"\x00") if part)
    if len(tokens) != 1:
        return tokens or None
    try:
        expanded = tuple(shlex.split(tokens[0], posix=os.name != "nt"))
    except ValueError:
        return None
    return expanded or None


def _cmdline_query(pid: int) -> list[str] | None:
    if os.name == "nt":
        powershell = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            return None
        script = f'(Get-CimInstance Win32_Process -Filter "ProcessId = {pid}").CommandLine'
        return [powershell, "-NoProfile", "-NonInteractive", "-Command", script]

    ps = shutil.which("ps")
    return [ps, "-p", str(pid), "-o", "command="] if ps is not None else None


def _read_cmdline_tokens(pid: int) -> tuple[str, ...] | None:
    tokens = _read_proc_cmdline_tokens(pid)
    if tokens is not None:
        return tokens

    query = _cmdline_query(pid)
    if query is None:
        return None
    try:
        completed = subprocess.run(query, text=True, capture_output=True, check=False)
    except OSError:
        return None
    text = completed.stdout.strip()
    if completed.returncode != 0 or not text:
        return None
    try:
        return tuple(shlex.split(text, posix=os.name != "nt"))
    except ValueError:
        return None


def _read_cmdline(pid: int) -> str:
    tokens = _read_cmdline_tokens(pid)
    return " ".join(tokens) if tokens else "(unreadable)"


def _listener_inode_to_port(ports: set[int]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    tcp_tables = [Path("/proc/net/tcp"), Path("/proc/net/tcp6")]

    for table in tcp_tables:
        if not table.exists():
            continue
        try:
            lines = table.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines[1:]:
            parts = line.split()
            if len(parts) < 10:
                continue
            local_addr = parts[1]
            state = parts[3]
            inode = parts[9]
            try:
                port = int(local_addr.split(":")[1], 16)
            except (IndexError, ValueError):
                continue
            if state != "0A" or port not in ports:
                continue
            mapping[inode] = port

    return mapping


def _port_owners_from_proc_sockets(ports: set[int]) -> dict[int, tuple[set[int], str]]:
    inode_to_port = _listener_inode_to_port(ports)
    if not inode_to_port:
        return {}

    owners: dict[int, tuple[set[int], str]] = {}
    for pid_name in os.listdir("/proc"):
        if not pid_name.isdigit():
            continue
        pid = int(pid_name)
        fd_dir = Path("/proc") / pid_name / "fd"
        if not fd_dir.exists():
            continue
        pid_ports: set[int] = set()
        try:
            fd_names = list(fd_dir.iterdir())
        except OSError:
            continue
        for fd in fd_names:
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if not target.startswith("socket:[") or not target.endswith("]"):
                continue
            inode = target[8:-1]
            port = inode_to_port.get(inode)
            if port is not None:
                pid_ports.add(port)
        if pid_ports:
            owners[pid] = (pid_ports, _read_cmdline(pid))
    return owners


def _parse_port(value: str) -> int | None:
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.startswith("[") and "]:" in candidate:
        candidate = candidate.rsplit("]:", 1)[1]
    elif ":" in candidate:
        candidate = candidate.rsplit(":", 1)[1]
    try:
        return int(candidate)
    except ValueError:
        return None


def _port_owners_from_ss(ports: set[int]) -> dict[int, tuple[set[int], str]]:
    ss = shutil.which("ss")
    if ss is None:
        return {}

    completed = subprocess.run([ss, "-lptn"], text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        return {}

    owners: dict[int, tuple[set[int], str]] = {}
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        port = _parse_port(parts[3])
        if port is None or port not in ports:
            continue

        tail = " ".join(parts[5:])
        pid_parts = [chunk for chunk in tail.replace(",", " ").split() if chunk.startswith("pid=")]
        for chunk in pid_parts:
            try:
                pid = int(chunk.split("=", 1)[1])
            except ValueError:
                continue

            current_ports, _ = owners.get(pid, (set(), ""))
            current_ports.add(port)
            owners[pid] = (current_ports, _read_cmdline(pid))
    return owners


def _cmdline_matches_port(tokens: list[str], port: int) -> bool:
    target = str(port)
    if not tokens:
        return False

    for idx, token in enumerate(tokens):
        if token == "--port" and idx + 1 < len(tokens) and tokens[idx + 1] == target:
            return True
        if token.startswith("--port=") and token.split("=", 1)[1] == target:
            return True
        if token.endswith(f":{target}"):
            return True

    joined = " ".join(tokens)
    if ("uvicorn" in joined or "http.server" in joined or "vite" in joined) and target in tokens:
        return True
    return False


def _port_owners_from_cmdline(ports: set[int]) -> dict[int, tuple[set[int], str]]:
    owners: dict[int, tuple[set[int], str]] = {}
    for pid_name in os.listdir("/proc"):
        if not pid_name.isdigit():
            continue
        pid = int(pid_name)
        cmdline_path = Path("/proc") / pid_name / "cmdline"
        try:
            raw = cmdline_path.read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        tokens = [part.decode(errors="replace") for part in raw.split(b"\x00") if part]
        if not tokens:
            continue

        pid_ports = {port for port in ports if _cmdline_matches_port(tokens, port)}
        if pid_ports:
            owners[pid] = (pid_ports, " ".join(tokens))

    return owners


def _merge_owners(base: dict[int, tuple[set[int], str]], extra: dict[int, tuple[set[int], str]]) -> None:
    for pid, (ports, cmdline) in extra.items():
        existing_ports, existing_cmd = base.get(pid, (set(), ""))
        merged_ports = set(existing_ports)
        merged_ports.update(ports)
        merged_cmd = existing_cmd or cmdline
        base[pid] = (merged_ports, merged_cmd)


def _port_owners(ports: set[int]) -> dict[int, tuple[set[int], str]]:
    owners: dict[int, tuple[set[int], str]] = {}
    _merge_owners(owners, _port_owners_from_proc_sockets(ports))
    _merge_owners(owners, _port_owners_from_ss(ports))
    if not owners:
        _merge_owners(owners, _port_owners_from_cmdline(ports))
    return owners


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _normalized_executable_name(value: str) -> str:
    name = Path(value.strip('"')).name.lower()
    for suffix in (".cmd", ".bat", ".exe"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name


def _launcher_matches(expected: str, current: tuple[str, ...]) -> bool:
    expected_name = _normalized_executable_name(expected)
    current_names = {_normalized_executable_name(token) for token in current}
    if expected_name in current_names:
        return True
    return expected_name in {"npm", "npx"} and f"{expected_name}-cli.js" in current_names


def _contains_token_sequence(tokens: tuple[str, ...], expected: tuple[str, ...]) -> bool:
    if not expected or len(expected) > len(tokens):
        return False
    return any(tokens[index : index + len(expected)] == expected for index in range(len(tokens) - len(expected) + 1))


def _command_arguments_match(expected: tuple[str, ...], current: tuple[str, ...]) -> bool:
    arguments = expected[1:]
    if _normalized_executable_name(expected[0]) in {"npm", "npx"}:
        arguments = tuple(token for token in arguments if token != "--")
        current = tuple(token for token in current if token != "--")
    return _contains_token_sequence(current, arguments)


def _tracked_identity_matches(service: dict, pid: int) -> tuple[bool, str]:
    raw_command = service.get("command")
    raw_port = service.get("port")
    stored_start_token = service.get("process_start_token")
    if not isinstance(stored_start_token, str) or not stored_start_token:
        return False, "stored process start identity is missing or invalid"
    current_start_token = process_start_token(pid)
    if current_start_token is None:
        return False, "current process start identity is unavailable"
    if current_start_token != stored_start_token:
        return False, "process start identity does not match tracked service"
    if (
        not isinstance(raw_command, list)
        or len(raw_command) < 2
        or not all(isinstance(item, str) for item in raw_command)
    ):
        return False, "stored command identity is missing or invalid"
    if not isinstance(raw_port, int) or isinstance(raw_port, bool) or not 1 <= raw_port <= 65535:
        return False, "stored port identity is missing or invalid"

    current = _read_cmdline_tokens(pid)
    if current is None:
        return False, "current process command line is unavailable"
    expected = tuple(raw_command)
    if not _launcher_matches(expected[0], current):
        return False, "process launcher does not match tracked service"
    if not _command_arguments_match(expected, current):
        return False, "process command does not match tracked service"
    if not _cmdline_matches_port(list(expected), raw_port) or not _cmdline_matches_port(list(current), raw_port):
        return False, "process port does not match tracked service"
    return True, "tracked command and port match"


def _process_group_alive(group_id: int) -> bool:
    if os.name != "nt" and not _process_group_has_live_member(group_id):
        return False
    try:
        os.killpg(group_id, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _process_group_has_live_member(group_id: int) -> bool:
    for pid_name in os.listdir("/proc"):
        if not pid_name.isdigit():
            continue
        try:
            pid = int(pid_name)
            if os.getpgid(pid) == group_id and not _is_zombie_process(pid):
                return True
        except OSError:
            continue
    return False


def _terminate_process_group(group_id: int, timeout_seconds: int = 8) -> bool:
    try:
        os.killpg(group_id, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        return False

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not _process_group_alive(group_id):
            return True
        time.sleep(0.2)
    try:
        os.killpg(group_id, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return not _process_group_alive(group_id)


def _terminate_tracked_service(service: dict, pid: int) -> bool:
    group_id = service.get("process_group_id")
    if not isinstance(group_id, int) or isinstance(group_id, bool) or group_id <= 0:
        return False
    if os.name == "nt":
        taskkill = shutil.which("taskkill.exe") or shutil.which("taskkill")
        if taskkill is None:
            return False
        completed = subprocess.run(
            [taskkill, "/PID", str(pid), "/T", "/F"], text=True, capture_output=True, check=False
        )
        return completed.returncode == 0 or not _is_process_alive(pid)
    try:
        if os.getpgid(pid) != group_id:
            return False
    except OSError:
        return False
    return _terminate_process_group(group_id)


def _stop_tracked_processes() -> tuple[set[int], int]:
    state = _read_state()
    stopped_pids: set[int] = set()
    failures = 0

    if not state or "services" not in state:
        logger.ok("No tracked services are running")
        _clear_state()
        return stopped_pids, failures

    for service in state.get("services", []):
        pid = int(service.get("pid", -1))
        name = str(service.get("name", "unknown"))
        if pid <= 0:
            continue
        stopped_pids.add(pid)
        if not _is_process_alive(pid):
            logger.ok(f"Tracked service is no longer running: {name} pid={pid}")
            continue
        identity_matches, identity_detail = _tracked_identity_matches(service, pid)
        if not identity_matches:
            logger.status(
                "FAIL",
                f"stop:stale:{name:<8} pid={pid} ({identity_detail}); process was not signaled",
            )
            failures += 1
            continue
        ok = _terminate_tracked_service(service, pid)
        raw_port = service.get("port")
        if ok and isinstance(raw_port, int) and not isinstance(raw_port, bool):
            ok = _port_is_free(raw_port)
        logger.status("OK" if ok else "FAIL", f"stop:tracked:{name:<8} pid={pid}")
        if not ok:
            failures += 1

    _clear_state()
    return stopped_pids, failures


def _stop_port_processes(ports: set[int], ignored_pids: set[int]) -> int:
    owners = _port_owners(ports)
    if not owners:
        still_occupied = [port for port in sorted(ports) if not _port_is_free(port)]
        if still_occupied:
            logger.fail(
                "Ports are occupied but no owner process could be resolved automatically: "
                f"{still_occupied}. Action: run 'sudo ss -lptn \"sport = :<port>\"' and stop the owner process."
            )
            return len(still_occupied)
        logger.ok(f"No listener process found on ports {sorted(ports)}")
        return 0

    failures = 0
    current_pid = os.getpid()
    for pid, (pid_ports, cmdline) in sorted(owners.items(), key=lambda item: item[0]):
        if pid in ignored_pids or pid == current_pid:
            continue
        ok = _terminate_pid(pid)
        port_text = ",".join(str(port) for port in sorted(pid_ports))
        short_cmd = (cmdline[:120] + "...") if len(cmdline) > 120 else cmdline
        logger.status("OK" if ok else "FAIL", f"stop:port:{port_text:<9} pid={pid} cmd={short_cmd}")
        if not ok:
            failures += 1

    still_occupied = [port for port in sorted(ports) if not _port_is_free(port)]
    if still_occupied:
        failures += len(still_occupied)
        logger.fail(
            "Unable to clear ports automatically: "
            f"{still_occupied}. Action: run 'sudo ss -lptn \"sport = :<port>\"' and stop the owner process."
        )

    return failures


def main(args: argparse.Namespace) -> int:
    tracked_stopped, failures = _stop_tracked_processes()
    if not args.tracked_only:
        profile = profile_runtime.active_profile(ROOT)
        try:
            resolved = resolve_configuration(
                profile,
                project_root=ROOT,
                cli_overrides={
                    "FRONTEND_PORT": getattr(args, "frontend_port", None),
                    "BACKEND_PORT": getattr(args, "backend_port", None),
                },
            )
        except ConfigLoadError as exc:
            logger.fail(f"Could not load development ports: {exc}")
            return 1
        relevant_names = {"FRONTEND_PORT", "BACKEND_PORT"}
        relevant_issues = [issue for issue in validate_configuration(resolved) if issue.name in relevant_names]
        if relevant_issues:
            for issue in relevant_issues:
                logger.fail(f"{issue.name}: {issue.message}")
            return 1
        ports: set[int] = set()
        if profile.has_feature("frontend"):
            frontend_port = resolved.value("FRONTEND_PORT")
            assert frontend_port is not None
            ports.add(int(frontend_port))
        if profile.has_feature("backend"):
            backend_port = resolved.value("BACKEND_PORT")
            assert backend_port is not None
            ports.add(int(backend_port))
        if ports:
            failures += _stop_port_processes(ports, tracked_stopped)
        else:
            logger.ok("Active profile has no development service ports to inspect")

    if failures == 0:
        logger.ok("Stop completed")
        return 0

    logger.fail(f"Stop completed with {failures} failure(s)")
    return 1
