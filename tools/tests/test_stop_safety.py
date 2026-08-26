from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tools.inst import stop


def _tracked_state(
    pid: int,
    command: list[str] | None = None,
    port: int = 8000,
    start_token: str = "linux:test-boot:100",
) -> dict[str, object]:
    return {
        "created_at": 1_700_000_000,
        "services": [
            {
                "name": "backend",
                "pid": pid,
                "port": port,
                "command": command
                or ["/project/backend/.venv/bin/python", "-m", "uvicorn", "app.main:app", "--port", str(port)],
                "process_start_token": start_token,
                "process_group_id": pid,
            }
        ],
    }


def _write_state(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize(
    ("current_command", "expected_detail"),
    [
        (("/usr/bin/python", "foreign.py", "--port", "8000"), "command does not match"),
        (None, "command line is unavailable"),
    ],
)
def test_tracked_cleanup_never_signals_unverifiable_or_reused_pid(
    monkeypatch,
    tmp_path: Path,
    current_command: tuple[str, ...] | None,
    expected_detail: str,
) -> None:
    state_file = tmp_path / "run_state.json"
    _write_state(state_file, _tracked_state(4242))
    signals: list[int] = []
    messages: list[tuple[str, str]] = []

    monkeypatch.setattr(stop, "STATE_FILE", state_file)
    monkeypatch.setattr(stop, "_is_process_alive", lambda _pid: True)
    monkeypatch.setattr(stop, "process_start_token", lambda _pid: "linux:test-boot:100")
    monkeypatch.setattr(stop, "_read_cmdline_tokens", lambda _pid: current_command)
    monkeypatch.setattr(stop, "_terminate_tracked_service", lambda _service, pid: signals.append(pid) or True)
    monkeypatch.setattr(stop.logger, "status", lambda status, message: messages.append((status, message)))

    protected_pids, failures = stop._stop_tracked_processes()

    assert protected_pids == {4242}
    assert failures == 1
    assert signals == []
    assert not state_file.exists()
    assert messages[0][0] == "FAIL"
    assert expected_detail in messages[0][1]
    assert "process was not signaled" in messages[0][1]


def test_tracked_cleanup_signals_only_matching_command_and_port(monkeypatch, tmp_path: Path) -> None:
    state_file = tmp_path / "run_state.json"
    command = ["/project/backend/.venv/bin/python", "-m", "uvicorn", "app.main:app", "--port", "8000"]
    _write_state(state_file, _tracked_state(4242, command))
    signals: list[int] = []

    monkeypatch.setattr(stop, "STATE_FILE", state_file)
    monkeypatch.setattr(stop, "_is_process_alive", lambda _pid: True)
    monkeypatch.setattr(stop, "process_start_token", lambda _pid: "linux:test-boot:100")
    monkeypatch.setattr(stop, "_read_cmdline_tokens", lambda _pid: tuple(command))
    monkeypatch.setattr(stop, "_terminate_tracked_service", lambda _service, pid: signals.append(pid) or True)
    monkeypatch.setattr(stop, "_port_is_free", lambda _port: True)

    protected_pids, failures = stop._stop_tracked_processes()

    assert protected_pids == {4242}
    assert failures == 0
    assert signals == [4242]
    assert not state_file.exists()


def test_npm_wrapper_identity_accepts_tracked_frontend_command(monkeypatch) -> None:
    service = {
        "name": "frontend",
        "pid": 4242,
        "port": 5173,
        "command": ["/opt/node/bin/npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173"],
        "process_start_token": "linux:test-boot:100",
        "process_group_id": 4242,
    }
    current = (
        "/opt/node/bin/node",
        "/opt/node/lib/node_modules/npm/bin/npm-cli.js",
        "run",
        "dev",
        "--",
        "--host",
        "127.0.0.1",
        "--port",
        "5173",
    )

    monkeypatch.setattr(stop, "_read_cmdline_tokens", lambda _pid: current)
    monkeypatch.setattr(stop, "process_start_token", lambda _pid: "linux:test-boot:100")

    matches, detail = stop._tracked_identity_matches(service, 4242)

    assert matches is True
    assert detail == "tracked command and port match"


def test_same_command_and_port_with_reused_pid_is_never_signaled(monkeypatch, tmp_path: Path) -> None:
    state_file = tmp_path / "run_state.json"
    command = ["/project/backend/.venv/bin/python", "-m", "uvicorn", "app.main:app", "--port", "8000"]
    _write_state(state_file, _tracked_state(4242, command, start_token="linux:test-boot:100"))
    signals: list[int] = []

    monkeypatch.setattr(stop, "STATE_FILE", state_file)
    monkeypatch.setattr(stop, "_is_process_alive", lambda _pid: True)
    monkeypatch.setattr(stop, "process_start_token", lambda _pid: "linux:test-boot:999")
    monkeypatch.setattr(stop, "_read_cmdline_tokens", lambda _pid: tuple(command))
    monkeypatch.setattr(stop, "_terminate_tracked_service", lambda _service, pid: signals.append(pid) or True)

    protected_pids, failures = stop._stop_tracked_processes()

    assert protected_pids == {4242}
    assert failures == 1
    assert signals == []
    assert not state_file.exists()


def test_single_token_npm_process_title_matches_without_separator(monkeypatch) -> None:
    raw = b"npm run dev --host 127.0.0.1 --port 5173\x00"
    monkeypatch.setattr(Path, "read_bytes", lambda _path: raw)

    assert stop._read_proc_cmdline_tokens(4242) == (
        "npm",
        "run",
        "dev",
        "--host",
        "127.0.0.1",
        "--port",
        "5173",
    )


def test_zombie_process_is_not_considered_alive(monkeypatch) -> None:
    monkeypatch.setattr(stop, "_is_zombie_process", lambda pid: pid == 4242)

    assert stop._is_process_alive(4242) is False


def test_process_group_with_only_zombies_is_not_alive(monkeypatch) -> None:
    monkeypatch.setattr(os, "listdir", lambda path: ["4242", "4243"] if path == "/proc" else [])
    monkeypatch.setattr(os, "getpgid", lambda _pid: 4242)
    monkeypatch.setattr(stop, "_is_zombie_process", lambda _pid: True)

    assert stop._process_group_alive(4242) is False
