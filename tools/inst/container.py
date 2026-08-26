from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tools import logger
from tools.config import ConfigLoadError, resolve_configuration
from tools.profiles import runtime as profile_runtime

ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT_DIR = ROOT / "deployment"
COMPOSE_FILE = DEPLOYMENT_DIR / "compose.yaml"


@dataclass(frozen=True, slots=True)
class ContainerCheck:
    name: str
    status: str
    message: str


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, stdout="", stderr=str(exc))


def _docker() -> str | None:
    return shutil.which("docker")


def _required_files() -> tuple[Path, ...]:
    return (
        ROOT / ".dockerignore",
        COMPOSE_FILE,
        DEPLOYMENT_DIR / "docker" / "backend.Dockerfile",
        DEPLOYMENT_DIR / "docker" / "frontend.Dockerfile",
        DEPLOYMENT_DIR / "docker" / "nginx.conf",
    )


def collect_checks(
    *,
    validate_compose: bool = True,
    require_docker: bool = True,
) -> list[ContainerCheck]:
    profile = profile_runtime.active_profile(ROOT)
    if not profile.has_feature("cloud"):
        return [
            ContainerCheck(
                "container-profile",
                "OK",
                f"disabled by active profile '{profile.profile_id}'",
            )
        ]

    checks: list[ContainerCheck] = []
    for path in _required_files():
        relative = path.relative_to(ROOT).as_posix()
        checks.append(
            ContainerCheck(
                f"container:{path.name}",
                "OK" if path.exists() else "FAIL",
                f"{relative} {'found' if path.exists() else 'missing'}",
            )
        )

    docker = _docker()
    if docker is None:
        checks.append(
            ContainerCheck(
                "docker",
                "FAIL" if require_docker else "WARN",
                "Docker is required for container builds but was not found."
                if require_docker
                else "Docker not found; required only for container validation and builds.",
            )
        )
        return checks

    version = _run([docker, "--version"])
    checks.append(
        ContainerCheck(
            "docker",
            "OK" if version.returncode == 0 else "FAIL",
            (version.stdout or version.stderr).strip() or "Docker version check failed",
        )
    )

    compose = _run([docker, "compose", "version"])
    compose_status = "OK" if compose.returncode == 0 else ("FAIL" if require_docker else "WARN")
    checks.append(
        ContainerCheck(
            "compose",
            compose_status,
            (compose.stdout or compose.stderr).strip() or "Docker Compose plugin is unavailable",
        )
    )
    if validate_compose and compose.returncode == 0 and COMPOSE_FILE.exists():
        configured = _run([docker, "compose", "--file", str(COMPOSE_FILE), "config", "--quiet"])
        checks.append(
            ContainerCheck(
                "compose-config",
                "OK" if configured.returncode == 0 else "FAIL",
                "Compose configuration is valid"
                if configured.returncode == 0
                else _tail(configured.stderr or configured.stdout),
            )
        )
    return checks


def _tail(value: str, limit: int = 6) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    return " | ".join(lines[-limit:]) or "command failed without output"


def _image_prefix() -> str:
    package_path = ROOT / "frontend" / "package.json"
    try:
        import json

        package = json.loads(package_path.read_text(encoding="utf-8"))
        raw = str(package.get("name", "template-project")).removesuffix("-frontend")
    except (OSError, ValueError):
        raw = "template-project"
    normalized = re.sub(r"[^a-z0-9._-]+", "-", raw.lower()).strip("-.")
    return normalized or "template-project"


def _version() -> str:
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "local"


def _build_component(component: str, *, no_cache: bool = False) -> int:
    docker = _docker()
    if docker is None:
        logger.fail("Docker is required for container builds but was not found.")
        return 1

    dockerfile = DEPLOYMENT_DIR / "docker" / f"{component}.Dockerfile"
    if not dockerfile.exists():
        logger.fail(f"Container definition missing: {dockerfile.relative_to(ROOT)}")
        return 1

    command = [
        docker,
        "build",
        "--file",
        str(dockerfile),
        "--tag",
        f"{_image_prefix()}/{component}:{_version()}",
    ]
    if no_cache:
        command.append("--no-cache")

    if component == "frontend":
        profile = profile_runtime.active_profile(ROOT)
        try:
            resolved = resolve_configuration(profile, project_root=ROOT)
            public_api_url = resolved.value("VITE_API_BASE_URL") or ""
        except ConfigLoadError as exc:
            logger.fail(f"Could not resolve public frontend configuration: {exc}")
            return 1
        command.extend(["--build-arg", f"VITE_API_BASE_URL={public_api_url}"])

    command.append(str(ROOT))
    logger.info(f"Building {component} container image")
    completed = _run(command)
    if completed.returncode != 0:
        logger.fail(f"{component} container build failed: {_tail(completed.stderr or completed.stdout)}")
        return 1
    logger.ok(f"container image: {_image_prefix()}/{component}:{_version()}")
    return 0


def build(args: argparse.Namespace) -> int:
    profile = profile_runtime.active_profile(ROOT)
    if not profile.has_feature("cloud"):
        logger.fail(f"Container builds are disabled by active profile '{profile.profile_id}'.")
        return 1

    component = getattr(args, "component", "all")
    components = ("backend", "frontend") if component == "all" else (component,)
    failures = sum(_build_component(item, no_cache=bool(getattr(args, "no_cache", False))) for item in components)
    return 1 if failures else 0


def doctor(_args: argparse.Namespace | None = None) -> int:
    checks = collect_checks()
    for check in checks:
        logger.status(check.status, f"{check.name:<28} {check.message}")
    return 1 if any(check.status == "FAIL" for check in checks) else 0


def validate(_args: argparse.Namespace | None = None) -> int:
    return doctor(_args)
