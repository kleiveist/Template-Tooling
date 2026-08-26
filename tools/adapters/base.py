"""Typed, filesystem-safe contracts shared by all feature adapters.

Adapters observe and describe project state.  They never own a filesystem
write boundary: applying their operations means submitting one immutable plan
to the transaction boundary supplied by the integration service.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from tools.core.context import ProjectContext
from tools.core.filesystem import (
    FilesystemSafetyError,
    read_regular_bytes,
    safe_join,
    safe_relative_path,
)
from tools.integration.model import (
    Conflict,
    Finding,
    FindingStatus,
    IntegrationPlan,
    IntegrationResult,
    Operation,
    Ownership,
    VerificationResult,
)
from tools.integration.planner import (
    DesiredProfile,
    DesiredResource,
    ObservedResource,
    create_plan,
)

if TYPE_CHECKING:
    from tools.profiles.model import ProjectProfile


_IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")


class AdapterError(RuntimeError):
    """Base class for adapter contract and orchestration failures."""


class AdapterContractError(AdapterError):
    """Raised when an adapter returns unsafe or contradictory data."""


class AdapterApplyError(AdapterError):
    """Raised when operations cannot be submitted to a transaction boundary."""


class AdapterCapability(str, Enum):
    """Optional operational surfaces an adapter may explicitly implement."""

    INSTALL = "install"
    RUN = "run"
    STOP = "stop"
    TEST = "test"
    BUILD = "build"


@dataclass(frozen=True, slots=True)
class AdapterActionResult:
    """Typed result returned by an optional technology action."""

    adapter: str
    capability: AdapterCapability
    ok: bool
    message: str = ""


@runtime_checkable
class InstallCapability(Protocol):
    def install(self, context: ProjectContext) -> AdapterActionResult: ...


@runtime_checkable
class RunCapability(Protocol):
    def run(self, context: ProjectContext) -> AdapterActionResult: ...


@runtime_checkable
class StopCapability(Protocol):
    def stop(self, context: ProjectContext) -> AdapterActionResult: ...


@runtime_checkable
class TestCapability(Protocol):
    def test(self, context: ProjectContext) -> AdapterActionResult: ...


@runtime_checkable
class BuildCapability(Protocol):
    def build(self, context: ProjectContext) -> AdapterActionResult: ...


@dataclass(frozen=True, slots=True)
class AdapterDesiredState:
    """The profile identity and expanded feature set seen by adapters."""

    profile: str
    features: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        profile = self.profile.strip()
        if not profile:
            raise AdapterContractError("Adapter desired profile must not be empty.")
        features = _normalized_identifiers(self.features, label="feature")
        object.__setattr__(self, "profile", profile)
        object.__setattr__(self, "features", features)

    @classmethod
    def from_profile(
        cls,
        value: AdapterDesiredState | DesiredProfile | ProjectProfile,
    ) -> AdapterDesiredState:
        """Normalize integration and profile-layer desired-state objects."""

        if isinstance(value, cls):
            return value
        profile = getattr(value, "profile", None)
        if profile is None:
            profile = getattr(value, "profile_id", None)
        features = getattr(value, "features", None)
        if not isinstance(profile, str) or not isinstance(features, tuple):
            raise AdapterContractError(
                "Desired state must expose a profile id and a tuple of features."
            )
        return cls(profile=profile, features=features)


@dataclass(frozen=True, slots=True)
class PathRequirement:
    """One conservative path requirement contributed by an adapter.

    ``content`` is permitted only for tooling-owned files.  Omitting content
    makes an existing path a presence requirement, not permission to invent or
    replace its payload.  ``create_if_missing`` must be explicit even when
    content is present.
    """

    path: str
    ownership: Ownership
    kind: str = "directory"
    required: bool = True
    reason: str = "required by the selected adapter"
    content: bytes | str | None = field(default=None, repr=False)
    create_if_missing: bool = False
    marker: bool = False
    marker_json_keys: tuple[str, ...] = ()
    marker_script_commands: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        try:
            path = safe_relative_path(self.path)
        except FilesystemSafetyError as exc:
            raise AdapterContractError(str(exc)) from exc
        if not isinstance(self.ownership, Ownership):
            raise AdapterContractError(
                f"Adapter path ownership must be an Ownership value: {self.path}."
            )
        if self.kind not in {"file", "directory"}:
            raise AdapterContractError(
                f"Unsupported adapter path kind at {self.path}: {self.kind!r}."
            )
        content = (
            self.content.encode("utf-8")
            if isinstance(self.content, str)
            else self.content
        )
        if content is not None and not isinstance(content, bytes):
            raise AdapterContractError(
                f"Adapter content must be bytes or text at {self.path}."
            )
        if content is not None and self.kind != "file":
            raise AdapterContractError(
                f"Directory requirement cannot carry content: {self.path}."
            )
        if content is not None and self.ownership is not Ownership.TOOLING:
            raise AdapterContractError(
                f"Only tooling-owned requirements may carry full content: {self.path}."
            )
        if self.create_if_missing and self.ownership is Ownership.PROJECT:
            raise AdapterContractError(
                f"Project-owned paths cannot be auto-created: {self.path}."
            )
        if self.create_if_missing and self.kind == "file" and content is None:
            raise AdapterContractError(
                f"A created tooling file requires materialized content: {self.path}."
            )
        if not isinstance(self.marker, bool):
            raise AdapterContractError(
                f"Adapter marker flag must be boolean: {self.path}."
            )
        if not isinstance(self.marker_json_keys, tuple) or not isinstance(
            self.marker_script_commands, tuple
        ):
            raise AdapterContractError(
                f"Adapter JSON marker selectors must be tuples: {self.path}."
            )
        marker_json_keys = tuple(self.marker_json_keys)
        marker_script_commands = tuple(self.marker_script_commands)
        if any(
            not isinstance(selector, str)
            or not selector
            or any(not part for part in selector.split("."))
            for selector in marker_json_keys
        ) or any(
            not isinstance(command, str) or not command
            for command in marker_script_commands
        ):
            raise AdapterContractError(
                f"Adapter JSON marker selectors must be non-empty text: {self.path}."
            )
        if (marker_json_keys or marker_script_commands) and (
            not self.marker or self.kind != "file"
        ):
            raise AdapterContractError(
                f"JSON marker selectors require a marked file: {self.path}."
            )
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "marker_json_keys", marker_json_keys)
        object.__setattr__(self, "marker_script_commands", marker_script_commands)


@dataclass(frozen=True, slots=True)
class AdapterDetection:
    """Deterministic read-only observations and detection diagnostics."""

    adapter: str
    resources: tuple[ObservedResource, ...] = ()
    findings: tuple[Finding, ...] = ()
    detected: bool = False

    def __post_init__(self) -> None:
        resources = tuple(sorted(self.resources, key=lambda item: item.path))
        findings = tuple(sorted(self.findings, key=_finding_key))
        if len(resources) != len({item.path.casefold() for item in resources}):
            raise AdapterContractError(
                f"Adapter {self.adapter!r} detected the same path more than once."
            )
        object.__setattr__(self, "resources", resources)
        object.__setattr__(self, "findings", findings)
        if not isinstance(self.detected, bool):
            raise AdapterContractError("Adapter detected flag must be boolean.")


@runtime_checkable
class TransactionBoundary(Protocol):
    """The only gateway through which adapters may request filesystem changes."""

    def submit(
        self,
        context: ProjectContext,
        plan: IntegrationPlan,
        *,
        source: str,
    ) -> IntegrationResult: ...


@runtime_checkable
class Adapter(Protocol):
    """Minimum profile-adapter surface consumed by orchestration."""

    name: str
    feature_ids: tuple[str, ...]
    core: bool
    capabilities: frozenset[AdapterCapability]

    def detect(self, context: ProjectContext) -> AdapterDetection: ...

    def plan(
        self,
        context: ProjectContext,
        desired_state: AdapterDesiredState | DesiredProfile | ProjectProfile,
    ) -> IntegrationPlan: ...

    def apply(
        self,
        context: ProjectContext,
        operations: Iterable[Operation],
    ) -> IntegrationResult: ...

    def verify(self, context: ProjectContext) -> VerificationResult: ...


class BaseAdapter:
    """Safe default implementation for declarative path adapters."""

    name = "adapter"
    feature_ids: tuple[str, ...] = ()
    core = False
    capabilities: frozenset[AdapterCapability] = frozenset()

    def __init__(self, transaction_boundary: TransactionBoundary | None = None) -> None:
        self._transaction_boundary = transaction_boundary
        self._validate_identity()

    def supports(self, desired_state: AdapterDesiredState) -> bool:
        return self.core or bool(
            set(self.feature_ids).intersection(desired_state.features)
        )

    def requirements(self, context: ProjectContext) -> tuple[PathRequirement, ...]:
        """Return adapter-owned requirements without reading or writing files."""

        del context
        return ()

    def configuration_findings(self, context: ProjectContext) -> tuple[Finding, ...]:
        """Return configuration-only diagnostics before path observation."""

        del context
        return ()

    def detect(self, context: ProjectContext) -> AdapterDetection:
        findings = list(self.configuration_findings(context))
        resources: list[ObservedResource] = []
        requirements = self._requirements(context)
        for requirement in requirements:
            try:
                observation = _observe(context, requirement)
                resources.append(observation)
                if (
                    observation.exists
                    and not observation.is_symlink
                    and observation.kind != requirement.kind
                ):
                    findings.append(
                        Finding(
                            check="path-kind",
                            status=FindingStatus.FAIL,
                            message=f"adapter path must be a {requirement.kind}",
                            adapter=self.name,
                            path=requirement.path,
                        )
                    )
            except FilesystemSafetyError as exc:
                resources.append(
                    ObservedResource(
                        path=requirement.path,
                        ownership=requirement.ownership,
                        exists=True,
                        kind=requirement.kind,
                        is_symlink=True,
                    )
                )
                findings.append(
                    Finding(
                        check="path-safety",
                        status=FindingStatus.FAIL,
                        message=str(exc),
                        adapter=self.name,
                        path=requirement.path,
                    )
                )
        resource_by_path = {item.path: item for item in resources}
        markers = tuple(item for item in requirements if item.marker)
        detection_requirements = markers or requirements
        detected = any(
            _matches_detection_marker(
                context,
                requirement,
                resource_by_path[requirement.path],
            )
            for requirement in detection_requirements
        )
        return AdapterDetection(
            adapter=self.name,
            resources=tuple(resources),
            findings=tuple(findings),
            detected=detected,
        )

    def plan(
        self,
        context: ProjectContext,
        desired_state: AdapterDesiredState | DesiredProfile | ProjectProfile,
    ) -> IntegrationPlan:
        desired = AdapterDesiredState.from_profile(desired_state)
        if not self.supports(desired):
            return IntegrationPlan(
                profile=desired.profile,
                desired_features=desired.features,
            )

        detection = self.detect(context)
        observed = {item.path: item for item in detection.resources}
        desired_resources: list[DesiredResource] = []
        conflicts: list[Conflict] = []
        for requirement in self._requirements(context):
            actual = observed[requirement.path]
            if (
                not actual.exists
                and requirement.ownership is Ownership.TOOLING
                and not requirement.create_if_missing
            ):
                if requirement.required:
                    conflicts.append(
                        Conflict(
                            path=requirement.path,
                            ownership=Ownership.TOOLING,
                            reason=(
                                "required tooling path is missing and this adapter "
                                "has no materialized repair payload"
                            ),
                            code="adapter-resource-missing",
                        )
                    )
                continue
            desired_resources.append(_desired_requirement(requirement, actual))

        plan = create_plan(
            detection.resources,
            DesiredProfile(
                profile=desired.profile,
                resources=tuple(desired_resources),
                features=desired.features,
            ),
        )
        detection_conflicts = tuple(
            Conflict(
                path=finding.path or "project-tooling.toml",
                ownership=(
                    observed[finding.path].ownership
                    if finding.check in {"path-kind", "path-safety"}
                    and finding.path in observed
                    and observed[finding.path].ownership is not None
                    else Ownership.STRUCTURED
                ),
                reason=finding.message,
                code={
                    "path-safety": "adapter-path-safety",
                    "path-kind": "adapter-path-kind",
                }.get(finding.check, "adapter-configuration"),
            )
            for finding in detection.findings
            if finding.status is FindingStatus.FAIL
            and finding.check in {"configured-path", "path-kind", "path-safety"}
        )
        return IntegrationPlan(
            profile=plan.profile,
            desired_features=plan.desired_features,
            operations=plan.operations,
            conflicts=tuple(
                sorted(
                    (*plan.conflicts, *conflicts, *detection_conflicts),
                    key=lambda item: (item.path, item.code, item.reason),
                )
            ),
        )

    def apply(
        self,
        context: ProjectContext,
        operations: Iterable[Operation],
    ) -> IntegrationResult:
        """Submit operations atomically; never perform an adapter-local write."""

        if self._transaction_boundary is None:
            raise AdapterApplyError(
                f"Adapter {self.name!r} has no shared transaction boundary."
            )
        normalized = _normalized_operations(operations)
        if any(item.ownership is Ownership.PROJECT for item in normalized):
            raise AdapterApplyError(
                f"Adapter {self.name!r} refused a project-owned write."
            )
        plan = IntegrationPlan(
            profile=context.config.profile,
            desired_features=tuple(sorted(self.feature_ids)),
            operations=normalized,
        )
        return self._transaction_boundary.submit(context, plan, source=self.name)

    def verify(self, context: ProjectContext) -> VerificationResult:
        detection = self.detect(context)
        requirements = {item.path: item for item in self._requirements(context)}
        findings = list(detection.findings)
        unsafe_paths = {
            item.path
            for item in findings
            if item.check == "path-safety" and item.status is FindingStatus.FAIL
        }
        wrong_kind_paths = {
            item.path
            for item in findings
            if item.check == "path-kind" and item.status is FindingStatus.FAIL
        }
        for actual in detection.resources:
            requirement = requirements[actual.path]
            if actual.path in unsafe_paths:
                continue
            if not actual.exists:
                findings.append(
                    Finding(
                        check="required-path"
                        if requirement.required
                        else "optional-path",
                        status=(
                            FindingStatus.FAIL
                            if requirement.required
                            else FindingStatus.INFO
                        ),
                        message=(
                            "required adapter path is missing"
                            if requirement.required
                            else "optional adapter path is not present"
                        ),
                        adapter=self.name,
                        path=requirement.path,
                    )
                )
            elif actual.is_symlink:
                findings.append(
                    Finding(
                        check="path-safety",
                        status=FindingStatus.FAIL,
                        message="adapter path must not be a symbolic link",
                        adapter=self.name,
                        path=requirement.path,
                    )
                )
            elif actual.kind != requirement.kind:
                if actual.path not in wrong_kind_paths:
                    findings.append(
                        Finding(
                            check="path-kind",
                            status=FindingStatus.FAIL,
                            message=f"adapter path must be a {requirement.kind}",
                            adapter=self.name,
                            path=requirement.path,
                        )
                    )
            else:
                findings.append(
                    Finding(
                        check="required-path"
                        if requirement.required
                        else "optional-path",
                        status=FindingStatus.PASS,
                        message="adapter path is present and safe",
                        adapter=self.name,
                        path=requirement.path,
                    )
                )
        return VerificationResult(tuple(sorted(findings, key=_finding_key)))

    def _requirements(self, context: ProjectContext) -> tuple[PathRequirement, ...]:
        requirements = tuple(
            sorted(self.requirements(context), key=lambda item: item.path)
        )
        duplicate = _duplicate(item.path.casefold() for item in requirements)
        if duplicate is not None:
            raise AdapterContractError(
                f"Adapter {self.name!r} declares a path more than once: {duplicate}."
            )
        return requirements

    def _validate_identity(self) -> None:
        if not _IDENTIFIER.fullmatch(self.name):
            raise AdapterContractError(
                f"Adapter name must use lowercase kebab-case: {self.name!r}."
            )
        normalized = _normalized_identifiers(self.feature_ids, label="feature")
        if normalized != self.feature_ids:
            raise AdapterContractError(
                f"Adapter {self.name!r} feature ids must be unique and sorted."
            )
        if not isinstance(self.core, bool):
            raise AdapterContractError(
                f"Adapter {self.name!r} core flag must be boolean."
            )
        if not isinstance(self.capabilities, frozenset) or any(
            not isinstance(item, AdapterCapability) for item in self.capabilities
        ):
            raise AdapterContractError(
                f"Adapter {self.name!r} capabilities must be AdapterCapability values."
            )
        for capability in self.capabilities:
            implementation = getattr(type(self), capability.value, None)
            if not callable(implementation):
                raise AdapterContractError(
                    f"Adapter {self.name!r} declares {capability.value!r} without implementing it."
                )


def project_relative_path(context: ProjectContext, path: Path) -> str:
    """Return a configured context path as a canonical project-relative path."""

    try:
        relative = path.absolute().relative_to(context.project_root).as_posix()
        return safe_relative_path(relative)
    except (ValueError, FilesystemSafetyError) as exc:
        raise AdapterContractError(
            "Configured adapter path is not a safe project-relative path."
        ) from exc


def _observe(
    context: ProjectContext,
    requirement: PathRequirement,
) -> ObservedResource:
    target = safe_join(
        context.project_root,
        requirement.path,
        allow_final_symlink=True,
    )
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        return ObservedResource(
            path=requirement.path,
            ownership=requirement.ownership,
            exists=False,
            kind=requirement.kind,
        )
    except OSError as exc:
        raise FilesystemSafetyError(
            f"Could not inspect adapter path safely: {requirement.path}."
        ) from exc

    is_symlink = stat.S_ISLNK(metadata.st_mode)
    if stat.S_ISDIR(metadata.st_mode):
        kind = "directory"
    elif stat.S_ISREG(metadata.st_mode):
        kind = "file"
    else:
        raise FilesystemSafetyError(
            f"Adapter path is not a regular file or directory: {requirement.path}."
        )
    digest: str | None = None
    if kind == "file" and not is_symlink and stat.S_ISREG(metadata.st_mode):
        digest = hashlib.sha256(
            read_regular_bytes(
                target,
                root=context.project_root,
                label=f"Adapter path {requirement.path}",
            )
        ).hexdigest()
    return ObservedResource(
        path=requirement.path,
        ownership=requirement.ownership,
        exists=True,
        sha256=digest,
        kind=kind,
        is_symlink=is_symlink,
    )


def _desired_requirement(
    requirement: PathRequirement,
    observed: ObservedResource,
) -> DesiredResource:
    digest = None
    if (
        requirement.kind == "file"
        and requirement.content is None
        and observed.exists
        and not observed.is_symlink
    ):
        digest = observed.sha256
    return DesiredResource(
        path=requirement.path,
        ownership=requirement.ownership,
        content=requirement.content,
        sha256=digest,
        required=requirement.required,
        reason=requirement.reason,
        kind=requirement.kind,
    )


def _matches_detection_marker(
    context: ProjectContext,
    requirement: PathRequirement,
    observed: ObservedResource,
) -> bool:
    if not observed.exists or observed.is_symlink or observed.kind != requirement.kind:
        return False
    if not requirement.marker_json_keys and not requirement.marker_script_commands:
        return True
    try:
        target = safe_join(
            context.project_root,
            requirement.path,
            require_exists=True,
        )
        raw = read_regular_bytes(
            target,
            root=context.project_root,
            label=f"Adapter marker {requirement.path}",
        ).decode("utf-8")
        payload = json.loads(raw)
    except (FilesystemSafetyError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    if any(_json_has_path(payload, key) for key in requirement.marker_json_keys):
        return True
    scripts = payload.get("scripts")
    if not isinstance(scripts, dict):
        return False
    return any(
        isinstance(script, str)
        and _script_runs_any(script, requirement.marker_script_commands)
        for script in scripts.values()
    )


def _json_has_path(payload: dict[str, object], dotted: str) -> bool:
    current: object = payload
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def _script_runs_any(script: str, commands: tuple[str, ...]) -> bool:
    # A technology name buried in an arbitrary shell script is not evidence
    # that the script launches that technology (for example ``echo vite``).
    # Keep detection deliberately conservative and require the command to be
    # the first executable token.
    return any(
        re.search(
            rf"^\s*{re.escape(command)}(?:$|\s)",
            script,
        )
        is not None
        for command in commands
    )


def _normalized_operations(operations: Iterable[Operation]) -> tuple[Operation, ...]:
    items = tuple(operations)
    if any(not isinstance(item, Operation) for item in items):
        raise AdapterApplyError("Adapters may submit only typed Operation objects.")
    paths = [item.path.casefold() for item in items]
    if len(paths) != len(set(paths)):
        raise AdapterApplyError("Adapter operations must target unique paths.")
    return tuple(
        sorted(
            items, key=lambda item: (item.path, str(item.kind), item.source_path or "")
        )
    )


def _normalized_identifiers(values: Iterable[str], *, label: str) -> tuple[str, ...]:
    items = tuple(values)
    if any(
        not isinstance(item, str) or not _IDENTIFIER.fullmatch(item) for item in items
    ):
        raise AdapterContractError(
            f"Adapter {label} ids must use lowercase kebab-case."
        )
    return tuple(sorted(dict.fromkeys(items)))


def _duplicate(values: Iterable[str]) -> str | None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return None


def _finding_key(finding: Finding) -> tuple[str, str, str, str, str]:
    return (
        finding.adapter or "",
        finding.check,
        finding.path or "",
        finding.status.value,
        finding.message,
    )


__all__ = [
    "Adapter",
    "AdapterActionResult",
    "AdapterApplyError",
    "AdapterCapability",
    "AdapterContractError",
    "AdapterDesiredState",
    "AdapterDetection",
    "AdapterError",
    "BaseAdapter",
    "BuildCapability",
    "InstallCapability",
    "PathRequirement",
    "RunCapability",
    "StopCapability",
    "TestCapability",
    "TransactionBoundary",
    "project_relative_path",
]
