#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import sys
import traceback
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = importlib.import_module("tools.logger")
build = importlib.import_module("tools.inst.build")
configuration = importlib.import_module("tools.inst.configuration")
console = importlib.import_module("tools.inst.console")
container = importlib.import_module("tools.inst.container")
db = importlib.import_module("tools.inst.db")
docs_index = importlib.import_module("tools.inst.docs_index")
doctor = importlib.import_module("tools.inst.doctor")
install = importlib.import_module("tools.inst.install")
release = importlib.import_module("tools.inst.release")
run = importlib.import_module("tools.inst.run")
run_test = importlib.import_module("tools.inst.run_test")
stop = importlib.import_module("tools.inst.stop")
profile_cli = importlib.import_module("tools.profiles.cli")
profile_runtime = importlib.import_module("tools.profiles.runtime")
quality_control = importlib.import_module("tools.quality.control")
tauri_build = importlib.import_module("tools.tauri.build")
tauri_control = importlib.import_module("tools.tauri.control")
template_lifecycle_cli = importlib.import_module("tools.template_lifecycle.cli")
control_parser = importlib.import_module("tools.control_parser")

Handler = Callable[[argparse.Namespace], int]
HelpFormatter = control_parser.HelpFormatter
ControlParser = control_parser.ControlParser
ROOT_HELP = control_parser.ROOT_HELP
ROOT_EXAMPLES = control_parser.ROOT_EXAMPLES


COMMAND_ALIASES: dict[str, str] = {
    "--doctor": "doctor",
    "--install": "install",
    "--run": "run",
    "--stop": "stop",
    "--test": "test",
}

TAURI_COMMAND_ALIASES: dict[str, str] = {
    "--doctor": "doctor",
    "--install": "install",
    "--run": "run",
    "--build": "build",
    "--install-appimage": "install-appimage",
    "--test": "test",
    "--copy": "copy",
}


def _normalize_argv(argv: list[str] | None) -> list[str]:
    normalized = list(sys.argv[1:] if argv is None else argv)
    if not normalized:
        return normalized

    first = normalized[0].lower()
    if first == "--build":
        if len(normalized) >= 2 and normalized[1].lower() == "--desktop":
            return ["build", "desktop", *normalized[2:]]
        return ["build", "web", *normalized[1:]]

    if len(normalized) >= 2 and first == "tauri":
        tauri_alias = TAURI_COMMAND_ALIASES.get(normalized[1].lower())
        if tauri_alias:
            normalized[1] = tauri_alias
            return normalized

    alias = COMMAND_ALIASES.get(first)
    if alias:
        show_test_guide = first == "--test" and len(normalized) == 1
        normalized[0] = alias
        if show_test_guide:
            normalized.append("--suite-help")
    return normalized


def _build_parser() -> argparse.ArgumentParser:
    return control_parser.build_parser()


def _handle_build(args: argparse.Namespace) -> int:
    if getattr(args, "build_command", None) is None:
        args.build_parser.print_help()
        return 0
    if args.build_command == "web":
        return build.main(args)
    if args.build_command == "desktop":
        if not profile_runtime.feature_enabled("tauri", ROOT):
            profile = profile_runtime.active_profile(ROOT)
            logger.fail(f"Tauri desktop build is disabled by active profile '{profile.profile_id}'.")
            return 1
        return tauri_build.main(args)
    if args.build_command == "container":
        return container.build(args)
    logger.fail(f"Unknown build target: {args.build_command}")
    return 2


def _handle_test(args: argparse.Namespace) -> int:
    if args.report == "done":
        return run_test.main(args)
    if args.suite is None and not args.suite_help:
        args.test_parser.print_help()
        return 0
    if args.suite is None:
        args.suite = "all"
    return run_test.main(args)


def _handle_console(_args: argparse.Namespace) -> int:
    return console.main()


def _handle_docs(args: argparse.Namespace) -> int:
    if getattr(args, "docs_command", None) is None:
        args.docs_parser.print_help()
        return 0
    if args.docs_command == "check":
        return docs_index.check(args)
    if args.docs_command == "index":
        return docs_index.main(args)
    logger.fail(f"Unknown documentation action: {args.docs_command}")
    return 2


def _handle_container(args: argparse.Namespace) -> int:
    command = getattr(args, "container_command", None)
    if command is None:
        args.container_parser.print_help()
        return 0
    if command == "doctor":
        return container.doctor(args)
    if command == "validate":
        return container.validate(args)
    logger.fail(f"Unknown container action: {command}")
    return 2


def _handlers() -> dict[str, Handler]:
    return {
        "init": profile_cli.main,
        "doctor": doctor.main,
        "install": install.main,
        "console": _handle_console,
        "build": _handle_build,
        "container": _handle_container,
        "config": configuration.main,
        "db": db.main,
        "docs": _handle_docs,
        "quality": quality_control.main,
        "version": release.version,
        "release": release.release,
        "run": run.run_command,
        "stop": stop.main,
        "test": _handle_test,
        "tauri": tauri_control.main,
        "template": template_lifecycle_cli.main,
    }


def main(argv: list[str] | None = None) -> int:
    normalized_argv = _normalize_argv(argv)
    parser = _build_parser()
    args = parser.parse_args(normalized_argv)
    if args.command is None:
        parser.print_help()
        return 0

    args.display_argv = list(sys.argv[1:] if argv is None else argv)
    handler = _handlers().get(args.command)
    if handler is None:
        logger.fail(f"Unknown command: {args.command}")
        logger.info(f"Next step: {parser.prog} --help")
        return 2

    try:
        code = handler(args)
        return 0 if code is None else int(code)
    except KeyboardInterrupt:
        logger.warn("Interrupted by user")
        return 130
    except Exception as exc:  # pragma: no cover
        logger.fail(f"Unhandled error: {exc}")
        for line in traceback.format_exc().strip().splitlines():
            logger.info(line)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
