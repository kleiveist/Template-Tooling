from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

CASE_STUDY = Path(__file__).resolve().parents[1]
LANGUAGES = ("de", "en")
GENERATED_ENDINGS = (
    ".aux",
    ".fdb_latexmk",
    ".fls",
    ".log",
    ".out",
    ".pdf",
    ".synctex",
    ".synctex.gz",
    ".toc",
)


def _generated_artifacts(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name.casefold().endswith(GENERATED_ENDINGS)
    )


def _run_build(
    script: Path, output: Path, compiler: str
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONHASHSEED"] = "0"
    return subprocess.run(
        (
            sys.executable,
            str(script),
            "--language",
            "all",
            "--output-dir",
            str(output),
            "--compiler",
            compiler,
        ),
        cwd=script.parent,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=600,
        shell=False,
    )


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "portable_case_study_build",
        CASE_STUDY / "build.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_case_study_has_bilingual_sources_portable_assets_and_clean_tree() -> None:
    expected = {
        "case-study.md",
        "build.py",
        "assets/architecture-pipeline.tex",
        "assets/ownership-boundary.tex",
        "source/de/main.tex",
        "source/en/main.tex",
        "tests/test_reproducible_build.py",
    }
    observed = {
        path.relative_to(CASE_STUDY).as_posix()
        for path in CASE_STUDY.rglob("*")
        if path.is_file() and path.name != ".gitkeep"
    }

    assert expected <= observed
    assert not list(CASE_STUDY.rglob(".gitkeep"))
    assert _generated_artifacts(CASE_STUDY) == []

    overview = (CASE_STUDY / "case-study.md").read_text(encoding="utf-8")
    assert overview.count("<!-- AUTO-GENERATED:backlink START -->") == 1
    assert overview.count("<!-- AUTO-GENERATED:backlink END -->") == 1
    assert overview.count("<!-- AUTO-GENERATED:docs-index START -->") == 1
    assert overview.count("<!-- AUTO-GENERATED:docs-index END -->") == 1
    assert "(../index.md)" in overview

    for language in LANGUAGES:
        source = (CASE_STUDY / "source" / language / "main.tex").read_text(
            encoding="utf-8"
        )
        assert "portable" in source.casefold()
        assert "architecture-pipeline.tex" in source
        assert "ownership-boundary.tex" in source
        assert "SOURCE_DATE_EPOCH" in source
        assert "\\end{document}" in source

    assets = sorted((CASE_STUDY / "assets").glob("*.tex"))
    assert len(assets) >= 2
    for asset in assets:
        text = asset.read_text(encoding="utf-8")
        assert "\\begin{picture}" in text
        assert "\\end{picture}" in text


def test_builder_stages_externally_and_publishes_only_final_pdfs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = _load_builder()
    monkeypatch.setattr(builder, "_resolve_compiler", lambda _explicit: "pdflatex")

    def fake_pdflatex(
        _compiler: str,
        source: Path,
        work: Path,
        *,
        timeout: int,
    ) -> None:
        assert timeout == builder.DEFAULT_TIMEOUT_SECONDS
        language = source.parent.name.encode("ascii")
        (work / "main.aux").write_text("temporary\n", encoding="utf-8")
        (work / "main.log").write_text("temporary\n", encoding="utf-8")
        (work / "main.pdf").write_bytes(b"%PDF-1.4\n" + language * 1024 + b"\n%%EOF\n")

    monkeypatch.setattr(builder, "_run_pdflatex", fake_pdflatex)
    output = tmp_path / "published"
    targets = builder.build(LANGUAGES, output)

    assert {target.name for target in targets} == {
        "portable-tooling-case-study-de.pdf",
        "portable-tooling-case-study-en.pdf",
    }
    assert {path.name for path in output.iterdir()} == {
        "portable-tooling-case-study-de.pdf",
        "portable-tooling-case-study-en.pdf",
    }
    assert _generated_artifacts(CASE_STUDY) == []

    environment = builder._build_environment(tmp_path / "environment")
    assert environment["SOURCE_DATE_EPOCH"] == "1704067200"
    assert environment["FORCE_SOURCE_DATE"] == "1"
    assert environment["TZ"] == "UTC"
    assert environment["LC_ALL"] == "C"

    with pytest.raises(builder.BuildError, match="outside the case-study source tree"):
        builder.build(("de",), CASE_STUDY / "generated")


def test_bilingual_publish_failure_restores_existing_editions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = _load_builder()
    monkeypatch.setattr(builder, "_resolve_compiler", lambda _explicit: "pdflatex")
    monkeypatch.setattr(
        builder,
        "_compile_language",
        lambda language, _compiler, _timeout: f"new-{language}".encode(),
    )
    output = tmp_path / "published"
    output.mkdir()
    targets = {
        language: output / f"portable-tooling-case-study-{language}.pdf"
        for language in LANGUAGES
    }
    originals = {language: f"old-{language}".encode() for language in LANGUAGES}
    for language, target in targets.items():
        target.write_bytes(originals[language])

    publish = builder._publish_pdf

    def fail_english(target: Path, payload: bytes) -> None:
        if target == targets["en"]:
            raise OSError("injected second publish failure")
        publish(target, payload)

    monkeypatch.setattr(builder, "_publish_pdf", fail_english)

    with pytest.raises(builder.BuildError, match="restored prior outputs"):
        builder.build(LANGUAGES, output)

    assert {language: target.read_bytes() for language, target in targets.items()} == (
        originals
    )
    assert {path.name for path in output.iterdir()} == {
        target.name for target in targets.values()
    }


def test_bilingual_pdfs_are_byte_reproducible_from_temporary_copy(
    tmp_path: Path,
) -> None:
    compiler = shutil.which("pdflatex")
    if compiler is None:
        pytest.skip("pdflatex is unavailable; reproducible PDF build not exercised")

    copied = tmp_path / "copied-case-study"
    shutil.copytree(
        CASE_STUDY,
        copied,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )
    assert _generated_artifacts(copied) == []

    output_one = tmp_path / "build-one"
    output_two = tmp_path / "build-two"
    first = _run_build(copied / "build.py", output_one, compiler)
    second = _run_build(copied / "build.py", output_two, compiler)
    assert first.returncode == 0, first.stdout + "\n" + first.stderr
    assert second.returncode == 0, second.stdout + "\n" + second.stderr

    first_hashes: dict[str, str] = {}
    second_hashes: dict[str, str] = {}
    for language in LANGUAGES:
        name = f"portable-tooling-case-study-{language}.pdf"
        first_pdf = output_one / name
        second_pdf = output_two / name
        for pdf in (first_pdf, second_pdf):
            payload = pdf.read_bytes()
            assert len(payload) >= 1024
            assert payload.startswith(b"%PDF-")
            assert b"%%EOF" in payload[-1024:]
        first_hashes[language] = hashlib.sha256(first_pdf.read_bytes()).hexdigest()
        second_hashes[language] = hashlib.sha256(second_pdf.read_bytes()).hexdigest()

    assert first_hashes == second_hashes
    assert first_hashes["de"] != first_hashes["en"]
    assert {path.name for path in output_one.iterdir()} == {
        "portable-tooling-case-study-de.pdf",
        "portable-tooling-case-study-en.pdf",
    }
    assert {path.name for path in output_two.iterdir()} == {
        "portable-tooling-case-study-de.pdf",
        "portable-tooling-case-study-en.pdf",
    }
    assert _generated_artifacts(copied) == []
