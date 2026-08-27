"""Render every PDF page and perform portable structural checks."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

try:  # Supports both ``python scripts/render_check.py`` and package imports in tests.
    from ._shared import CaseStudyError, executable
except ImportError:  # pragma: no cover - command-line entry point.
    from _shared import CaseStudyError, executable


def _run(
    command: Sequence[str], *, timeout: int = 120
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CaseStudyError(f"Could not run {' '.join(command)}: {exc}") from exc


def pdf_info(pdf: Path, *, pdfinfo: str | None = None) -> dict[str, str]:
    info = executable("pdfinfo", pdfinfo)
    completed = _run((info, str(pdf)))
    if completed.returncode != 0:
        raise CaseStudyError(f"pdfinfo rejected {pdf}: {completed.stderr.strip()}")
    values: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", maxsplit=1)
        values[key.strip()] = value.strip()
    return values


def pdf_page_count(pdf: Path, *, pdfinfo: str | None = None) -> int:
    values = pdf_info(pdf, pdfinfo=pdfinfo)
    try:
        pages = int(values["Pages"])
    except (KeyError, ValueError) as exc:
        raise CaseStudyError(
            f"pdfinfo did not report a valid page count for {pdf}."
        ) from exc
    if pages <= 0:
        raise CaseStudyError(f"PDF has no pages: {pdf}")
    return pages


def pdf_text(pdf: Path, *, pdftotext: str | None = None) -> str:
    converter = executable("pdftotext", pdftotext)
    completed = _run((converter, "-enc", "UTF-8", str(pdf), "-"))
    if completed.returncode != 0:
        raise CaseStudyError(f"pdftotext rejected {pdf}: {completed.stderr.strip()}")
    if not completed.stdout.strip():
        raise CaseStudyError(f"PDF has no extractable text: {pdf}")
    return completed.stdout


def pdf_page_texts(
    pdf: Path, pages: int, *, pdftotext: str | None = None
) -> tuple[str, ...]:
    converter = executable("pdftotext", pdftotext)
    extracted: list[str] = []
    for page in range(1, pages + 1):
        completed = _run(
            (
                converter,
                "-f",
                str(page),
                "-l",
                str(page),
                "-enc",
                "UTF-8",
                str(pdf),
                "-",
            )
        )
        if completed.returncode != 0:
            raise CaseStudyError(
                f"pdftotext rejected page {page} of {pdf}: {completed.stderr.strip()}"
            )
        text = completed.stdout.strip()
        if len(re.sub(r"\s+", "", text)) < 3:
            raise CaseStudyError(f"PDF has an unexpected blank page {page}: {pdf}")
        extracted.append(text)
    return tuple(extracted)


def pdf_fonts(pdf: Path, *, pdffonts: str | None = None) -> tuple[str, ...]:
    inspector = executable("pdffonts", pdffonts)
    completed = _run((inspector, str(pdf)))
    if completed.returncode != 0:
        raise CaseStudyError(f"pdffonts rejected {pdf}: {completed.stderr.strip()}")
    fonts: list[str] = []
    for line in completed.stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.casefold().startswith(("name ", "---")):
            continue
        flags = [
            token.casefold()
            for token in stripped.split()
            if token.casefold() in {"yes", "no"}
        ]
        if len(flags) < 3:
            continue
        if flags[0] != "yes":
            raise CaseStudyError(f"PDF has a non-embedded font: {stripped}")
        fonts.append(stripped)
    if not fonts:
        raise CaseStudyError(f"pdffonts found no embedded fonts in {pdf}")
    return tuple(fonts)


def pdf_destinations(pdf: Path, *, pdfinfo: str | None = None) -> tuple[str, ...]:
    info = executable("pdfinfo", pdfinfo)
    completed = _run((info, "-dests", str(pdf)))
    if completed.returncode != 0:
        raise CaseStudyError(
            f"pdfinfo could not inspect destinations for {pdf}: {completed.stderr.strip()}"
        )
    destinations = tuple(
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip() and not line.casefold().startswith(("page ", "----"))
    )
    if not destinations:
        raise CaseStudyError(
            f"PDF has no named destinations for its contents links: {pdf}"
        )
    return destinations


def pdf_language(pdf: Path) -> str:
    match = re.search(rb"/Lang\s*\(([^()]*)\)", pdf.read_bytes())
    if match is None:
        raise CaseStudyError(f"PDF does not declare a document language: {pdf}")
    return match.group(1).decode("ascii", errors="replace")


def render_pdf(
    pdf: Path, output: Path, *, pdftoppm: str | None = None
) -> tuple[Path, ...]:
    renderer = executable("pdftoppm", pdftoppm)
    output.mkdir(parents=True, exist_ok=True)
    prefix = output / "page"
    completed = _run(
        (renderer, "-png", "-r", "144", str(pdf), str(prefix)), timeout=180
    )
    if completed.returncode != 0:
        raise CaseStudyError(f"pdftoppm rejected {pdf}: {completed.stderr.strip()}")
    pages = tuple(sorted(output.glob("page-*.png")))
    if not pages or any(page.stat().st_size < 128 for page in pages):
        raise CaseStudyError(
            f"PDF rendering did not produce complete PNG pages for {pdf}"
        )
    return pages


def validate_pdf(pdf: Path) -> tuple[int, str, tuple[Path, ...]]:
    if not pdf.is_file() or pdf.stat().st_size < 1024:
        raise CaseStudyError(f"PDF is missing or implausibly small: {pdf}")
    payload = pdf.read_bytes()
    if not payload.startswith(b"%PDF-") or b"%%EOF" not in payload[-2048:]:
        raise CaseStudyError(f"PDF structure is invalid or truncated: {pdf}")
    info = pdf_info(pdf)
    pages = pdf_page_count(pdf)
    text = pdf_text(pdf)
    pdf_page_texts(pdf, pages)
    pdf_fonts(pdf)
    pdf_destinations(pdf)
    if not info.get("Title"):
        raise CaseStudyError(f"PDF has no title metadata: {pdf}")
    if not info.get("Subject"):
        raise CaseStudyError(f"PDF has no subject metadata: {pdf}")
    with tempfile.TemporaryDirectory(prefix="case-study-render-") as temporary:
        rendered = render_pdf(pdf, Path(temporary))
        retained = tuple(Path(page.name) for page in rendered)
    return pages, text, retained


def validate_case_study_pdf(
    pdf: Path, *, title: str, language: str, tooling_version: str
) -> tuple[int, str, tuple[Path, ...]]:
    """Apply the source-specific PDF contract after generic rendering checks."""

    pages, text, rendered = validate_pdf(pdf)
    info = pdf_info(pdf)
    if info.get("Title") != title:
        raise CaseStudyError(
            f"PDF title metadata is wrong for {pdf}: {info.get('Title')!r} != {title!r}"
        )
    if tooling_version not in info.get("Subject", ""):
        raise CaseStudyError(
            f"PDF subject does not contain tooling version {tooling_version}: {pdf}"
        )
    actual_language = pdf_language(pdf)
    if actual_language != language:
        raise CaseStudyError(
            f"PDF language metadata is wrong for {pdf}: {actual_language!r} != {language!r}"
        )
    if title not in text:
        raise CaseStudyError(f"PDF text does not contain its expected title: {pdf}")
    contents_heading = "Inhaltsverzeichnis" if language == "de-DE" else "Contents"
    if contents_heading not in text:
        raise CaseStudyError(f"PDF text does not contain its contents heading: {pdf}")
    return pages, text, rendered


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render and inspect every case-study PDF page."
    )
    parser.add_argument("pdf", type=Path, nargs="+", help="PDF file(s) to validate")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        for pdf in args.pdf:
            pages, text, rendered = validate_pdf(pdf.resolve())
            print(
                f"validated {pdf}: {pages} pages, {len(text)} text characters, {len(rendered)} renders"
            )
    except CaseStudyError as exc:
        print(f"PDF validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
