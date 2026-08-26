from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from tools.inst import docs_index


def _args(script: Path, **overrides) -> argparse.Namespace:
    values = {
        "script": str(script),
        "docs_dir": "docs",
        "dry_run": False,
        "force": False,
        "compact": False,
        "no_backlinks": False,
        "no_readme": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _page(title: str, backlink: str, index: str | None = None) -> str:
    text = f"{docs_index.BACKLINK_START}\n[← Back]({backlink})\n{docs_index.BACKLINK_END}\n# {title}\n"
    if index is not None:
        text += f"\n{docs_index.INDEX_START}\n{index}\n{docs_index.INDEX_END}\n"
    return text


def test_generated_empty_labels_are_translated_but_authored_text_is_preserved(
    tmp_path,
) -> None:
    root = tmp_path / "project"
    docs = root / "docs"
    docs.mkdir(parents=True)
    readme = root / "README.md"
    readme.write_text(
        "<!-- AUTO-GENERATED:docs-index START -->\n"
        "- ⏭️ (keine Markdown-Dateien im Projekt-Root)\n"
        "<!-- AUTO-GENERATED:docs-index END -->\n"
        "Authored phrase: keine Seiten\n",
        encoding="utf-8",
    )
    index = docs / "index.md"
    index.write_text(
        "<!-- AUTO-GENERATED:docs-index START -->\n- ⏭️ (keine Seiten)\n<!-- AUTO-GENERATED:docs-index END -->\n",
        encoding="utf-8",
    )

    assert docs_index.normalize_generated_english(root) == 2
    assert "(no Markdown files in the project root)" in readme.read_text(encoding="utf-8")
    assert "Authored phrase: keine Seiten" in readme.read_text(encoding="utf-8")
    assert "(no pages)" in index.read_text(encoding="utf-8")


def test_index_command_uses_explicit_pygitindex_and_normalizes_output(monkeypatch, tmp_path) -> None:
    root = tmp_path / "project"
    docs = root / "docs"
    docs.mkdir(parents=True)
    script = tmp_path / "PyGitIndex.py"
    script.write_text("# fake", encoding="utf-8")
    (root / "README.md").write_text("# Project\n", encoding="utf-8")
    calls: list[tuple[list[str], Path]] = []

    def fake_run(command: list[str], cwd: Path, check: bool) -> subprocess.CompletedProcess[str]:
        calls.append((command, cwd))
        (docs / "index.md").write_text(
            "<!-- AUTO-GENERATED:docs-index START -->\n- ⏭️ (keine Seiten)\n<!-- AUTO-GENERATED:docs-index END -->\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(docs_index, "ROOT", root)
    monkeypatch.setattr(docs_index.subprocess, "run", fake_run)

    assert docs_index.main(_args(script)) == 0
    assert calls[0][1] == root
    assert calls[0][0][1] == str(script.resolve())
    assert "(no pages)" in (docs / "index.md").read_text(encoding="utf-8")


def test_index_dry_run_does_not_normalize_files(monkeypatch, tmp_path) -> None:
    root = tmp_path / "project"
    (root / "docs").mkdir(parents=True)
    script = tmp_path / "PyGitIndex.py"
    script.write_text("# fake", encoding="utf-8")
    monkeypatch.setattr(docs_index, "ROOT", root)
    monkeypatch.setattr(
        docs_index.subprocess,
        "run",
        lambda command, cwd, check: subprocess.CompletedProcess(command, 0),
    )
    normalized: list[Path] = []
    monkeypatch.setattr(
        docs_index,
        "normalize_generated_english",
        lambda path: normalized.append(path) or 0,
    )

    assert docs_index.main(_args(script, dry_run=True)) == 0
    assert normalized == []


def test_navigation_check_accepts_complete_indices_and_backlinks(monkeypatch, tmp_path) -> None:
    root = tmp_path / "project"
    docs = root / "docs"
    section = docs / "guide"
    section.mkdir(parents=True)
    (root / "README.md").write_text(
        f"# Project\n{docs_index.INDEX_START}\n"
        "[Docs](docs/index.md)\n[Page](docs/page.md)\n[Guide](docs/guide/guide.md)\n"
        f"{docs_index.INDEX_END}\n",
        encoding="utf-8",
    )
    (docs / "index.md").write_text(
        _page("Docs", "../README.md", "[Page](page.md)\n[Guide](guide/guide.md)"),
        encoding="utf-8",
    )
    (docs / "page.md").write_text(_page("Page", "index.md"), encoding="utf-8")
    (section / "guide.md").write_text(
        _page("Guide", "../index.md", "- (no pages)"),
        encoding="utf-8",
    )
    monkeypatch.setattr(docs_index, "ROOT", root)

    assert docs_index.check(argparse.Namespace(docs_dir="docs")) == 0


def test_navigation_check_accepts_url_encoded_unicode_targets(monkeypatch, tmp_path) -> None:
    root = tmp_path / "project"
    docs = root / "docs"
    docs.mkdir(parents=True)
    page_name = "release–process.md"
    encoded_name = "release%E2%80%93process.md"
    (root / "README.md").write_text(
        f"# Project\n{docs_index.INDEX_START}\n"
        f"[Docs](docs/index.md)\n[Release](docs/{encoded_name})\n"
        f"{docs_index.INDEX_END}\n",
        encoding="utf-8",
    )
    (docs / "index.md").write_text(
        _page("Docs", "../README.md", f"[Release]({encoded_name})"),
        encoding="utf-8",
    )
    (docs / page_name).write_text(_page("Release", "index.md"), encoding="utf-8")
    monkeypatch.setattr(docs_index, "ROOT", root)

    assert docs_index.check(argparse.Namespace(docs_dir="docs")) == 0


def test_navigation_check_reports_invalid_url_encoded_targets(monkeypatch, tmp_path) -> None:
    root = tmp_path / "project"
    docs = root / "docs"
    docs.mkdir(parents=True)
    (root / "README.md").write_text(
        f"# Project\n{docs_index.INDEX_START}\n[Docs](docs/index.md)\n[Invalid](docs/%00.md)\n{docs_index.INDEX_END}\n",
        encoding="utf-8",
    )
    (docs / "index.md").write_text(
        _page("Docs", "../README.md", "[Invalid](%00.md)"),
        encoding="utf-8",
    )
    monkeypatch.setattr(docs_index, "ROOT", root)
    messages: list[str] = []
    monkeypatch.setattr(docs_index.logger, "fail", messages.append)

    assert docs_index.check(argparse.Namespace(docs_dir="docs")) == 1
    assert "generated link target is invalid: %00.md" in "\n".join(messages)


def test_navigation_check_accepts_child_pages_in_a_nested_section_index(monkeypatch, tmp_path) -> None:
    root = tmp_path / "project"
    docs = root / "docs"
    section = docs / "section"
    child = section / "active"
    child.mkdir(parents=True)
    (root / "README.md").write_text(
        f"# Project\n{docs_index.INDEX_START}\n[Docs](docs/index.md)\n"
        "[Section](docs/section/section.md)\n"
        f"{docs_index.INDEX_END}\n",
        encoding="utf-8",
    )
    (docs / "index.md").write_text(
        _page("Docs", "../README.md", "[Section](section/section.md)"),
        encoding="utf-8",
    )
    (section / "section.md").write_text(
        _page(
            "Section",
            "../index.md",
            "[Active](active/active.md)\n[ATP](active/ATP-0001.md)",
        ),
        encoding="utf-8",
    )
    (child / "active.md").write_text(_page("Active", "../section.md", "[ATP](ATP-0001.md)"), encoding="utf-8")
    (child / "ATP-0001.md").write_text(_page("ATP", "active.md"), encoding="utf-8")
    monkeypatch.setattr(docs_index, "ROOT", root)

    assert docs_index.check(argparse.Namespace(docs_dir="docs")) == 0


def test_navigation_check_includes_root_markdown_pages(monkeypatch, tmp_path) -> None:
    root = tmp_path / "project"
    docs = root / "docs"
    docs.mkdir(parents=True)
    (root / "README.md").write_text(
        f"# Project\n{docs_index.INDEX_START}\n[Agents](AGENTS.md)\n[Docs](docs/index.md)\n{docs_index.INDEX_END}\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text(_page("Agents", "README.md"), encoding="utf-8")
    (docs / "index.md").write_text(
        _page("Docs", "../README.md", "- (no pages)"),
        encoding="utf-8",
    )
    monkeypatch.setattr(docs_index, "ROOT", root)

    assert docs_index.check(argparse.Namespace(docs_dir="docs")) == 0


def test_navigation_check_reports_missing_and_stale_index_entries(monkeypatch, tmp_path) -> None:
    root = tmp_path / "project"
    docs = root / "docs"
    docs.mkdir(parents=True)
    (root / "README.md").write_text(
        f"# Project\n{docs_index.INDEX_START}\n[Docs](docs/index.md)\n{docs_index.INDEX_END}\n",
        encoding="utf-8",
    )
    (docs / "index.md").write_text(
        _page("Docs", "../README.md", "[Missing](missing.md)"),
        encoding="utf-8",
    )
    (docs / "page.md").write_text(_page("Page", "index.md"), encoding="utf-8")
    monkeypatch.setattr(docs_index, "ROOT", root)
    messages: list[str] = []
    monkeypatch.setattr(docs_index.logger, "fail", messages.append)

    assert docs_index.check(argparse.Namespace(docs_dir="docs")) == 1
    output = "\n".join(messages)
    assert "generated link target does not exist" in output
    assert "index is missing docs/page.md" in output
    assert "index has stale entries docs/missing.md" in output


def test_navigation_check_rejects_wrong_backlink(monkeypatch, tmp_path) -> None:
    root = tmp_path / "project"
    docs = root / "docs"
    docs.mkdir(parents=True)
    (root / "README.md").write_text(
        f"# Project\n{docs_index.INDEX_START}\n[Docs](docs/index.md)\n[Page](docs/page.md)\n{docs_index.INDEX_END}\n",
        encoding="utf-8",
    )
    (docs / "index.md").write_text(
        _page("Docs", "../README.md", "[Page](page.md)"),
        encoding="utf-8",
    )
    (docs / "page.md").write_text(_page("Page", "../README.md"), encoding="utf-8")
    monkeypatch.setattr(docs_index, "ROOT", root)
    messages: list[str] = []
    monkeypatch.setattr(docs_index.logger, "fail", messages.append)

    assert docs_index.check(argparse.Namespace(docs_dir="docs")) == 1
    assert "backlink must target docs/index.md" in "\n".join(messages)


def test_navigation_check_reports_escaping_generated_link_without_traceback(monkeypatch, tmp_path) -> None:
    root = tmp_path / "project"
    docs = root / "docs"
    docs.mkdir(parents=True)
    (root / "README.md").write_text(
        f"# Project\n{docs_index.INDEX_START}\n"
        "[Docs](docs/index.md)\n[Escape](../outside.md)\n"
        f"{docs_index.INDEX_END}\n",
        encoding="utf-8",
    )
    (docs / "index.md").write_text(
        _page("Docs", "../README.md", "- (no pages)"),
        encoding="utf-8",
    )
    monkeypatch.setattr(docs_index, "ROOT", root)
    messages: list[str] = []
    monkeypatch.setattr(docs_index.logger, "fail", messages.append)

    assert docs_index.check(argparse.Namespace(docs_dir="docs")) == 1
    assert "generated link escapes the project root" in "\n".join(messages)
