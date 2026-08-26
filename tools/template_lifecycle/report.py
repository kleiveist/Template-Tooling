from __future__ import annotations

import difflib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.template_lifecycle.merge import read_path_payload
from tools.template_lifecycle.model import (
    REPORT_SCHEMA_VERSION,
    LifecycleError,
    UpdatePlan,
    VerificationResult,
)
from tools.template_lifecycle.state import _atomic_write

SENSITIVE_LINE = re.compile(
    r"(?i)[\"']?(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|authorization|database_url)"
    r"[a-z0-9_.-]*[\"']?\s*[:=]"
)
PRIVATE_KEY_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
PRIVATE_KEY_END = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY-----")
WINDOWS_ABSOLUTE_PATH = re.compile(r"(?i)(?<![A-Za-z0-9_.-])[A-Z]:[\\/][^\s;]+")
POSIX_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_.-])/(?:home|Users|tmp|var/tmp)/[^\s;]+")


def create_report_directory(target_root: Path, report_dir: str | None = None) -> Path:
    root = target_root.resolve()
    requested = Path(report_dir).expanduser() if report_dir else Path(".report/template-lifecycle")
    project_local = not requested.is_absolute()
    base = requested if requested.is_absolute() else root / requested
    if project_local:
        _validate_project_local_report_base(root, base)
    elif base.is_symlink():
        raise LifecycleError("Explicit report directory must not be a symbolic link.")
    base.mkdir(parents=True, exist_ok=True)
    resolved_base = base.resolve(strict=True)
    if project_local and not resolved_base.is_relative_to(root):
        raise LifecycleError("Project-local report directory resolves outside the product root.")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = resolved_base / run_id
    destination.mkdir(parents=True, exist_ok=False)
    return destination


def _validate_project_local_report_base(root: Path, base: Path) -> None:
    try:
        relative = base.relative_to(root)
    except ValueError as exc:
        raise LifecycleError("Relative report directory escapes the product root.") from exc
    if relative.parts[:1] != (".report",):
        raise LifecycleError("Project-local reports must be stored below .report/.")
    candidate = root
    for part in relative.parts:
        candidate /= part
        if candidate.is_symlink():
            raise LifecycleError("Project-local report directory must not contain symbolic links.")
    if not base.resolve(strict=False).is_relative_to(root):
        raise LifecycleError("Project-local report directory resolves outside the product root.")


def write_report(
    directory: Path,
    *,
    plan: UpdatePlan | None,
    target_root: Path,
    verification: VerificationResult | None,
    outcome: str,
    notices: tuple[str, ...] = (),
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    _write_json(directory / "plan.json", _plan_payload(plan, outcome))
    _write_json(directory / "conflicts.json", _conflict_payload(plan))
    _write_json(
        directory / "verification.json",
        _verification_payload(verification, target_root),
    )
    _atomic_write(
        directory / "changes.patch",
        _changes_patch(plan, target_root).encode("utf-8"),
        mode=0o644,
    )
    _atomic_write(
        directory / "summary.md",
        _summary(plan, outcome, verification, notices, target_root).encode("utf-8"),
        mode=0o644,
    )


def finalize_report(
    directory: Path,
    *,
    plan: UpdatePlan | None,
    target_root: Path,
    verification: VerificationResult | None,
    outcome: str,
    notices: tuple[str, ...] = (),
) -> None:
    """Finalize outcome evidence without regenerating the pre-apply patch."""

    _write_json(directory / "plan.json", _plan_payload(plan, outcome))
    _write_json(directory / "conflicts.json", _conflict_payload(plan))
    _write_json(
        directory / "verification.json",
        _verification_payload(verification, target_root),
    )
    _atomic_write(
        directory / "summary.md",
        _summary(plan, outcome, verification, notices, target_root).encode("utf-8"),
        mode=0o644,
    )


def plan_to_dict(plan: UpdatePlan) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "baseline_commit": plan.baseline_commit,
        "target_commit": plan.target_commit,
        "target_version": plan.target_version,
        "architecture_change": plan.architecture_change,
        "migrations": list(plan.migrations),
        "operations": [
            {
                "action": operation.action,
                "path": operation.path,
                "source_path": operation.source_path,
                "reason": operation.reason,
                "base_sha256": operation.base_sha256,
                "local_sha256": operation.local_sha256,
                "incoming_sha256": operation.incoming_sha256,
                "result_sha256": operation.result_sha256,
                "kind": operation.kind,
                "executable": operation.executable,
            }
            for operation in plan.operations
        ],
    }


def verification_to_dict(result: VerificationResult) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "ok": result.ok,
        "findings": [
            {
                "check": finding.check,
                "status": finding.status,
                "message": finding.message,
            }
            for finding in result.findings
        ],
    }


def _plan_payload(plan: UpdatePlan | None, outcome: str) -> dict[str, Any]:
    if plan is None:
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "outcome": outcome,
            "plan": None,
        }
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "outcome": outcome,
        "plan": plan_to_dict(plan),
    }


def _conflict_payload(plan: UpdatePlan | None) -> dict[str, Any]:
    conflicts = () if plan is None else plan.conflicts
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "conflicts": [
            {
                "path": conflict.path,
                "reason": conflict.reason,
                "base_sha256": conflict.base_sha256,
                "local_sha256": conflict.local_sha256,
                "incoming_sha256": conflict.incoming_sha256,
            }
            for conflict in conflicts
        ],
    }


def _verification_payload(result: VerificationResult | None, target_root: Path) -> dict[str, Any]:
    if result is None:
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "status": "NOT_RUN",
            "findings": [],
        }
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "PASS" if result.ok else "FAIL",
        "ok": result.ok,
        "findings": [
            {
                "check": finding.check,
                "status": finding.status,
                "message": _sanitize_message(finding.message, target_root),
            }
            for finding in result.findings
        ],
    }


def _summary(
    plan: UpdatePlan | None,
    outcome: str,
    verification: VerificationResult | None,
    notices: tuple[str, ...],
    target_root: Path,
) -> str:
    operations = () if plan is None else plan.operations
    counts: dict[str, int] = {}
    for operation in operations:
        counts[operation.action] = counts.get(operation.action, 0) + 1
    verification_status = "NOT RUN" if verification is None else ("PASS" if verification.ok else "FAIL")
    lines = [
        "# Template lifecycle report",
        "",
        f"- Outcome: `{outcome}`",
        f"- Verification: `{verification_status}`",
        f"- Conflicts: `{0 if plan is None else len(plan.conflicts)}`",
        "",
        "## Operations",
        "",
    ]
    if counts:
        lines.extend(f"- `{action}`: {counts[action]}" for action in sorted(counts))
    else:
        lines.append("- No operations were planned.")
    if notices:
        lines.extend(["", "## Notices", ""])
        lines.extend(f"- {_sanitize_message(notice, target_root)}" for notice in notices)
    lines.append("")
    return "\n".join(lines)


def _changes_patch(plan: UpdatePlan | None, target_root: Path) -> str:
    if plan is None:
        return "# No update plan was produced.\n"
    chunks: list[str] = []
    for operation in plan.operations:
        if not operation.changes_product:
            continue
        if operation.kind != "text" or operation.action == "MOVE":
            chunks.append(f"# {operation.action} {operation.path} ({operation.kind or 'path'})\n")
            continue
        before = b"" if operation.action == "ADD" else _existing_payload(target_root, operation.path)
        after = b"" if operation.action == "DELETE" else (operation.result or b"")
        chunks.append(_text_diff(operation.path, before, after))
    return "".join(chunks) or "# No product file changes.\n"


def _existing_payload(target_root: Path, relative: str) -> bytes:
    try:
        return read_path_payload(target_root, relative)
    except LifecycleError:
        return b""


def _text_diff(relative: str, before: bytes, after: bytes) -> str:
    before_lines = _redacted_lines(before)
    after_lines = _redacted_lines(after)
    return "".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
            lineterm="\n",
        )
    )


def _redacted_lines(content: bytes) -> list[str]:
    text = content.decode("utf-8", errors="replace")
    redacted: list[str] = []
    private_key = False
    for line in text.splitlines():
        if PRIVATE_KEY_BEGIN.search(line):
            private_key = True
        if private_key:
            redacted.append("<redacted private key material>\n")
            if PRIVATE_KEY_END.search(line):
                private_key = False
            continue
        redacted.append(_redact_line(line) + "\n")
    return redacted


def _redact_line(line: str) -> str:
    if SENSITIVE_LINE.search(line):
        prefix = line[: len(line) - len(line.lstrip())]
        return f"{prefix}<redacted sensitive line>"
    return line


def _sanitize_message(message: str, target_root: Path) -> str:
    sanitized = message.replace(str(target_root.resolve()), ".")
    sanitized = WINDOWS_ABSOLUTE_PATH.sub("<absolute-path>", sanitized)
    sanitized = POSIX_ABSOLUTE_PATH.sub("<absolute-path>", sanitized)
    return "\n".join(_redact_line(line) for line in sanitized.splitlines())


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    content = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    _atomic_write(path, content.encode("utf-8"), mode=0o644)
