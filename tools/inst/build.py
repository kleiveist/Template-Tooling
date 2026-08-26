from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

from tools import logger
from tools.process import prepare_command
from tools.profiles import runtime as profile_runtime

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"
WEB_ARTIFACT_DIR = ROOT / ".dist" / "web"
WEB_ZIP_PATH = WEB_ARTIFACT_DIR / "template-project-web.zip"


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(prepare_command(cmd), cwd=cwd, text=True, capture_output=True, check=False)


def _tail_lines(text: str, limit: int = 12) -> list[str]:
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-limit:]


def _print_tail(label: str, text: str) -> None:
    lines = _tail_lines(text)
    if not lines:
        return
    logger.info(f"  {label}:")
    for line in lines:
        print(f"    {line}")


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _create_web_zip() -> tuple[bool, str]:
    files = sorted(path for path in DIST_DIR.rglob("*") if path.is_file())
    if not files:
        return False, "frontend/dist contains no files to package"

    WEB_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(WEB_ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(DIST_DIR).as_posix())

    return True, _relative(WEB_ZIP_PATH)


def main(args: argparse.Namespace) -> int:
    _ = args
    if not profile_runtime.feature_enabled("frontend", ROOT):
        profile = profile_runtime.active_profile(ROOT)
        logger.fail(f"Web build is disabled by active profile '{profile.profile_id}'.")
        return 1

    package_json = FRONTEND_DIR / "package.json"
    if not package_json.exists():
        logger.fail("frontend/package.json missing; cannot run web build")
        return 1

    npm = shutil.which("npm")
    if npm is None:
        logger.fail("npm not found. Action: install Node.js and npm.")
        return 1

    command = [npm, "run", "build"]
    logger.info("Building frontend web release")
    logger.info(f"  command: {shlex.join(command)}")
    logger.info(f"  cwd: {FRONTEND_DIR}")

    started = time.monotonic()
    completed = _run(command, cwd=FRONTEND_DIR)
    elapsed = time.monotonic() - started
    if completed.returncode != 0:
        logger.fail(f"Web build failed ({elapsed:.2f}s)")
        logger.info(f"  exit code: {completed.returncode}")
        _print_tail("stdout tail", completed.stdout or "")
        _print_tail("stderr tail", completed.stderr or "")
        return 1

    if not DIST_DIR.exists():
        logger.fail("Web build completed but frontend/dist was not created")
        logger.info(f"  exit code: {completed.returncode}")
        _print_tail("stdout tail", completed.stdout or "")
        _print_tail("stderr tail", completed.stderr or "")
        return 1

    zip_ok, zip_message = _create_web_zip()
    if not zip_ok:
        logger.fail(f"Web build completed but release ZIP was not created: {zip_message}")
        return 1

    logger.ok(f"Web build completed ({elapsed:.2f}s)")
    logger.ok(f"web artifact: {_relative(DIST_DIR)}")
    logger.ok(f"github release zip: {zip_message}")
    return 0
