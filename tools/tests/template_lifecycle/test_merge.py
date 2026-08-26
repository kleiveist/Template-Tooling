from __future__ import annotations

import subprocess

import pytest

from tools.template_lifecycle.merge import merge_text, text_equivalent


def test_non_overlapping_text_changes_merge_without_conflict() -> None:
    base = b"heading\nlocal-value=old\nseparator\nincoming-value=old\nfooter\n"
    local = b"heading\nlocal-value=new\nseparator\nincoming-value=old\nfooter\n"
    incoming = b"heading\nlocal-value=old\nseparator\nincoming-value=new\nfooter\n"

    result = merge_text(base, local, incoming)

    assert result.conflict is False
    assert result.content == (b"heading\nlocal-value=new\nseparator\nincoming-value=new\nfooter\n")


def test_overlapping_text_changes_return_staged_conflict_markers() -> None:
    base = b"heading\nvalue=old\nfooter\n"
    local = b"heading\nvalue=product\nfooter\n"
    incoming = b"heading\nvalue=template\nfooter\n"

    result = merge_text(base, local, incoming)

    assert result.conflict is True
    assert b"<<<<<<< LOCAL" in result.content
    assert b"||||||| BASE" in result.content
    assert b">>>>>>> INCOMING" in result.content
    assert local == b"heading\nvalue=product\nfooter\n"


def test_multiple_conflict_regions_are_reported_as_conflict() -> None:
    base = b"first=old\nkeep-a\nkeep-b\nsecond=old\n"
    local = b"first=local\nkeep-a\nkeep-b\nsecond=local\n"
    incoming = b"first=incoming\nkeep-a\nkeep-b\nsecond=incoming\n"

    result = merge_text(base, local, incoming)

    assert result.conflict is True
    assert result.content.count(b"<<<<<<< LOCAL") == 2


def test_merge_preserves_consistent_local_crlf_style() -> None:
    base = b"heading\nlocal=old\nkeep-a\nkeep-b\nincoming=old\nfooter\n"
    local = b"heading\r\nlocal=new\r\nkeep-a\r\nkeep-b\r\nincoming=old\r\nfooter\r\n"
    incoming = b"heading\nlocal=old\nkeep-a\nkeep-b\nincoming=new\nfooter\n"

    result = merge_text(base, local, incoming)

    assert result.conflict is False
    assert b"\r\n" in result.content
    assert b"\n" not in result.content.replace(b"\r\n", b"")
    assert b"local=new\r\n" in result.content
    assert b"incoming=new\r\n" in result.content


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (b"one\ntwo\n", b"one\r\ntwo\r\n"),
        (b"one\rtwo\r", b"one\ntwo\n"),
        (b"plain", b"plain"),
    ],
)
def test_text_equivalence_normalizes_line_endings(left: bytes, right: bytes) -> None:
    assert text_equivalent(left, right)


def test_merge_subprocess_uses_argument_list_without_shell(monkeypatch) -> None:
    from tools.template_lifecycle import merge as merge_module

    original_run = merge_module.subprocess.run
    observed: list[list[str]] = []

    def guarded_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[bytes]:
        observed.append(command)
        assert kwargs.get("shell") is not True
        return original_run(command, **kwargs)

    monkeypatch.setattr(merge_module.subprocess, "run", guarded_run)

    result = merge_text(
        b"one\nkeep-a\nkeep-b\nkeep-c\nfive\n",
        b"local\nkeep-a\nkeep-b\nkeep-c\nfive\n",
        b"one\nkeep-a\nkeep-b\nkeep-c\nincoming\n",
    )

    assert result.conflict is False
    assert observed and observed[0][:2] == ["git", "merge-file"]
