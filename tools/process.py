from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def prepare_command(command: list[str]) -> list[str]:
    """Return a subprocess-safe command on every supported host.

    Node.js exposes npm, npx, and local package binaries as ``.cmd`` launchers
    on Windows. CreateProcess cannot execute those scripts directly, so route
    only that launcher type through the system command interpreter.
    """

    if sys.platform != "win32" or not command:
        return command
    if not command[0].lower().endswith((".cmd", ".bat")):
        return command

    command_processor = os.environ.get("COMSPEC", "cmd.exe")
    # Keep the batch path and its arguments separate. Pre-serializing them
    # makes Python quote the serialized value a second time using C-runtime
    # rules that cmd.exe does not decode. `call` makes a quoted launcher path
    # unambiguous when it contains spaces.
    return [command_processor, "/d", "/s", "/c", "call", *command]


def _linux_process_start_token(pid: int) -> str | None:
    try:
        stat = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8")
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    command_end = stat.rfind(")")
    fields = stat[command_end + 2 :].split() if command_end >= 0 else []
    if len(fields) <= 19 or not boot_id:
        return None
    return f"linux:{boot_id}:{fields[19]}"


def _process_start_query(pid: int) -> tuple[list[str], str] | None:
    if os.name == "nt":
        powershell = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            return None
        script = (
            f"$p = Get-Process -Id {pid} -ErrorAction SilentlyContinue; "
            "if ($null -ne $p) { $p.StartTime.ToUniversalTime().Ticks }"
        )
        return [powershell, "-NoProfile", "-NonInteractive", "-Command", script], "windows"

    ps = shutil.which("ps")
    return ([ps, "-p", str(pid), "-o", "lstart="], sys.platform) if ps is not None else None


def process_start_token(pid: int) -> str | None:
    """Return an OS-scoped process creation marker suitable for PID-reuse checks."""

    if pid <= 0:
        return None
    if sys.platform.startswith("linux"):
        token = _linux_process_start_token(pid)
        if token is not None:
            return token

    query = _process_start_query(pid)
    if query is None:
        return None
    command, prefix = query
    try:
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
    except OSError:
        return None
    value = completed.stdout.strip()
    return f"{prefix}:{value}" if completed.returncode == 0 and value else None
