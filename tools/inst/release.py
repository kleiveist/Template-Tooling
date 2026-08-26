from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools import logger
from tools.profiles import runtime as profile_runtime

ROOT = Path(__file__).resolve().parents[2]
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


@dataclass(frozen=True, slots=True)
class ReleaseCheck:
    name: str
    status: str
    message: str


def source_version() -> str:
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def collect_version_checks() -> list[ReleaseCheck]:
    expected = source_version()
    if not expected:
        return [ReleaseCheck("version:source", "FAIL", "VERSION is missing or empty")]
    checks = [
        ReleaseCheck(
            "version:source",
            "OK" if SEMVER.fullmatch(expected) else "FAIL",
            f"VERSION={expected}" if SEMVER.fullmatch(expected) else f"VERSION is not SemVer: {expected}",
        )
    ]
    profile = profile_runtime.active_profile(ROOT)
    versions: list[tuple[str, str]] = []
    try:
        if profile.has_feature("frontend"):
            package = _read_json(ROOT / "frontend" / "package.json")
            package_lock = _read_json(ROOT / "frontend" / "package-lock.json")
            lock_packages = package_lock.get("packages", {})
            lock_root = lock_packages.get("", {}) if isinstance(lock_packages, dict) else {}
            versions.extend(
                [
                    ("frontend/package.json", str(package.get("version", ""))),
                    ("frontend/package-lock.json", str(package_lock.get("version", ""))),
                    (
                        "frontend/package-lock.json root package",
                        str(lock_root.get("version", "")) if isinstance(lock_root, dict) else "",
                    ),
                ]
            )
        if profile.has_feature("tauri"):
            tauri = _read_json(ROOT / "src-tauri" / "tauri.conf.json")
            cargo = tomllib.loads((ROOT / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8"))
            cargo_name = str(cargo.get("package", {}).get("name", ""))
            cargo_lock = tomllib.loads((ROOT / "src-tauri" / "Cargo.lock").read_text(encoding="utf-8"))
            locked_root = next(
                (
                    item
                    for item in cargo_lock.get("package", [])
                    if isinstance(item, dict) and item.get("name") == cargo_name
                ),
                {},
            )
            versions.extend(
                [
                    ("src-tauri/tauri.conf.json", str(tauri.get("version", ""))),
                    ("src-tauri/Cargo.toml", str(cargo.get("package", {}).get("version", ""))),
                    ("src-tauri/Cargo.lock root package", str(locked_root.get("version", ""))),
                ]
            )
    except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        checks.append(ReleaseCheck("version:metadata", "FAIL", f"Could not read version metadata: {exc}"))
        return checks

    for path, actual in versions:
        checks.append(
            ReleaseCheck(
                f"version:{Path(path).name}",
                "OK" if actual == expected else "FAIL",
                f"{path}={actual or '<missing>'}; expected {expected}",
            )
        )
    return checks


def _placeholder_checks() -> list[ReleaseCheck]:
    # The source repository intentionally owns the canonical scaffold identity.
    # Generated projects do not include CI workflow sources and must still replace
    # every known placeholder before their first release.
    if (ROOT / ".github" / "workflows" / "profiles.yml").is_file():
        return [
            ReleaseCheck(
                "template-identity",
                "OK",
                "canonical template identity is expected in the template source repository",
            )
        ]

    profile = profile_runtime.active_profile(ROOT)
    targets: list[tuple[Path, tuple[str, ...]]] = []
    if profile.has_feature("frontend"):
        targets.extend(
            [
                (ROOT / "frontend" / "package.json", ("template-project", "project-template")),
                (ROOT / "frontend" / "index.html", ("Template Project",)),
                (ROOT / "frontend" / "src" / "main.ts", ("Template Project",)),
            ]
        )
    if profile.has_feature("backend"):
        targets.extend(
            [
                (ROOT / "backend" / "app" / "config" / "settings.py", ("Template Project API",)),
                (ROOT / "backend" / "app" / "api" / "health.py", ("template-backend",)),
            ]
        )
    targets.append((ROOT / "tools" / "inst" / "build.py", ("template-project-web.zip",)))
    if profile.has_feature("cloud"):
        targets.append(
            (ROOT / "deployment" / "compose.yaml", ("Template Project", "template-project", "project-template"))
        )
    if profile.has_feature("tauri"):
        targets.extend(
            [
                (
                    ROOT / "src-tauri" / "tauri.conf.json",
                    ("Template Project", "template-project", "project-template", "com.example.templateproject"),
                ),
                (ROOT / "src-tauri" / "Cargo.toml", ("Template Project", "project-template")),
                (ROOT / "src-tauri" / "app-icon.svg", ("Template Project",)),
            ]
        )

    checks: list[ReleaseCheck] = []
    for path, placeholders in targets:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            checks.append(ReleaseCheck("template-identity", "FAIL", f"Could not read {path}: {exc}"))
            continue
        matches = [placeholder for placeholder in placeholders if placeholder in content]
        if matches:
            checks.append(
                ReleaseCheck(
                    "template-identity",
                    "FAIL",
                    f"Template identifier still present: {path.relative_to(ROOT)} -> {', '.join(matches)}",
                )
            )
    if not checks:
        checks.append(
            ReleaseCheck("template-identity", "OK", "release identity contains no known template placeholders")
        )
    return checks


def _tauri_security_checks() -> list[ReleaseCheck]:
    if not profile_runtime.feature_enabled("tauri", ROOT):
        return [ReleaseCheck("tauri-security", "OK", "Tauri disabled by active profile")]
    try:
        config = _read_json(ROOT / "src-tauri" / "tauri.conf.json")
        capabilities = _read_json(ROOT / "src-tauri" / "capabilities" / "default.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [ReleaseCheck("tauri-security", "FAIL", f"Could not read Tauri security configuration: {exc}")]
    csp = config.get("app", {}).get("security", {}).get("csp")
    hardened_csp = (
        isinstance(csp, str) and "default-src 'self'" in csp and "'unsafe-eval'" not in csp and "*" not in csp
    )
    checks = [
        ReleaseCheck(
            "tauri-csp",
            "OK" if hardened_csp else "WARN",
            "Tauri CSP is explicitly restricted" if hardened_csp else "Tauri CSP is not production hardened.",
        )
    ]
    permissions = capabilities.get("permissions", [])
    unexpected = (
        [item for item in permissions if item != "core:default"] if isinstance(permissions, list) else ["invalid"]
    )
    checks.append(
        ReleaseCheck(
            "tauri-capabilities",
            "OK" if not unexpected else "WARN",
            "only core:default is enabled" if not unexpected else f"review additional permissions: {unexpected}",
        )
    )
    return checks


def _git_check() -> ReleaseCheck:
    try:
        probe = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return ReleaseCheck("git", "WARN", f"working tree status unavailable: {exc}")
    if probe.returncode != 0:
        return ReleaseCheck("git", "WARN", "working tree status unavailable")
    return ReleaseCheck(
        "git",
        "OK" if not probe.stdout.strip() else "FAIL",
        "working tree is clean" if not probe.stdout.strip() else "working tree contains uncommitted changes",
    )


def _signing_checks() -> list[ReleaseCheck]:
    if not profile_runtime.feature_enabled("tauri", ROOT):
        return []
    expected = (
        "WINDOWS_CERTIFICATE_BASE64",
        "WINDOWS_CERTIFICATE_PASSWORD",
        "APPLE_CERTIFICATE",
        "APPLE_CERTIFICATE_PASSWORD",
        "APPLE_SIGNING_IDENTITY",
        "APPLE_ID",
        "APPLE_PASSWORD",
        "APPLE_TEAM_ID",
    )
    configured = [name for name in expected if os.environ.get(name)]
    return [
        ReleaseCheck(
            "signing",
            "OK" if len(configured) == len(expected) else "WARN",
            "signing environment is configured"
            if len(configured) == len(expected)
            else "verification build is unsigned; production signing remains externally configured",
        )
    ]


def _tag_check() -> ReleaseCheck:
    ref_type = os.environ.get("GITHUB_REF_TYPE", "")
    ref = os.environ.get("GITHUB_REF", "")
    if ref_type != "tag" and not ref.startswith("refs/tags/"):
        return ReleaseCheck("release-tag", "OK", "validation is not tag-triggered")
    tag = os.environ.get("GITHUB_REF_NAME") or ref.removeprefix("refs/tags/")
    expected = f"v{source_version()}"
    return ReleaseCheck(
        "release-tag",
        "OK" if tag == expected else "FAIL",
        f"tag {tag or '<missing>'}; expected {expected}",
    )


def sync_versions() -> int:
    expected = source_version()
    if not SEMVER.fullmatch(expected):
        logger.fail(f"VERSION is missing or not valid SemVer: {expected or '<empty>'}")
        return 1
    profile = profile_runtime.active_profile(ROOT)
    try:
        if profile.has_feature("frontend"):
            package_path = ROOT / "frontend" / "package.json"
            package = _read_json(package_path)
            package["version"] = expected
            package_path.write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8", newline="\n")

            lock_path = ROOT / "frontend" / "package-lock.json"
            package_lock = _read_json(lock_path)
            package_lock["version"] = expected
            packages = package_lock.get("packages")
            if isinstance(packages, dict) and isinstance(packages.get(""), dict):
                packages[""]["version"] = expected
            lock_path.write_text(json.dumps(package_lock, indent=2) + "\n", encoding="utf-8", newline="\n")

        if profile.has_feature("tauri"):
            tauri_path = ROOT / "src-tauri" / "tauri.conf.json"
            tauri = _read_json(tauri_path)
            tauri["version"] = expected
            tauri_path.write_text(json.dumps(tauri, indent=2) + "\n", encoding="utf-8", newline="\n")

            cargo_path = ROOT / "src-tauri" / "Cargo.toml"
            cargo_text = cargo_path.read_text(encoding="utf-8")
            cargo_text, replacements = re.subn(
                r'(\[package\][\s\S]*?^version\s*=\s*")[^"]+("\s*$)',
                rf"\g<1>{expected}\g<2>",
                cargo_text,
                count=1,
                flags=re.MULTILINE,
            )
            if replacements != 1:
                raise ValueError("Cargo package version could not be located")
            cargo_path.write_text(cargo_text, encoding="utf-8", newline="\n")

            cargo = tomllib.loads(cargo_text)
            package_name = str(cargo.get("package", {}).get("name", ""))
            lock_path = ROOT / "src-tauri" / "Cargo.lock"
            lock_text = lock_path.read_text(encoding="utf-8")
            pattern = rf'(\[\[package\]\]\nname = "{re.escape(package_name)}"\nversion = ")[^"]+("\n)'
            lock_text, replacements = re.subn(pattern, rf"\g<1>{expected}\g<2>", lock_text, count=1)
            if replacements != 1:
                raise ValueError("Cargo.lock root package version could not be located")
            lock_path.write_text(lock_text, encoding="utf-8", newline="\n")
    except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        logger.fail(f"Could not synchronize version metadata: {exc}")
        return 1
    logger.ok(f"Synchronized enabled component metadata to {expected}")
    return 0


def collect_release_checks() -> list[ReleaseCheck]:
    return [
        *collect_version_checks(),
        *_placeholder_checks(),
        *_tauri_security_checks(),
        _tag_check(),
        _git_check(),
        *_signing_checks(),
    ]


def _print_checks(checks: list[ReleaseCheck]) -> int:
    for check in checks:
        logger.status(check.status, f"{check.name:<24} {check.message}")
    return 1 if any(check.status == "FAIL" for check in checks) else 0


def version(args: argparse.Namespace) -> int:
    if getattr(args, "version_command", None) == "check":
        return _print_checks(collect_version_checks())
    if getattr(args, "version_command", None) == "sync":
        return sync_versions()
    value = source_version()
    if not value:
        logger.fail("VERSION is missing or empty")
        return 1
    print(value)
    return 0


def release(args: argparse.Namespace) -> int:
    if getattr(args, "release_command", None) != "check":
        args.release_parser.print_help()
        return 0
    return _print_checks(collect_release_checks())
