"""Ensure Gate 0 reports verified readiness and rejects missing evidence."""

from __future__ import annotations

import json
from pathlib import Path

from tools import ci_gate

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_gate_zero_reports_current_implementation_readiness(capsys) -> None:
    evidence = ci_gate.evaluate_gate_zero(REPOSITORY_ROOT / "tools")
    statuses = {item.check: item.status for item in evidence}

    assert statuses == {
        "profile-desired-state": ci_gate.GateStatus.PASS,
        "transactional-action-kinds": ci_gate.GateStatus.PASS,
        "deterministic-failpoints": ci_gate.GateStatus.PASS,
        "transaction-boundary": ci_gate.GateStatus.PASS,
        "versioned-migration": ci_gate.GateStatus.PASS,
        "deterministic-export": ci_gate.GateStatus.PASS,
    }

    assert (
        ci_gate.main(
            ["--require-ready", "--tools-root", str(REPOSITORY_ROOT / "tools")]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"


def test_require_ready_fails_closed_when_any_evidence_is_blocked(
    capsys,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ci_gate,
        "evaluate_gate_zero",
        lambda _tools_root: (
            ci_gate.GateEvidence(
                "missing-capability",
                ci_gate.GateStatus.BLOCKED,
                "implementation is absent",
            ),
        ),
    )

    assert ci_gate.main(["--require-ready"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "BLOCKED"
