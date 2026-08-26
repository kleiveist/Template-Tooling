from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tools import control
from tools.inst import stop
from tools.profiles import generator, loader, validator
from tools.profiles.model import ProjectProfile

ROOT = Path(__file__).resolve().parents[2]
PROFILES_DIR = ROOT / "profiles"
HAS_BACKEND_SOURCE = (ROOT / "backend" / "app" / "main.py").exists()
HAS_TAURI_SOURCE = (ROOT / "src-tauri" / "tauri.conf.json").exists()
HAS_DATABASE_SOURCE = (ROOT / "backend" / "app" / "db" / "engine.py").exists()
HAS_CLOUD_SOURCE = (ROOT / "deployment" / "compose.yaml").exists()


def test_all_declared_profiles_load() -> None:
    catalog = loader.load_catalog(PROFILES_DIR, validate_paths=False)

    assert list(sorted(catalog.profiles)) == [
        "desktop-cloud",
        "desktop-local",
        "full-platform",
        "web-cloud",
        "web-only",
    ]


@pytest.mark.skipif(
    not HAS_BACKEND_SOURCE or not HAS_TAURI_SOURCE or not HAS_DATABASE_SOURCE,
    reason="Complete feature sources exist only in the master repository",
)
def test_master_catalog_paths_exist() -> None:
    loader.load_catalog(PROFILES_DIR)


def test_unknown_profile_is_rejected() -> None:
    catalog = loader.load_catalog(PROFILES_DIR, validate_paths=False)

    with pytest.raises(validator.ProfileLookupError):
        loader.resolve_profile(catalog, "unknown-profile")


def test_init_command_rejects_unknown_profile_cleanly(monkeypatch) -> None:
    messages: list[str] = []
    monkeypatch.setattr("tools.profiles.cli.logger.fail", messages.append)

    assert control.main(["init", "--profile", "unknown-profile", "--dry-run"]) == 2

    assert len(messages) == 1
    assert "Unknown profile 'unknown-profile'" in messages[0]
    assert "Available profiles:" in messages[0]
    assert "Unhandled error" not in messages[0]


def test_invalid_feature_dependencies_are_detected(tmp_path) -> None:
    root = tmp_path / "repo"
    profiles_dir = root / "profiles"
    docs_dir = root / "docs"
    profiles_dir.mkdir(parents=True)
    docs_dir.mkdir()

    (profiles_dir / "features.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                "",
                "[core]",
                'paths = ["docs"]',
                "",
                "[features.frontend]",
                'name = "Frontend"',
                'description = "Frontend runtime"',
                "paths = []",
                "",
                "[features.tauri]",
                'name = "Tauri"',
                'description = "Desktop shell"',
                "paths = []",
                'requires = ["frontend"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (profiles_dir / "broken-profile.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                'id = "broken-profile"',
                'name = "Broken profile"',
                'description = "Missing required frontend feature"',
                'features = ["tauri"]',
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(validator.CatalogValidationError, match="requires feature 'frontend'"):
        loader.load_catalog(profiles_dir, validate_paths=False)


def test_each_profile_activates_expected_features() -> None:
    catalog = loader.load_catalog(PROFILES_DIR, validate_paths=False)

    expected = {
        "web-only": ("frontend",),
        "web-cloud": ("frontend", "backend", "cloud"),
        "desktop-local": ("frontend", "tauri"),
        "desktop-cloud": ("frontend", "backend", "tauri", "cloud"),
        "full-platform": ("frontend", "backend", "tauri", "cloud"),
    }

    for profile_id, features in expected.items():
        resolved = loader.resolve_profile(catalog, profile_id)
        assert resolved.features == features


def test_database_and_postgres_features_are_declarative_capabilities() -> None:
    catalog = loader.load_catalog(PROFILES_DIR, validate_paths=False)

    database = catalog.features["database"]
    postgres = catalog.features["postgres"]

    assert database.optional is True
    assert database.selectable is False
    assert database.requires == ("backend",)
    assert postgres.optional is True
    assert postgres.selectable is True
    assert postgres.requires == ("database",)


def test_postgres_resolves_transitive_database_dependency() -> None:
    catalog = loader.load_catalog(PROFILES_DIR, validate_paths=False)

    resolved = loader.resolve_profile(catalog, "web-cloud", optional_features=("postgres",))

    assert resolved.optional_features == ("postgres",)
    assert resolved.features == ("frontend", "backend", "cloud", "database", "postgres")


def test_postgres_rejects_profile_without_backend() -> None:
    catalog = loader.load_catalog(PROFILES_DIR, validate_paths=False)

    with pytest.raises(validator.CatalogValidationError, match="not enabled by the selected project profile"):
        loader.resolve_profile(catalog, "web-only", optional_features=("postgres",))


def test_unknown_optional_feature_is_rejected() -> None:
    catalog = loader.load_catalog(PROFILES_DIR, validate_paths=False)

    with pytest.raises(validator.CatalogValidationError, match="Unknown optional feature"):
        loader.resolve_profile(catalog, "web-cloud", optional_features=("unknown-db",))


def test_profile_configuration_can_be_extended_without_breaking_loading(tmp_path) -> None:
    root = tmp_path / "repo"
    profiles_dir = root / "profiles"
    docs_dir = root / "docs"
    profiles_dir.mkdir(parents=True)
    docs_dir.mkdir()

    (profiles_dir / "features.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                "",
                "[core]",
                'paths = ["docs"]',
                "",
                "[features.frontend]",
                'name = "Frontend"',
                'description = "Frontend runtime"',
                "paths = []",
                'future_flag = "safe-to-ignore"',
                "",
                "[features.monitoring]",
                'name = "Monitoring"',
                'description = "Optional monitoring integration"',
                "paths = []",
                'requires = ["frontend"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (profiles_dir / "base.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                'id = "base"',
                'name = "Base"',
                'description = "Existing frontend profile"',
                'features = ["frontend"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (profiles_dir / "extensible.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                'id = "extensible"',
                "order = 15",
                'name = "Extensible"',
                'description = "Allows future metadata"',
                'features = ["frontend", "monitoring"]',
                'notes = "ignored extension field"',
                "",
                "[metadata]",
                'owner = "template-team"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    catalog = loader.load_catalog(profiles_dir, validate_paths=False)
    base = loader.resolve_profile(catalog, "base")
    resolved = loader.resolve_profile(catalog, "extensible")

    assert base.features == ("frontend",)
    assert resolved.profile_id == "extensible"
    assert resolved.features == ("frontend", "monitoring")


@pytest.mark.parametrize(
    ("profile_id", "expected", "excluded"),
    [
        ("web-only", {"frontend"}, {"backend", "src-tauri"}),
        ("web-cloud", {"frontend", "backend"}, {"src-tauri"}),
        ("desktop-local", {"frontend", "src-tauri"}, {"backend"}),
        ("desktop-cloud", {"frontend", "backend", "src-tauri"}, set()),
        ("full-platform", {"frontend", "backend", "src-tauri"}, set()),
    ],
)
def test_scaffold_plan_uses_only_profile_feature_paths(
    tmp_path: Path,
    profile_id: str,
    expected: set[str],
    excluded: set[str],
) -> None:
    if ("backend" in expected and not HAS_BACKEND_SOURCE) or ("src-tauri" in expected and not HAS_TAURI_SOURCE):
        pytest.skip("Selected feature sources are not present in this derived project")

    catalog = loader.load_catalog(PROFILES_DIR, validate_paths=False)
    plan = generator.build_scaffold_plan(
        catalog,
        project_root=ROOT,
        target_dir=tmp_path / profile_id,
        profile_id=profile_id,
    )
    selected = {path.relative_to(ROOT).as_posix() for path in plan.paths}

    assert all(any(item == prefix or item.startswith(f"{prefix}/") for item in selected) for prefix in expected)
    assert all(not any(item == prefix or item.startswith(f"{prefix}/") for item in selected) for prefix in excluded)
    assert {"AGENTS.md", "README.md", "docs", "profiles", "shared", "tools"} <= selected


def test_catalog_rejects_paths_outside_repository(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    profiles_dir = root / "profiles"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "features.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                "",
                "[core]",
                'paths = ["../outside"]',
                "",
                "[features.frontend]",
                'name = "Frontend"',
                'description = "Frontend runtime"',
                "paths = []",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (profiles_dir / "safe-name.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                'id = "safe-name"',
                'name = "Unsafe path"',
                'description = "Attempts to escape the repository"',
                'features = ["frontend"]',
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(validator.CatalogValidationError, match="must not be empty or contain"):
        loader.load_catalog(profiles_dir, validate_paths=False)


def test_catalog_rejects_unknown_dependencies_and_cycles(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    profiles_dir = root / "profiles"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "features.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                "",
                "[core]",
                'paths = ["docs"]',
                "",
                "[features.alpha]",
                'name = "Alpha"',
                'description = "Alpha feature"',
                "paths = []",
                'requires = ["beta", "missing"]',
                "",
                "[features.beta]",
                'name = "Beta"',
                'description = "Beta feature"',
                "paths = []",
                'requires = ["alpha"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (profiles_dir / "cyclic.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                'id = "cyclic"',
                'name = "Cyclic"',
                'description = "Cyclic feature selection"',
                'features = ["alpha", "beta"]',
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(validator.CatalogValidationError) as exc_info:
        loader.load_catalog(profiles_dir, validate_paths=False)

    message = str(exc_info.value)
    assert "requires unknown feature 'missing'" in message
    assert "dependency cycle detected" in message


def test_generated_profile_serialization_escapes_metadata() -> None:
    profile = ProjectProfile(
        schema_version=1,
        profile_id="web-only",
        name='Web "quoted"',
        description="First line\nSecond line",
        features=("frontend",),
    )

    rendered = generator.render_project_profile(profile)
    parsed = tomllib.loads(rendered)

    assert parsed["name"] == 'Web "quoted"'
    assert parsed["description"] == "First line\nSecond line"


def test_scaffold_removes_master_only_readme_content(tmp_path: Path) -> None:
    target = tmp_path / "generated"
    target.mkdir()
    (target / "README.md").write_text(
        "# Product\n\n"
        "Visible before.\n\n"
        "<!-- MASTER-ONLY START -->\n"
        "[Master case study](case-study/README.md)\n"
        "<!-- MASTER-ONLY END -->\n\n"
        "Visible after.\n\n"
        "- [Conduct](CODE_OF_CONDUCT.md)\n"
        "- [Contributing](CONTRIBUTING.md)\n"
        "- [License](LICENSE)\n",
        encoding="utf-8",
    )

    generator._remove_master_only_readme_content(target)

    generated_readme = (target / "README.md").read_text(encoding="utf-8")
    assert "Visible before." in generated_readme
    assert "Visible after." in generated_readme
    assert "MASTER-ONLY" not in generated_readme
    assert "case-study/README.md" not in generated_readme
    assert "CODE_OF_CONDUCT.md" not in generated_readme
    assert "CONTRIBUTING.md" not in generated_readme
    assert "LICENSE" in generated_readme


def test_master_readme_case_study_links_are_removed_from_scaffold(tmp_path: Path) -> None:
    catalog = loader.load_catalog(PROFILES_DIR, validate_paths=False)
    target = tmp_path / "web-only"
    plan = generator.build_scaffold_plan(
        catalog,
        project_root=ROOT,
        target_dir=target,
        profile_id="web-only",
    )

    generator.scaffold_project(plan)

    generated_readme = (target / "README.md").read_text(encoding="utf-8")
    assert "case-study/" not in generated_readme
    assert "MASTER-ONLY" not in generated_readme


def test_scaffold_copies_the_pinned_rust_analyzer_runtime(tmp_path: Path) -> None:
    catalog = loader.load_catalog(PROFILES_DIR, validate_paths=False)
    target = tmp_path / "web-only"
    plan = generator.build_scaffold_plan(
        catalog,
        project_root=ROOT,
        target_dir=target,
        profile_id="web-only",
    )

    generator.scaffold_project(plan)

    analyzer = Path("tools/quality/rust_analyzer")
    artifact = analyzer / "dist/rust_quality_analyzer.wasm"
    provenance = analyzer / "provenance.json"
    assert (target / artifact).read_bytes() == (ROOT / artifact).read_bytes()
    assert (target / provenance).read_text(encoding="utf-8") == (ROOT / provenance).read_text(encoding="utf-8")
    requirements = (target / "tools/requirements.txt").read_text(encoding="utf-8")
    assert "wasmtime==47.0.1" in requirements.splitlines()
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert (target / ".gitattributes").read_text(encoding="utf-8") == attributes
    assert "/tools/quality/rust_analyzer/Cargo.lock text eol=lf" in attributes
    assert "/tools/quality/rust_analyzer/src/** text eol=lf" in attributes
    assert "/tools/quality/rust_analyzer/dist/*.wasm binary" in attributes
    assert generator._ignore_transient_content(
        "frontend",
        ["dist", "playwright-report", "src", "test-results"],
    ) == ["dist", "playwright-report", "test-results"]


def test_scaffold_copy_excludes_playwright_runtime_outputs(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "src").mkdir(parents=True)
    (source / "src/main.ts").write_text("export {};\n", encoding="utf-8")
    (source / "playwright-report").mkdir()
    (source / "playwright-report/index.html").write_text("transient\n", encoding="utf-8")
    (source / "test-results").mkdir()
    (source / "test-results/.last-run.json").write_text("{}\n", encoding="utf-8")

    destination = tmp_path / "destination"
    shutil.copytree(source, destination, ignore=generator._ignore_transient_content)

    assert (destination / "src/main.ts").is_file()
    assert not (destination / "playwright-report").exists()
    assert not (destination / "test-results").exists()


def test_missing_required_scaffold_artifact_fails_before_writing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    catalog = loader.load_catalog(PROFILES_DIR, validate_paths=False)
    target = tmp_path / "web-only"
    monkeypatch.setattr(
        generator,
        "REQUIRED_SCAFFOLD_ARTIFACTS",
        (Path("tools/quality/rust_analyzer/dist/missing.wasm"),),
    )

    with pytest.raises(generator.GenerationError, match="Required scaffold artifact is missing"):
        generator.build_scaffold_plan(
            catalog,
            project_root=ROOT,
            target_dir=target,
            profile_id="web-only",
        )

    assert not target.exists()


@pytest.mark.skipif(not HAS_TAURI_SOURCE, reason="Tauri source is absent in this derived project")
def test_init_command_scaffolds_selected_profile(tmp_path: Path) -> None:
    target = tmp_path / "desktop-local-project"

    assert (
        control.main(
            [
                "init",
                "--profile",
                "desktop-local",
                "--target-dir",
                str(target),
            ]
        )
        == 0
    )

    assert (target / "frontend").exists()
    assert (target / "src-tauri").exists()
    assert (target / "tools").exists()
    assert (target / "docs").exists()
    assert (target / "shared").exists()
    assert (target / "profiles").exists()
    assert (target / "AGENTS.md").is_file()
    assert not (target / "backend").exists()
    assert 'id = "desktop-local"' in (target / "project-profile.toml").read_text(encoding="utf-8")
    frontend_profile = (target / "frontend" / "src" / "project-profile.ts").read_text(encoding="utf-8")
    assert 'export const activeProfileId = "desktop-local";' in frontend_profile
    assert '"tauri"' in frontend_profile
    assert '"backend"' not in frontend_profile
    package = json.loads((target / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert package["scripts"]["tauri"] == "tauri"
    assert "@tauri-apps/cli" in package["devDependencies"]
    env_example = (target / ".env.example").read_text(encoding="utf-8")
    assert "APP_ENV=development" in env_example
    assert "FRONTEND_HOST=127.0.0.1" in env_example
    assert "BACKEND_HOST=" not in env_example
    assert "VITE_API_BASE_URL=" not in env_example
    assert "DATABASE_URL=" not in env_example

    active = loader.load_active_profile(target)
    assert active.profile_id == "desktop-local"
    assert active.features == ("frontend", "tauri")


def test_init_command_supports_interactive_profile_selection(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "web-only-project"
    monkeypatch.setattr("builtins.input", lambda _prompt="": "1")

    assert control.main(["init", "--target-dir", str(target)]) == 0
    assert 'id = "web-only"' in (target / "project-profile.toml").read_text(encoding="utf-8")


@pytest.mark.skipif(not HAS_BACKEND_SOURCE, reason="Backend source is absent in this derived project")
def test_init_command_dry_run_does_not_write_files(tmp_path: Path) -> None:
    target = tmp_path / "web-cloud-project"

    assert (
        control.main(
            [
                "init",
                "--profile",
                "web-cloud",
                "--target-dir",
                str(target),
                "--dry-run",
            ]
        )
        == 0
    )

    assert not target.exists()


def test_existing_control_commands_remain_registered() -> None:
    assert {"doctor", "install", "run", "stop", "test", "build"} <= set(control._handlers())


def test_stop_inspects_only_ports_enabled_by_profile(monkeypatch) -> None:
    inspected: list[set[int]] = []
    profile = ProjectProfile(
        schema_version=1,
        profile_id="web-only",
        name="Web only",
        description="Frontend only",
        features=("frontend",),
    )
    monkeypatch.setattr(stop.profile_runtime, "active_profile", lambda _root: profile)
    monkeypatch.setattr(stop, "_stop_tracked_processes", lambda: (set(), 0))
    monkeypatch.setattr(
        stop,
        "_stop_port_processes",
        lambda ports, _ignored: inspected.append(ports) or 0,
    )
    args = control._build_parser().parse_args(["stop"])

    assert stop.main(args) == 0
    assert inspected == [{5173}]


def test_web_only_scaffold_runs_profile_aware_commands(tmp_path: Path) -> None:
    target = tmp_path / "web-only-project"
    assert control.main(["init", "--profile", "web-only", "--target-dir", str(target)]) == 0

    active = loader.load_active_profile(target)
    assert active.profile_id == "web-only"
    assert active.features == ("frontend",)

    package = json.loads((target / "frontend" / "package.json").read_text(encoding="utf-8"))
    lock_text = (target / "frontend" / "package-lock.json").read_text(encoding="utf-8")
    assert "tauri" not in package["scripts"]
    assert "@tauri-apps/cli" not in package["devDependencies"]
    assert "@tauri-apps/cli" not in lock_text
    assert not (target / "backend" / "app" / "db").exists()
    assert not (target / "backend" / "alembic.ini").exists()
    env_example = (target / ".env.example").read_text(encoding="utf-8")
    assert "APP_ENV=development" in env_example
    assert "FRONTEND_HOST=127.0.0.1" in env_example
    assert "BACKEND_HOST=" not in env_example
    assert "VITE_API_BASE_URL=" not in env_example
    assert "DATABASE_URL=" not in env_example

    command = [sys.executable, str(target / "tools" / "control.py"), "build", "desktop", "--dry-run"]
    completed = subprocess.run(command, cwd=target, text=True, capture_output=True, check=False)
    output = completed.stdout + completed.stderr
    assert completed.returncode == 1
    assert "disabled by active profile 'web-only'" in output
    assert "Unhandled error" not in output

    db_command = [sys.executable, str(target / "tools" / "control.py"), "db", "doctor"]
    db_completed = subprocess.run(db_command, cwd=target, text=True, capture_output=True, check=False)
    db_output = db_completed.stdout + db_completed.stderr
    assert db_completed.returncode == 1
    assert "Database feature is not enabled for this project" in db_output
    assert "Traceback" not in db_output

    config_command = [sys.executable, str(target / "tools" / "control.py"), "config", "doctor"]
    config_completed = subprocess.run(config_command, cwd=target, text=True, capture_output=True, check=False)
    assert config_completed.returncode == 0
    assert "effective configuration is valid" in (config_completed.stdout + config_completed.stderr)


@pytest.mark.skipif(not HAS_BACKEND_SOURCE, reason="Backend source is absent in this derived project")
def test_web_cloud_without_database_omits_database_capability(tmp_path: Path) -> None:
    target = tmp_path / "web-cloud-project"

    assert control.main(["init", "--profile", "web-cloud", "--target-dir", str(target)]) == 0

    assert (target / "backend" / "app" / "main.py").exists()
    assert (target / "backend" / "requirements-production.lock").exists()
    assert (target / "deployment" / "docker" / "backend.Dockerfile").exists()
    assert not (target / "backend" / "app" / "db").exists()
    assert not (target / "backend" / "alembic.ini").exists()
    assert not (target / "backend" / "requirements-database.txt").exists()
    assert not (target / "backend" / "requirements-postgres.txt").exists()
    env_example = (target / ".env.example").read_text(encoding="utf-8")
    assert "BACKEND_HOST=127.0.0.1" in env_example
    assert "BACKEND_PORT=8000" in env_example
    assert "VITE_API_BASE_URL=http://127.0.0.1:8000" in env_example
    assert "DATABASE_URL=" not in env_example

    active = loader.load_active_profile(target)
    assert active.features == ("frontend", "backend", "cloud")
    assert active.optional_features == ()


@pytest.mark.skipif(
    not HAS_BACKEND_SOURCE or not HAS_TAURI_SOURCE,
    reason="Desktop-cloud feature sources are absent in this derived project",
)
def test_desktop_cloud_scaffold_includes_public_api_config_only(tmp_path: Path) -> None:
    target = tmp_path / "desktop-cloud-project"

    assert control.main(["init", "--profile", "desktop-cloud", "--target-dir", str(target)]) == 0

    env_example = (target / ".env.example").read_text(encoding="utf-8")
    assert "FRONTEND_HOST=127.0.0.1" in env_example
    assert "BACKEND_HOST=127.0.0.1" in env_example
    assert "VITE_API_BASE_URL=http://127.0.0.1:8000" in env_example
    assert "DATABASE_URL=" not in env_example


@pytest.mark.skipif(not HAS_DATABASE_SOURCE, reason="Database sources are absent in this derived project")
def test_web_cloud_with_postgres_scaffolds_database_capability(tmp_path: Path) -> None:
    target = tmp_path / "web-cloud-postgres"

    assert control.main(["init", "--profile", "web-cloud", "--with", "postgres", "--target-dir", str(target)]) == 0

    assert (target / "backend" / "app" / "db" / "base.py").exists()
    assert (target / "backend" / "alembic.ini").exists()
    assert (target / "backend" / "alembic" / "env.py").exists()
    assert (target / "backend" / "requirements-database.txt").exists()
    assert (target / "backend" / "requirements-postgres.txt").exists()
    assert (target / "backend" / "requirements-database-production.lock").exists()
    assert (target / "backend" / "requirements-postgres-production.lock").exists()
    assert not (target / "src-tauri").exists()

    manifest = tomllib.loads((target / "project-profile.toml").read_text(encoding="utf-8"))
    assert manifest["optional_features"] == ["postgres"]
    assert manifest["features"] == ["frontend", "backend", "cloud", "database", "postgres"]
    assert "postgresql+psycopg://app:change-me" in (target / ".env.example").read_text(encoding="utf-8")

    active = loader.load_active_profile(target)
    assert active.has_feature("database")
    assert active.has_feature("postgres")


@pytest.mark.parametrize("profile_id", ["web-only", "desktop-local"])
def test_init_with_postgres_rejects_profiles_without_backend_cleanly(
    monkeypatch,
    tmp_path: Path,
    profile_id: str,
) -> None:
    messages: list[str] = []
    monkeypatch.setattr("tools.profiles.cli.logger.fail", messages.append)

    code = control.main(
        ["init", "--profile", profile_id, "--with", "postgres", "--target-dir", str(tmp_path / "invalid")]
    )

    assert code == 1
    assert any("not enabled by the selected project profile" in message for message in messages)


@pytest.mark.skipif(not HAS_DATABASE_SOURCE, reason="Database sources are absent in this derived project")
def test_interactive_init_offers_postgres_for_backend_profile(monkeypatch, tmp_path: Path) -> None:
    answers = iter(["2", "1"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    target = tmp_path / "interactive-postgres"

    assert control.main(["init", "--target-dir", str(target)]) == 0
    assert loader.load_active_profile(target).optional_features == ("postgres",)
