from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "portable-tooling.yml"
SMOKE = ROOT / "tests" / "source" / "portable_ci_smoke.py"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _block(content: str, name: str, *, indent: int) -> str:
    lines = content.splitlines(keepends=True)
    header = f"{' ' * indent}{name}:"
    matches = [index for index, line in enumerate(lines) if line.rstrip() == header]
    assert len(matches) == 1
    start = matches[0]
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        end += 1
    return "".join(lines[start:end])


def _step(content: str, name: str) -> str:
    pattern = re.compile(
        rf"(?ms)^      - name: {re.escape(name)}\n.*?(?=^      - name: |\Z)"
    )
    matches = pattern.findall(content)
    assert len(matches) == 1
    return matches[0]


def test_ci_has_safe_clean_checkout_policy_and_immutable_actions() -> None:
    content = _workflow()

    assert "pull_request:" in content
    assert "push:" in content
    assert "workflow_dispatch:" in content
    assert "permissions:\n  contents: read" in content
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in content
    assert "continue-on-error" not in content
    assert "secrets." not in content
    assert "deploy" not in content.casefold()
    assert "publish" not in content.casefold()
    assert "tools/.venv" not in content
    assert 'PYTHONDONTWRITEBYTECODE: "1"' in content
    assert 'PYTHONHASHSEED: "0"' in content
    assert 'PYTHONNOUSERSITE: "1"' in content

    action_line = re.compile(
        r"uses:\s+([^@\s]+)@([0-9a-f]{40})\s+#\s+(v\d+(?:\.\d+)*)$"
    )
    uses = [line.strip() for line in content.splitlines() if "uses:" in line]
    matches = [action_line.fullmatch(line) for line in uses]
    assert all(match is not None for match in matches)
    identities = [(match.group(1), match.group(3)) for match in matches if match]
    assert identities.count(("actions/checkout", "v6.0.2")) == 2
    assert identities.count(("actions/setup-python", "v6.3.0")) == 2

    checkout = _step(content, "Check out complete source history")
    assert "fetch-depth: 0" in checkout
    assert 'test -z "$(git status --porcelain=v1 --untracked-files=all)"' in content


def test_linux_ci_exercises_export_migration_customer_repeat_and_case_study() -> None:
    linux = _block(_workflow(), "portable-linux", indent=2)

    assert "runs-on: ubuntu-24.04" in linux
    assert "RUNNER_TEMP/template-tooling-venv" in linux
    assert 'echo "$RUNNER_TEMP/template-tooling-venv/bin" >> "$GITHUB_PATH"' in linux
    assert "-p no:cacheprovider tests/source" in linux
    assert "tools/tests/acceptance/test_copy_matrix.py" in linux
    assert "tools/tests/acceptance/test_tooling_replacement.py" in linux
    assert "tools/control.py tooling export --output" in linux
    assert "tests/source/portable_ci_smoke.py" in linux
    assert "Template-Tooling-$VERSION" in linux
    assert linux.count("docs/toolingdocs/case-study/build.py") == 2
    assert "portable-tooling-case-study-de.pdf" in linux
    assert "portable-tooling-case-study-en.pdf" in linux
    assert linux.count("cmp ") == 2
    assert "texlive-latex-base" in linux

    smoke = SMOKE.read_text(encoding="utf-8")
    assert '"integrate", "--check"' in smoke
    assert smoke.count('"integrate", "--full-fix"') == 2
    assert '"test",\n        "--suite",\n        "all"' in smoke
    assert "copied_before_check" in smoke
    assert "before_second_check" in smoke
    assert "before_second_fix" in smoke
    assert "product_hashes" in smoke and "observed_hashes" in smoke
    assert "tests/source" not in smoke


def test_windows_ci_runs_portable_tests_and_exported_cli() -> None:
    windows = _block(_workflow(), "portable-windows", indent=2)

    assert "runs-on: windows-2025" in windows
    assert 'Join-Path $env:RUNNER_TEMP "template-tooling-venv"' in windows
    assert 'Join-Path $venv "Scripts"' in windows
    assert 'Join-Path $scripts "python.exe"' in windows
    assert "GITHUB_PATH" in windows
    assert "TEMPLATE_TOOLING_NESTED_TEST" in windows
    assert "tools/tests docs/toolingdocs/case-study/tests" in windows
    assert "tools/control.py tooling export --output" in windows
    assert 'Join-Path $output "Template-Tooling-$version"' in windows
    assert "tools/control.py --help" in windows
    assert "tools/control.py docs check" in windows
    assert "tools/tests/core tools/tests/integration" in windows
    assert windows.count("if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }") >= 5


def test_source_only_tests_are_physically_outside_portable_tools_tests() -> None:
    portable_root = ROOT / "tools" / "tests"
    portable_sources = tuple(portable_root.rglob("*.py"))
    marker_references = [
        path.relative_to(ROOT).as_posix()
        for path in portable_sources
        if ".template-tooling-source" in path.read_text(encoding="utf-8")
    ]

    assert marker_references == ["tools/tests/integration/test_export.py"]
    assert "skipif" not in (ROOT / marker_references[0]).read_text(encoding="utf-8")
    assert not (portable_root / "test_ci_workflows.py").exists()
    assert not (portable_root / "quality" / "test_typescript_ast.py").exists()
    assert (
        ROOT / "tests" / "source" / "test_historical_tooling_migration.py"
    ).is_file()
    assert (ROOT / "tests" / "source" / "test_typescript_ast.py").is_file()
    assert (ROOT / "tests" / "source" / "test_repository_documentation.py").is_file()
