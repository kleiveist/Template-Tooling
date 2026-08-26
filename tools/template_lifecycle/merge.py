from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from tools.template_lifecycle.model import LifecycleError


@dataclass(frozen=True, slots=True)
class MergeResult:
    content: bytes
    conflict: bool


def merge_text(base: bytes, local: bytes, incoming: bytes) -> MergeResult:
    """Perform a real three-way text merge without touching the product tree."""
    normalized_base = _normalize_newlines(base)
    normalized_local = _normalize_newlines(local)
    normalized_incoming = _normalize_newlines(incoming)
    newline = _preferred_newline(local)
    with tempfile.TemporaryDirectory(prefix="template-lifecycle-merge-") as directory:
        root = Path(directory)
        paths = [root / name for name in ("local", "base", "incoming")]
        for path, content in zip(paths, (normalized_local, normalized_base, normalized_incoming), strict=True):
            path.write_bytes(content)
        command = [
            "git",
            "merge-file",
            "--stdout",
            "--diff3",
            "-L",
            "LOCAL",
            "-L",
            "BASE",
            "-L",
            "INCOMING",
            str(paths[0]),
            str(paths[1]),
            str(paths[2]),
        ]
        try:
            completed = subprocess.run(command, capture_output=True, check=False)
        except OSError as exc:
            raise LifecycleError(f"Could not execute the three-way merge engine: {exc}.") from exc
    if completed.returncode == 0:
        conflict = False
    elif 1 <= completed.returncode <= 127:
        # git-merge-file reports the number of conflict regions, capped at 127.
        conflict = True
    else:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise LifecycleError(f"Three-way merge failed: {detail or f'exit code {completed.returncode}'}.")
    content = _restore_newlines(completed.stdout, newline)
    return MergeResult(content=content, conflict=conflict)


def read_path_payload(root: Path, relative: str) -> bytes:
    path = root / Path(relative)
    try:
        if path.is_symlink():
            return os.readlink(path).encode("utf-8")
        return path.read_bytes()
    except OSError as exc:
        raise LifecycleError(f"Could not read lifecycle path {relative}.") from exc


def text_equivalent(left: bytes, right: bytes) -> bool:
    return _normalize_newlines(left) == _normalize_newlines(right)


def _normalize_newlines(content: bytes) -> bytes:
    return content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _preferred_newline(content: bytes) -> bytes:
    without_crlf = content.replace(b"\r\n", b"")
    if b"\r\n" in content and b"\n" not in without_crlf and b"\r" not in without_crlf:
        return b"\r\n"
    return b"\n"


def _restore_newlines(content: bytes, newline: bytes) -> bytes:
    if newline == b"\n":
        return content
    return content.replace(b"\n", newline)
