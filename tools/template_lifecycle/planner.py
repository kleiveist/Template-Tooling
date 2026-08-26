from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from tools.template_lifecycle.manifest import (
    create_manifest,
    inspect_relative,
    safe_relative_path,
)
from tools.template_lifecycle.merge import (
    merge_text,
    read_path_payload,
    text_equivalent,
)
from tools.template_lifecycle.model import (
    BaselineManifest,
    LifecycleError,
    ManifestEntry,
    PlanOperation,
    UpdatePlan,
)


@dataclass(frozen=True, slots=True)
class PlanRequest:
    base_root: Path
    local_root: Path
    incoming_root: Path
    baseline_manifest: BaselineManifest
    baseline_commit: str
    target_commit: str
    target_version: str
    migrations: tuple[str, ...] = ()
    architecture_change: bool = False
    moves: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class _Roots:
    base: Path
    local: Path
    incoming: Path


@dataclass(frozen=True, slots=True)
class _PathContext:
    path: str
    roots: _Roots
    base: ManifestEntry | None
    local: ManifestEntry | None
    incoming: ManifestEntry | None


@dataclass(frozen=True, slots=True)
class _MoveContext:
    source: str
    destination: str
    roots: _Roots
    base: ManifestEntry
    local: ManifestEntry | None
    local_path: str
    incoming: ManifestEntry


def create_update_plan(request: PlanRequest) -> tuple[UpdatePlan, BaselineManifest]:
    roots = _Roots(request.base_root, request.local_root, request.incoming_root)
    incoming_manifest = create_manifest(request.incoming_root)
    owned = request.baseline_manifest.by_path()
    incoming = incoming_manifest.by_path()
    reconstructed = _reconstruct_owned_entries(request.base_root, owned)
    declared_moves = _expand_moves(request.moves, owned, incoming)
    operations = [
        _plan_move(_move_context(roots, source, destination, reconstructed, incoming))
        for source, destination in declared_moves
    ]
    consumed = {path for move in declared_moves for path in move}
    operations.extend(
        _plan_regular_paths(
            roots,
            sorted((set(owned) | set(incoming)) - consumed),
            reconstructed,
            incoming,
        )
    )
    operations.sort(key=lambda operation: (operation.path, operation.action))
    if request.baseline_commit != request.target_commit or request.migrations or request.architecture_change:
        operations.append(_state_update(incoming_manifest))
    return (
        UpdatePlan(
            baseline_commit=request.baseline_commit,
            target_commit=request.target_commit,
            target_version=request.target_version,
            operations=tuple(operations),
            migrations=request.migrations,
            architecture_change=request.architecture_change,
        ),
        incoming_manifest,
    )


def _move_context(
    roots: _Roots,
    source: str,
    destination: str,
    base: dict[str, ManifestEntry],
    incoming: dict[str, ManifestEntry],
) -> _MoveContext:
    source_local = inspect_relative(roots.local, source)
    return _MoveContext(
        source,
        destination,
        roots,
        base[source],
        source_local or inspect_relative(roots.local, destination),
        source if source_local else destination,
        incoming[destination],
    )


def _plan_regular_paths(
    roots: _Roots,
    paths: list[str],
    base: dict[str, ManifestEntry],
    incoming: dict[str, ManifestEntry],
) -> list[PlanOperation]:
    operations: list[PlanOperation] = []
    for relative in paths:
        operation = _plan_path(
            _PathContext(
                relative,
                roots,
                base.get(relative),
                inspect_relative(roots.local, relative),
                incoming.get(relative),
            )
        )
        if operation is not None:
            operations.append(operation)
    return operations


def _state_update(manifest: BaselineManifest) -> PlanOperation:
    return PlanOperation(
        action="STATE_UPDATE",
        path=".template/state.toml",
        reason="record the resolved target template commit and canonical incoming baseline",
        result_sha256=manifest.digest.removeprefix("sha256:"),
    )


def _plan_path(context: _PathContext) -> PlanOperation | None:
    if context.base is None:
        return _plan_template_addition(context)
    if context.incoming is None:
        return _plan_template_deletion(context)
    if _context_same(context, "base", "incoming"):
        return None
    if context.local is not None and _context_same(context, "local", "incoming"):
        return None
    if context.local is not None and _context_same(context, "local", "base"):
        return _template_update_operation(context)
    if context.local is None:
        return _conflict(context, "product deleted a file changed by the template", None)
    if any(entry.kind != "text" for entry in (context.base, context.local, context.incoming)):
        return _conflict(
            context,
            "both sides changed a binary or symbolic-link path",
            context.local,
        )
    return _plan_text_merge(context)


def _plan_text_merge(context: _PathContext) -> PlanOperation | None:
    assert context.base is not None
    assert context.local is not None
    assert context.incoming is not None
    merge = merge_text(
        read_path_payload(context.roots.base, context.path),
        read_path_payload(context.roots.local, context.path),
        read_path_payload(context.roots.incoming, context.path),
    )
    executable = _merged_executable(context.base, context.local, context.incoming)
    if merge.conflict:
        return _conflict(
            context,
            "both product and template modified overlapping lines",
            context.local,
            merge.content,
        )
    if hashlib.sha256(merge.content).hexdigest() == context.local.sha256 and executable == context.local.executable:
        return None
    return _result_operation(
        "MERGE",
        context,
        merge.content,
        "text",
        executable,
        "product and template changes merged without overlap",
    )


def _plan_move(context: _MoveContext) -> PlanOperation:
    destination_local = inspect_relative(context.roots.local, context.destination)
    if destination_local is not None and context.local_path != context.destination:
        return _conflict(
            context,
            "template move collides with an existing product path",
            destination_local,
        )
    if context.local is None:
        return _conflict(context, "product deleted the source of a template move", None)
    if _context_same(context, "local", "incoming"):
        return _move_existing(context, "move a managed path whose content already matches the template")
    if _context_same(context, "base", "incoming"):
        return _move_existing(context, "move a product-modified managed path")
    if _context_same(context, "local", "base"):
        content, kind, executable = _template_update_result(context)
        return _result_operation(
            "MOVE",
            context,
            content,
            kind,
            executable,
            "move a managed path and apply its template update",
        )
    if any(entry.kind != "text" for entry in (context.base, context.local, context.incoming)):
        return _conflict(
            context,
            "both sides changed a binary or symbolic-link path during a move",
            context.local,
        )
    return _plan_move_text_merge(context)


def _plan_move_text_merge(context: _MoveContext) -> PlanOperation:
    assert context.local is not None
    merge = merge_text(
        read_path_payload(context.roots.base, context.source),
        read_path_payload(context.roots.local, context.local_path),
        read_path_payload(context.roots.incoming, context.destination),
    )
    if merge.conflict:
        return _conflict(
            context,
            "product and template changed overlapping lines during a move",
            context.local,
            merge.content,
        )
    return _result_operation(
        "MOVE",
        context,
        merge.content,
        "text",
        _merged_executable(context.base, context.local, context.incoming),
        "move a managed path and merge product and template changes",
    )


def _plan_template_addition(context: _PathContext) -> PlanOperation | None:
    if context.incoming is None:
        return None
    if context.local is None:
        content = read_path_payload(context.roots.incoming, context.path)
        return _result_operation(
            "ADD",
            context,
            content,
            context.incoming.kind,
            context.incoming.executable,
            "template added file",
        )
    if _context_same(context, "local", "incoming"):
        return None
    return _conflict(
        context,
        "template addition collides with a product-owned file",
        context.local,
    )


def _plan_template_deletion(context: _PathContext) -> PlanOperation | None:
    assert context.base is not None
    if context.local is None:
        return None
    if _context_same(context, "local", "base"):
        return PlanOperation(
            action="DELETE",
            path=context.path,
            reason="template deleted an unchanged managed file",
            base_sha256=context.base.sha256,
            local_sha256=context.local.sha256,
            kind=context.base.kind,
        )
    return _conflict(
        context,
        "template deleted a product-modified managed file",
        context.local,
    )


def _template_update_operation(context: _PathContext) -> PlanOperation:
    assert context.base is not None
    assert context.local is not None
    assert context.incoming is not None
    move_context = _MoveContext(
        context.path,
        context.path,
        context.roots,
        context.base,
        context.local,
        context.path,
        context.incoming,
    )
    content, kind, executable = _template_update_result(move_context)
    return _result_operation("UPDATE", context, content, kind, executable, "template changed")


def _template_update_result(context: _MoveContext) -> tuple[bytes, str, bool]:
    assert context.local is not None
    incoming_payload = read_path_payload(context.roots.incoming, context.destination)
    if any(entry.kind != "text" for entry in (context.base, context.local, context.incoming)):
        return incoming_payload, context.incoming.kind, context.incoming.executable
    merge = merge_text(
        read_path_payload(context.roots.base, context.source),
        read_path_payload(context.roots.local, context.local_path),
        incoming_payload,
    )
    if merge.conflict:
        raise LifecycleError(f"Template-only change unexpectedly conflicted at {context.destination}.")
    return merge.content, context.incoming.kind, context.incoming.executable


def _result_operation(
    action: str,
    context: _PathContext | _MoveContext,
    content: bytes,
    kind: str,
    executable: bool,
    reason: str,
) -> PlanOperation:
    move = isinstance(context, _MoveContext)
    return PlanOperation(
        action=action,
        path=context.destination if move else context.path,
        source_path=context.source if move else None,
        reason=reason,
        base_sha256=context.base.sha256 if context.base else None,
        local_sha256=context.local.sha256 if context.local else None,
        incoming_sha256=context.incoming.sha256 if context.incoming else None,
        result_sha256=hashlib.sha256(content).hexdigest(),
        kind=kind,
        executable=executable,
        result=content,
    )


def _move_existing(context: _MoveContext, reason: str) -> PlanOperation:
    assert context.local is not None
    return _result_operation(
        "MOVE",
        context,
        read_path_payload(context.roots.local, context.local_path),
        context.local.kind,
        context.local.executable,
        reason,
    )


def _conflict(
    context: _PathContext | _MoveContext,
    reason: str,
    local: ManifestEntry | None,
    result: bytes | None = None,
) -> PlanOperation:
    move = isinstance(context, _MoveContext)
    entry = context.incoming or local or context.base
    return PlanOperation(
        action="CONFLICT",
        path=context.destination if move else context.path,
        source_path=context.source if move else None,
        reason=reason,
        base_sha256=context.base.sha256 if context.base else None,
        local_sha256=local.sha256 if local else None,
        incoming_sha256=context.incoming.sha256 if context.incoming else None,
        kind=entry.kind if entry else None,
        conflict_result=result,
    )


def _context_same(
    context: _PathContext | _MoveContext,
    left: str,
    right: str,
) -> bool:
    left_root, left_path, left_entry = _context_location(context, left)
    right_root, right_path, right_entry = _context_location(context, right)
    if left_entry.kind != right_entry.kind or left_entry.executable != right_entry.executable:
        return False
    if left_entry.sha256 == right_entry.sha256:
        return True
    return left_entry.kind == "text" and text_equivalent(
        read_path_payload(left_root, left_path),
        read_path_payload(right_root, right_path),
    )


def _context_location(
    context: _PathContext | _MoveContext,
    side: str,
) -> tuple[Path, str, ManifestEntry]:
    entry = getattr(context, side)
    if not isinstance(entry, ManifestEntry):
        raise LifecycleError(f"Lifecycle comparison lacks its {side} entry.")
    if isinstance(context, _PathContext):
        relative = context.path
    else:
        relative = {
            "base": context.source,
            "local": context.local_path,
            "incoming": context.destination,
        }[side]
    return getattr(context.roots, side), relative, entry


def _reconstruct_owned_entries(
    base_root: Path,
    owned: dict[str, ManifestEntry],
) -> dict[str, ManifestEntry]:
    reconstructed: dict[str, ManifestEntry] = {}
    for relative in sorted(owned):
        entry = inspect_relative(base_root, relative)
        if entry is None:
            raise LifecycleError(
                f"Stored baseline cannot be reconstructed at {relative}; re-adopt against a clean commit."
            )
        reconstructed[relative] = entry
    return reconstructed


def _expand_moves(
    moves: tuple[tuple[str, str], ...],
    owned: dict[str, ManifestEntry],
    incoming: dict[str, ManifestEntry],
) -> tuple[tuple[str, str], ...]:
    expanded: list[tuple[str, str]] = []
    consumed: set[str] = set()
    for move in moves:
        source, destination = _move_paths(move)
        pairs = _expand_move_alias(source, destination, owned, incoming)
        for pair in pairs:
            if pair[0] in consumed or pair[1] in consumed:
                raise LifecycleError(f"Lifecycle moves overlap at {pair[0]} or {pair[1]}.")
            consumed.update(pair)
            expanded.append(pair)
    return tuple(sorted(expanded, key=lambda pair: (pair[1], pair[0])))


def _move_paths(move: tuple[str, str]) -> tuple[str, str]:
    if not isinstance(move, tuple) or len(move) != 2 or not all(isinstance(path, str) for path in move):
        raise LifecycleError("Lifecycle moves must contain source/destination paths.")
    source, destination = (safe_relative_path(path) for path in move)
    if source == destination:
        raise LifecycleError(f"Lifecycle move has identical paths: {source}.")
    return source, destination


def _expand_move_alias(
    source: str,
    destination: str,
    owned: dict[str, ManifestEntry],
    incoming: dict[str, ManifestEntry],
) -> tuple[tuple[str, str], ...]:
    sources = (source,) if source in owned else _descendants(source, owned)
    if not sources:
        raise LifecycleError(f"Lifecycle move source is not baseline-owned: {source}.")
    if source not in owned:
        _validate_directory_move_boundaries(source, destination, owned, incoming)
    pairs = tuple((old, _mapped_path(source, destination, old)) for old in sources)
    for old, new in pairs:
        if new not in incoming:
            raise LifecycleError(f"Lifecycle move destination is absent from incoming: {new}.")
        if new in owned or old in incoming:
            raise LifecycleError(f"Lifecycle move does not describe a clean rename: {old} -> {new}.")
    return pairs


def _validate_directory_move_boundaries(
    source: str,
    destination: str,
    owned: dict[str, ManifestEntry],
    incoming: dict[str, ManifestEntry],
) -> None:
    if _descendants(destination, owned) or destination in owned:
        raise LifecycleError(f"Lifecycle directory move destination is already baseline-owned: {destination}.")
    if _descendants(source, incoming) or source in incoming:
        raise LifecycleError(f"Lifecycle directory move source remains in incoming: {source}.")


def _descendants(prefix: str, entries: dict[str, ManifestEntry]) -> tuple[str, ...]:
    marker = f"{prefix}/"
    return tuple(path for path in sorted(entries) if path.startswith(marker))


def _mapped_path(source: str, destination: str, path: str) -> str:
    return destination + path.removeprefix(source)


def _merged_executable(base: ManifestEntry, local: ManifestEntry, incoming: ManifestEntry) -> bool:
    if local.executable == base.executable:
        return incoming.executable
    if incoming.executable == base.executable or local.executable == incoming.executable:
        return local.executable
    raise LifecycleError("Executable mode changes could not be merged deterministically.")
