from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT / "frontend"
TAURI_DIR = ROOT / "src-tauri"
DIST_DIR = ROOT / ".dist" / "desktop"

FRONTEND_PACKAGE_JSON = FRONTEND_DIR / "package.json"
FRONTEND_PACKAGE_LOCK = FRONTEND_DIR / "package-lock.json"
FRONTEND_PNPM_LOCK = FRONTEND_DIR / "pnpm-lock.yaml"
TAURI_CONFIG = TAURI_DIR / "tauri.conf.json"


def _app_identity() -> tuple[str, str]:
    fallback = ("Template Project", "com.example.templateproject")
    try:
        payload = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    name = payload.get("productName")
    identifier = payload.get("identifier")
    return (
        name if isinstance(name, str) and name.strip() else fallback[0],
        identifier if isinstance(identifier, str) and identifier.strip() else fallback[1],
    )


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "template-project"


APP_NAME, APP_ID = _app_identity()
APP_SLUG = _slug(APP_NAME)


def local_tauri_binary() -> Path:
    binary_name = "tauri.cmd" if os.name == "nt" else "tauri"
    return FRONTEND_DIR / "node_modules" / ".bin" / binary_name


def bundle_roots() -> list[Path]:
    roots = [TAURI_DIR / "target" / "release" / "bundle"]
    target_root = TAURI_DIR / "target"
    if target_root.exists():
        roots.extend(sorted(target_root.glob("*/release/bundle")))
    return roots
