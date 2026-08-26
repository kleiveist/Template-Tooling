from __future__ import annotations

from pathlib import Path

import pytest

from tools import control
from tools.template_lifecycle.manifest import load_manifest
from tools.template_lifecycle.state import load_state

EXPECTED_FEATURES = {
    "web-only": ("frontend",),
    "web-cloud": ("frontend", "backend", "cloud"),
    "desktop-local": ("frontend", "tauri"),
    "desktop-cloud": ("frontend", "backend", "tauri", "cloud"),
    "full-platform": ("frontend", "backend", "tauri", "cloud"),
}
TEMPLATE_ROOT = Path(__file__).resolve().parents[3]
REQUIRES_TEMPLATE_GIT = pytest.mark.skipif(
    not (TEMPLATE_ROOT / ".git").exists() or (TEMPLATE_ROOT / ".template" / "state.toml").is_file(),
    reason="Cross-profile generation requires the master template Git checkout",
)


@REQUIRES_TEMPLATE_GIT
@pytest.mark.parametrize("profile", tuple(EXPECTED_FEATURES))
def test_every_profile_gets_valid_deterministic_lifecycle_metadata(profile: str, tmp_path: Path) -> None:
    first = tmp_path / f"{profile}-first"
    second = tmp_path / f"{profile}-second"
    arguments = [
        "init",
        "--profile",
        profile,
        "--name",
        "Lifecycle Product",
        "--slug",
        "lifecycle-product",
        "--identifier",
        "com.example.lifecycleproduct",
    ]

    assert control.main([*arguments, "--target-dir", str(first)]) == 0
    assert control.main([*arguments, "--target-dir", str(second)]) == 0

    first_state = load_state(first)
    second_state = load_state(second)
    first_manifest = load_manifest(first / first_state.baseline.manifest)
    second_manifest = load_manifest(second / second_state.baseline.manifest)
    assert first_state.selection.profile == profile
    assert first_state.selection.resolved_features == EXPECTED_FEATURES[profile]
    assert first_state.identity.name == "Lifecycle Product"
    assert first_state.identity.slug == "lifecycle-product"
    assert first_state.identity.identifier == "com.example.lifecycleproduct"
    assert first_state.identity.binary == "lifecycle-product"
    assert first_state.source.commit == second_state.source.commit
    assert first_manifest == second_manifest
    assert first_state.baseline.digest == first_manifest.digest
    assert "LICENSE" in first_manifest.by_path()
    assert ".template/state.toml" not in first_manifest.by_path()


@REQUIRES_TEMPLATE_GIT
def test_postgres_capability_is_recorded_with_resolved_dependencies(tmp_path: Path) -> None:
    target = tmp_path / "postgres-product"

    assert (
        control.main(
            [
                "init",
                "--profile",
                "web-cloud",
                "--with",
                "postgres",
                "--target-dir",
                str(target),
            ]
        )
        == 0
    )

    state = load_state(target)
    assert state.selection.optional_features == ("postgres",)
    assert state.selection.resolved_features == (
        "frontend",
        "backend",
        "cloud",
        "database",
        "postgres",
    )


def test_init_dry_run_never_creates_lifecycle_files(tmp_path: Path) -> None:
    target = tmp_path / "dry-run-product"

    assert control.main(["init", "--profile", "web-only", "--target-dir", str(target), "--dry-run"]) == 0

    assert not target.exists()
