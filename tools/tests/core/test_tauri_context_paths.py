from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

from tools.tauri import paths, run


def _platform_quote(value: str) -> str:
    return subprocess.list2cmdline([value]) if os.name == "nt" else shlex.quote(value)


def test_tauri_dev_command_quotes_configured_frontend_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    frontend = tmp_path / "ui; user content"
    tauri = tmp_path / "desktop shell"
    monkeypatch.setattr(paths, "FRONTEND_DIR", frontend)
    monkeypatch.setattr(paths, "TAURI_DIR", tauri)
    relative = Path(os.path.relpath(frontend, tauri)).as_posix()

    payload = json.loads(run._dev_config_override(5173))

    assert payload["build"]["beforeDevCommand"] == (
        f"cd {_platform_quote(relative)} && npm run dev -- --host 127.0.0.1 --port 5173"
    )
