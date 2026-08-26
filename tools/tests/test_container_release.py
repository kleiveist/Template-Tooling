from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from tools import control
from tools.inst import container, release
from tools.profiles.model import ProjectProfile

ROOT = Path(__file__).resolve().parents[2]


def _profile(*features: str) -> ProjectProfile:
    return ProjectProfile(
        schema_version=1,
        profile_id="test-profile",
        name="Test profile",
        description="Test profile",
        features=features,
    )


@pytest.mark.skipif(not (ROOT / "deployment").exists(), reason="Cloud deployment is absent from this derived project")
def test_container_baseline_is_non_root_and_profiled() -> None:
    backend = (ROOT / "deployment" / "docker" / "backend.Dockerfile").read_text(encoding="utf-8")
    frontend = (ROOT / "deployment" / "docker" / "frontend.Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "deployment" / "compose.yaml").read_text(encoding="utf-8")

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
    profile = container.profile_runtime.active_profile(ROOT)
    if not profile.has_feature("backend"):
        pytest.skip("Backend production locks are absent from this derived project")

    lock_names = ["requirements-production.lock"]
    if profile.has_feature("database"):
        lock_names.append("requirements-database-production.lock")
    if profile.has_feature("postgres"):
        lock_names.append("requirements-postgres-production.lock")
    locks = {name: (ROOT / "backend" / name).read_text(encoding="utf-8") for name in lock_names}

    assert all("pip-compile with Python 3.11" in content for content in locks.values())
    if profile.has_feature("database"):
        assert "greenlet==" in locks["requirements-database-production.lock"]


def test_container_build_is_rejected_for_non_cloud_profile(monkeypatch) -> None:
    monkeypatch.setattr(container.profile_runtime, "active_profile", lambda _root: _profile("frontend"))

    assert container.build(argparse.Namespace(component="all", no_cache=False)) == 1


def test_container_doctor_reports_missing_docker_as_actionable_failure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(container, "ROOT", tmp_path)
    monkeypatch.setattr(container, "DEPLOYMENT_DIR", tmp_path / "deployment")
    monkeypatch.setattr(container, "COMPOSE_FILE", tmp_path / "deployment" / "compose.yaml")
    monkeypatch.setattr(container.profile_runtime, "active_profile", lambda _root: _profile("backend", "cloud"))
    monkeypatch.setattr(container, "_docker", lambda: None)

    checks = container.collect_checks()

    docker = next(check for check in checks if check.name == "docker")
    assert docker.status == "FAIL"
    assert docker.message == "Docker is required for container builds but was not found."


def test_general_doctor_treats_missing_compose_as_optional_warning(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(container, "ROOT", tmp_path)
    monkeypatch.setattr(container, "DEPLOYMENT_DIR", tmp_path / "deployment")
    monkeypatch.setattr(container, "COMPOSE_FILE", tmp_path / "deployment" / "compose.yaml")
    monkeypatch.setattr(container.profile_runtime, "active_profile", lambda _root: _profile("backend", "cloud"))
    monkeypatch.setattr(container, "_docker", lambda: "/usr/bin/docker")

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="Docker version fixture", stderr="")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="compose unavailable")

    monkeypatch.setattr(container, "_run", fake_run)

    checks = container.collect_checks(require_docker=False)

    compose = next(check for check in checks if check.name == "compose")
    assert compose.status == "WARN"


def test_container_build_dispatches_profile_components(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    monkeypatch.setattr(container, "ROOT", tmp_path)
    monkeypatch.setattr(
        container.profile_runtime, "active_profile", lambda _root: _profile("frontend", "backend", "cloud")
    )
    monkeypatch.setattr(container, "_build_component", lambda component, no_cache=False: calls.append(component) or 0)

    assert container.build(argparse.Namespace(component="all", no_cache=False)) == 0
    assert calls == ["backend", "frontend"]


def test_container_commands_are_available_from_control_parser() -> None:
    parser = control._build_parser()

    assert parser.parse_args(["build", "container", "--component", "backend"]).component == "backend"
    assert parser.parse_args(["container", "doctor"]).container_command == "doctor"
    assert parser.parse_args(["container", "validate"]).container_command == "validate"


def test_version_check_detects_inconsistent_metadata(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "frontend").mkdir()
    (tmp_path / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (tmp_path / "frontend" / "package.json").write_text(
        json.dumps({"name": "customer-app-frontend", "version": "1.2.4"}), encoding="utf-8"
    )
    (tmp_path / "frontend" / "package-lock.json").write_text(
        json.dumps({"name": "customer-app-frontend", "version": "1.2.3"}), encoding="utf-8"
    )
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(release.profile_runtime, "active_profile", lambda _root: _profile("frontend"))

    checks = release.collect_version_checks()

    assert any(check.status == "FAIL" and "package.json=1.2.4" in check.message for check in checks)


def test_master_version_metadata_is_consistent() -> None:
    checks = release.collect_version_checks()

    assert checks
    assert not [check for check in checks if check.status == "FAIL"]


def test_version_sync_updates_frontend_metadata(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "frontend").mkdir()
    (tmp_path / "VERSION").write_text("2.3.4\n", encoding="utf-8")
    (tmp_path / "frontend" / "package.json").write_text(
        json.dumps({"name": "customer-app-frontend", "version": "1.0.0"}), encoding="utf-8"
    )
    (tmp_path / "frontend" / "package-lock.json").write_text(
        json.dumps(
            {
                "name": "customer-app-frontend",
                "version": "1.0.0",
                "packages": {"": {"name": "customer-app-frontend", "version": "1.0.0"}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(release.profile_runtime, "active_profile", lambda _root: _profile("frontend"))

    assert release.sync_versions() == 0

    package = json.loads((tmp_path / "frontend" / "package.json").read_text(encoding="utf-8"))
    package_lock = json.loads((tmp_path / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    assert package["version"] == "2.3.4"
    assert package_lock["version"] == "2.3.4"
    assert package_lock["packages"][""]["version"] == "2.3.4"


def test_release_check_accepts_canonical_master_template_identity() -> None:
    if not (ROOT / ".github" / "workflows" / "profiles.yml").is_file():
        pytest.skip("Canonical template master marker is absent from this derived project")
    if not (ROOT / "src-tauri" / "tauri.conf.json").exists():
        pytest.skip("Tauri source is absent from this derived project")
    tauri = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    if tauri.get("identifier") != "com.example.templateproject":
        pytest.skip("Known template identity is already customized in this derived project")

    checks = release._placeholder_checks()

    assert checks == [
        release.ReleaseCheck(
            "template-identity",
            "OK",
            "canonical template identity is expected in the template source repository",
        )
    ]


def test_release_check_rejects_default_generated_identity(monkeypatch, tmp_path: Path) -> None:
    if not (ROOT / "src-tauri" / "tauri.conf.json").exists():
        pytest.skip("Tauri source is absent from this derived project")
    profile = container.profile_runtime.active_profile(ROOT)
    target = tmp_path / "default-app"
    assert (
        control.main(
            [
                "init",
                "--profile",
                profile.profile_id,
                "--target-dir",
                str(target),
            ]
        )
        == 0
    )
    monkeypatch.setattr(release, "ROOT", target)

    checks = release._placeholder_checks()

    assert any(check.status == "FAIL" and "com.example.templateproject" in check.message for check in checks)


def test_release_check_accepts_custom_generated_identity(monkeypatch, tmp_path: Path) -> None:
    if not (ROOT / "src-tauri" / "tauri.conf.json").exists() or not (ROOT / "deployment").exists():
        pytest.skip("Complete desktop-cloud sources are absent from this derived project")
    target = tmp_path / "customer-app"
    assert (
        control.main(
            [
                "init",
                "--profile",
                "desktop-cloud",
                "--name",
                "CustomerApp",
                "--identifier",
                "com.customer.app",
                "--target-dir",
                str(target),
            ]
        )
        == 0
    )
    monkeypatch.setattr(release, "ROOT", target)

    checks = [
        *release.collect_version_checks(),
        *release._placeholder_checks(),
        *release._tauri_security_checks(),
    ]

    assert not any(check.status == "FAIL" for check in checks)


def test_template_tauri_capability_is_least_privilege() -> None:
    capability_path = ROOT / "src-tauri" / "capabilities" / "default.json"
    if not capability_path.is_file():
        pytest.skip("Tauri capability is absent from this derived project")

    capability = json.loads(capability_path.read_text(encoding="utf-8"))

    assert capability["identifier"] == "default"
    assert capability["windows"] == ["main"]
    assert capability["permissions"] == ["core:default"]
    assert "remote" not in capability


def test_git_release_check_rejects_dirty_tree(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(
        release.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout=" M changed.txt\n", stderr=""),
    )

    assert release._git_check().status == "FAIL"


def test_release_tag_must_match_source_version(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setenv("GITHUB_REF_TYPE", "tag")
    monkeypatch.setenv("GITHUB_REF_NAME", "v1.2.4")

    check = release._tag_check()

    assert check.status == "FAIL"
    assert "expected v1.2.3" in check.message
