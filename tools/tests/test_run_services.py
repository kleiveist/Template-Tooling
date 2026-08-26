from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from tools.config import RuntimeConfig
from tools.inst import run
from tools.profiles.model import ProjectProfile


def _profile(*features: str) -> ProjectProfile:
    return ProjectProfile(1, "test-profile", "Test profile", "Test service profile", features)


def _config() -> RuntimeConfig:
    return RuntimeConfig(
        app_env="test",
        app_name="Test App",
        frontend_host="127.0.0.1",
        frontend_port=5173,
        vite_api_base_url="http://127.0.0.1:8000",
        backend_host="127.0.0.1",
        backend_port=8000,
        backend_cors_origins=("http://127.0.0.1:5173",),
    )


def test_service_builders_preserve_frontend_and_backend_commands(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "backend" / "app").mkdir(parents=True)
    (tmp_path / "backend" / "app" / "main.py").write_text("\n", encoding="utf-8")
    backend_python = tmp_path / "backend" / ".venv" / "bin" / "python"
    backend_python.parent.mkdir(parents=True)
    backend_python.touch()

    monkeypatch.setattr(run, "ROOT", tmp_path)
    monkeypatch.setattr(run.profile_runtime, "active_profile", lambda _root: _profile("frontend", "backend"))
    monkeypatch.setattr(run.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    monkeypatch.setattr(
        run.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, stdout="", stderr=""),
    )

    services, errors = run._build_service_defs(_config())

    assert errors == []
    assert [service.name for service in services] == ["frontend", "backend"]
    assert services[0].command == [
        "/usr/bin/npm",
        "run",
        "dev",
        "--",
        "--host",
        "127.0.0.1",
        "--port",
        "5173",
    ]
    assert services[1].command[:4] == [str(backend_python), "-m", "uvicorn", "app.main:app"]


def test_backend_builder_reports_missing_python_without_probing_directory(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "backend" / "app").mkdir(parents=True)
    (tmp_path / "backend" / "app" / "main.py").write_text("\n", encoding="utf-8")
    monkeypatch.setattr(run, "ROOT", tmp_path)
    monkeypatch.setattr(run.profile_runtime, "active_profile", lambda _root: _profile("backend"))
    monkeypatch.setattr(run.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        run.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("runtime probe must not run")),
    )

    services, errors = run._build_service_defs(_config())

    assert services == []
    assert errors == ["Python executable not found for backend service."]


def test_service_state_records_process_start_identity(monkeypatch, tmp_path: Path) -> None:
    service = run.ServiceDef("frontend", ["npm", "run", "dev", "--", "--port", "5173"], tmp_path, 5173, "127.0.0.1", {})
    monkeypatch.setattr(run, "process_start_token", lambda pid: f"linux:test-boot:{pid}")

    state = run._service_state(service, SimpleNamespace(pid=4242), "tools/.runtime/logs/frontend.log")

    assert state["process_start_token"] == "linux:test-boot:4242"
    assert state["process_group_id"] == 4242
