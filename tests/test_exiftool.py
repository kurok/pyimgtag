"""Tests for the one exiftool invocation path.

Every other test in the suite asserts the argument vector a module builds.
These assert what becomes of it, because in #366 the vector was correct and
what reached the file was not.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404
from pathlib import Path
from unittest.mock import patch

import pytest

from pyimgtag import exiftool


class TestRun:
    """How the arguments are handed over, which is the whole of #366.

    Everything else in this file asserts the argument vector; these assert what
    becomes of it, because on Windows the vector was correct and the file was
    not.
    """

    @staticmethod
    def _captured_argfile(args, stdout=b"", returncode=0):
        """Run the helper against a mocked subprocess and return the argfile text.

        The file is inside a TemporaryDirectory that is gone by the time the
        helper returns, so it has to be read from within the mock.
        """
        seen = {}

        def _fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            seen["text"] = Path(cmd[-1]).read_text(encoding="utf-8")
            seen["dir"] = Path(cmd[-1]).parent
            seen["siblings"] = {
                p.name: p.read_text(encoding="utf-8")
                for p in Path(cmd[-1]).parent.iterdir()
                if p.name != "args.txt"
            }
            return subprocess.CompletedProcess(cmd, returncode, stdout, b"")

        with patch("pyimgtag.exiftool.subprocess.run", side_effect=_fake_run):
            proc = exiftool.run(args)
        return seen, proc

    def test_the_values_go_in_a_file_not_on_the_command_line(self):
        seen, _ = self._captured_argfile(
            ["exiftool", "-overwrite_original", "-XMP:Subject=Óbidos", "/p/a.jpg"]
        )

        assert seen["cmd"][:4] == ["exiftool", "-charset", "UTF8", "-@"]
        assert "Óbidos" not in " ".join(seen["cmd"])
        assert seen["text"].splitlines() == [
            "-overwrite_original",
            "-XMP:Subject=Óbidos",
            "/p/a.jpg",
        ]

    def test_the_file_is_utf8_whatever_the_locale(self, tmp_path):
        """The bytes on disk are what exiftool reads; the locale must not matter."""
        captured = {}

        def _fake_run(cmd, **kwargs):
            captured["bytes"] = Path(cmd[-1]).read_bytes()
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        with patch("pyimgtag.exiftool.subprocess.run", side_effect=_fake_run):
            exiftool.run(["exiftool", "-XMP:Subject=Óbidos", "/p/a.jpg"])

        assert "Óbidos".encode() in captured["bytes"]

    def test_a_value_with_a_newline_is_spilled_to_its_own_file(self):
        """One argument per line is literal, so a newline would split the value.

        exiftool would read the second line as a filename, truncate the tag,
        and still report the file updated -- a caller checking only for success
        would never notice.
        """
        seen, _ = self._captured_argfile(
            ["exiftool", "-XMP:Description=line one\nline two", "/p/a.jpg"]
        )

        lines = seen["text"].splitlines()
        assert len(lines) == 2, lines
        assert lines[0].startswith("-XMP:Description<=")
        assert lines[1] == "/p/a.jpg"
        assert list(seen["siblings"].values()) == ["line one\nline two"]

    def test_a_spilled_value_keeps_its_line_endings(self):
        """write_text would translate \n to \r\n on Windows.

        exiftool reads the spilled file whole, so the CR would land inside the
        description -- a corruption introduced by the very code meant to stop
        one. Caught by CI on windows-latest, not by review.
        """
        seen, _ = self._captured_argfile(
            ["exiftool", "-XMP:Description=line one\nline two", "/p/a.jpg"]
        )
        spilled = next(iter(seen["siblings"].values()))
        assert spilled == "line one\nline two"
        assert "\r" not in spilled

    def test_the_argfile_itself_uses_plain_newlines(self):
        seen, _ = self._captured_argfile(["exiftool", "-XMP:Subject=x", "/p/a.jpg"])
        assert "\r" not in seen["text"]

    def test_a_single_line_value_stays_inline(self):
        """Spilling everything would make every argument unreadable in a log."""
        seen, _ = self._captured_argfile(["exiftool", "-XMP:Description=one line", "/p/a.jpg"])
        assert "-XMP:Description=one line" in seen["text"].splitlines()
        assert seen["siblings"] == {}

    def test_list_operators_are_never_rewritten(self):
        """'+=' and '-=' mean something; '<=' would silently change it."""
        seen, _ = self._captured_argfile(
            ["exiftool", "-IPTC:Keywords+=a\nb", "-IPTC:Keywords-=c\nd", "/p/a.jpg"]
        )
        lines = seen["text"].splitlines()
        assert "<=" not in "".join(lines)
        assert seen["siblings"] == {}

    def test_an_empty_value_survives_as_an_empty_value(self):
        """'-XMP:Subject=' is the clear-the-list idiom, not a blank line."""
        seen, _ = self._captured_argfile(["exiftool", "-XMP:Subject=", "/p/a.jpg"])
        assert "-XMP:Subject=" in seen["text"].splitlines()

    def test_output_is_decoded_as_utf8_not_by_locale(self):
        _, proc = self._captured_argfile(
            ["exiftool", "-json", "/p/a.jpg"], stdout="Óbidos".encode()
        )
        assert proc.stdout == "Óbidos"

    def test_undecodable_output_does_not_raise(self):
        """A truncated read should degrade, not take down the caller."""
        _, proc = self._captured_argfile(["exiftool", "-json", "/p/a.jpg"], stdout=b"\xff\xfe")
        assert isinstance(proc.stdout, str)

    def test_the_return_code_is_passed_through(self):
        _, proc = self._captured_argfile(["exiftool", "/p/a.jpg"], returncode=2)
        assert proc.returncode == 2

    def test_the_temporary_directory_does_not_outlive_the_call(self):
        seen, _ = self._captured_argfile(["exiftool", "-XMP:Subject=x", "/p/a.jpg"])
        assert not seen["dir"].exists()


class TestBytesMode:
    """`exiftool -b` puts binary on stdout, and decoding it destroys it.

    The damage is silent: the call succeeds, the return code is 0, and every
    unit test that mocks the invocation still passes. Only the written file is
    wrong. That is why `raw_converter` has to ask for bytes explicitly.
    """

    @staticmethod
    def _run(stdout: bytes, *, text: bool):
        proc = subprocess.CompletedProcess([], 0, stdout, b"")
        with patch("pyimgtag.exiftool.subprocess.run", return_value=proc):
            return exiftool.run(["exiftool", "-b", "-ThumbnailImage", "/p/a.cr2"], text=text)

    def test_bytes_come_back_untouched(self):
        jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x02\x03\xfe\xff\xd9"
        assert self._run(jpeg, text=False).stdout == jpeg

    def test_text_mode_would_have_mangled_those_same_bytes(self):
        """Names what the bytes mode is protecting against, so it is not guessed at."""
        jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x02\x03\xfe\xff\xd9"
        decoded = self._run(jpeg, text=True).stdout
        assert isinstance(decoded, str)
        assert decoded.encode("utf-8", "replace") != jpeg

    def test_stderr_is_bytes_too_in_bytes_mode(self):
        """A caller that decodes one and not the other is a trap of its own."""
        proc = subprocess.CompletedProcess([], 1, b"", b"oops")
        with patch("pyimgtag.exiftool.subprocess.run", return_value=proc):
            result = exiftool.run(["exiftool", "/p/a.cr2"], text=False)
        assert result.stderr == b"oops"

    def test_the_return_code_still_comes_through(self):
        proc = subprocess.CompletedProcess([], 2, b"", b"")
        with patch("pyimgtag.exiftool.subprocess.run", return_value=proc):
            assert exiftool.run(["exiftool", "/p/a.cr2"], text=False).returncode == 2

    @pytest.mark.skipif(not shutil.which("exiftool"), reason="requires exiftool on PATH")
    def test_a_real_binary_extraction_is_byte_identical(self, tmp_path):
        """End to end: embed a thumbnail, pull it back with -b, compare bytes.

        Under an accented directory, so the path and the payload are both
        exercised in one go.
        """
        from PIL import Image

        folder = tmp_path / "Óbidos"
        folder.mkdir()
        photo = folder / "praça.jpg"
        Image.new("RGB", (64, 64), (200, 40, 40)).save(photo)

        thumb_source = tmp_path / "thumb.jpg"
        Image.new("RGB", (16, 16), (10, 20, 30)).save(thumb_source)
        expected = thumb_source.read_bytes()

        embed = exiftool.run(
            [
                "exiftool",
                "-overwrite_original",
                f"-ThumbnailImage<={thumb_source}",
                str(photo),
            ],
            timeout=60,
        )
        assert embed.returncode == 0, embed.stderr

        extracted = exiftool.run(
            ["exiftool", "-b", "-ThumbnailImage", str(photo)], timeout=60, text=False
        )
        assert extracted.returncode == 0
        assert extracted.stdout == expected


class TestIsAvailable:
    def test_true_when_on_path(self):
        with patch("pyimgtag.exiftool.shutil.which", return_value="/usr/bin/exiftool"):
            assert exiftool.is_available() is True

    def test_false_when_absent(self):
        with patch("pyimgtag.exiftool.shutil.which", return_value=None):
            assert exiftool.is_available() is False
