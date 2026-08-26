from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tools.template_lifecycle.manifest import create_manifest, write_manifest
from tools.template_lifecycle.model import (
    STATE_SCHEMA_VERSION,
    TEMPLATE_ID,
    TEMPLATE_URL,
    BaselineState,
    LifecycleState,
    ProductIdentity,
    SelectionState,
    SourceState,
)
from tools.template_lifecycle.state import (
    BASELINE_RELATIVE_PATH,
    validate_lifecycle_directory,
    write_state,
)
from tools.template_lifecycle.verify import (
    product_version,
    verify_lifecycle_metadata,
    verify_project,
)

COMMIT = "d" * 40
IDENTITY = ProductIdentity(
    name="Customer App",
    slug="customer-app",
    identifier="com.customer.app",
    binary="customer-binary",
)
RESOLVED_FEATURES = (
    "frontend",
    "backend",
    "tauri",
    "cloud",
    "database",
    "postgres",
)
FEATURES_TOML = """schema_version = 1

[core]
paths = ["VERSION"]

[features.frontend]
name = "Frontend"
description = "Frontend"
paths = []

[features.backend]
name = "Backend"
description = "Backend"
paths = []

[features.tauri]
name = "Tauri"
description = "Tauri"
paths = []
requires = ["frontend"]

[features.cloud]
name = "Cloud"
description = "Cloud"
paths = []
requires = ["backend"]

[features.database]
name = "Database"
description = "Database"
paths = []
requires = ["backend"]
optional = true

[features.postgres]
name = "PostgreSQL"
description = "PostgreSQL"
paths = []
requires = ["database"]
optional = true
selectable = true
"""
PROFILE_TOML = """schema_version = 1
id = "desktop-cloud"
name = "Desktop cloud"
description = "Fixture profile"
features = ["frontend", "backend", "tauri", "cloud"]
"""


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_profiles(root: Path) -> None:
    profiles = root / "profiles"
    profiles.mkdir()
    (profiles / "features.toml").write_text(FEATURES_TOML, encoding="utf-8")
    (profiles / "desktop-cloud.toml").write_text(PROFILE_TOML, encoding="utf-8")
    _write_project_profile(root, postgres=True)


def _write_project_profile(root: Path, *, postgres: bool) -> None:
    optional = '["postgres"]' if postgres else "[]"
    features = RESOLVED_FEATURES if postgres else RESOLVED_FEATURES[:4]
    rendered_features = ", ".join(json.dumps(item) for item in features)
    (root / "project-profile.toml").write_text(
        "\n".join(
            (
                "schema_version = 1",
                'id = "desktop-cloud"',
                'name = "Desktop cloud"',
                'description = "Fixture profile"',
                f"optional_features = {optional}",
                f"features = [{rendered_features}]",
                "",
            )
        ),
        encoding="utf-8",
    )


def _write_product_metadata(root: Path, version: str) -> None:
    (root / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    _write_json(
        root / "frontend/package.json",
        {"name": "customer-app-frontend", "version": version},
    )
    _write_json(
        root / "frontend/package-lock.json",
        {
            "name": "customer-app-frontend",
            "version": version,
            "packages": {"": {"name": "customer-app-frontend", "version": version}},
        },
    )
    (root / "frontend/src").mkdir(parents=True)
    (root / "frontend/index.html").write_text("<title>Customer App</title>\n", encoding="utf-8")
    (root / "frontend/src/main.ts").write_text('document.title = "Customer App";\n', encoding="utf-8")
    (root / "backend/app/api").mkdir(parents=True)
    (root / "backend/app/api/health.py").write_text('SERVICE = "customer-app-backend"\n', encoding="utf-8")
    (root / "tools/inst").mkdir(parents=True)
    (root / "tools/inst/build.py").write_text('ARTIFACT = "customer-app-web.zip"\n', encoding="utf-8")
    (root / "deployment").mkdir()
    (root / "deployment/compose.yaml").write_text("name: customer-app\n", encoding="utf-8")
    _write_tauri_metadata(root, version)


def _write_tauri_metadata(root: Path, version: str) -> None:
    _write_json(
        root / "src-tauri/tauri.conf.json",
        {
            "productName": IDENTITY.name,
            "identifier": IDENTITY.identifier,
            "mainBinaryName": IDENTITY.binary,
            "version": version,
            "app": {"windows": [{"label": "main", "title": IDENTITY.name}]},
        },
    )
    (root / "src-tauri/Cargo.toml").write_text(
        f'[package]\nname = "{IDENTITY.binary}"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "src-tauri/Cargo.lock").write_text(
        f'version = 3\n\n[[package]]\nname = "{IDENTITY.binary}"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "src-tauri/app-icon.svg").write_text("<svg><title>Customer App</title></svg>\n", encoding="utf-8")


def _managed_product(
    tmp_path: Path,
    *,
    dirty: bool = False,
    version: str = "0.7.0",
) -> tuple[Path, LifecycleState]:
    root = tmp_path / "managed product"
    root.mkdir()
    _write_profiles(root)
    _write_product_metadata(root, version)
    manifest = create_manifest(root)
    state = LifecycleState(
        schema_version=STATE_SCHEMA_VERSION,
        repository_kind="product",
        template_id=TEMPLATE_ID,
        provenance="working-tree" if dirty else "generated",
        source_dirty=dirty,
        source=SourceState(
            url=TEMPLATE_URL,
            version="1.1.0",
            ref=COMMIT,
            commit=COMMIT,
            tree_digest=manifest.digest,
        ),
        selection=SelectionState(
            profile="desktop-cloud",
            optional_features=("postgres",),
            resolved_features=RESOLVED_FEATURES,
        ),
        identity=IDENTITY,
        baseline=BaselineState(
            manifest=BASELINE_RELATIVE_PATH,
            digest=manifest.digest,
            applied_migrations=(),
        ),
    )
    lifecycle_dir = validate_lifecycle_directory(root)
    write_manifest(lifecycle_dir / "baseline.json", manifest)
    write_state(root, state)
    return root, state


def _finding_statuses(result) -> dict[str, str]:
    return {finding.check: finding.status for finding in result.findings}


def test_verify_accepts_matching_profile_capabilities_identity_and_versions(
    tmp_path: Path,
) -> None:
    root, _state = _managed_product(tmp_path)

    result = verify_project(root)
    statuses = _finding_statuses(result)

    assert result.ok
    assert statuses == {
        "lifecycle-paths": "PASS",
        "state": "PASS",
        "manifest": "PASS",
        "source-reproducibility": "PASS",
        "profile": "PASS",
        "identity": "PASS",
        "product-version": "PASS",
        "migrations": "PASS",
        "baseline-files": "PASS",
    }


def test_verify_reports_profile_capability_identity_and_version_drift(
    tmp_path: Path,
) -> None:
    root, _state = _managed_product(tmp_path)
    _write_project_profile(root, postgres=False)
    tauri_path = root / "src-tauri/tauri.conf.json"
    tauri = json.loads(tauri_path.read_text(encoding="utf-8"))
    tauri["identifier"] = "com.example.tampered"
    tauri["app"]["windows"][0]["title"] = "Wrong window title"
    _write_json(tauri_path, tauri)
    package_path = root / "frontend/package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["version"] = "9.9.9"
    _write_json(package_path, package)

    result = verify_project(root)
    statuses = _finding_statuses(result)

    assert result.ok is False
    assert statuses["profile"] == "FAIL"
    assert statuses["identity"] == "FAIL"
    assert statuses["product-version"] == "FAIL"
    identity = next(finding for finding in result.findings if finding.check == "identity")
    assert "main window title" in identity.message


def test_verify_warns_for_dirty_baseline_without_failing_local_verification(
    tmp_path: Path,
) -> None:
    root, _state = _managed_product(tmp_path, dirty=True)

    result = verify_project(root)
    source_finding = next(finding for finding in result.findings if finding.check == "source-reproducibility")

    assert result.ok
    assert source_finding.status == "WARN"
    assert "dirty template working tree" in source_finding.message


def test_verify_supports_full_semver_product_versions(tmp_path: Path) -> None:
    root, _state = _managed_product(tmp_path, version="0.7.0-rc.1+build.5")

    assert product_version(root) == "0.7.0-rc.1+build.5"
    assert _finding_statuses(verify_project(root))["product-version"] == "PASS"


@pytest.mark.parametrize("content", ("{not-json", "[]"))
def test_verify_reports_malformed_json_as_findings_without_traceback(
    tmp_path: Path,
    content: str,
) -> None:
    root, _state = _managed_product(tmp_path)
    (root / "frontend/package.json").write_text(content, encoding="utf-8")

    result = verify_project(root)
    statuses = _finding_statuses(result)

    assert result.ok is False
    assert statuses["identity"] == "FAIL"
    assert statuses["product-version"] == "FAIL"
    assert "Traceback" not in "\n".join(finding.message for finding in result.findings)


def test_verify_rejects_external_product_symlink_before_reading_it(
    tmp_path: Path,
) -> None:
    root, _state = _managed_product(tmp_path)
    package = root / "frontend/package.json"
    external = tmp_path / "external-secret.json"
    external.write_text('{"secret": "must-not-be-reported"}\n', encoding="utf-8")
    package.unlink()
    try:
        package.symlink_to(external)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")

    result = verify_project(root)
    statuses = _finding_statuses(result)
    messages = "\n".join(finding.message for finding in result.findings)

    assert result.ok is False
    assert statuses["product-paths"] == "FAIL"
    assert "outside its root" in messages
    assert "must-not-be-reported" not in messages
    assert "identity" not in statuses
    assert "product-version" not in statuses


def test_metadata_verification_ignores_legacy_product_drift(tmp_path: Path) -> None:
    root = tmp_path / "legacy product"
    root.mkdir()
    (root / "legacy.txt").write_text("product-owned\n", encoding="utf-8")
    manifest = create_manifest(root)
    state = LifecycleState(
        schema_version=STATE_SCHEMA_VERSION,
        repository_kind="product",
        template_id=TEMPLATE_ID,
        provenance="adopted",
        source_dirty=False,
        source=SourceState(
            url=TEMPLATE_URL,
            version="1.0.0",
            ref=COMMIT,
            commit=COMMIT,
            tree_digest=manifest.digest,
        ),
        selection=SelectionState("desktop-local", (), ("frontend", "tauri")),
        identity=IDENTITY,
        baseline=BaselineState(BASELINE_RELATIVE_PATH, manifest.digest, ()),
    )
    lifecycle_dir = validate_lifecycle_directory(root)
    write_manifest(lifecycle_dir / "baseline.json", manifest)
    write_state(root, state)

    metadata_result = verify_lifecycle_metadata(root)
    full_result = verify_project(root)

    assert metadata_result.ok
    assert _finding_statuses(metadata_result) == {
        "lifecycle-paths": "PASS",
        "state": "PASS",
        "manifest": "PASS",
    }
    assert full_result.ok is False
    assert _finding_statuses(full_result)["profile"] == "FAIL"
    assert _finding_statuses(full_result)["product-version"] == "FAIL"


def test_metadata_verification_rejects_state_manifest_digest_tampering(
    tmp_path: Path,
) -> None:
    root, state = _managed_product(tmp_path)
    tampered_digest = "sha256:" + "e" * 64
    write_state(
        root,
        replace(
            state,
            source=replace(state.source, tree_digest=tampered_digest),
            baseline=replace(state.baseline, digest=tampered_digest),
        ),
    )

    result = verify_lifecycle_metadata(root)

    assert result.ok is False
    assert _finding_statuses(result)["manifest"] == "FAIL"


def test_metadata_verification_rejects_external_lifecycle_symlink(
    tmp_path: Path,
) -> None:
    root, _state = _managed_product(tmp_path)
    baseline = root / ".template/baseline.json"
    external = tmp_path / "external-baseline.json"
    baseline.replace(external)
    try:
        baseline.symlink_to(external)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")

    result = verify_lifecycle_metadata(root)

    assert result.ok is False
    assert _finding_statuses(result) == {"lifecycle-paths": "FAIL"}
