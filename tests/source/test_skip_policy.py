"""Source-only audit for explicit, bounded pytest skips.

The portable test payload is allowed to skip tests when a genuinely optional
platform feature is unavailable.  A skip must nevertheless stay visible and
deliberate: every supported pytest skip construct needs a non-empty technical
reason, and adding a new skip site requires consciously updating this policy.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TEST_ROOTS = (
    REPOSITORY_ROOT / "tools" / "tests",
    REPOSITORY_ROOT / "tests" / "source",
    REPOSITORY_ROOT / "docs" / "toolingdocs" / "case-study" / "tests",
)
SKIP_CALLS = frozenset(
    {
        "pytest.skip",
        "pytest.mark.skip",
        "pytest.mark.skipif",
        "pytest.importorskip",
    }
)
# Raise this only with a technical reason in the changed test and review of the
# resulting CI evidence.  Decreases are harmless and need no policy update.
MAXIMUM_SKIP_SITES = 57


@dataclass(frozen=True, slots=True)
class SkipSite:
    path: Path
    line: int
    call: str
    reason: ast.expr | None

    @property
    def display(self) -> str:
        return f"{self.path.relative_to(REPOSITORY_ROOT)}:{self.line} ({self.call})"


def test_every_pytest_skip_is_justified_and_skip_sites_are_bounded() -> None:
    sites = tuple(_skip_sites())
    missing_reasons = [site.display for site in sites if not _has_visible_reason(site)]

    assert not missing_reasons, (
        "Skip without a visible technical reason:\n" + "\n".join(missing_reasons)
    )
    assert len(sites) <= MAXIMUM_SKIP_SITES, (
        "The number of pytest skip sites increased from the reviewed policy "
        f"limit ({MAXIMUM_SKIP_SITES}) to {len(sites)}. Add a technical reason and "
        "deliberately update MAXIMUM_SKIP_SITES if the new skip is unavoidable."
    )


def _skip_sites() -> list[SkipSite]:
    sites: list[SkipSite] = []
    for root in TEST_ROOTS:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                call = _dotted_name(node.func)
                if call not in SKIP_CALLS:
                    continue
                sites.append(
                    SkipSite(
                        path=path,
                        line=node.lineno,
                        call=call,
                        reason=_reason_argument(node, call),
                    )
                )
    return sites


def _dotted_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _reason_argument(node: ast.Call, call: str) -> ast.expr | None:
    for keyword in node.keywords:
        if keyword.arg == "reason":
            return keyword.value
    if call in {"pytest.skip", "pytest.mark.skip", "pytest.importorskip"}:
        return node.args[0] if node.args else None
    # ``pytest.mark.skipif(condition, reason)`` accepts the reason after the
    # condition, although keyword form is preferred for readable evidence.
    return node.args[1] if len(node.args) > 1 else None


def _has_visible_reason(site: SkipSite) -> bool:
    reason = site.reason
    if isinstance(reason, ast.Constant) and isinstance(reason.value, str):
        return bool(reason.value.strip())
    if isinstance(reason, ast.JoinedStr):
        return any(
            isinstance(part, ast.Constant)
            and isinstance(part.value, str)
            and bool(part.value.strip())
            for part in reason.values
        )
    return False
