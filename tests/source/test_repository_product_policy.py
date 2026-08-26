from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.core.context import load_context
from tools.inst import container

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_container_baseline_is_non_root_and_profiled() -> None:
    deployment = REPOSITORY_ROOT / "deployment"
    if not deployment.is_dir():
        pytest.skip("Container baselines are absent from this source profile")
    backend = (deployment / "docker" / "backend.Dockerfile").read_text(encoding="utf-8")
    frontend = (deployment / "docker" / "frontend.Dockerfile").read_text(
        encoding="utf-8"
    )
    compose = (deployment / "compose.yaml").read_text(encoding="utf-8")

    assert backend.count("FROM python:3.11.16-slim-bookworm") == 2
    assert "USER 10001:10001" in backend
    assert "requirements-production.lock" in backend
    assert "--require-hashes" in backend
    assert "/api/health" in backend
    assert "USER 101:101" in frontend
    assert "FROM node:24.19.0-alpine3.24 AS build" in frontend
    assert "FROM nginxinc/nginx-unprivileged:1.30.4-alpine3.24 AS runtime" in frontend
    assert "ARG VITE_API_BASE_URL" in frontend
    assert 'profiles: ["postgres"]' in compose
    assert "DATABASE_URL: ${DATABASE_URL:-}" in compose
    assert "image: postgres:16.15-alpine3.24" in compose
    assert "python tools/control.py db upgrade" not in compose
    assert "secrets:" not in compose


def test_production_locks_match_container_python_runtime() -> None:
    profile = container.profile_runtime.active_profile(REPOSITORY_ROOT)
    if not profile.has_feature("backend"):
        pytest.skip("Backend production locks are absent from this source profile")

    backend = load_context(
        project_root=REPOSITORY_ROOT,
        tools_root=REPOSITORY_ROOT / "tools",
    ).paths.backend
    assert backend is not None, "active backend profile must configure a backend path"

    lock_names = ["requirements-production.lock"]
    if profile.has_feature("database"):
        lock_names.append("requirements-database-production.lock")
    if profile.has_feature("postgres"):
        lock_names.append("requirements-postgres-production.lock")
    lock_paths = tuple(backend / name for name in lock_names)
    missing = tuple(
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for path in lock_paths
        if not path.is_file()
    )
    assert not missing, (
        f"source repository is missing production lock(s): {', '.join(missing)}"
    )
    locks = {path.name: path.read_text(encoding="utf-8") for path in lock_paths}

    assert all("pip-compile with Python 3.11" in content for content in locks.values())
    if profile.has_feature("database"):
        assert "greenlet==" in locks["requirements-database-production.lock"]


def test_template_tauri_capability_is_least_privilege() -> None:
    capability_path = REPOSITORY_ROOT / "src-tauri" / "capabilities" / "default.json"
    if not capability_path.is_file():
        pytest.skip("Tauri capability is absent from this source profile")

    capability = json.loads(capability_path.read_text(encoding="utf-8"))

    assert capability["identifier"] == "default"
    assert capability["windows"] == ["main"]
    assert capability["permissions"] == ["core:default"]
    assert "remote" not in capability
