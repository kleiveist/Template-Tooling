from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from tools.template_lifecycle.model import TEMPLATE_ID, TEMPLATE_URL, LifecycleError

FULL_COMMIT = re.compile(r"[0-9a-f]{40}")
SEMVER = re.compile(
    r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?"
)
_SCP_ORIGIN = re.compile(r"(?:[^@/:\s]+@)?(?P<host>[^/:\s]+):(?P<path>[^\s]+)")


@dataclass(frozen=True, slots=True)
class LocalTemplateSource:
    root: Path
    origin: str
    head_commit: str
    dirty: bool
    version: str
    status: str = ""
    template_id: str = TEMPLATE_ID


@dataclass(frozen=True, slots=True)
class ResolvedTemplateRef:
    requested: str
    commit: str
    version: str


def resolve_source(source_dir: Path, *, expected_template_id: str = TEMPLATE_ID) -> LocalTemplateSource:
    """Validate and describe a local checkout of the canonical template."""

    requested_root = source_dir.expanduser().resolve()
    if not requested_root.is_dir():
        raise LifecycleError(f"Template source directory does not exist: {source_dir}.")

    inside = _run_git(requested_root, "rev-parse", "--is-inside-work-tree").stdout.strip()
    if inside != "true":
        raise LifecycleError(f"Template source is not a Git working tree: {requested_root}.")
    top_level_text = _run_git(requested_root, "rev-parse", "--show-toplevel").stdout.strip()
    top_level = Path(top_level_text).resolve()
    if top_level != requested_root:
        raise LifecycleError(f"Template source must name the repository root, not a nested path: {requested_root}.")

    origin_probe = _run_git(requested_root, "config", "--get", "remote.origin.url", allowed=(0, 1))
    raw_origin = origin_probe.stdout.strip()
    if origin_probe.returncode != 0 or not raw_origin:
        raise LifecycleError("Template source has no remote.origin.url; the canonical template identity is unknown.")
    template_id, canonical_origin = normalize_origin(raw_origin)
    if template_id.casefold() != expected_template_id.casefold():
        raise LifecycleError(f"Template source identity is '{template_id}', expected '{expected_template_id}'.")

    head_commit = _full_commit(_run_git(requested_root, "rev-parse", "--verify", "HEAD^{commit}").stdout)
    status = working_tree_status(requested_root)
    version = _read_version(requested_root / "VERSION", context="template working tree")
    return LocalTemplateSource(
        root=requested_root,
        origin=canonical_origin,
        head_commit=head_commit,
        dirty=bool(status),
        version=version,
        status=status,
        template_id=expected_template_id,
    )


def normalize_origin(origin: str) -> tuple[str, str]:
    """Return the canonical template id and URL for a supported GitHub origin."""

    value = origin.strip()
    if not value or any(character in value for character in "\r\n\0"):
        raise LifecycleError("Template source origin is empty or unsafe.")

    scp_match = _SCP_ORIGIN.fullmatch(value) if "://" not in value else None
    if scp_match is not None:
        host = scp_match.group("host")
        repository_path = scp_match.group("path")
    else:
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "ssh", "git"} or not parsed.hostname:
            raise LifecycleError(f"Unsupported template source origin: {origin}.")
        if parsed.query or parsed.fragment:
            raise LifecycleError("Template source origin must not contain a query or fragment.")
        host = parsed.hostname
        repository_path = parsed.path

    if host.casefold() != "github.com":
        raise LifecycleError(f"Template source origin host must be github.com, got '{host}'.")
    parts = [unquote(part) for part in repository_path.strip("/").split("/") if part]
    if len(parts) != 2 or any(part in {".", ".."} for part in parts):
        raise LifecycleError(f"Template source origin does not identify an owner/repository pair: {origin}.")
    owner, repository = parts
    if repository.casefold().endswith(".git"):
        repository = repository[:-4]
    if not owner or not repository:
        raise LifecycleError(f"Template source origin is incomplete: {origin}.")
    template_id = f"{owner}/{repository}".casefold()
    canonical = f"https://github.com/{owner}/{repository}.git"
    if template_id == TEMPLATE_ID:
        canonical = TEMPLATE_URL
    return template_id, canonical


def working_tree_status(source: LocalTemplateSource | Path) -> str:
    root = source.root if isinstance(source, LocalTemplateSource) else source.resolve()
    completed = _run_git(root, "status", "--porcelain=v1", "--untracked-files=all")
    return completed.stdout.rstrip("\n")


def resolve_ref(source: LocalTemplateSource, requested_ref: str) -> ResolvedTemplateRef:
    requested = _safe_ref(requested_ref)
    completed = _run_git(
        source.root,
        "rev-parse",
        "--verify",
        "--end-of-options",
        f"{requested}^{{commit}}",
        allowed=(0, 1, 128),
    )
    if completed.returncode != 0:
        raise LifecycleError(
            f"Template ref '{requested}' is unavailable locally. Fetch the trusted history manually and retry."
        )
    commit = _full_commit(completed.stdout)
    version_text = _run_git(source.root, "show", f"{commit}:VERSION", allowed=(0, 128))
    if version_text.returncode != 0:
        raise LifecycleError(f"Template commit {commit} does not contain a readable VERSION file.")
    version = _parse_version(version_text.stdout, context=f"template commit {commit}")
    return ResolvedTemplateRef(requested=requested, commit=commit, version=version)


def assert_ancestor(source: LocalTemplateSource, baseline_commit: str, target_commit: str) -> None:
    baseline = _require_commit(baseline_commit, label="baseline")
    target = _require_commit(target_commit, label="target")
    _require_available(source, baseline, label="baseline")
    _require_available(source, target, label="target")
    completed = _run_git(
        source.root,
        "merge-base",
        "--is-ancestor",
        baseline,
        target,
        allowed=(0, 1),
    )
    if completed.returncode == 1:
        raise LifecycleError(
            f"Target commit {target} is not a descendant of baseline commit {baseline}; "
            "downgrades and unrelated histories are not supported."
        )


@contextmanager
def temporary_worktree(source: LocalTemplateSource, commit: str) -> Iterator[Path]:
    resolved_commit = _require_commit(commit, label="worktree")
    _require_available(source, resolved_commit, label="worktree")
    temporary = tempfile.TemporaryDirectory(prefix="template-lifecycle-worktree-")
    checkout = Path(temporary.name) / "checkout"
    added = False
    body_failure: BaseException | None = None
    try:
        _run_git(source.root, "worktree", "add", "--detach", str(checkout), resolved_commit)
        added = True
        try:
            yield checkout
        except (Exception, KeyboardInterrupt, SystemExit) as exc:
            body_failure = exc
            raise
    finally:
        cleanup_failure: LifecycleError | None = None
        if added:
            try:
                _run_git(source.root, "worktree", "remove", "--force", str(checkout))
            except LifecycleError as exc:
                cleanup_failure = exc
                shutil.rmtree(checkout, ignore_errors=True)
        temporary.cleanup()
        if cleanup_failure is not None:
            if body_failure is not None:
                body_failure.add_note(f"Temporary Git worktree cleanup also failed: {cleanup_failure}")
            else:
                raise cleanup_failure


def _require_available(source: LocalTemplateSource, commit: str, *, label: str) -> None:
    completed = _run_git(
        source.root,
        "cat-file",
        "-e",
        f"{commit}^{{commit}}",
        allowed=(0, 1, 128),
    )
    if completed.returncode != 0:
        raise LifecycleError(
            f"The {label} commit {commit} is unavailable locally. Fetch the trusted history manually and retry."
        )


def _run_git(root: Path, *arguments: str, allowed: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[str]:
    command = ["git", *arguments]
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=False,
        )
    except (OSError, UnicodeError) as exc:
        raise LifecycleError(f"Could not execute local Git command '{arguments[0]}': {exc}.") from exc
    if completed.returncode not in allowed:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise LifecycleError(f"Local Git command '{arguments[0]}' failed: {detail}.")
    return completed


def _safe_ref(value: str) -> str:
    requested = value.strip()
    if not requested or requested.startswith("-") or any(character in requested for character in "\r\n\0"):
        raise LifecycleError("Template ref is empty or unsafe.")
    return requested


def _require_commit(value: str, *, label: str) -> str:
    commit = value.strip().lower()
    if not FULL_COMMIT.fullmatch(commit):
        raise LifecycleError(f"The {label} commit must be a full 40-character SHA.")
    return commit


def _full_commit(value: str) -> str:
    commit = value.strip().lower()
    if not FULL_COMMIT.fullmatch(commit):
        raise LifecycleError("Git did not resolve the ref to a full 40-character commit SHA.")
    return commit


def _read_version(path: Path, *, context: str) -> str:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LifecycleError(f"Could not read VERSION from {context}.") from exc
    return _parse_version(content, context=context)


def _parse_version(content: str, *, context: str) -> str:
    version = content.strip()
    if not SEMVER.fullmatch(version):
        raise LifecycleError(f"VERSION in {context} is missing or not valid SemVer: {version or '<empty>'}.")
    return version


validate_local_source = resolve_source
