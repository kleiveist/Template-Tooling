"""Stable service facade for portable integration commands.

Phase 3 establishes the command boundary before profile adapters are wired in.
Until that orchestration is installed, every command fails closed without
reading or mutating a target project.
"""

from __future__ import annotations

import json

_NOT_READY = "Profile adapter orchestration is not configured yet."


def run_check(*, json_output: bool = False) -> int:
    return _unavailable("integrate-check", json_output=json_output)


def run_full_fix(*, json_output: bool = False) -> int:
    return _unavailable("integrate-full-fix", json_output=json_output)


def run_migrate(*, check_only: bool = False, json_output: bool = False) -> int:
    action = "tooling-migrate-check" if check_only else "tooling-migrate"
    return _unavailable(action, json_output=json_output)


def run_verify(*, json_output: bool = False) -> int:
    return _unavailable("tooling-verify", json_output=json_output)


def run_export(*, output: str | None = None) -> int:
    del output
    return _unavailable("tooling-export", json_output=False)


def _unavailable(action: str, *, json_output: bool) -> int:
    payload = {"action": action, "status": "NOT_READY", "message": _NOT_READY}
    if json_output:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"Tooling integration: {payload['status']}")
        print(_NOT_READY)
    return 2
