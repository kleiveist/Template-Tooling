from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tools.quality.architecture import architecture_result
from tools.quality.model import QualityConfig
from tools.quality.scanner import SourceMetrics, scan_repository
from tools.quality.typescript import analyze_typescript

ROOT = Path(__file__).resolve().parents[3]
TYPESCRIPT_RUNTIME = ROOT / "frontend" / "node_modules" / "typescript"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not TYPESCRIPT_RUNTIME.exists(),
    reason="real TypeScript AST integration requires Node.js and installed frontend dependencies",
)


def test_real_typescript_ast_feeds_frontend_architecture(quality_config: QualityConfig) -> None:
    metrics = scan_repository(ROOT, quality_config)

    analysis, ast_result = analyze_typescript(ROOT, metrics)

    assert ast_result.status == "PASS"
    assert analysis is not None
    assert any(edge.path == "frontend/src/main.ts" and edge.specifier == "./api/backend" for edge in analysis.imports)
    assert any(function.symbol == "readRootDotenv" for function in analysis.functions)
    architecture = architecture_result(ROOT, quality_config, metrics, analysis)
    assert architecture.status == "PASS"
    assert architecture.findings == []


def _prepare_typescript_tooling(tmp_path: Path) -> Path:
    frontend = tmp_path / "frontend"
    scripts = frontend / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "quality-ast.mjs").symlink_to(ROOT / "frontend/scripts/quality-ast.mjs")
    (frontend / "node_modules").mkdir()
    (frontend / "node_modules/typescript").symlink_to(TYPESCRIPT_RUNTIME, target_is_directory=True)
    return frontend


def test_real_typescript_ast_qualifies_same_named_methods_by_class(tmp_path: Path) -> None:
    frontend = _prepare_typescript_tooling(tmp_path)
    source = frontend / "src/classes.ts"
    source.parent.mkdir()
    source.write_text(
        "class First { refresh() { return 1; } }\nclass Second { refresh() { return 2; } }\n",
        encoding="utf-8",
    )
    metric = SourceMetrics(source, "frontend/src/classes.ts", 2, frozenset({1, 2}), ())

    analysis, result = analyze_typescript(tmp_path, [metric])

    assert result.status == "PASS"
    assert analysis is not None
    assert [function.symbol for function in analysis.functions] == ["First.refresh", "Second.refresh"]


def test_real_typescript_ast_qualifies_namespace_object_and_anonymous_class_symbols(tmp_path: Path) -> None:
    frontend = _prepare_typescript_tooling(tmp_path)
    source = frontend / "src/scopes.ts"
    source.parent.mkdir()
    source.write_text(
        "namespace A { export class Service { run() {} } export function measured() {} }\n"
        "namespace B { export class Service { run() {} } export function measured() {} }\n"
        "const X = class { run() {} };\n"
        "const Y = class { run() {} };\n"
        "const Left = { measured() {} };\n"
        "const Right = { measured() {} };\n",
        encoding="utf-8",
    )
    metric = SourceMetrics(source, "frontend/src/scopes.ts", 6, frozenset(range(1, 7)), ())

    analysis, result = analyze_typescript(tmp_path, [metric])

    assert result.status == "PASS"
    assert analysis is not None
    assert {class_metric.symbol for class_metric in analysis.classes} == {"A.Service", "B.Service", "X", "Y"}
    assert {function.symbol for function in analysis.functions} == {
        "A.Service.run",
        "A.measured",
        "B.Service.run",
        "B.measured",
        "X.run",
        "Y.run",
        "Left.measured",
        "Right.measured",
    }


def test_real_typescript_ast_rejects_parse_errors(tmp_path: Path) -> None:
    frontend = _prepare_typescript_tooling(tmp_path)
    source = frontend / "src/invalid.ts"
    source.parent.mkdir()
    source.write_text("export function invalid( {\n", encoding="utf-8")
    metric = SourceMetrics(source, "frontend/src/invalid.ts", 1, frozenset({1}), ())

    analysis, result = analyze_typescript(tmp_path, [metric])

    assert analysis is None
    assert result.status == "FAIL"
    assert "failed with exit code 1" in result.detail
