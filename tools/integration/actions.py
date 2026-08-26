"""Fixed, shell-free actions for isolated integration staging trees."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from tools.core.filesystem import (
    FilesystemSafetyError,
    read_regular_bytes,
    safe_join,
    safe_relative_path,
    validate_root,
)
from tools.integration.model import (
    Finding,
    FindingStatus,
    IntegrationError,
    VerificationResult,
)
from tools.integration.sanitize import sanitize_text
from tools.process import run_bounded, safe_platform_environment

_DEFAULT_TIMEOUT_SECONDS = 900
_MAX_TIMEOUT_SECONDS = 1800
_OUTPUT_LIMIT = 1200
_PYTEST_LAUNCHER = (
    "import importlib.util,pathlib,sys;"
    "import pytest;"
    "root=pathlib.Path(sys.argv.pop(1));"
    "spec=importlib.util.spec_from_file_location("
    "'tools',root/'__init__.py',submodule_search_locations=[str(root)]);"
    "module=importlib.util.module_from_spec(spec);"
    "sys.modules['tools']=module;"
    "spec.loader.exec_module(module);"
    "raise SystemExit(pytest.main(sys.argv[1:]))"
)


class ActionKind(str, Enum):
    """Allowlisted action kinds; callers cannot supply commands or arguments."""

    DEPENDENCIES = "dependencies"
    QUALITY = "quality"
    TESTS = "tests"


_ACTION_ORDER = {
    ActionKind.DEPENDENCIES: 0,
    ActionKind.QUALITY: 1,
    ActionKind.TESTS: 2,
}


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """One fixed action with bounded trigger paths and execution-time policy."""

    kind: ActionKind | str
    paths: tuple[str, ...] = ()
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        kind = self.kind
        if not isinstance(kind, ActionKind):
            try:
                kind = ActionKind(str(kind).strip().lower())
            except ValueError as exc:
                raise ValueError(
                    f"Unsupported integration action kind: {self.kind!r}."
                ) from exc
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int)
            or not 1 <= self.timeout_seconds <= _MAX_TIMEOUT_SECONDS
        ):
            raise ValueError(
                "Integration action timeout must be an integer between 1 and "
                f"{_MAX_TIMEOUT_SECONDS} seconds."
            )
        if not isinstance(self.paths, tuple):
            raise TypeError("Integration action paths must be a tuple.")
        try:
            paths = tuple(sorted({safe_relative_path(path) for path in self.paths}))
        except (FilesystemSafetyError, TypeError) as exc:
            raise ValueError(
                "Integration action paths must be safe relative paths."
            ) from exc
        if len(paths) != len(self.paths):
            raise ValueError("Integration action paths must be unique and normalized.")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "paths", paths)


class ActionRunner:
    """Run canonical installability and verification checks in isolated staging."""

    def __init__(self, actions: Iterable[ActionSpec]) -> None:
        try:
            supplied = tuple(actions)
        except TypeError as exc:
            raise TypeError(
                "Integration actions must be an iterable of ActionSpec values."
            ) from exc
        if any(not isinstance(action, ActionSpec) for action in supplied):
            raise TypeError("Integration actions must contain only ActionSpec values.")
        kinds = tuple(action.kind for action in supplied)
        if len(kinds) != len(set(kinds)):
            raise ValueError("Integration action kinds must be unique.")
        self.actions = tuple(
            sorted(supplied, key=lambda item: _ACTION_ORDER[item.kind])
        )

    def __call__(self, project_root: Path) -> VerificationResult:
        """Make the runner directly usable as a transaction staged callback."""

        return self.run(project_root)

    def run(self, project_root: Path) -> VerificationResult:
        """Execute configured actions with a sanitized, disposable environment."""

        root = _validated_action_root(project_root)
        if not self.actions:
            return VerificationResult(())
        findings: list[Finding] = []
        with tempfile.TemporaryDirectory(prefix="tooling-actions-") as temporary:
            environment = _action_environment(Path(temporary), root)
            for action in self.actions:
                finding = _run_action(root, action, environment)
                findings.append(finding)
                if finding.status is FindingStatus.FAIL:
                    break
        return VerificationResult(tuple(findings))


def _validated_action_root(project_root: Path) -> Path:
    try:
        return validate_root(project_root)
    except FilesystemSafetyError as exc:
        raise IntegrationError(str(exc)) from exc


def _command(kind: ActionKind, root: Path) -> tuple[str, ...]:
    tools = root / "tools"
    if kind is ActionKind.QUALITY:
        return (
            sys.executable,
            "-I",
            "-B",
            "-m",
            "ruff",
            "check",
            "--isolated",
            "--no-cache",
            str(tools / "adapters"),
            str(tools / "integration"),
        )
    if kind is ActionKind.TESTS:
        return (
            sys.executable,
            "-I",
            "-B",
            "-c",
            _PYTEST_LAUNCHER,
            str(tools),
            "-c",
            os.devnull,
            "--rootdir",
            str(tools),
            "--confcutdir",
            str(tools / "tests"),
            "--import-mode=importlib",
            "-p",
            "no:cacheprovider",
            "-q",
            str(tools / "tests" / "adapters"),
            str(tools / "tests" / "integration"),
        )
    raise IntegrationError(f"Unsupported integration action kind: {kind!r}.")


def _run_action(
    root: Path,
    action: ActionSpec,
    environment: Mapping[str, str],
) -> Finding:
    check = f"transaction-action:{action.kind.value}"
    if action.kind is ActionKind.DEPENDENCIES:
        return _run_dependency_action(root, action, environment)
    command = _command(action.kind, root)
    try:
        completed = run_bounded(
            command,
            cwd=root,
            env=dict(environment),
            check=False,
            capture_output=True,
            text=True,
            timeout=action.timeout_seconds,
            shell=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return Finding(
            check,
            FindingStatus.FAIL,
            f"Staged {action.kind.value} action timed out after "
            f"{action.timeout_seconds} seconds.",
            adapter="transaction-actions",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        detail = _safe_detail(exc, root)
        return Finding(
            check,
            FindingStatus.FAIL,
            f"Staged {action.kind.value} action could not run: {detail}",
            adapter="transaction-actions",
        )

    if completed.returncode == 0:
        return Finding(
            check,
            FindingStatus.PASS,
            f"Staged {action.kind.value} action passed.",
            adapter="transaction-actions",
        )
    output = "\n".join(
        part for part in (completed.stdout or "", completed.stderr or "") if part
    )
    detail = _safe_detail(output, root)
    return Finding(
        check,
        FindingStatus.FAIL,
        f"Staged {action.kind.value} action failed with exit code "
        f"{completed.returncode}: {detail}",
        adapter="transaction-actions",
    )


def _run_dependency_action(
    root: Path,
    action: ActionSpec,
    environment: Mapping[str, str],
) -> Finding:
    check = f"transaction-action:{action.kind.value}"
    if not action.paths:
        return _dependency_failure(
            check,
            "no dependency manifest trigger path was declared",
        )
    npm = shutil.which("npm", path=environment.get("PATH"))
    if npm is None:
        return _dependency_failure(check, "npm is unavailable")

    manifests: list[Path] = []
    for relative in action.paths:
        candidate = Path(relative)
        if candidate.name.casefold() != "package.json":
            return _dependency_failure(
                check,
                f"no deterministic offline installer is defined for {relative}",
            )
        try:
            manifest = safe_join(root, relative, require_exists=True)
            lock = safe_join(
                root,
                (candidate.parent / "package-lock.json").as_posix(),
                require_exists=True,
            )
        except FilesystemSafetyError:
            return _dependency_failure(
                check,
                f"a safe package-lock.json is required for {relative}",
            )
        if manifest.is_symlink() or lock.is_symlink():
            return _dependency_failure(
                check,
                f"dependency manifests must not be symbolic links at {relative}",
            )
        try:
            read_regular_bytes(
                manifest,
                root=root,
                label=f"Dependency manifest {relative}",
            )
            read_regular_bytes(
                lock,
                root=root,
                label=f"Dependency lockfile for {relative}",
            )
        except FilesystemSafetyError:
            return _dependency_failure(
                check,
                f"dependency manifest and lockfile must be regular files at {relative}",
            )
        manifests.append(manifest)

    for manifest in manifests:
        command = (
            npm,
            "ci",
            "--ignore-scripts",
            "--offline",
            "--no-audit",
            "--no-fund",
        )
        try:
            completed = run_bounded(
                command,
                cwd=manifest.parent,
                env=dict(environment),
                check=False,
                capture_output=True,
                text=True,
                timeout=action.timeout_seconds,
                shell=False,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return _dependency_failure(
                check,
                f"offline npm ci timed out after {action.timeout_seconds} seconds",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return _dependency_failure(
                check,
                f"offline npm ci could not run: {_safe_detail(exc, root)}",
            )
        if completed.returncode != 0:
            output = "\n".join(
                part
                for part in (completed.stdout or "", completed.stderr or "")
                if part
            )
            return _dependency_failure(
                check,
                "offline npm ci failed with exit code "
                f"{completed.returncode}: {_safe_detail(output, root)}",
            )
    return Finding(
        check,
        FindingStatus.PASS,
        "Staged locked-dependency validation passed for "
        f"{len(manifests)} npm project(s).",
        adapter="transaction-actions",
    )


def _dependency_failure(check: str, detail: str) -> Finding:
    return Finding(
        check,
        FindingStatus.FAIL,
        "Staged locked-dependency validation was refused or failed: "
        f"{detail}. Network and lifecycle scripts remain disabled.",
        adapter="transaction-actions",
    )


def _safe_detail(value: object, root: Path) -> str:
    sanitized = sanitize_text(value, root).strip()
    if not sanitized:
        return "no diagnostic output"
    if len(sanitized) > _OUTPUT_LIMIT:
        sanitized = sanitized[-_OUTPUT_LIMIT:]
    return " | ".join(line.strip() for line in sanitized.splitlines() if line.strip())


def _action_environment(temporary: Path, root: Path) -> dict[str, str]:
    home = temporary / "home"
    cache = temporary / "cache"
    config = temporary / "config"
    data = temporary / "data"
    pycache = temporary / "pycache"
    for directory in (home, cache, config, data, pycache):
        directory.mkdir(mode=0o700)

    environment = safe_platform_environment(os.environ)
    environment.update(
        {
            "PATH": _sanitized_search_path(os.environ.get("PATH"), root),
            "HOME": str(home),
            "USERPROFILE": str(home),
            "TMP": str(temporary),
            "TEMP": str(temporary),
            "TMPDIR": str(temporary),
            "XDG_CACHE_HOME": str(cache),
            "XDG_CONFIG_HOME": str(config),
            "XDG_DATA_HOME": str(data),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPYCACHEPREFIX": str(pycache),
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INDEX": "1",
            "PIP_NO_INPUT": "1",
            "NPM_CONFIG_USERCONFIG": os.devnull,
            "NPM_CONFIG_CACHE": str(cache / "npm"),
            "NPM_CONFIG_IGNORE_SCRIPTS": "true",
            "NPM_CONFIG_OFFLINE": "true",
            "npm_config_ignore_scripts": "true",
            "npm_config_offline": "true",
            "CARGO_HOME": str(cache / "cargo"),
            "CARGO_NET_OFFLINE": "true",
            "UV_NO_CONFIG": "1",
            "UV_OFFLINE": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "CI": "1",
            "NO_COLOR": "1",
            "TERM": "dumb",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "TEMPLATE_TOOLING_NESTED_TEST": "1",
        }
    )
    return environment


def _sanitized_search_path(value: str | None, root: Path) -> str:
    candidates = (value or os.defpath).split(os.pathsep)
    accepted: list[str] = []
    for candidate in candidates:
        path = Path(candidate)
        if not candidate or not path.is_absolute():
            continue
        try:
            resolved = path.resolve(strict=False)
        except OSError:
            continue
        if resolved == root or resolved.is_relative_to(root):
            continue
        rendered = str(resolved)
        if rendered not in accepted:
            accepted.append(rendered)
    return os.pathsep.join(accepted) if accepted else os.defpath
