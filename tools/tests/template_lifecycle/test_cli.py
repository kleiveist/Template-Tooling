from __future__ import annotations

import json

import pytest

from tools import control


@pytest.mark.parametrize("command", ["status", "audit", "adopt", "plan", "update", "verify"])
def test_every_lifecycle_subcommand_has_help(command: str, capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        control.main(["template", command, "--help"])

    assert raised.value.code == 0
    assert "usage:" in capsys.readouterr().out


def test_invalid_lifecycle_parameter_uses_exit_code_two() -> None:
    with pytest.raises(SystemExit) as raised:
        control.main(["template", "status", "--format", "xml"])

    assert raised.value.code == 2


def test_json_operational_failure_has_no_traceback(capsys) -> None:
    code = control.main(["template", "verify", "--target-dir", "/definitely/missing", "--format", "json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["status"] == "ERROR"
    assert payload["exit_code"] == 1
    assert "Traceback" not in payload["error"]
