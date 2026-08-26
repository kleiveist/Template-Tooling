from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tools import process


def test_prepare_command_keeps_native_executable(monkeypatch) -> None:
    monkeypatch.setattr(process.sys, "platform", "win32")

    command = [r"C:\Python311\python.exe", "--version"]

    assert process.prepare_command(command) is command


def test_prepare_command_routes_windows_batch_launcher_through_comspec(monkeypatch) -> None:
    monkeypatch.setattr(process.sys, "platform", "win32")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")

    prepared = process.prepare_command([r"C:\Program Files\nodejs\npm.cmd", "ci", "--no-audit"])

    assert prepared == [
        r"C:\Windows\System32\cmd.exe",
        "/d",
        "/s",
        "/c",
        "call",
        r"C:\Program Files\nodejs\npm.cmd",
        "ci",
        "--no-audit",
    ]


def test_linux_process_start_token_combines_boot_and_start_ticks(monkeypatch) -> None:
    fields = ["S", *[str(value) for value in range(4, 23)]]
    fields[19] = "987654"

    def fake_read_text(path: Path, encoding: str) -> str:
        assert encoding == "utf-8"
        if path.name == "stat":
            return f"4242 (npm worker) {' '.join(fields)}"
        if path.name == "boot_id":
            return "test-boot-id\n"
        raise AssertionError(path)

    monkeypatch.setattr(process.Path, "read_text", fake_read_text)

    assert process._linux_process_start_token(4242) == "linux:test-boot-id:987654"
    assert process._linux_process_start_token(4242) == "linux:test-boot-id:987654"


def test_process_start_token_uses_platform_query_when_proc_is_unavailable(monkeypatch) -> None:
    command = ["ps", "-p", "4242", "-o", "lstart="]
    monkeypatch.setattr(process.sys, "platform", "darwin")
    monkeypatch.setattr(process, "_process_start_query", lambda _pid: (command, "darwin"))
    monkeypatch.setattr(
        process.subprocess,
        "run",
        lambda actual, **_kwargs: subprocess.CompletedProcess(
            actual, 0, stdout="Mon Aug 24 19:50:04 2026\n", stderr=""
        ),
    )

    assert process.process_start_token(4242) == "darwin:Mon Aug 24 19:50:04 2026"


@pytest.mark.skipif(sys.platform != "win32", reason="requires the Windows command interpreter")
def test_prepare_command_executes_batch_launcher_with_spaced_path_and_arguments(tmp_path: Path) -> None:
    launcher_dir = tmp_path / "launcher path with spaces"
    launcher_dir.mkdir()
    launcher = launcher_dir / "argument probe.cmd"
    launcher.write_text('@echo off\r\n<nul set /p "=[%~1]|[%~2]"\r\nexit /b 0\r\n', encoding="utf-8")

    completed = subprocess.run(
        process.prepare_command([str(launcher), "first value", "second value"]),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "[first value]|[second value]"
