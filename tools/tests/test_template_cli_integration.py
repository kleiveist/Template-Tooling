from __future__ import annotations

import argparse

from tools import control, control_parser


def test_root_help_lists_template_command(capsys) -> None:
    assert control.main([]) == 0

    output = capsys.readouterr().out
    assert "python tools/control.py template" in output
    assert "python tools/control.py template status" in output


def test_control_dispatches_template_commands_to_lifecycle_cli() -> None:
    assert control._handlers()["template"] is control.template_lifecycle_cli.main


def test_control_parser_delegates_template_parser_configuration(monkeypatch) -> None:
    configured: list[tuple[argparse._SubParsersAction, type[argparse.HelpFormatter]]] = []

    def configure_parser(
        subparsers: argparse._SubParsersAction,
        *,
        formatter_class: type[argparse.HelpFormatter],
    ) -> None:
        configured.append((subparsers, formatter_class))

    monkeypatch.setattr(
        control_parser.template_lifecycle_cli,
        "configure_parser",
        configure_parser,
    )

    parser = control_parser.build_parser()

    assert parser.prog == "python tools/control.py"
    assert len(configured) == 1
    assert configured[0][1] is control_parser.HelpFormatter


def test_bare_template_prints_lifecycle_help(capsys) -> None:
    assert control.main(["template"]) == 0

    output = capsys.readouterr().out
    for command in ("status", "audit", "adopt", "plan", "update", "verify"):
        assert command in output
