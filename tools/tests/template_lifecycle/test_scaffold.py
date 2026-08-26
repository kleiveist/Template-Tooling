from __future__ import annotations

import json
import subprocess
from pathlib import Path

import tomllib

from tools.profiles.generator import ProjectIdentity as GeneratorIdentity
from tools.profiles.generator import ScaffoldPlan
from tools.profiles.model import ProjectProfile
from tools.template_lifecycle.manifest import load_manifest
from tools.template_lifecycle.model import ProductIdentity
from tools.template_lifecycle.scaffold import (
    ScaffoldRequest,
    finalize_generated_project,
    reconstruct_scaffold,
)
from tools.template_lifecycle.source import resolve_ref, resolve_source
from tools.template_lifecycle.state import load_state

FAKE_GENERATOR = r"""from __future__ import annotations

import argparse
import json
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
(target / "frontend").mkdir(parents=True)
(target / "src-tauri").mkdir(parents=True)
(target / ".template").mkdir(parents=True)
version = (root / "VERSION").read_text(encoding="utf-8").strip()
(target / "VERSION").write_text(version + "\n", encoding="utf-8")
(target / "identity.txt").write_text(
    f"{args.name}|{args.slug}|{args.identifier}|{args.profile}|{','.join(args.features)}\n",
    encoding="utf-8",
)
(target / ".template" / "generated-by-fixture").write_text("remove me\n", encoding="utf-8")
(target / "frontend" / "package.json").write_text(
    json.dumps({"name": args.slug + "-frontend", "version": version}, indent=2) + "\n",
    encoding="utf-8",
)
(target / "frontend" / "package-lock.json").write_text(
    json.dumps(
        {
            "name": args.slug + "-frontend",
            "version": version,
            "packages": {"": {"name": args.slug + "-frontend", "version": version}},
        },
        indent=2,
    ) + "\n",
    encoding="utf-8",
)
(target / "src-tauri" / "tauri.conf.json").write_text(
    json.dumps(
        {
            "productName": args.name,
            "identifier": args.identifier,
            "mainBinaryName": args.slug,
            "version": version,
            "app": {"windows": [{"label": "main", "title": args.name}]},
        },
        indent=2,
    ) + "\n",
    encoding="utf-8",
)
(target / "src-tauri" / "Cargo.toml").write_text(
    f'[package]\nname = "{args.slug}"\nversion = "{version}"\n',
    encoding="utf-8",
)
(target / "src-tauri" / "Cargo.lock").write_text(
    f'version = 3\n\n[[package]]\nname = "{args.slug}"\nversion = "{version}"\n',
    encoding="utf-8",
)
"""


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


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _template_repository(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "source with spaces"
    (root / "tools").mkdir(parents=True)
    _git(root, "init", "--quiet", "--initial-branch=main")
    _git(root, "config", "user.name", "Lifecycle Tests")
    _git(root, "config", "user.email", "lifecycle@example.invalid")
    _git(root, "config", "core.autocrlf", "false")
    _git(
        root,
        "remote",
        "add",
        "origin",
        "https://github.com/kleiveist/Template-Projekte.git",
    )
    (root / "tools" / "control.py").write_text(FAKE_GENERATOR, encoding="utf-8")
    (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    first = _commit(root, "template v1")
    (root / "VERSION").write_text("1.1.0\n", encoding="utf-8")
    second = _commit(root, "template v2")
    return root, first, second


def _request() -> ScaffoldRequest:
    return ScaffoldRequest(
        profile="desktop-cloud",
        optional_features=("postgres",),
        identity=ProductIdentity(
            name="Customer Ü App",
            slug="customer-app",
            identifier="com.customer.app",
            binary="customer-binary",
        ),
        product_version="0.7.0",
    )


def test_reconstruction_uses_exact_commit_and_preserves_product_metadata(
    tmp_path: Path,
) -> None:
    root, first, second = _template_repository(tmp_path)
    source = resolve_source(root)
    destination = tmp_path / "reconstructed project with spaces"

    reconstructed = reconstruct_scaffold(source, resolve_ref(source, first), _request(), destination)

    assert reconstructed == destination.resolve()
    assert (destination / "VERSION").read_text(encoding="utf-8") == "0.7.0\n"
    assert not (destination / ".template").exists()
    assert (destination / "identity.txt").read_text(encoding="utf-8") == (
        "Customer Ü App|customer-app|com.customer.app|desktop-cloud|postgres\n"
    )

    package = json.loads((destination / "frontend" / "package.json").read_text(encoding="utf-8"))
    package_lock = json.loads((destination / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    tauri = json.loads((destination / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    cargo = tomllib.loads((destination / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8"))
    cargo_lock = tomllib.loads((destination / "src-tauri" / "Cargo.lock").read_text(encoding="utf-8"))
    locked_root = next(item for item in cargo_lock["package"] if item["name"] == "customer-binary")

    assert package["version"] == "0.7.0"
    assert package_lock["version"] == "0.7.0"
    assert package_lock["packages"][""]["version"] == "0.7.0"
    assert tauri["productName"] == "Customer Ü App"
    assert tauri["identifier"] == "com.customer.app"
    assert tauri["mainBinaryName"] == "customer-binary"
    assert tauri["version"] == "0.7.0"
    assert cargo["package"] == {"name": "customer-binary", "version": "0.7.0"}
    assert locked_root["version"] == "0.7.0"
    assert _git(root, "rev-parse", "HEAD") == second
    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert _git(root, "status", "--porcelain", "--untracked-files=all") == ""


def test_finalize_generated_project_writes_deterministic_clean_provenance(
    tmp_path: Path,
) -> None:
    root, _first, second = _template_repository(tmp_path)
    target = tmp_path / "generated product"
    target.mkdir()
    (target / "VERSION").write_text("0.4.0\n", encoding="utf-8")
    (target / "product.txt").write_text("generated bytes\n", encoding="utf-8")
    plan = _plan(root, target)

    state = finalize_generated_project(plan)
    loaded_state = load_state(target)
    manifest = load_manifest(target / ".template" / "baseline.json")
    first_manifest_bytes = (target / ".template" / "baseline.json").read_bytes()

    assert state == loaded_state
    assert state.provenance == "generated"
    assert state.source_dirty is False
    assert state.source.commit == second
    assert state.source.ref == second
    assert state.source.version == "1.1.0"
    assert state.selection.profile == "desktop-local"
    assert state.selection.optional_features == ()
    assert state.selection.resolved_features == ("frontend", "tauri")
    assert state.identity.binary == "customer-binary"
    assert state.baseline.digest == manifest.digest
    assert ".template/state.toml" not in manifest.by_path()
    assert ".template/baseline.json" not in manifest.by_path()

    finalize_generated_project(plan)
    assert (target / ".template" / "baseline.json").read_bytes() == first_manifest_bytes


def test_finalize_marks_dirty_template_as_non_reproducible_working_tree(
    tmp_path: Path,
) -> None:
    root, _first, second = _template_repository(tmp_path)
    (root / "uncommitted-template-file.txt").write_text("dirty\n", encoding="utf-8")
    target = tmp_path / "dirty generated product"
    target.mkdir()
    (target / "VERSION").write_text("0.5.0\n", encoding="utf-8")

    state = finalize_generated_project(_plan(root, target))

    assert state.provenance == "working-tree"
    assert state.source_dirty is True
    assert state.source.commit == second


def _plan(root: Path, target: Path) -> ScaffoldPlan:
    profile = ProjectProfile(
        schema_version=1,
        profile_id="desktop-local",
        name="Desktop local",
        description="Fixture profile",
        features=("frontend", "tauri"),
        optional_features=(),
    )
    return ScaffoldPlan(
        project_root=root,
        target_dir=target,
        profile=profile,
        paths=(),
        env_example="",
        identity=GeneratorIdentity(
            name="Customer App",
            slug="customer-app",
            identifier="com.customer.app",
            binary="customer-binary",
        ),
    )
