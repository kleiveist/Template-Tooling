"""Status and offline verification evidence for LC-001, LC-017, and LC-018."""

from __future__ import annotations

import json
import socket
import urllib.request
from dataclasses import replace
from pathlib import Path

from tools import control
from tools.template_lifecycle.state import load_state, write_state


def test_status_rejects_state_and_manifest_digest_mismatch(
    lifecycle_fixture,
    tmp_path: Path,
    capsys,
) -> None:
    target = lifecycle_fixture.managed_product(tmp_path / "digest mismatch product")
    state = load_state(target)
    tampered_digest = "sha256:" + "e" * 64
    write_state(
        target,
        replace(
            state,
            source=replace(state.source, tree_digest=tampered_digest),
            baseline=replace(state.baseline, digest=tampered_digest),
        ),
    )

    code = control.main(
        [
            "template",
            "status",
            "--target-dir",
            str(target),
            "--format",
            "json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["status"] == "ERROR"
    assert "digest" in payload["error"].lower()


def test_status_with_local_source_and_verify_are_offline(
    lifecycle_fixture,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    target = lifecycle_fixture.managed_product(tmp_path / "offline product")

    def reject_network(*_args, **_kwargs):
        raise AssertionError("Lifecycle status and verify must not access the network")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    monkeypatch.setattr(urllib.request, "urlopen", reject_network)

    status_code = control.main(
        [
            "template",
            "status",
            "--target-dir",
            str(target),
            "--source-dir",
            str(lifecycle_fixture.source_root),
            "--to-ref",
            lifecycle_fixture.v2,
            "--format",
            "json",
        ]
    )
    status_payload = json.loads(capsys.readouterr().out)
    verify_code = control.main(
        [
            "template",
            "verify",
            "--target-dir",
            str(target),
            "--format",
            "json",
        ]
    )
    verify_payload = json.loads(capsys.readouterr().out)

    assert status_code == 0
    assert status_payload["target_commit"] == lifecycle_fixture.v2
    assert verify_code == 0
    assert verify_payload["ok"] is True
