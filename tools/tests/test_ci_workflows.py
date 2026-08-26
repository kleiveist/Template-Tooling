from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"
CODEOWNERS = ROOT / ".github" / "CODEOWNERS"
NODE_WORKFLOW_SETUP_COUNTS = {
    "ci.yml": 2,
    "profiles.yml": 1,
    "postgres.yml": 1,
    "desktop.yml": 1,
    "release.yml": 1,
}
ARTIFACT_UPLOAD_COUNTS = {
    "ci.yml": 0,
    "profiles.yml": 0,
    "postgres.yml": 0,
    "desktop.yml": 1,
    "release.yml": 1,
}

pytestmark = pytest.mark.skipif(
    not WORKFLOWS.exists(),
    reason="Master-repository CI workflows are not scaffolded into derived projects",
)


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _action_versions(content: str, action: str) -> list[str]:
    pattern = re.compile(rf"(?m)^\s*uses:\s*{re.escape(action)}@([^\s#]+)(?:\s+#\s+v[^\s]+)?\s*$")
    return pattern.findall(content)


def _action_release_versions(content: str, action: str) -> list[str]:
    pattern = re.compile(rf"(?m)^\s*uses:\s*{re.escape(action)}@[0-9a-f]{{40}}\s+#\s+(v\d+(?:\.\d+)*)\s*$")
    return pattern.findall(content)


def _assert_action_major(content: str, action: str, major: str, *, count: int | None = None) -> None:
    pins = _action_versions(content, action)
    releases = _action_release_versions(content, action)
    if count is not None:
        assert len(pins) == count
    else:
        assert pins
    assert len(releases) == len(pins)
    assert all(re.fullmatch(r"[0-9a-f]{40}", pin) for pin in pins)
    assert all(release == major or release.startswith(f"{major}.") for release in releases)


def _steps_using(content: str, action: str) -> list[str]:
    steps = re.findall(r"(?ms)^      - .*?(?=^      - |\Z)", content)
    action_pattern = re.compile(rf"(?m)^        uses:\s*{re.escape(action)}@[^\s#]+(?:\s+#\s+v[^\s]+)?\s*$")
    return [step for step in steps if action_pattern.search(step)]


def _step_named(content: str, name: str) -> str:
    steps = re.findall(r"(?ms)^      - .*?(?=^      - |\Z)", content)
    matches = [step for step in steps if f"name: {name}\n" in step]
    assert len(matches) == 1
    return matches[0]


def _block_named(content: str, name: str, *, indent: int) -> str:
    lines = content.splitlines(keepends=True)
    header = f"{' ' * indent}{name}:"
    matches = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == header]
    assert len(matches) == 1

    start = matches[0]
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip():
            line_indent = len(line) - len(line.lstrip())
            if line_indent <= indent:
                break
        end += 1
    return "".join(lines[start:end])


@pytest.mark.parametrize(("name", "expected_setup_count"), NODE_WORKFLOW_SETUP_COUNTS.items())
def test_every_node_workflow_pins_node_24(name: str, expected_setup_count: int) -> None:
    content = _workflow(name)
    setup_steps = _steps_using(content, "actions/setup-node")

    _assert_action_major(content, "actions/setup-node", "v7", count=expected_setup_count)
    assert len(setup_steps) == expected_setup_count
    for step in setup_steps:
        node_versions = re.findall(r"(?m)^          node-version:\s*([^\s#]+)\s*$", step)
        assert node_versions == ['"24"']


def test_every_artifact_upload_uses_v7() -> None:
    for name, expected_upload_count in ARTIFACT_UPLOAD_COUNTS.items():
        _assert_action_major(
            _workflow(name),
            "actions/upload-artifact",
            "v7",
            count=expected_upload_count,
        )


def test_every_external_action_is_pinned_to_an_immutable_commit() -> None:
    external_action = re.compile(r"uses:\s*([^@\s]+)@([0-9a-f]{40})\s+#\s+(v\d+(?:\.\d+)*)")
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        for line_number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if not stripped.startswith("uses:") or stripped.startswith("uses: ./"):
                continue
            match = external_action.fullmatch(stripped)
            assert match is not None, (
                f"{workflow}:{line_number} must pin the action to a full SHA with a version comment"
            )


def test_ci_workflows_have_safe_common_policy() -> None:
    for name in ("ci.yml", "profiles.yml", "postgres.yml", "desktop.yml"):
        content = _workflow(name)
        assert "pull_request:" in content
        assert "push:" in content
        assert "workflow_dispatch:" in content
        assert "- main" in content
        assert "permissions:\n  contents: read" in content
        assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in content
        assert "timeout-minutes:" in content
        assert "continue-on-error" not in content
        assert "secrets." not in content
        assert "deploy" not in content.lower()
        assert "gh release" not in content.lower()
        assert "python tools/control.py release check" not in content


def test_security_workflow_has_least_privilege_scanners() -> None:
    content = _workflow("security.yml")
    dependency_review = _block_named(content, "dependency-review", indent=2)
    codeql = _block_named(content, "codeql", indent=2)

    assert "pull_request:" in content
    assert "push:" in content
    assert "schedule:" in content
    assert "workflow_dispatch:" in content
    assert "permissions:\n  contents: read" in content
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in content
    assert "continue-on-error" not in content
    assert "secrets." not in content

    assert "if: github.event_name == 'pull_request'" in dependency_review
    assert "fail-on-severity: high" in dependency_review
    assert "fail-on-scopes: development, runtime, unknown" in dependency_review
    _assert_action_major(dependency_review, "actions/dependency-review-action", "v5", count=1)

    assert "security-events: write" in codeql
    assert "fail-fast: false" in codeql
    for language in ("javascript-typescript", "python", "rust"):
        assert f"- {language}" in codeql
    assert "build-mode: none" in codeql
    assert "queries: security-extended" in codeql
    _assert_action_major(codeql, "github/codeql-action/init", "v4", count=1)
    _assert_action_major(codeql, "github/codeql-action/analyze", "v4", count=1)


def test_dependabot_covers_every_versioned_dependency_ecosystem() -> None:
    content = DEPENDABOT.read_text(encoding="utf-8")

    assert content.startswith("version: 2\n")
    assert content.count("package-ecosystem: github-actions") == 1
    assert content.count("package-ecosystem: npm") == 1
    assert content.count("package-ecosystem: pip") == 2
    assert content.count("package-ecosystem: cargo") == 2
    assert content.splitlines().count("  - package-ecosystem: docker") == 1
    assert content.splitlines().count("  - package-ecosystem: docker-compose") == 1
    for directory in (
        'directory: "/"',
        'directory: "/frontend"',
        'directory: "/backend"',
        'directory: "/tools"',
        'directory: "/src-tauri"',
        'directory: "/tools/quality/rust_analyzer"',
        'directory: "/deployment"',
        'directory: "/deployment/docker"',
    ):
        assert content.count(directory) == 1
    assert content.count("interval: weekly") == 8


def test_dependabot_groups_routine_updates_and_keeps_majors_individual() -> None:
    content = DEPENDABOT.read_text(encoding="utf-8")

    assert content.count("open-pull-requests-limit: 3") == 8
    assert "open-pull-requests-limit: 10" not in content
    assert content.count("cooldown:") == 5
    assert content.count("semver-major-days: 30") == 5
    assert content.count("groups:") == 8
    assert content.count("applies-to: version-updates") == 8
    assert content.count('patterns:\n          - "*"') == 8
    assert content.count("update-types:\n          - minor\n          - patch") == 8
    assert "version-update:semver-major" not in content
    assert "          - major" not in content


def test_codeowners_assigns_security_sensitive_master_paths() -> None:
    content = CODEOWNERS.read_text(encoding="utf-8")

    assert "* @kleiveist" in content
    for path in ("/.github/", "/config/", "/deployment/", "/src-tauri/", "/tools/"):
        assert f"{path} @kleiveist" in content


def test_core_ci_uses_supported_runtimes_and_public_tooling() -> None:
    content = _workflow("ci.yml")

    assert 'python-version: "3.11"' in content
    assert 'node-version: "24"' in content
    assert "name: Core / Code Quality & Architecture" in content
    assert (
        "rustup toolchain install 1.97.1 --profile minimal --component rustfmt,clippy --target wasm32-wasip1"
    ) in content
    assert "rustup default 1.97.1" in content
    assert "tools/quality/rust_analyzer/target" in content
    assert "tools/quality/rust_analyzer/Cargo.lock" in content
    assert "python tools/quality/rust_analyzer/build.py --check" in content
    assert "cargo clippy --manifest-path tools/quality/rust_analyzer/Cargo.toml" in content
    assert "tools/tests/quality/test_rust_syntax.py" in content
    assert "tools/tests/quality/test_rust_payload.py" in content
    assert "tools/tests/quality/test_rust_wasi_host.py" in content
    assert "python tools/control.py install --skip-backend --skip-playwright" in content
    assert "python tools/control.py quality" in content
    assert "tools/.venv/bin/python -m pytest -q tools/tests/quality/test_typescript_ast.py" in content
    assert content.count("needs: quality") == 5
    assert "name: Core / Documentation Check" in content
    assert "python tools/control.py docs check" in content
    assert "tools/.venv/bin/python -m pytest -q tools/tests/test_docs_index.py" in content
    assert "python tools/control.py test --suite tools" in content
    assert "python tools/control.py test --suite schema" in content
    assert "python tools/control.py test --suite api" in content
    assert "python tools/control.py test --suite database" in content
    assert "python tools/control.py container validate" in content
    assert "python tools/control.py build container" in content
    assert "python tools/control.py version check" in content
    assert "cache: pip" in content
    assert "cache: npm" in content
    _assert_action_major(content, "actions/checkout", "v7")
    _assert_action_major(content, "actions/setup-python", "v7")
    _assert_action_major(content, "actions/setup-node", "v7")
    _assert_action_major(content, "actions/cache", "v6")
    assert "\nenv:\n  DATABASE_URL:" not in content
    assert content.index("python tools/control.py quality") < content.index(
        "python tools/control.py test --suite tools"
    )


def test_core_ci_blocks_on_frontend_browser_accessibility_and_budget_checks() -> None:
    content = _workflow("ci.yml")
    frontend_job = _block_named(content, "frontend", indent=2)
    browser_install = _step_named(frontend_job, "Install Playwright Chromium and system dependencies")
    browser_test = _step_named(frontend_job, "Run browser smoke and accessibility tests")

    assert "name: Core / Frontend, Browser & Web Build" in frontend_job
    assert "python tools/control.py install --skip-frontend --skip-tooling --skip-playwright" in frontend_job
    assert "python tools/control.py test --suite frontend" in frontend_job
    assert "working-directory: frontend" in browser_install
    assert "npx playwright install --with-deps chromium" in browser_install
    assert "python tools/control.py test --suite e2e" in browser_test
    assert "DATABASE_URL: postgresql+psycopg://" in browser_test
    assert "python tools/control.py build web" in frontend_job
    assert content.index("python tools/control.py quality") < content.index("python tools/control.py build web")
    assert frontend_job.index(browser_install) < frontend_job.index(browser_test)


def test_profile_matrix_generates_and_tests_every_profile() -> None:
    content = _workflow("profiles.yml")
    cargo_cache_step = _step_named(content, "Cache Cargo dependencies")
    lifecycle_step = _step_named(content, "Verify generated template lifecycle")
    browser_install = _step_named(content, "Install generated Playwright Chromium and system dependencies")

    assert "fail-fast: false" in content
    for profile_id in (
        "web-only",
        "web-cloud",
        "desktop-local",
        "desktop-cloud",
        "full-platform",
    ):
        assert f"profile: {profile_id}" in content
    assert "python tools/control.py init" in content
    assert "working-directory: .generated/ci-${{ matrix.profile }}" in lifecycle_step
    assert "python tools/control.py template status" in lifecycle_step
    assert "python tools/control.py template verify" in lifecycle_step
    assert 'status["provenance"] == "generated"' in lifecycle_step
    assert 'status["source_reproducible"] is True' in lifecycle_step
    assert 'status["baseline_manifest"] == "valid"' in lifecycle_step
    assert "python tools/control.py doctor" in content
    assert "python tools/control.py config doctor" in content
    assert "python tools/control.py install --skip-playwright" in content
    assert "working-directory: .generated/ci-${{ matrix.profile }}/frontend" in browser_install
    assert "npx playwright install --with-deps chromium" in browser_install
    assert "python tools/control.py test --suite all" in content
    assert "python tools/control.py build web" in content
    assert "python tools/control.py container validate" in content
    assert "python tools/control.py tauri doctor" in content
    assert "python tools/control.py build desktop --dry-run --no-clean" in content
    assert "python tools/control.py quality" in content
    assert ("rustup toolchain install 1.97.1 --profile minimal --component rustfmt,clippy") in content
    _assert_action_major(content, "actions/checkout", "v7")
    _assert_action_major(content, "actions/setup-python", "v7")
    _assert_action_major(content, "actions/setup-node", "v7")
    _assert_action_major(content, "actions/cache", "v6")
    assert ".generated/ci-${{ matrix.profile }}/src-tauri/target" in cargo_cache_step
    assert content.index("name: Generate profile project") < content.index("name: Cache Cargo dependencies")
    assert content.index("name: Generate profile project") < content.index("name: Verify generated template lifecycle")
    assert content.index("name: Verify generated template lifecycle") < content.index("name: Cache Cargo dependencies")
    assert content.index("python tools/control.py quality") < content.index("python tools/control.py test --suite all")
    assert content.index(browser_install) < content.index("python tools/control.py test --suite all")


def test_postgres_ci_uses_isolated_service_health_check_and_migration() -> None:
    content = _workflow("postgres.yml")
    generated_content = content.split("  generated-postgres:", maxsplit=1)[1]
    lifecycle_step = _step_named(generated_content, "Verify generated template lifecycle")
    browser_install = _step_named(
        generated_content,
        "Install generated Playwright Chromium and system dependencies",
    )

    assert content.count("image: postgres:16.15-alpine3.24") == 2
    assert "POSTGRES_PASSWORD: test-password" in content
    assert "POSTGRES_DB: template_test" in content
    assert "DATABASE_URL_TEST:" in content
    assert "--health-cmd" in content
    assert "pg_isready" in content
    assert "sleep " not in content
    assert "python tools/control.py db upgrade" in content
    assert "python tools/control.py test --suite postgres" in content
    assert "--profile ${{ matrix.profile }}" in content
    assert "--with postgres" in content
    assert "working-directory: .generated/ci-${{ matrix.profile }}-postgres" in lifecycle_step
    assert "python tools/control.py template status" in lifecycle_step
    assert "python tools/control.py template verify" in lifecycle_step
    assert 'status["provenance"] == "generated"' in lifecycle_step
    assert 'status["source_reproducible"] is True' in lifecycle_step
    assert 'status["baseline_manifest"] == "valid"' in lifecycle_step
    for profile_id in ("web-cloud", "desktop-cloud", "full-platform"):
        assert f"profile: {profile_id}" in content
    assert "python tools/control.py container validate" in content
    assert "working-directory: .generated/ci-${{ matrix.profile }}-postgres/frontend" in browser_install
    assert "npx playwright install --with-deps chromium" in browser_install
    assert "python tools/control.py quality" in generated_content
    assert "python tools/control.py config doctor" in generated_content
    assert "python tools/control.py tauri doctor" in generated_content
    assert "python tools/control.py build desktop --dry-run --no-clean" in generated_content
    assert ("rustup toolchain install 1.97.1 --profile minimal --component rustfmt,clippy") in generated_content
    _assert_action_major(content, "actions/checkout", "v7")
    _assert_action_major(content, "actions/setup-python", "v7")
    _assert_action_major(content, "actions/setup-node", "v7")
    assert generated_content.index("name: Generate PostgreSQL profile project") < generated_content.index(
        "name: Verify generated template lifecycle"
    )
    assert generated_content.index("python tools/control.py quality") < generated_content.index(
        "python tools/control.py test --suite all"
    )
    assert generated_content.index(browser_install) < generated_content.index(
        "python tools/control.py test --suite all"
    )


def test_desktop_ci_builds_unsigned_native_artifacts_on_each_platform() -> None:
    content = _workflow("desktop.yml")

    assert "workflow_call:" in content
    assert "ubuntu-latest" in content
    assert "macos-latest" in content
    assert "windows-latest" in content
    assert "target: linux" in content
    assert "target: macos" in content
    assert "target: windows" in content
    assert ("rustup toolchain install 1.97.1 --profile minimal --component rustfmt,clippy") in content
    assert "name: Install portable tooling runtime" in content
    assert "python tools/control.py install --skip-backend --skip-frontend --skip-playwright" in content
    assert "name: Verify the portable Rust analyzer runtime" in content
    analyzer_step = _step_named(content, "Verify the portable Rust analyzer runtime")
    assert "env:" in analyzer_step
    assert "DATABASE_URL: postgresql+psycopg://template_test:test-password@127.0.0.1:5432/template_test" in (
        analyzer_step
    )
    assert "run: python tools/control.py doctor" in analyzer_step
    assert "name: Verify UTF-8 Rust analyzer transport" in content
    assert "test_subprocess_transport_is_utf8_when_child_text_stdio_is_cp1252" in content
    assert "if:" not in _step_named(content, "Install portable tooling runtime")
    assert "if:" not in analyzer_step
    assert "if:" not in _step_named(content, "Verify UTF-8 Rust analyzer transport")
    assert "tools\\.venv\\Scripts\\python.exe -m pytest -q tools/tests/test_process.py" in content
    assert "python tools/control.py test --suite tauri" in content
    assert "python tools/control.py build desktop" in content
    _assert_action_major(content, "actions/upload-artifact", "v7", count=1)
    assert "unsigned" in content.lower()
    assert "secrets." not in content
    assert "publish" not in content.lower()
    assert "deploy" not in content.lower()


def test_desktop_ci_declares_typed_linux_bundle_inputs_with_deb_defaults() -> None:
    content = _workflow("desktop.yml")
    expected_input = (
        "      linux_bundles:\n"
        "        description: Comma-separated unsigned Linux bundle targets\n"
        "        required: false\n"
        "        default: deb\n"
        "        type: string\n"
    )

    for event_name in ("workflow_dispatch", "workflow_call"):
        event = _block_named(content, event_name, indent=2)
        assert event.count("linux_bundles:") == 1
        assert expected_input in event

    assert (
        "          - label: Linux\n"
        "            os: ubuntu-latest\n"
        "            target: linux\n"
        "            bundles: deb\n"
        "            archive: desktop-linux-unsigned.tar.gz\n"
    ) in content


def test_desktop_ci_resolves_linux_bundle_input_without_shell_interpolation() -> None:
    content = _workflow("desktop.yml")
    build_step = _step_named(content, "Build unsigned Linux packages")
    verify_step = _step_named(content, "Verify unsigned Linux packages")
    resolution = "LINUX_BUNDLES: ${{ inputs.linux_bundles || matrix.bundles }}"

    for step in (build_step, verify_step):
        assert "if: matrix.target == 'linux'" in step
        assert resolution in step

    assert 'run: python tools/control.py build desktop --target linux --bundles "$LINUX_BUNDLES"' in build_step
    assert "${{ inputs.linux_bundles" not in next(
        line for line in build_step.splitlines() if line.strip().startswith("run:")
    )
    assert "python tools/control.py tauri verify-artifacts" in verify_step
    assert '--bundles "$LINUX_BUNDLES"' in verify_step
    assert '--summary-file "$GITHUB_STEP_SUMMARY"' in verify_step


def test_desktop_ci_prearchives_native_candidates_before_aggregate_upload() -> None:
    content = _workflow("desktop.yml")
    build_step = _step_named(content, "Build unsigned Linux packages")
    verify_step = _step_named(content, "Verify unsigned Linux packages")
    posix_archive_step = _step_named(content, "Archive unsigned POSIX verification artifacts")
    windows_archive_step = _step_named(content, "Archive unsigned Windows verification artifacts")
    upload_step = _step_named(content, "Upload unsigned verification artifacts")

    assert (
        content.index(build_step)
        < content.index(verify_step)
        < content.index(posix_archive_step)
        < content.index(windows_archive_step)
        < content.index(upload_step)
    )
    assert "if: matrix.target != 'windows'" in posix_archive_step
    assert "shell: bash" in posix_archive_step
    assert "ARCHIVE_NAME: ${{ matrix.archive }}" in posix_archive_step
    assert 'bundle_root="src-tauri/target/release/bundle"' in posix_archive_step
    assert 'tar --create --gzip --file "$archive_path" -- "${archive_inputs[@]}"' in posix_archive_step
    assert 'tar --list --gzip --file "$archive_path"' in posix_archive_step
    assert ".dist/desktop/linux/linux-bundles.json" in posix_archive_step
    assert ".dist/desktop/linux/SHA256SUMS" in posix_archive_step
    assert "if: matrix.target == 'windows'" in windows_archive_step
    assert "shell: pwsh" in windows_archive_step
    assert "ARCHIVE_NAME: ${{ matrix.archive }}" in windows_archive_step
    assert '$bundleRoot = "src-tauri/target/x86_64-pc-windows-msvc/release/bundle"' in windows_archive_step
    assert "[System.IO.FileAttributes]::ReparsePoint" in windows_archive_step
    assert "Compress-Archive -LiteralPath $bundleRoot" in windows_archive_step
    assert "[System.IO.Compression.ZipFile]::OpenRead" in windows_archive_step
    _assert_action_major(upload_step, "actions/upload-artifact", "v7", count=1)
    assert "name: desktop-${{ matrix.target }}-unsigned" in upload_step
    assert "if-no-files-found: error" in upload_step
    assert "include-hidden-files: true" in upload_step
    assert "compression-level: 0" in upload_step
    assert "path: .dist/desktop/artifacts/${{ matrix.archive }}" in upload_step
    assert "src-tauri/target/release/bundle" not in upload_step
    assert "src-tauri/target/x86_64-pc-windows-msvc/release/bundle" not in upload_step
    assert ".dist/desktop/linux" not in upload_step
    assert ".dist/desktop/**" not in upload_step


def test_desktop_ci_preserves_macos_and_windows_build_contracts() -> None:
    content = _workflow("desktop.yml")
    native_step = _step_named(content, "Build unsigned native package")
    upload_step = _step_named(content, "Upload unsigned verification artifacts")

    assert (
        "          - label: macOS\n"
        "            os: macos-latest\n"
        "            target: macos\n"
        "            bundles: all\n"
        "            archive: desktop-macos-unsigned.tar.gz\n"
    ) in content
    assert (
        "          - label: Windows\n"
        "            os: windows-latest\n"
        "            target: windows\n"
        "            bundles: all\n"
        "            archive: desktop-windows-unsigned.zip\n"
    ) in content
    assert "if: matrix.target != 'linux'" in native_step
    assert "run: python tools/control.py build desktop --target ${{ matrix.target }}" in native_step
    assert "name: desktop-${{ matrix.target }}-unsigned" in upload_step


def test_release_validation_is_explicit_and_never_publishes() -> None:
    content = _workflow("release.yml")
    browser_install = _step_named(content, "Install Playwright Chromium and system dependencies")
    full_test = _step_named(content, "Run release-independent tests")

    assert "workflow_dispatch:" in content
    assert '"v*.*.*"' in content
    assert "timeout-minutes: 60" in content
    assert "branches:" not in content
    assert "python tools/control.py release check" in content
    assert "python tools/control.py quality --release" in content
    assert "tools/.venv/bin/python -m pytest -q tools/tests/test_docs_index.py" in content
    assert (
        "rustup toolchain install 1.97.1 --profile minimal --component rustfmt,clippy --target wasm32-wasip1"
    ) in content
    assert "python tools/quality/rust_analyzer/build.py --check" in content
    assert "python tools/control.py build web" in content
    assert "python tools/control.py build container" in content
    assert "working-directory: frontend" in browser_install
    assert "npx playwright install --with-deps chromium" in browser_install
    assert "DATABASE_URL: postgresql+psycopg://" in full_test
    assert "python tools/control.py test --suite all" in full_test
    assert "uses: ./.github/workflows/desktop.yml" in content
    assert "permissions:\n  contents: read" in content
    assert "secrets." not in content
    assert "continue-on-error" not in content
    assert "publish" not in content.lower()
    assert "deploy" not in content.lower()
    assert content.index("python tools/control.py quality") < content.index("python tools/control.py test --suite all")
    assert content.index(browser_install) < content.index(full_test)


def test_release_validation_generates_and_attests_sbom_evidence() -> None:
    content = _workflow("release.yml")
    validate_job = _block_named(content, "validate", indent=2)
    sbom_step = _step_named(content, "Generate SPDX dependency SBOM")
    provenance_step = _step_named(content, "Attest web candidate build provenance")
    sbom_attestation_step = _step_named(content, "Attest web candidate SBOM")
    upload_step = _step_named(content, "Upload web candidate")

    assert "attestations: write" in validate_job
    assert "contents: read" in validate_job
    assert "id-token: write" in validate_job
    _assert_action_major(sbom_step, "anchore/sbom-action", "v0", count=1)
    assert "format: spdx-json" in sbom_step
    assert "upload-artifact: false" in sbom_step
    assert "upload-release-assets: false" in sbom_step
    _assert_action_major(provenance_step, "actions/attest", "v4", count=1)
    _assert_action_major(sbom_attestation_step, "actions/attest", "v4", count=1)
    assert "subject-path: .dist/web/*.zip" in provenance_step
    assert "subject-path: .dist/web/*.zip" in sbom_attestation_step
    assert "sbom-path: .dist/sbom/template-project-${{ github.sha }}.spdx.json" in sbom_attestation_step
    assert ".dist/sbom/*.spdx.json" in upload_step
    assert content.index(sbom_step) < content.index(provenance_step) < content.index(upload_step)


def test_release_validation_hands_off_exact_linux_bundle_contract() -> None:
    content = _workflow("release.yml")
    desktop_job = _block_named(content, "desktop-candidates", indent=2)

    assert "needs: validate" in desktop_job
    assert "uses: ./.github/workflows/desktop.yml" in desktop_job
    assert 'with:\n      linux_bundles: "deb,rpm,appimage"\n' in desktop_job
    assert desktop_job.count("linux_bundles:") == 1
    assert "linux_bundles: all" not in desktop_job
    assert "secrets:" not in desktop_job
    assert "publish" not in content.lower()
    assert "deploy" not in content.lower()


def test_release_publication_runs_only_after_successful_tag_validation() -> None:
    content = _workflow("release-publish.yml")
    prepare_job = _block_named(content, "prepare", indent=2)
    publish_job = _block_named(content, "publish", indent=2)

    assert "workflow_run:" in content
    assert "- Release Validation" in content
    assert "- completed" in content
    assert "workflow_dispatch:" not in content
    assert "pull_request:" not in content
    assert "github.event.workflow_run.conclusion == 'success'" in prepare_job
    assert "github.event.workflow_run.event == 'push'" in prepare_job
    assert "startsWith(github.event.workflow_run.head_branch, 'v')" in prepare_job
    assert "needs: prepare" in publish_job
    assert "group: release-publication-${{ github.event.workflow_run.head_branch }}" in content
    assert "cancel-in-progress: false" in content
    assert "continue-on-error" not in content


def test_release_publication_uses_candidate_bound_least_privilege_control_plane() -> None:
    content = _workflow("release-publish.yml")
    prepare_job = _block_named(content, "prepare", indent=2)
    publish_job = _block_named(content, "publish", indent=2)
    candidate_checkout = _step_named(content, "Check out validated release source as data")

    assert "actions: read" in prepare_job
    assert "contents: read" in prepare_job
    assert "contents: write" not in prepare_job
    assert "id-token: write" not in content
    assert "attestations: write" not in content
    assert "actions/attest@" not in content
    assert "actions: read" in publish_job
    assert "attestations: read" in publish_job
    assert "contents: write" in publish_job
    assert content.count('run: test "$PUBLISHER_CONTROL_SHA" = "$RELEASE_SHA"') == 2
    assert content.count("ref: ${{ github.workflow_sha }}") == 2
    assert "ref: ${{ github.event.workflow_run.head_sha }}" in candidate_checkout
    assert "path: .release-source" in candidate_checkout
    assert "fetch-depth: 0" in candidate_checkout
    assert "persist-credentials: false" in candidate_checkout
    _assert_action_major(content, "actions/checkout", "v7", count=3)


def test_release_publication_hands_off_only_same_run_exact_sha_bundle() -> None:
    content = _workflow("release-publish.yml")
    artifact_download = _step_named(content, "Download validated release artifacts")
    prepare = _step_named(content, "Verify exact-SHA gates and prepare publication bundle")
    upload = _step_named(content, "Upload verified publication bundle")
    same_run_download = _step_named(content, "Download same-run verified publication bundle")
    bundle_verification = _step_named(content, "Reverify bundle and remote annotated tag")

    _assert_action_major(content, "actions/download-artifact", "v7", count=2)
    _assert_action_major(upload, "actions/upload-artifact", "v7", count=1)
    assert "run-id: ${{ github.event.workflow_run.id }}" in artifact_download
    assert "github-token: ${{ secrets.GITHUB_TOKEN }}" in artifact_download
    assert "name: exact-sha-release-publication" in upload
    assert "name: exact-sha-release-publication" in same_run_download
    assert "PYTHONPATH=.publisher-control python -m tools.inst.release_publish_cli prepare" in prepare
    assert "--root .release-source" in prepare
    assert '--tag "$RELEASE_TAG"' in prepare
    assert '--sha "$RELEASE_SHA"' in prepare
    assert '--release-run-id "$RELEASE_RUN_ID"' in prepare
    assert '--release-run-attempt "$RELEASE_RUN_ATTEMPT"' in prepare
    assert content.count("github.event.workflow_run.run_attempt") == 7
    assert content.count('--release-run-attempt "$RELEASE_RUN_ATTEMPT"') == 9
    assert "verify-bundle" in bundle_verification
    assert "verify-remote-tag" in bundle_verification
    assert content.index(prepare) < content.index(upload) < content.index(bundle_verification)


def test_release_publication_is_governed_resumable_and_native_immutable() -> None:
    content = _workflow("release-publish.yml")
    bundle_verification = _step_named(content, "Reverify bundle and remote annotated tag")
    governance = _step_named(content, "Require immutable release and protected tag governance")
    resume = _step_named(content, "Inspect resumable publication state")
    draft = _step_named(content, "Create and verify complete draft release")
    publication = _step_named(content, "Publish verified draft against protected tag")
    final_verification = _step_named(content, "Verify immutable publication and release attestation")

    assert "RELEASE_GOVERNANCE_TOKEN" in governance
    assert "verify-governance" in governance
    assert "publication-state" in resume
    assert 'echo "state=$state" >> "$GITHUB_OUTPUT"' in resume
    assert "if: steps.publication.outputs.state == 'absent'" in draft
    assert "if: steps.publication.outputs.state == 'absent'" in publication
    assert 'gh release create "$RELEASE_TAG"' in draft
    assert "--draft" in draft
    assert 'gh release upload "$RELEASE_TAG"' in draft
    assert "--state draft" in draft
    assert "verify-remote-tag" in publication
    assert content.count("verify-remote-tag") == 3
    assert "--draft=false" in publication
    assert "--state published" in final_verification
    assert 'gh release verify "$RELEASE_TAG"' in final_verification
    assert "for attempt in 1 2 3 4 5" in final_verification
    assert 'if [ "$STARTING_STATE" = "absent" ]' in final_verification
    assert 'gh release edit "$RELEASE_TAG" --repo "$GITHUB_REPOSITORY" --draft' in final_verification
    assert content.index(bundle_verification) < content.index(governance) < content.index(resume)
    assert content.index(resume) < content.index(draft) < content.index(publication)
    assert content.index(publication) < content.index(final_verification)
