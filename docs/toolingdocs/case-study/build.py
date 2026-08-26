"""Build both case-study editions without leaving TeX state in the source tree."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path

CASE_STUDY_ROOT = Path(__file__).resolve().parent
LANGUAGES = ("de", "en")
SOURCE_DATE_EPOCH = "1704067200"  # 2024-01-01 00:00:00 UTC
PDF_PREFIX = "portable-tooling-case-study"
DEFAULT_TIMEOUT_SECONDS = 120


class BuildError(RuntimeError):
    """Raised when the deterministic PDF build cannot be completed."""


def _inside(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_compiler(explicit: str | None) -> str:
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.parent != Path("."):
            resolved = candidate.resolve()
            if not resolved.is_file():
                raise BuildError(f"pdflatex executable does not exist: {resolved}")
            return str(resolved)
        discovered = shutil.which(explicit)
    else:
        discovered = shutil.which("pdflatex")
    if discovered is None:
        raise BuildError(
            "pdflatex is required; install a TeX distribution or pass --compiler."
        )
    return discovered


def _build_environment(work: Path) -> dict[str, str]:
    home = work / "home"
    texmf_config = work / "texmf-config"
    texmf_home = work / "texmf-home"
    texmf_var = work / "texmf-var"
    for directory in (home, texmf_config, texmf_home, texmf_var):
        directory.mkdir(parents=True, exist_ok=True)

    environment: dict[str, str] = {"PATH": os.environ.get("PATH", os.defpath)}
    for key in ("COMSPEC", "PATHEXT", "SYSTEMROOT", "WINDIR"):
        value = os.environ.get(key)
        if value:
            environment[key] = value
    environment.update(
        {
            "SOURCE_DATE_EPOCH": SOURCE_DATE_EPOCH,
            "FORCE_SOURCE_DATE": "1",
            "TZ": "UTC",
            "LANG": "C",
            "LANGUAGE": "C",
            "LC_ALL": "C",
            "HOME": str(home),
            "USERPROFILE": str(home),
            "TMP": str(work),
            "TEMP": str(work),
            "TMPDIR": str(work),
            "TEXMFCONFIG": str(texmf_config),
            "TEXMFHOME": str(texmf_home),
            "TEXMFVAR": str(texmf_var),
            "TEXMFOUTPUT": str(work),
        }
    )
    return environment


def _run_pdflatex(
    compiler: str,
    source: Path,
    work: Path,
    *,
    timeout: int,
) -> None:
    command = (
        compiler,
        "-no-shell-escape",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-file-line-error",
        f"-output-directory={work}",
        source.name,
    )
    environment = _build_environment(work)
    for pass_number in (1, 2):
        try:
            completed = subprocess.run(
                command,
                cwd=source.parent,
                env=environment,
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
            raise BuildError(
                f"pdflatex pass {pass_number} could not run for {source.parent.name}: "
                f"{exc}"
            ) from exc
        if completed.returncode != 0:
            diagnostic = "\n".join(
                part.strip()
                for part in (completed.stdout, completed.stderr)
                if part.strip()
            )
            if len(diagnostic) > 6000:
                diagnostic = diagnostic[-6000:]
            raise BuildError(
                f"pdflatex pass {pass_number} failed for {source.parent.name} "
                f"with exit code {completed.returncode}:\n{diagnostic}"
            )


def _compile_language(language: str, compiler: str, timeout: int) -> bytes:
    source = CASE_STUDY_ROOT / "source" / language / "main.tex"
    if not source.is_file():
        raise BuildError(f"case-study source is missing: {source}")
    with tempfile.TemporaryDirectory(prefix=f"case-study-{language}-") as temporary:
        work = Path(temporary)
        _run_pdflatex(compiler, source, work, timeout=timeout)
        pdf = work / "main.pdf"
        try:
            payload = pdf.read_bytes()
        except OSError as exc:
            raise BuildError(f"pdflatex did not produce a readable PDF: {pdf}") from exc
    if len(payload) < 1024 or not payload.startswith(b"%PDF-"):
        raise BuildError(f"pdflatex produced an invalid or empty PDF for {language}.")
    if b"%%EOF" not in payload[-1024:]:
        raise BuildError(f"pdflatex produced a truncated PDF for {language}.")
    return payload


def _publish_pdf(target: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _existing_pdf(target: Path) -> bytes | None:
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise BuildError(f"Could not inspect existing PDF target: {target}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise BuildError(f"Existing PDF target must be a regular file: {target}")
    try:
        return target.read_bytes()
    except OSError as exc:
        raise BuildError(f"Could not back up existing PDF target: {target}") from exc


def _restore_pdfs(
    published: Sequence[Path],
    originals: dict[Path, bytes | None],
) -> str | None:
    failures: list[str] = []
    for target in reversed(published):
        try:
            original = originals[target]
            if original is None:
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass
            else:
                _publish_pdf(target, original)
        except (KeyError, OSError) as exc:
            failures.append(f"{target.name}: {exc}")
    return "; ".join(failures) or None


def _publish_editions(
    output: Path,
    selected: Sequence[str],
    payloads: dict[str, bytes],
) -> tuple[Path, ...]:
    targets = tuple(output / f"{PDF_PREFIX}-{language}.pdf" for language in selected)
    originals = {target: _existing_pdf(target) for target in targets}
    published: list[Path] = []
    try:
        for language, target in zip(selected, targets, strict=True):
            _publish_pdf(target, payloads[language])
            published.append(target)
    except OSError as exc:
        rollback_error = _restore_pdfs(published, originals)
        message = (
            f"Could not publish case-study editions; restored prior outputs: {exc}"
        )
        if rollback_error is not None:
            message += f"; rollback also failed: {rollback_error}"
        raise BuildError(message) from exc
    return targets


def build(
    languages: Iterable[str],
    output_directory: Path,
    *,
    compiler: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[Path, ...]:
    """Compile all selected editions and publish them with rollback on failure."""

    selected = tuple(languages)
    if not selected or any(language not in LANGUAGES for language in selected):
        raise BuildError("languages must be a non-empty subset of: de, en")
    if len(set(selected)) != len(selected):
        raise BuildError("languages must not contain duplicates")
    if timeout <= 0:
        raise BuildError("timeout must be greater than zero")

    source_root = CASE_STUDY_ROOT.resolve()
    output = output_directory.expanduser().resolve()
    if _inside(output, source_root):
        raise BuildError(
            "PDF output must be outside the case-study source tree to keep it clean."
        )
    executable = _resolve_compiler(compiler)

    # Compile every edition before publishing any of them. A compilation failure in
    # a bilingual run therefore cannot leave a mixed-generation result set.
    payloads = {
        language: _compile_language(language, executable, timeout)
        for language in selected
    }
    try:
        output.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BuildError(f"Could not prepare PDF output directory: {output}") from exc
    return _publish_editions(output, selected, payloads)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build reproducible German and English case-study PDFs."
    )
    parser.add_argument(
        "--language",
        choices=(*LANGUAGES, "all"),
        default="all",
        help="edition to build (default: all)",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="external directory receiving only the final PDFs",
    )
    parser.add_argument(
        "--compiler",
        help="pdflatex executable name or path (default: discover on PATH)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"timeout per pdflatex pass in seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    selected = LANGUAGES if args.language == "all" else (args.language,)
    try:
        targets = build(
            selected,
            args.output_dir,
            compiler=args.compiler,
            timeout=args.timeout,
        )
    except BuildError as exc:
        print(f"case-study build failed: {exc}", file=sys.stderr)
        return 1
    for target in targets:
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        print(f"built {target} sha256:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
