from __future__ import annotations

from dataclasses import dataclass, field

TEMPLATE_ID = "kleiveist/template-projekte"
TEMPLATE_URL = "https://github.com/kleiveist/Template-Projekte.git"
STATE_SCHEMA_VERSION = 1
MANIFEST_SCHEMA_VERSION = 1
PLAN_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1


class LifecycleError(RuntimeError):
    """A safe, expected lifecycle failure suitable for CLI output."""


class LifecycleUsageError(LifecycleError):
    """A lifecycle invocation error that maps to CLI exit code 2."""


@dataclass(frozen=True, slots=True)
class ProductIdentity:
    name: str
    slug: str
    identifier: str
    binary: str


@dataclass(frozen=True, slots=True)
class SourceState:
    url: str
    version: str
    ref: str
    commit: str
    tree_digest: str


@dataclass(frozen=True, slots=True)
class SelectionState:
    profile: str
    optional_features: tuple[str, ...]
    resolved_features: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BaselineState:
    manifest: str
    digest: str
    applied_migrations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LifecycleState:
    schema_version: int
    repository_kind: str
    template_id: str
    provenance: str
    source_dirty: bool
    source: SourceState
    selection: SelectionState
    identity: ProductIdentity
    baseline: BaselineState


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    path: str
    sha256: str
    size: int
    kind: str
    executable: bool = False


@dataclass(frozen=True, slots=True)
class BaselineManifest:
    schema_version: int
    files: tuple[ManifestEntry, ...]
    digest: str

    def by_path(self) -> dict[str, ManifestEntry]:
        return {entry.path: entry for entry in self.files}


@dataclass(frozen=True, slots=True)
class PlanOperation:
    action: str
    path: str
    reason: str
    source_path: str | None = None
    base_sha256: str | None = None
    local_sha256: str | None = None
    incoming_sha256: str | None = None
    result_sha256: str | None = None
    kind: str | None = None
    executable: bool | None = None
    result: bytes | None = field(default=None, repr=False, compare=False)
    conflict_result: bytes | None = field(default=None, repr=False, compare=False)

    @property
    def is_conflict(self) -> bool:
        return self.action == "CONFLICT"

    @property
    def changes_product(self) -> bool:
        return self.action in {"ADD", "UPDATE", "MERGE", "DELETE", "MOVE"}


@dataclass(frozen=True, slots=True)
class UpdatePlan:
    baseline_commit: str
    target_commit: str
    target_version: str
    operations: tuple[PlanOperation, ...]
    migrations: tuple[str, ...] = ()
    architecture_change: bool = False

    @property
    def conflicts(self) -> tuple[PlanOperation, ...]:
        return tuple(operation for operation in self.operations if operation.is_conflict)

    @property
    def changes(self) -> tuple[PlanOperation, ...]:
        return tuple(operation for operation in self.operations if operation.changes_product)

    @property
    def is_noop(self) -> bool:
        return not self.changes and self.baseline_commit == self.target_commit and not self.migrations


@dataclass(frozen=True, slots=True)
class VerificationFinding:
    check: str
    status: str
    message: str


@dataclass(frozen=True, slots=True)
class VerificationResult:
    findings: tuple[VerificationFinding, ...]

    @property
    def ok(self) -> bool:
        return all(finding.status != "FAIL" for finding in self.findings)
