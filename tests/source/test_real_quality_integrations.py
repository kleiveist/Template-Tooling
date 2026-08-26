from __future__ import annotations

from pathlib import Path

import pytest

from tools.quality import tooling
from tools.quality.model import QualityConfig
from tools.quality.scanner import SourceMetrics

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_real_eslint_metrics_ignore_inline_disable(
    tmp_path: Path,
    quality_config: QualityConfig,
) -> None:
    installed_frontend = REPOSITORY_ROOT / "frontend"
    eslint = tooling._frontend_binary(REPOSITORY_ROOT, "eslint")
    config = installed_frontend / "eslint.config.js"
    if eslint is None or not config.is_file():
        pytest.skip("ESLint is not installed")
    frontend = tmp_path / "frontend"
    source = frontend / "src" / "suppressed.ts"
    source.parent.mkdir(parents=True)
    (frontend / "node_modules").symlink_to(
        installed_frontend / "node_modules", target_is_directory=True
    )
    (frontend / "eslint.config.js").symlink_to(config)
    (frontend / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
    branches = "\n".join(
        f"  if (value === {index}) result += 1;" for index in range(21)
    )
    source.write_text(
        "/* eslint-disable complexity */\n"
        "export function measured(value: number) {\n"
        f"  let result = 0;\n{branches}\n  return result;\n}}\n",
        encoding="utf-8",
    )
    metric = SourceMetrics(
        source,
        "frontend/src/suppressed.ts",
        26,
        frozenset(range(1, 27)),
        (),
    )

    result = tooling.run_typescript_metrics(tmp_path, [metric], quality_config)

    assert any(finding.rule.rule_id == "CQ102" for finding in result.findings)
    assert result.status == "FAIL"
