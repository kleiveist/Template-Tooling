"""Create the deterministic archive and checksum for a portable tooling release."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import stat
import tarfile
import tempfile
from pathlib import Path

_SEMVER = re.compile(
    r"(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)


class ReleaseBundleError(RuntimeError):
    """Raised when the candidate cannot be packaged safely."""


def _version(export_root: Path) -> str:
    version_path = export_root / "tools" / "VERSION"
    if not version_path.is_file() or version_path.is_symlink():
        raise ReleaseBundleError("portable export has no regular tools/VERSION")
    version = version_path.read_text(encoding="utf-8").strip()
    if _SEMVER.fullmatch(version) is None:
        raise ReleaseBundleError(f"tools/VERSION is not valid SemVer: {version!r}")
    if export_root.name != f"Template-Tooling-{version}":
        raise ReleaseBundleError(
            "portable export directory does not match tools/VERSION: "
            f"{export_root.name!r}"
        )
    return version


def _validate_boundary(export_root: Path) -> None:
    if export_root.is_symlink() or not export_root.is_dir():
        raise ReleaseBundleError("portable export root must be a regular directory")
    if {path.name for path in export_root.iterdir()} != {"docs", "tools"}:
        raise ReleaseBundleError(
            "portable export root must contain exactly docs/ and tools/"
        )
    docs = export_root / "docs"
    tooling_docs = docs / "toolingdocs"
    if (
        docs.is_symlink()
        or not docs.is_dir()
        or {path.name for path in docs.iterdir()} != {"toolingdocs"}
        or tooling_docs.is_symlink()
        or not tooling_docs.is_dir()
    ):
        raise ReleaseBundleError(
            "portable export docs/ must contain exactly docs/toolingdocs/"
        )
    manifest = export_root / "tools" / "PORTABLE-PAYLOAD.json"
    if not manifest.is_file() or manifest.is_symlink():
        raise ReleaseBundleError(
            "portable export has no regular tools/PORTABLE-PAYLOAD.json"
        )


def _candidate_paths(export_root: Path) -> tuple[Path, ...]:
    paths = (export_root, *export_root.rglob("*"))
    return tuple(
        sorted(
            paths,
            key=lambda path: (
                0 if path == export_root else len(path.relative_to(export_root).parts),
                "" if path == export_root else path.relative_to(export_root).as_posix(),
            ),
        )
    )


def _archive_info(path: Path, export_root: Path) -> tarfile.TarInfo:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        raise ReleaseBundleError(f"portable release refuses symlink: {path}")
    if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
        raise ReleaseBundleError(f"portable release refuses special file: {path}")

    relative = Path(export_root.name)
    if path != export_root:
        relative /= path.relative_to(export_root)
    info = tarfile.TarInfo(relative.as_posix())
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.pax_headers = {}
    if stat.S_ISDIR(metadata.st_mode):
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        info.size = 0
    else:
        info.type = tarfile.REGTYPE
        info.mode = 0o755 if metadata.st_mode & stat.S_IXUSR else 0o644
        info.size = metadata.st_size
    return info


def _write_archive(export_root: Path, destination: Path) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as raw:
            temporary = Path(raw.name)
            with (
                gzip.GzipFile(
                    filename="",
                    mode="wb",
                    compresslevel=9,
                    fileobj=raw,
                    mtime=0,
                ) as compressed,
                tarfile.open(
                    fileobj=compressed,
                    mode="w",
                    format=tarfile.PAX_FORMAT,
                ) as archive,
            ):
                for path in _candidate_paths(export_root):
                    info = _archive_info(path, export_root)
                    if info.isfile():
                        with path.open("rb") as payload:
                            archive.addfile(info, payload)
                    else:
                        archive.addfile(info)
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_release_bundle(export_root: Path, output_dir: Path) -> dict[str, str]:
    if export_root.is_symlink():
        raise ReleaseBundleError("portable export root must not be a symlink")
    if output_dir.is_symlink():
        raise ReleaseBundleError("output directory must not be a symlink")
    export_root = export_root.resolve(strict=True)
    output_dir = output_dir.resolve(strict=True)
    if not output_dir.is_dir():
        raise ReleaseBundleError("output directory must be a regular directory")
    if output_dir == export_root or output_dir.is_relative_to(export_root):
        raise ReleaseBundleError("output directory must be outside the portable export")

    _validate_boundary(export_root)
    version = _version(export_root)
    archive = output_dir / f"Template-Tooling-{version}.tar.gz"
    checksums = output_dir / "SHA256SUMS"
    for target in (archive, checksums):
        if target.exists() or target.is_symlink():
            raise ReleaseBundleError(f"release output already exists: {target}")

    _write_archive(export_root, archive)
    checksum_temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="ascii",
            newline="\n",
            dir=output_dir,
            prefix=".SHA256SUMS.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            checksum_temporary = Path(handle.name)
            handle.write(f"{_sha256(archive)}  {archive.name}\n")
        os.replace(checksum_temporary, checksums)
        checksum_temporary = None
    except Exception:
        archive.unlink(missing_ok=True)
        raise
    finally:
        if checksum_temporary is not None:
            checksum_temporary.unlink(missing_ok=True)

    return {
        "version": version,
        "tag": f"tooling-v{version}",
        "archive": str(archive),
        "checksums": str(checksums),
        "sha256": _sha256(archive),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        result = create_release_bundle(arguments.export_root, arguments.output_dir)
    except (OSError, ReleaseBundleError) as error:
        raise SystemExit(f"portable release packaging failed: {error}") from error
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
