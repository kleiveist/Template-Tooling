from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from tools.profiles.loader import load_project_profile
from tools.template_lifecycle.apply import (
    ApplyRequest,
    apply_adoption_metadata,
    apply_update,
    copy_product_tree,
    require_clean_product_repository,
)
from tools.template_lifecycle.manifest import (
    create_manifest,
    inspect_relative,
    load_manifest,
    safe_relative_path,
)
from tools.template_lifecycle.migrations import REGISTRY, Migration, run_migrations
from tools.template_lifecycle.model import (
    STATE_SCHEMA_VERSION,
    TEMPLATE_ID,
    BaselineManifest,
    BaselineState,
    LifecycleError,
    LifecycleState,
    PlanOperation,
    ProductIdentity,
    SelectionState,
    SourceState,
    UpdatePlan,
    VerificationResult,
)
from tools.template_lifecycle.planner import PlanRequest, create_update_plan
from tools.template_lifecycle.report import (
    create_report_directory,
    finalize_report,
    plan_to_dict,
    verification_to_dict,
    write_report,
)
from tools.template_lifecycle.scaffold import (
    ScaffoldRequest,
    read_product_version,
    reconstruct_scaffold,
    request_from_state,
)
from tools.template_lifecycle.source import (
    LocalTemplateSource,
    ResolvedTemplateRef,
    assert_ancestor,
    resolve_ref,
    resolve_source,
)
from tools.template_lifecycle.state import (
    BASELINE_RELATIVE_PATH,
    STATE_RELATIVE_PATH,
    load_state,
)
from tools.template_lifecycle.verify import (
    drift_counts,
    identity_issues,
    product_version,
    verify_lifecycle_metadata,
    verify_project,
)

SLUG = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9-]*){2,}")


@dataclass(frozen=True, slots=True)
class CommonOptions:
    executor_root: Path
    target_dir: Path | None
    source_dir: Path | None
    report_dir: str | None


@dataclass(frozen=True, slots=True)
class AdoptionOptions:
    profile: str
    optional_features: tuple[str, ...]
    name: str
    slug: str
    identifier: str
    binary: str


@dataclass(frozen=True, slots=True)
class CommandOutput:
    payload: dict[str, object]
    lines: tuple[str, ...]
    exit_code: int = 0


@dataclass(frozen=True, slots=True)
class PlanBundle:
    state: LifecycleState
    source: LocalTemplateSource
    target: ResolvedTemplateRef
    plan: UpdatePlan
    incoming_manifest: BaselineManifest
    migrations: tuple[Migration, ...]
    incoming_selection: SelectionState


@dataclass(frozen=True, slots=True)
class MigrationPreviewRequest:
    target_root: Path
    scratch: Path
    migrations: tuple[Migration, ...]
    baseline_manifest: BaselineManifest
    incoming_manifest: BaselineManifest
    applied_migrations: tuple[str, ...]
    move_collisions: tuple[tuple[str, str], ...]


def status(options: CommonOptions, *, to_ref: str | None) -> CommandOutput:
    target = _target(options)
    state_path = target / STATE_RELATIVE_PATH
    if not state_path.exists() and not state_path.is_symlink():
        return _unmanaged_status(options, target, to_ref)
    state, manifest = _load_managed_metadata(target)
    modified, missing, owned = drift_counts(target, manifest)
    identity_warnings = identity_issues(target, state.identity)
    payload: dict[str, object] = {
        "schema_version": 1,
        "repository_kind": state.repository_kind,
        "managed": True,
        "template_id": state.template_id,
        "installed_template_version": state.source.version,
        "installed_template_commit": state.source.commit,
        "provenance": state.provenance,
        "source_reproducible": not state.source_dirty,
        "profile": state.selection.profile,
        "optional_features": list(state.selection.optional_features),
        "resolved_features": list(state.selection.resolved_features),
        "product_version": product_version(target),
        "baseline_manifest": "valid",
        "drift": {"modified": modified, "missing": missing, "product_owned": owned},
        "identity_warnings": list(identity_warnings),
    }
    lines = _status_lines(payload)
    if options.source_dir is not None or to_ref is not None:
        source = _source(options)
        resolved = resolve_ref(source, to_ref or source.head_commit)
        payload.update(
            {
                "target_version": resolved.version,
                "target_commit": resolved.commit,
                "update_available": resolved.commit != state.source.commit,
            }
        )
        lines = (
            *lines,
            f"Target version: {resolved.version}",
            f"Target commit: {resolved.commit}",
            f"Update available: {_yes_no(resolved.commit != state.source.commit)}",
        )
    return CommandOutput(payload, lines)


def verify(options: CommonOptions) -> CommandOutput:
    target = _target(options)
    result = verify_project(target, registry=REGISTRY)
    payload: dict[str, object] = verification_to_dict(result)
    lines = tuple(f"{finding.status:<4} {finding.check}: {finding.message}" for finding in result.findings)
    if options.report_dir:
        directory = create_report_directory(target, options.report_dir)
        write_report(
            directory,
            plan=None,
            target_root=target,
            verification=result,
            outcome="VERIFIED" if result.ok else "FAILED",
        )
    return CommandOutput(payload, lines, 0 if result.ok else 1)


def audit(options: CommonOptions, *, to_ref: str, adoption: AdoptionOptions) -> CommandOutput:
    target = _target(options)
    source = _source(options)
    resolved = resolve_ref(source, to_ref)
    request = _request(adoption, target)
    with tempfile.TemporaryDirectory(prefix="template-lifecycle-audit-") as temporary:
        incoming = Path(temporary) / "incoming"
        reconstruct_scaffold(source, resolved, request, incoming)
        incoming_manifest = create_manifest(incoming)
        operations = _audit_operations(target, incoming, incoming_manifest)
    plan = UpdatePlan(resolved.commit, resolved.commit, resolved.version, operations)
    local_manifest = create_manifest(target)
    incoming_paths = set(incoming_manifest.by_path())
    product_owned = sum(entry.path not in incoming_paths for entry in local_manifest.files)
    payload = {
        "schema_version": 1,
        "repository_kind": "legacy" if not (target / STATE_RELATIVE_PATH).exists() else "product",
        "target_commit": resolved.commit,
        "target_version": resolved.version,
        "product_owned": product_owned,
        "plan": plan_to_dict(plan),
    }
    if options.report_dir:
        directory = create_report_directory(target, options.report_dir)
        write_report(directory, plan=plan, target_root=target, verification=None, outcome="AUDIT")
    counts = _operation_counts(plan)
    lines = (
        f"Target template version: {resolved.version}",
        f"Target template commit: {resolved.commit}",
        f"Matching template files: {counts.get('PRESERVE', 0)}",
        f"Missing template files: {counts.get('ADD', 0)}",
        f"Potential conflicts: {counts.get('CONFLICT', 0)}",
        f"Product-owned files: {product_owned}",
        "Audit is read-only and does not replace a product migration.",
    )
    return CommandOutput(payload, lines)


def adopt(
    options: CommonOptions,
    *,
    baseline_ref: str,
    adoption: AdoptionOptions,
    apply: bool,
) -> CommandOutput:
    target = _target(options)
    source = _source(options)
    baseline = resolve_ref(source, baseline_ref)
    request = _request(adoption, target)
    with tempfile.TemporaryDirectory(prefix="template-lifecycle-adopt-") as temporary:
        scaffold = Path(temporary) / "baseline"
        reconstruct_scaffold(source, baseline, request, scaffold)
        manifest = create_manifest(scaffold)
        selection = _selection(scaffold)
    state = _state_for_adoption(source, baseline, request.identity, selection, manifest.digest)
    issues = identity_issues(target, request.identity)
    if issues:
        raise LifecycleError("Stored identity would not match the product: " + "; ".join(issues))
    verification = None
    if apply:
        verification = apply_adoption_metadata(target, state, manifest, verifier=verify_lifecycle_metadata)
    payload: dict[str, object] = {
        "schema_version": 1,
        "applied": apply,
        "provenance": "adopted",
        "baseline_commit": baseline.commit,
        "baseline_version": baseline.version,
        "manifest_digest": manifest.digest,
        "written_paths": [STATE_RELATIVE_PATH, BASELINE_RELATIVE_PATH] if apply else [],
    }
    if options.report_dir:
        directory = create_report_directory(target, options.report_dir)
        write_report(
            directory,
            plan=None,
            target_root=target,
            verification=verification,
            outcome="ADOPTED" if apply else "PREVIEW",
        )
    lines = (
        f"Baseline template version: {baseline.version}",
        f"Baseline template commit: {baseline.commit}",
        f"Manifest digest: {manifest.digest}",
        "Adoption changes only lifecycle metadata." if apply else "Adoption preview: no files were written.",
        "Adoption does not replace a product-specific migration.",
    )
    return CommandOutput(payload, lines)


def plan(options: CommonOptions, *, to_ref: str) -> CommandOutput:
    target = _target(options)
    bundle = _build_plan(options, target, to_ref)
    if options.report_dir:
        directory = create_report_directory(target, options.report_dir)
        write_report(
            directory,
            plan=bundle.plan,
            target_root=target,
            verification=None,
            outcome="CONFLICT" if bundle.plan.conflicts else "PLANNED",
        )
    return _plan_output(bundle.plan)


def update(
    options: CommonOptions,
    *,
    to_ref: str,
    apply: bool,
    allow_architecture_change: bool,
) -> CommandOutput:
    target_root = _target(options)
    expected_head = require_clean_product_repository(target_root) if apply else None
    bundle = _build_plan(options, target_root, to_ref)
    if not apply:
        report_directory = create_report_directory(target_root, options.report_dir)
        write_report(
            report_directory,
            plan=bundle.plan,
            target_root=target_root,
            verification=None,
            outcome="CONFLICT" if bundle.plan.conflicts else "PREVIEW",
        )
        return _plan_output(bundle.plan)
    if bundle.state.source_dirty:
        report_directory = create_report_directory(target_root, options.report_dir)
        _report_and_raise(
            report_directory,
            bundle.plan,
            target_root,
            "BLOCKED",
            "Automatic update is blocked for a dirty working-tree baseline; re-adopt a clean commit.",
        )
    if bundle.plan.conflicts:
        report_directory = create_report_directory(target_root, options.report_dir)
        _report_and_raise(
            report_directory,
            bundle.plan,
            target_root,
            "CONFLICT",
            "Update plan contains conflicts; no product files were changed.",
        )
    if bundle.plan.architecture_change and not allow_architecture_change:
        report_directory = create_report_directory(target_root, options.report_dir)
        _report_and_raise(
            report_directory,
            bundle.plan,
            target_root,
            "BLOCKED",
            "Architecture change requires an applicable migration and --allow-architecture-change.",
        )
    repeated = resolve_ref(bundle.source, to_ref)
    if repeated.commit != bundle.target.commit:
        raise LifecycleError(
            "Target ref moved between planning and apply; rerun the command against the new resolved SHA."
        )
    report_directory = create_report_directory(target_root, options.report_dir)
    return _apply_bundle(
        bundle,
        target_root,
        report_directory,
        expected_head,
    )


def _apply_bundle(
    bundle: PlanBundle,
    target_root: Path,
    report_directory: Path,
    expected_head: str | None,
) -> CommandOutput:
    write_report(
        report_directory,
        plan=bundle.plan,
        target_root=target_root,
        verification=None,
        outcome="APPLYING",
    )
    finalized = False

    def finish_report(verification: VerificationResult, outcome: str, notices: tuple[str, ...]) -> None:
        nonlocal finalized
        finalize_report(
            report_directory,
            plan=bundle.plan,
            target_root=target_root,
            verification=verification,
            outcome=outcome,
            notices=notices,
        )
        finalized = True

    try:
        applied = apply_update(
            ApplyRequest(
                project_root=target_root,
                plan=bundle.plan,
                new_state=_updated_state(bundle),
                new_manifest=bundle.incoming_manifest,
                migrations=bundle.migrations,
                report_directory=report_directory,
                expected_head=expected_head,
                verifier=lambda root: verify_project(root, registry=REGISTRY),
                report_finalizer=finish_report,
            )
        )
    except LifecycleError:
        if not finalized:
            finalize_report(
                report_directory,
                plan=bundle.plan,
                target_root=target_root,
                verification=None,
                outcome="FAILED",
            )
        raise
    output = _plan_output(bundle.plan)
    payload = {
        **output.payload,
        "applied": True,
        "verification": verification_to_dict(applied.verification),
    }
    return CommandOutput(payload, (*output.lines, "Update applied and verified."))


def _report_and_raise(
    directory: Path,
    plan: UpdatePlan,
    target_root: Path,
    outcome: str,
    message: str,
) -> None:
    write_report(
        directory,
        plan=plan,
        target_root=target_root,
        verification=None,
        outcome=outcome,
    )
    raise LifecycleError(message)


def _build_plan(options: CommonOptions, target_root: Path, to_ref: str) -> PlanBundle:
    state, manifest = _load_managed_metadata(target_root)
    _require_managed_preflight(target_root)
    source = _source(options)
    if source.origin != state.source.url:
        raise LifecycleError("Stored template source does not match the canonical local source; explicitly re-adopt.")
    baseline = resolve_ref(source, state.source.commit)
    if baseline.version != state.source.version:
        raise LifecycleError(
            "Stored template version does not match VERSION at the baseline commit; explicitly re-adopt."
        )
    target = resolve_ref(source, to_ref)
    assert_ancestor(source, baseline.commit, target.commit)
    request = request_from_state(state, target_root)
    migrations = REGISTRY.select(
        source_version=state.source.version,
        source_commit=state.source.commit,
        target_version=target.version,
        target_commit=target.commit,
        applied=state.baseline.applied_migrations,
    )
    move_collisions = _migration_move_collisions(target_root, migrations)
    with tempfile.TemporaryDirectory(prefix="template-lifecycle-plan-") as temporary:
        scratch = Path(temporary)
        base_root = reconstruct_scaffold(source, baseline, request, scratch / "base")
        incoming_root = reconstruct_scaffold(source, target, request, scratch / "incoming")
        incoming_selection = _selection(incoming_root)
        architecture_change = incoming_selection != state.selection
        local_root = _migration_preview_root(
            MigrationPreviewRequest(
                target_root,
                scratch,
                migrations,
                manifest,
                create_manifest(incoming_root),
                state.baseline.applied_migrations,
                move_collisions,
            )
        )
        plan_result, incoming_manifest = create_update_plan(
            PlanRequest(
                base_root=base_root,
                local_root=local_root,
                incoming_root=incoming_root,
                baseline_manifest=manifest,
                baseline_commit=baseline.commit,
                target_commit=target.commit,
                target_version=target.version,
                migrations=tuple(migration.migration_id for migration in migrations),
                architecture_change=architecture_change,
                moves=_migration_moves(migrations),
            )
        )
    plan_result = _add_move_collision_conflicts(plan_result, move_collisions)
    if architecture_change and not any(migration.architecture_change for migration in migrations):
        conflict = PlanOperation(
            action="CONFLICT",
            path="project-profile.toml",
            reason="resolved profile meaning changed without an explicit architecture migration",
        )
        plan_result = replace(plan_result, operations=(*plan_result.operations, conflict))
    return PlanBundle(
        state,
        source,
        target,
        plan_result,
        incoming_manifest,
        migrations,
        incoming_selection,
    )


def _load_managed_metadata(
    target_root: Path,
) -> tuple[LifecycleState, BaselineManifest]:
    state = load_state(target_root)
    manifest = load_manifest(target_root / state.baseline.manifest)
    if manifest.digest != state.baseline.digest:
        raise LifecycleError(
            "Lifecycle state digest does not match the baseline manifest; run template verify and re-adopt."
        )
    return state, manifest


def _require_managed_preflight(target_root: Path) -> None:
    result = verify_project(target_root, registry=REGISTRY)
    failures = tuple(finding.message for finding in result.findings if finding.status == "FAIL")
    if failures:
        raise LifecycleError("Managed project verification failed before planning: " + "; ".join(failures))


def _migration_preview_root(request: MigrationPreviewRequest) -> Path:
    if not request.migrations or request.move_collisions:
        return request.target_root
    local_root = request.scratch / "local-after-migrations"
    copy_product_tree(request.target_root, local_root)
    owned_paths = tuple(sorted(set(request.baseline_manifest.by_path()) | set(request.incoming_manifest.by_path())))
    run_migrations(
        local_root,
        request.migrations,
        owned_paths=owned_paths,
        already_applied=request.applied_migrations,
    )
    return local_root


def _migration_move_collisions(root: Path, migrations: tuple[Migration, ...]) -> tuple[tuple[str, str], ...]:
    collisions: list[tuple[str, str]] = []
    for source, destination in _migration_moves(migrations):
        source_path = root / Path(safe_relative_path(source))
        destination_path = root / Path(safe_relative_path(destination))
        source_exists = source_path.exists() or source_path.is_symlink()
        destination_exists = destination_path.exists() or destination_path.is_symlink()
        if source_exists and destination_exists:
            collisions.append((source, destination))
    return tuple(sorted(collisions, key=lambda pair: (pair[1], pair[0])))


def _add_move_collision_conflicts(plan: UpdatePlan, collisions: tuple[tuple[str, str], ...]) -> UpdatePlan:
    existing = {operation.path for operation in plan.conflicts}
    additional = tuple(
        PlanOperation(
            action="CONFLICT",
            path=destination,
            source_path=source,
            reason="migration move destination collides with an existing product path",
        )
        for source, destination in collisions
        if destination not in existing
    )
    if not additional:
        return plan
    state_updates = tuple(operation for operation in plan.operations if operation.action == "STATE_UPDATE")
    file_operations = tuple(operation for operation in plan.operations if operation.action != "STATE_UPDATE")
    ordered = tuple(sorted((*file_operations, *additional), key=lambda operation: (operation.path, operation.action)))
    return replace(plan, operations=(*ordered, *state_updates))


def _updated_state(bundle: PlanBundle) -> LifecycleState:
    applied = tuple(dict.fromkeys((*bundle.state.baseline.applied_migrations, *bundle.plan.migrations)))
    return replace(
        bundle.state,
        source_dirty=False,
        source=SourceState(
            url=bundle.source.origin,
            version=bundle.target.version,
            ref=bundle.target.commit,
            commit=bundle.target.commit,
            tree_digest=bundle.incoming_manifest.digest,
        ),
        selection=bundle.incoming_selection,
        baseline=BaselineState(BASELINE_RELATIVE_PATH, bundle.incoming_manifest.digest, applied),
    )


def _migration_moves(
    migrations: tuple[Migration, ...],
) -> tuple[tuple[str, str], ...]:
    moves: list[tuple[str, str]] = []
    for migration in migrations:
        for operation in migration.operations:
            if operation.kind != "move_path":
                continue
            if operation.source is None or operation.destination is None:
                raise LifecycleError(f"Migration {migration.migration_id} has an incomplete move_path operation.")
            moves.append((operation.source, operation.destination))
    return tuple(moves)


def _unmanaged_status(options: CommonOptions, target: Path, to_ref: str | None) -> CommandOutput:
    try:
        source = resolve_source(target)
    except LifecycleError:
        payload: dict[str, object] = {
            "schema_version": 1,
            "repository_kind": "legacy",
            "managed": False,
        }
        return CommandOutput(
            payload,
            (
                "Repository kind: legacy/unmanaged",
                "Lifecycle state: missing",
                "Next step: run template audit, then adopt a trusted baseline.",
            ),
        )
    payload = {
        "schema_version": 1,
        "repository_kind": "template",
        "managed": False,
        "template_id": source.template_id,
        "template_version": source.version,
        "template_commit": source.head_commit,
        "source_dirty": source.dirty,
    }
    lines = (
        "Repository kind: template",
        f"Template ID: {source.template_id}",
        f"Template version: {source.version}",
        f"Template commit: {source.head_commit}",
        f"Source dirty: {_yes_no(source.dirty)}",
    )
    if to_ref:
        resolved = resolve_ref(_source(options), to_ref)
        payload.update({"target_version": resolved.version, "target_commit": resolved.commit})
        lines = (
            *lines,
            f"Target version: {resolved.version}",
            f"Target commit: {resolved.commit}",
        )
    return CommandOutput(payload, lines)


def _source(options: CommonOptions) -> LocalTemplateSource:
    if options.source_dir is not None:
        return resolve_source(options.source_dir)
    try:
        return resolve_source(options.executor_root)
    except LifecycleError as exc:
        raise LifecycleError("A canonical local template checkout is required; provide --source-dir PATH.") from exc


def _target(options: CommonOptions) -> Path:
    target = (options.target_dir or options.executor_root).expanduser().resolve()
    if not target.is_dir():
        raise LifecycleError(f"Target project directory does not exist: {target}.")
    return target


def _request(options: AdoptionOptions, target: Path) -> ScaffoldRequest:
    if not SLUG.fullmatch(options.slug) or not SLUG.fullmatch(options.binary):
        raise LifecycleError("Product slug and binary must use lowercase kebab-case.")
    if not options.name.strip() or not IDENTIFIER.fullmatch(options.identifier):
        raise LifecycleError("Product name or reverse-domain identifier is invalid.")
    return ScaffoldRequest(
        profile=options.profile,
        optional_features=options.optional_features,
        identity=ProductIdentity(options.name.strip(), options.slug, options.identifier, options.binary),
        product_version=read_product_version(target),
    )


def _selection(scaffold: Path) -> SelectionState:
    profile = load_project_profile(scaffold / "project-profile.toml")
    return SelectionState(profile.profile_id, profile.optional_features, profile.features)


def _state_for_adoption(
    source: LocalTemplateSource,
    baseline: ResolvedTemplateRef,
    identity: ProductIdentity,
    selection: SelectionState,
    digest: str,
) -> LifecycleState:
    return LifecycleState(
        STATE_SCHEMA_VERSION,
        "product",
        TEMPLATE_ID,
        "adopted",
        False,
        SourceState(source.origin, baseline.version, baseline.commit, baseline.commit, digest),
        selection,
        identity,
        BaselineState(BASELINE_RELATIVE_PATH, digest, ()),
    )


def _audit_operations(target: Path, incoming: Path, manifest: object) -> tuple[PlanOperation, ...]:
    operations: list[PlanOperation] = []
    for expected in manifest.files:
        actual = inspect_relative(target, expected.path)
        if actual is None:
            operations.append(PlanOperation("ADD", expected.path, "template component is missing"))
        elif actual == expected:
            operations.append(
                PlanOperation(
                    "PRESERVE",
                    expected.path,
                    "product and target scaffold are identical",
                )
            )
        else:
            operations.append(
                PlanOperation(
                    "CONFLICT",
                    expected.path,
                    "existing product path differs from the target scaffold",
                    local_sha256=actual.sha256,
                    incoming_sha256=expected.sha256,
                )
            )
    return tuple(operations)


def _plan_output(plan: UpdatePlan) -> CommandOutput:
    counts = _operation_counts(plan)
    payload: dict[str, object] = {
        "schema_version": 1,
        "plan": plan_to_dict(plan),
        "applied": False,
    }
    lines = (
        f"Baseline commit: {plan.baseline_commit}",
        f"Target version: {plan.target_version}",
        f"Target commit: {plan.target_commit}",
        f"Operations: {sum(counts.values())}",
        f"Conflicts: {len(plan.conflicts)}",
        f"Architecture change: {_yes_no(plan.architecture_change)}",
    )
    for operation in plan.operations:
        lines = (
            *lines,
            f"{operation.action:<12} {operation.path} — {operation.reason}",
        )
    return CommandOutput(payload, lines, 1 if plan.conflicts else 0)


def _operation_counts(plan: UpdatePlan) -> dict[str, int]:
    counts: dict[str, int] = {}
    for operation in plan.operations:
        counts[operation.action] = counts.get(operation.action, 0) + 1
    return counts


def _status_lines(payload: dict[str, object]) -> tuple[str, ...]:
    drift = payload["drift"]
    return (
        f"Repository kind: {payload['repository_kind']}",
        f"Template ID: {payload['template_id']}",
        f"Installed template version: {payload['installed_template_version']}",
        f"Installed template commit: {payload['installed_template_commit']}",
        f"Provenance: {payload['provenance']}",
        f"Source reproducible: {_yes_no(bool(payload['source_reproducible']))}",
        f"Profile: {payload['profile']}",
        f"Capabilities: {', '.join(payload['optional_features']) or 'none'}",
        f"Product version: {payload['product_version']}",
        "Baseline manifest: valid",
        f"Local template drift: {drift['modified']} modified, {drift['missing']} missing, {drift['product_owned']} product-owned",
        *(f"WARNING identity: {warning}" for warning in payload["identity_warnings"]),
    )


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
