from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from tools.template_lifecycle.manifest import create_manifest, write_manifest
from tools.template_lifecycle.migrations import (
    Migration,
    MigrationCondition,
    MigrationOperation,
    MigrationRange,
)
from tools.template_lifecycle.model import (
    STATE_SCHEMA_VERSION,
    TEMPLATE_ID,
    BaselineState,
    LifecycleState,
    ProductIdentity,
    SelectionState,
    SourceState,
)
from tools.template_lifecycle.scaffold import ScaffoldRequest, reconstruct_scaffold
from tools.template_lifecycle.source import resolve_ref, resolve_source
from tools.template_lifecycle.state import BASELINE_RELATIVE_PATH, write_state

GENERATOR = r"""from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("command")
parser.add_argument("--profile", required=True)
parser.add_argument("--target-dir", required=True)
parser.add_argument("--name", required=True)
parser.add_argument("--slug", required=True)
parser.add_argument("--identifier", required=True)
parser.add_argument("--with", dest="features", action="append", default=[])
args = parser.parse_args()
if args.command != "init":
    raise SystemExit(2)
root = Path(__file__).resolve().parents[1]
target = Path(args.target_dir)
shutil.copytree(root / "scaffold", target)
template_version = (root / "VERSION").read_text(encoding="utf-8").strip()


def write(relative, content):
    path = target / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(relative, payload):
    write(relative, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


frontend_name = f"{args.slug}-frontend"
write_json(
    "frontend/package.json",
    {"name": frontend_name, "version": template_version},
)
write_json(
    "frontend/package-lock.json",
    {
        "name": frontend_name,
        "version": template_version,
        "lockfileVersion": 3,
        "packages": {"": {"name": frontend_name, "version": template_version}},
    },
)
write("frontend/index.html", f"<title>{args.name}</title>\n")
write("frontend/src/main.ts", f'export const PRODUCT_NAME = "{args.name}";\n')
write("backend/app/api/health.py", f'SERVICE_NAME = "{args.slug}-backend"\n')
write("tools/inst/build.py", f'WEB_ARTIFACT = "{args.slug}-web.zip"\n')
write("deployment/compose.yaml", f'name: {args.slug}\n')
write_json(
    "src-tauri/tauri.conf.json",
    {
        "productName": args.name,
        "version": template_version,
        "identifier": args.identifier,
        "mainBinaryName": args.slug,
        "app": {"windows": [{"label": "main", "title": args.name}]},
    },
)
write(
    "src-tauri/Cargo.toml",
    f'[package]\nname = "{args.slug}"\nversion = "{template_version}"\n',
)
write(
    "src-tauri/Cargo.lock",
    f'[[package]]\nname = "{args.slug}"\nversion = "{template_version}"\n',
)
write(
    "src-tauri/app-icon.svg",
    f'<svg xmlns="http://www.w3.org/2000/svg"><title>{args.name}</title></svg>\n',
)
features = ["frontend"]
optional = list(dict.fromkeys(args.features))
if "postgres" in optional:
    features.extend(["backend", "database", "postgres"])
(target / "project-profile.toml").write_text(
    "\n".join(
        [
            "schema_version = 1",
            f'id = "{args.profile}"',
            f'name = "{args.profile}"',
            'description = "Synthetic lifecycle fixture"',
            "optional_features = [" + ", ".join(repr(item) for item in optional) + "]",
            "features = [" + ", ".join(repr(item) for item in features) + "]",
            "",
        ]
    ).replace("'", '"'),
    encoding="utf-8",
)
"""

FEATURES = """schema_version = 1

[core]
paths = ["VERSION", "project-profile.toml", "profiles", "managed.txt"]

[features.frontend]
name = "Frontend"
description = "Synthetic frontend"
paths = []
"""

PROFILE = """schema_version = 1
id = "web-only"
order = 10
name = "Web only"
description = "Synthetic web profile"
features = ["frontend"]
"""


@dataclass(frozen=True, slots=True)
class LifecycleFixture:
    source_root: Path
    v1: str
    v2: str
    identity: ProductIdentity
    backend_service: str
    product_version: str
    request: ScaffoldRequest
    migration: Migration
    renamed_from: str
    renamed_to: str

    def managed_product(
        self,
        destination: Path,
        *,
        conflict: bool = False,
        rename_edit: bool = False,
    ) -> Path:
        source = resolve_source(self.source_root)
        reconstruct_scaffold(source, resolve_ref(source, self.v1), self.request, destination)
        manifest = create_manifest(destination)
        state = LifecycleState(
            STATE_SCHEMA_VERSION,
            "product",
            TEMPLATE_ID,
            "generated",
            False,
            SourceState(source.origin, "1.0.0", self.v1, self.v1, manifest.digest),
            SelectionState("web-only", (), ("frontend",)),
            self.identity,
            BaselineState(BASELINE_RELATIVE_PATH, manifest.digest, ()),
        )
        write_manifest(destination / BASELINE_RELATIVE_PATH, manifest)
        write_state(destination, state)
        _init_git(destination)
        _commit(destination, "generated product")
        lines = ["alpha", "middle", "omega-local" if conflict else "omega"]
        if not conflict:
            lines[0] = "alpha-local"
        (destination / "managed.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        if rename_edit:
            (destination / self.renamed_from).write_text(
                "product customization\nunchanged separator\nshared setting\n",
                encoding="utf-8",
            )
        (destination / "product-owned.txt").write_text("keep product data\n", encoding="utf-8")
        _commit(destination, "product changes")
        return destination

    def legacy_product(self, destination: Path) -> Path:
        source = resolve_source(self.source_root)
        reconstruct_scaffold(source, resolve_ref(source, self.v1), self.request, destination)
        _init_git(destination)
        _commit(destination, "legacy product")
        return destination


@pytest.fixture
def lifecycle_fixture(tmp_path: Path) -> LifecycleFixture:
    root, scaffold = _template_root(tmp_path)
    v1, renamed_from, renamed_to = _write_v1(root, scaffold)
    v2 = _write_v2(root, scaffold, renamed_from, renamed_to)
    identity = ProductIdentity(
        "Fixture Product",
        "fixture-product",
        "com.example.fixture",
        "fixture-product",
    )
    product_version = "0.7.0"
    return LifecycleFixture(
        root,
        v1,
        v2,
        identity,
        "fixture-product-backend",
        product_version,
        ScaffoldRequest("web-only", (), identity, product_version),
        _rename_migration(v1, v2, renamed_from, renamed_to),
        renamed_from,
        renamed_to,
    )


def _template_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "template source with spaces"
    scaffold = root / "scaffold"
    (root / "tools").mkdir(parents=True)
    (scaffold / "profiles").mkdir(parents=True)
    _init_git(root)
    _git(
        root,
        "remote",
        "add",
        "origin",
        "https://github.com/kleiveist/Template-Projekte.git",
    )
    (root / "tools/control.py").write_text(GENERATOR, encoding="utf-8")
    return root, scaffold


def _write_v1(root: Path, scaffold: Path) -> tuple[str, str, str]:
    _write_template_version(scaffold, root, "1.0.0")
    (scaffold / "profiles/features.toml").write_text(FEATURES, encoding="utf-8")
    (scaffold / "profiles/web-only.toml").write_text(PROFILE, encoding="utf-8")
    (scaffold / "project-profile.toml").write_text(PROFILE, encoding="utf-8")
    (scaffold / "managed.txt").write_text("alpha\nmiddle\nomega\n", encoding="utf-8")
    renamed_from = "legacy-layout.txt"
    renamed_to = "current-layout.txt"
    (scaffold / renamed_from).write_text(
        "product setting\nunchanged separator\nshared setting\n",
        encoding="utf-8",
    )
    (scaffold / "obsolete.txt").write_text("remove in v2\n", encoding="utf-8")
    (scaffold / ".gitignore").write_text("/.report/\n", encoding="utf-8")
    executable = scaffold / "executable.sh"
    executable.write_text("#!/bin/sh\nprintf 'fixture\\n'\n", encoding="utf-8")
    os.chmod(executable, 0o755)
    return _commit(root, "template v1"), renamed_from, renamed_to


def _write_v2(root: Path, scaffold: Path, renamed_from: str, renamed_to: str) -> str:
    _write_template_version(scaffold, root, "1.1.0")
    (scaffold / "managed.txt").write_text("alpha\nmiddle\nomega-template\n", encoding="utf-8")
    (scaffold / renamed_from).unlink()
    (scaffold / renamed_to).write_text(
        "product setting\nunchanged separator\nshared template update\n",
        encoding="utf-8",
    )
    (scaffold / "obsolete.txt").unlink()
    (scaffold / "template-added.txt").write_text("new template file\n", encoding="utf-8")
    return _commit(root, "template v2")


def _rename_migration(v1: str, v2: str, renamed_from: str, renamed_to: str) -> Migration:
    return Migration(
        migration_id="rename-legacy-layout",
        description="Move the managed layout file to its v2 path.",
        order=10,
        applies=MigrationRange(
            source_commits=(v1,),
            target_commit=v2,
        ),
        operations=(
            MigrationOperation(
                kind="move_path",
                source=renamed_from,
                destination=renamed_to,
            ),
        ),
        preconditions=(
            MigrationCondition(kind="path_exists", path=renamed_from),
            MigrationCondition(kind="path_missing", path=renamed_to),
        ),
        postconditions=(
            MigrationCondition(kind="path_missing", path=renamed_from),
            MigrationCondition(kind="path_exists", path=renamed_to),
        ),
    )


def _write_template_version(scaffold: Path, root: Path, version: str) -> None:
    (root / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (scaffold / "VERSION").write_text(f"{version}\n", encoding="utf-8")


def _init_git(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "--quiet", "--initial-branch=main")
    _git(root, "config", "user.name", "Lifecycle Tests")
    _git(root, "config", "user.email", "lifecycle@example.invalid")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "config", "core.fileMode", "true")


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()
