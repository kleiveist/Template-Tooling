from __future__ import annotations

import pytest

from tools import control


def test_root_help_exposes_portable_integration_commands(capsys) -> None:
    assert control.main([]) == 0

    output = capsys.readouterr().out
    assert "python tools/control.py integrate --check" in output
    assert "python tools/control.py integrate --full-fix" in output
    assert "python tools/control.py tooling verify" in output
    assert "python tools/control.py template" not in output


def test_control_dispatches_integration_and_tooling_commands() -> None:
    handlers = control._handlers()

    assert handlers["integrate"] is control.integration_cli.main
    assert handlers["tooling"] is control.integration_cli.main
    assert "template" not in handlers
    assert "init" not in handlers


def test_integrate_requires_exactly_one_mode() -> None:
    parser = control._build_parser()

    check = parser.parse_args(["integrate", "--check"])
    full_fix = parser.parse_args(["integrate", "--full-fix"])
    assert check.check and not check.full_fix
    assert full_fix.full_fix and not full_fix.check

    with pytest.raises(SystemExit) as missing:
        parser.parse_args(["integrate"])
    assert missing.value.code == 2

    with pytest.raises(SystemExit) as conflicting:
        parser.parse_args(["integrate", "--check", "--full-fix"])
    assert conflicting.value.code == 2


def test_bare_tooling_prints_maintenance_map(capsys) -> None:
    assert control.main(["tooling"]) == 0

    output = capsys.readouterr().out
    assert "migrate" in output
    assert "verify" in output
    assert "export" in output


@pytest.mark.parametrize(
    "arguments",
    (
        ["integrate", "--check"],
        ["integrate", "--full-fix"],
        ["tooling", "migrate", "--check"],
        ["tooling", "verify"],
        ["tooling", "export"],
    ),
)
def test_phase_three_service_boundary_fails_closed_without_traceback(
    tmp_path, monkeypatch, capsys, arguments: list[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    assert control.main(arguments) == 2

    output = capsys.readouterr().out
    assert "NOT_READY" in output
    assert list(tmp_path.iterdir()) == []
