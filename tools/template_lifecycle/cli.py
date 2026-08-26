from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools import logger
from tools.template_lifecycle import service
from tools.template_lifecycle.model import LifecycleError, LifecycleUsageError

ROOT = Path(__file__).resolve().parents[2]


def configure_parser(
    subparsers: argparse._SubParsersAction,
    *,
    formatter_class: type[argparse.HelpFormatter],
) -> None:
    parser = subparsers.add_parser(
        "template",
        help="audit, adopt, plan, update, and verify template provenance",
        description="Safe local template lifecycle commands. A bare command shows this map and changes nothing.",
        formatter_class=formatter_class,
    )
    parser.set_defaults(template_parser=parser)
    actions = parser.add_subparsers(dest="template_command", title="template actions", metavar="<action>")
    _status_parser(actions, formatter_class)
    _audit_parser(actions, formatter_class)
    _adopt_parser(actions, formatter_class)
    _plan_parser(actions, formatter_class)
    _update_parser(actions, formatter_class)
    _verify_parser(actions, formatter_class)
    parser.epilog = """examples:
  python tools/control.py template status
  python tools/control.py template audit --target-dir ../Product --source-dir . --to-ref <trusted-sha> ...
  python tools/control.py template plan --source-dir ../Template-Projekte --to-ref <trusted-tag-or-sha>
  python tools/control.py template update --source-dir ../Template-Projekte --to-ref <trusted-tag-or-sha> --apply
  python tools/control.py template verify"""


def main(args: argparse.Namespace) -> int:
    command = getattr(args, "template_command", None)
    if command is None:
        args.template_parser.print_help()
        return 0
    try:
        output = _dispatch(command, args)
    except LifecycleUsageError as exc:
        _print_error(args, str(exc), exit_code=2)
        return 2
    except (LifecycleError, OSError, ValueError) as exc:
        _print_error(args, str(exc), exit_code=1)
        return 1
    if getattr(args, "output_format", "text") == "json":
        print(json.dumps(output.payload, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        for line in output.lines:
            print(line)
    return output.exit_code


def _dispatch(command: str, args: argparse.Namespace) -> service.CommandOutput:
    common = service.CommonOptions(
        executor_root=ROOT,
        target_dir=_path(getattr(args, "target_dir", None)),
        source_dir=_path(getattr(args, "source_dir", None)),
        report_dir=getattr(args, "report_dir", None),
    )
    if command == "status":
        return service.status(common, to_ref=getattr(args, "to_ref", None))
    if command == "verify":
        return service.verify(common)
    if command in {"audit", "adopt"}:
        identity = service.AdoptionOptions(
            profile=args.profile,
            optional_features=_features(args.optional_features),
            name=args.project_name,
            slug=args.project_slug,
            identifier=args.identifier,
            binary=args.binary or args.project_slug,
        )
        if command == "audit":
            return service.audit(common, to_ref=args.to_ref, adoption=identity)
        return service.adopt(
            common,
            baseline_ref=args.baseline_ref,
            adoption=identity,
            apply=bool(args.apply),
        )
    if command == "plan":
        return service.plan(common, to_ref=args.to_ref)
    if command == "update":
        return service.update(
            common,
            to_ref=args.to_ref,
            apply=bool(args.apply),
            allow_architecture_change=bool(args.allow_architecture_change),
        )
    raise LifecycleUsageError(f"Unknown template action: {command}.")


def _status_parser(actions: argparse._SubParsersAction, formatter: type[argparse.HelpFormatter]) -> None:
    parser = actions.add_parser(
        "status",
        help="show installed provenance and local drift",
        formatter_class=formatter,
    )
    _common(parser, source=True, target_ref=False)
    parser.add_argument("--to-ref", metavar="REF", help="optional target ref to resolve and compare")


def _audit_parser(actions: argparse._SubParsersAction, formatter: type[argparse.HelpFormatter]) -> None:
    parser = actions.add_parser(
        "audit",
        help="compare an unmanaged product with a target scaffold",
        formatter_class=formatter,
    )
    _common(parser, source=True, target_ref=True)
    _identity(parser)


def _adopt_parser(actions: argparse._SubParsersAction, formatter: type[argparse.HelpFormatter]) -> None:
    parser = actions.add_parser(
        "adopt",
        help="record a reconstructed baseline without changing product code",
        formatter_class=formatter,
    )
    _common(parser, source=True, target_ref=False)
    parser.add_argument(
        "--baseline-ref",
        required=True,
        metavar="REF",
        help="exact baseline tag, branch, or commit to resolve",
    )
    _identity(parser)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write only .template/state.toml and baseline.json",
    )


def _plan_parser(actions: argparse._SubParsersAction, formatter: type[argparse.HelpFormatter]) -> None:
    parser = actions.add_parser(
        "plan",
        help="build a deterministic BASE/LOCAL/INCOMING update plan",
        formatter_class=formatter,
    )
    _common(parser, source=True, target_ref=True)


def _update_parser(actions: argparse._SubParsersAction, formatter: type[argparse.HelpFormatter]) -> None:
    parser = actions.add_parser(
        "update",
        help="preview or transactionally apply a conflict-free plan",
        formatter_class=formatter,
    )
    _common(parser, source=True, target_ref=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply the resolved plan to a clean product repository",
    )
    parser.add_argument(
        "--allow-architecture-change",
        action="store_true",
        help="confirm an architecture change already covered by an applicable migration",
    )


def _verify_parser(actions: argparse._SubParsersAction, formatter: type[argparse.HelpFormatter]) -> None:
    parser = actions.add_parser(
        "verify",
        help="verify lifecycle, identity, profile, and version integrity",
        formatter_class=formatter,
    )
    _common(parser, source=False, target_ref=False)


def _common(parser: argparse.ArgumentParser, *, source: bool, target_ref: bool) -> None:
    parser.add_argument(
        "--target-dir",
        metavar="PATH",
        help="product root (default: current project root)",
    )
    if source:
        parser.add_argument("--source-dir", metavar="PATH", help="local canonical template checkout")
    if target_ref:
        parser.add_argument(
            "--to-ref",
            required=True,
            metavar="REF",
            help="trusted target ref resolved to a full commit",
        )
    parser.add_argument("--format", dest="output_format", choices=("text", "json"), default="text")
    parser.add_argument("--report-dir", metavar="PATH", help="optional report base directory")


def _identity(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", required=True, help="template profile id")
    parser.add_argument(
        "--with",
        dest="optional_features",
        action="append",
        default=[],
        metavar="FEATURE",
    )
    parser.add_argument("--name", dest="project_name", required=True, help="product display name")
    parser.add_argument("--slug", dest="project_slug", required=True, help="product kebab-case slug")
    parser.add_argument("--identifier", required=True, help="product reverse-domain identifier")
    parser.add_argument("--binary", help="product binary name (default: slug)")


def _features(values: list[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        for item in value.split(","):
            feature = item.strip()
            if feature and feature not in result:
                result.append(feature)
    return tuple(result)


def _path(value: str | None) -> Path | None:
    return None if value is None else Path(value).expanduser()


def _print_error(args: argparse.Namespace, message: str, *, exit_code: int) -> None:
    if getattr(args, "output_format", "text") == "json":
        print(
            json.dumps(
                {"schema_version": 1, "status": "ERROR", "exit_code": exit_code, "error": message},
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
        )
    else:
        logger.fail(message)
