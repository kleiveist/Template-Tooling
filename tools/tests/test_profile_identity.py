from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import control
from tools.profiles import generator, loader

ROOT = Path(__file__).resolve().parents[2]
HAS_BACKEND_SOURCE = (ROOT / "backend" / "app" / "main.py").exists()
HAS_TAURI_SOURCE = (ROOT / "src-tauri" / "tauri.conf.json").exists()
HAS_CLOUD_SOURCE = (ROOT / "deployment" / "compose.yaml").exists()


@pytest.mark.skipif(
    not HAS_TAURI_SOURCE or not HAS_BACKEND_SOURCE or not HAS_CLOUD_SOURCE,
    reason="Complete desktop-cloud sources are absent in this derived project",
)
def test_init_command_applies_complete_release_identity(tmp_path: Path) -> None:
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

    package = json.loads((target / "frontend" / "package.json").read_text(encoding="utf-8"))
    package_lock = json.loads((target / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    tauri = json.loads((target / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    assert package["name"] == "customer-app-frontend"
    assert package_lock["name"] == "customer-app-frontend"
    assert tauri["productName"] == "CustomerApp"
    assert tauri["identifier"] == "com.customer.app"
    assert tauri["mainBinaryName"] == "customer-app"
    assert 'name = "customer-app"' in (target / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
    assert "CustomerApp Contributors" in (target / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
    assert 'name = "customer-app"' in (target / "src-tauri" / "Cargo.lock").read_text(encoding="utf-8")
    assert "name: customer-app" in (target / "deployment" / "compose.yaml").read_text(encoding="utf-8")
    assert "APP_NAME=CustomerApp API" in (target / ".env.example").read_text(encoding="utf-8")
    assert "customer-app-backend" in (target / "backend" / "app" / "api" / "health.py").read_text(encoding="utf-8")
    assert "customer-app-web.zip" in (target / "tools" / "inst" / "build.py").read_text(encoding="utf-8")


@pytest.mark.skipif(
    not HAS_TAURI_SOURCE or not HAS_BACKEND_SOURCE or not HAS_CLOUD_SOURCE,
    reason="Complete desktop-cloud sources are absent in this derived project",
)
def test_nested_default_scaffold_resets_inherited_custom_identity(tmp_path: Path) -> None:
    customized = tmp_path / "customer-app"
    assert (
        control.main(
            [
                "init",
                "--profile",
                "desktop-cloud",
                "--name",
                "Customer App",
                "--identifier",
                "com.customer.app",
                "--target-dir",
                str(customized),
            ]
        )
        == 0
    )
    target = tmp_path / "nested-default"
    catalog = loader.load_catalog(customized / "profiles", validate_paths=False)
    plan = generator.build_scaffold_plan(
        catalog,
        project_root=customized,
        target_dir=target,
        profile_id="desktop-cloud",
    )

    generator.scaffold_project(plan)

    package = json.loads((target / "frontend" / "package.json").read_text(encoding="utf-8"))
    tauri = json.loads((target / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    assert package["name"] == "template-project-frontend"
    assert "<title>Template Project</title>" in (target / "frontend" / "index.html").read_text(encoding="utf-8")
    assert tauri["productName"] == "Template Project"
    assert tauri["identifier"] == "com.example.templateproject"
    assert tauri["mainBinaryName"] == "project-template"
    assert 'name = "project-template"' in (target / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
    assert "name: template-project" in (target / "deployment" / "compose.yaml").read_text(encoding="utf-8")
    assert "template-project-backend" in (target / "backend" / "app" / "api" / "health.py").read_text(encoding="utf-8")


def test_init_requires_identifier_for_custom_tauri_identity(tmp_path: Path) -> None:
    assert (
        control.main(
            [
                "init",
                "--profile",
                "desktop-local",
                "--name",
                "Customer App",
                "--target-dir",
                str(tmp_path / "customer-app"),
            ]
        )
        == 1
    )
