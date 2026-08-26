from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import tomllib

from tools.template_lifecycle.model import (
    STATE_SCHEMA_VERSION,
    TEMPLATE_ID,
    TEMPLATE_URL,
    BaselineState,
    LifecycleError,
    LifecycleState,
    ProductIdentity,
    SelectionState,
    SourceState,
)
from tools.template_lifecycle.state import (
    BASELINE_RELATIVE_PATH,
    load_state,
    render_state,
    state_digest,
    validate_lifecycle_directory,
    validate_state,
    write_state,
)

COMMIT = "a" * 40
DIGEST = "sha256:" + "b" * 64


def _state(*, dirty: bool = False) -> LifecycleState:
    return LifecycleState(
        schema_version=STATE_SCHEMA_VERSION,
        repository_kind="product",
        template_id=TEMPLATE_ID,
        provenance="working-tree" if dirty else "generated",
        source_dirty=dirty,
        source=SourceState(
            url=TEMPLATE_URL,
            version="1.2.3",
            ref=COMMIT,
            commit=COMMIT,
            tree_digest=DIGEST,
        ),
        selection=SelectionState(
            profile="desktop-cloud",
            optional_features=("postgres",),
            resolved_features=(
                "frontend",
                "backend",
                "tauri",
                "cloud",
                "database",
                "postgres",
            ),
        ),
        identity=ProductIdentity(
            name="Customer Ü App",
            slug="customer-app",
            identifier="com.customer.app",
            binary="customer-binary",
        ),
        baseline=BaselineState(
            manifest=BASELINE_RELATIVE_PATH,
            digest=DIGEST,
            applied_migrations=("first-migration",),
        ),
    )


def test_state_render_write_and_digest_are_deterministic(tmp_path: Path) -> None:
    product = tmp_path / "product with spaces"
    product.mkdir()
    state = _state()

    first_render = render_state(state)
    write_state(product, state)
    first_bytes = (product / ".template/state.toml").read_bytes()
    write_state(product, state)

    assert load_state(product) == state
    assert render_state(state) == first_render
    assert (product / ".template/state.toml").read_bytes() == first_bytes
    assert state_digest(state) == state_digest(state)
    assert state_digest(state).startswith("sha256:")
    assert str(tmp_path) not in first_render
    assert tomllib.loads(first_render)["identity"]["name"] == "Customer Ü App"


@pytest.mark.parametrize(
    "manifest_path",
    (
        "../baseline.json",
        "/tmp/baseline.json",
        "C:/outside/baseline.json",
        ".template\\baseline.json",
        "./.template/baseline.json",
    ),
)
def test_state_rejects_unsafe_manifest_paths(manifest_path: str) -> None:
    state = _state()
    tampered = replace(
        state,
        baseline=replace(state.baseline, manifest=manifest_path),
    )

    with pytest.raises(LifecycleError, match="unsafe|must be stored"):
        validate_state(tampered)


def test_state_rejects_schema_commit_digest_and_migration_tampering() -> None:
    state = _state()
    invalid_states = (
        replace(state, schema_version=STATE_SCHEMA_VERSION + 1),
        replace(state, template_id="example/other-template"),
        replace(state, source=replace(state.source, commit="A" * 40)),
        replace(state, source=replace(state.source, tree_digest="sha256:" + "c" * 64)),
        replace(
            state,
            baseline=replace(
                state.baseline,
                applied_migrations=("duplicate", "duplicate"),
            ),
        ),
        replace(state, source_dirty=True),
    )

    for tampered in invalid_states:
        with pytest.raises(LifecycleError):
            validate_state(tampered)


@pytest.mark.parametrize(
    "source_url",
    (
        "/home/developer/Template-Projekte",
        "https://token@github.com/kleiveist/Template-Projekte.git",
        "https://github.com/other/Template-Projekte.git",
        "git@github.com:kleiveist/Template-Projekte.git",
    ),
)
def test_state_rejects_noncanonical_or_credentialed_source_url(
    source_url: str,
) -> None:
    state = _state()

    with pytest.raises(LifecycleError, match="canonical credential-free"):
        validate_state(replace(state, source=replace(state.source, url=source_url)))


def test_load_state_rejects_tampered_tracked_toml(tmp_path: Path) -> None:
    product = tmp_path / "product"
    product.mkdir()
    write_state(product, _state())
    path = product / ".template/state.toml"
    content = path.read_text(encoding="utf-8")
    path.write_text(
        content.replace("schema_version = 1", "schema_version = 99", 1),
        encoding="utf-8",
    )

    with pytest.raises(LifecycleError, match="Unsupported lifecycle state schema"):
        load_state(product)


def test_load_state_never_follows_state_file_symlink(tmp_path: Path) -> None:
    product = tmp_path / "product"
    external = tmp_path / "external-state.toml"
    product.mkdir()
    write_state(product, _state())
    state_path = product / ".template/state.toml"
    state_path.replace(external)
    try:
        state_path.symlink_to(external)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")

    with pytest.raises(LifecycleError, match="regular file, not a symbolic link"):
        load_state(product)


def test_lifecycle_directory_rejects_external_symlink_and_non_directory(
    tmp_path: Path,
) -> None:
    product = tmp_path / "product"
    external = tmp_path / "external"
    product.mkdir()
    external.mkdir()
    lifecycle = product / ".template"
    try:
        lifecycle.symlink_to(external, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")

    with pytest.raises(LifecycleError, match="symbolic link"):
        validate_lifecycle_directory(product)
    with pytest.raises(LifecycleError, match="symbolic link"):
        write_state(product, _state())
    assert not (external / "state.toml").exists()

    lifecycle.unlink()
    lifecycle.write_text("not a directory\n", encoding="utf-8")
    with pytest.raises(LifecycleError, match="must be a directory"):
        validate_lifecycle_directory(product)
