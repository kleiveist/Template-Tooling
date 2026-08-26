from __future__ import annotations

import re
import tomllib
from pathlib import Path

from tools import control
from tools.profiles.loader import load_catalog


ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def test_first_time_quickstart_precedes_generated_navigation() -> None:
    content = _readme()

    assert content.startswith("# Full-Stack Project Template\n")
    assert content.index("## Start here") < content.index("<!-- AUTO-GENERATED:docs-index START -->")
    assert "Product development (normal path)" in content
    assert "Template maintenance" in content


def test_quickstart_commands_and_examples_match_the_cli_parser() -> None:
    parser = control._build_parser()
    examples = [
        ["doctor"],
        ["init"],
        [
            "init",
            "--profile",
            "web-cloud",
            "--name",
            "MyProject",
            "--target-dir",
            "../MyProject",
        ],
        [
            "init",
            "--profile",
            "desktop-cloud",
            "--name",
            "MyProject",
            "--identifier",
            "com.example.myproject",
            "--target-dir",
            "../MyProject",
        ],
        ["init", "--profile", "web-cloud", "--with", "postgres"],
        ["install"],
        ["run"],
        ["test", "--suite", "all"],
        ["console"],
        ["config", "doctor"],
        ["tauri", "doctor"],
        ["tauri", "run", "--foreground"],
    ]

    for example in examples:
        parser.parse_args(example)


def test_documented_profile_choices_match_the_profile_catalog() -> None:
    # Reduced generated projects retain the complete profile catalog while
    # intentionally omitting source paths owned by disabled features.
    catalog = load_catalog(ROOT / "profiles", validate_paths=False)
    content = _readme()
    documented = set(
        re.findall(
            r"^\| .* \| `(web-only|web-cloud|desktop-local|desktop-cloud|full-platform)` \|$",
            content,
            flags=re.MULTILINE,
        )
    )

    assert documented == set(catalog.profiles)


def test_documented_default_endpoints_match_the_environment_contract() -> None:
    contract = tomllib.loads((ROOT / "config" / "environment.toml").read_text(encoding="utf-8"))
    defaults = {item["name"]: item.get("default") for item in contract["variables"]}
    content = _readme()

    frontend = f"http://{defaults['FRONTEND_HOST']}:{defaults['FRONTEND_PORT']}"
    backend = f"http://{defaults['BACKEND_HOST']}:{defaults['BACKEND_PORT']}"
    assert frontend in content
    assert backend in content
    assert f"{backend}/api/health" in content
    assert f"{backend}/api/ready" in content


def test_all_local_readme_links_resolve() -> None:
    targets = re.findall(r"\[[^\]]+\]\(([^)]+)\)", _readme())

    for target in targets:
        if target.startswith(("http://", "https://", "#")):
            continue
        relative_path = target.split("#", 1)[0]
        assert (ROOT / relative_path).exists(), f"README link does not exist: {target}"
