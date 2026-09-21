"""One way to invoke exiftool, for every module that needs to.

Arguments do not go on the command line. On Windows the C runtime converts
the command line to the active code page before exiftool's Perl runtime reads
argv, and every character that code page cannot represent becomes a literal
``?``. That destroyed metadata values (#366) and it destroys file paths the
same way -- a photo under ``~/Fotos/\u00d3bidos`` becomes a file that does not
exist, and the caller sees a non-zero exit it has no way to attribute.

An argument file is read as UTF-8 while ``-charset UTF8`` is in force, so both
arrive intact. Arriving intact is not sufficient for a path: ``-charset
filename=utf8`` is a separate setting deciding how exiftool *opens* what it
was handed, and without it a correctly received path is still resolved through
the Windows ANSI API and still not found. Both are set here, because either
alone leaves half the problem.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404
import tempfile
from pathlib import Path


def is_available() -> bool:
    """True when exiftool is on PATH."""
    return shutil.which("exiftool") is not None


def run(args: list[str], *, timeout: int = 30, text: bool = True) -> subprocess.CompletedProcess:
    """Invoke exiftool with *args*, handing the arguments over in a UTF-8 file.

    ``args[0]`` is the executable; everything after it is what exiftool would
    otherwise have been given on the command line.

    Values do not go on the command line any more. On Windows the C runtime
    converts the command line to the active code page before Perl reads argv,
    and every character that code page cannot represent becomes a literal
    ``?`` -- in the written file, unrecoverably. A geocoded ``\u00d3bidos``
    was stored as ``?bidos``. An argument file is read as UTF-8 while
    ``-charset UTF8`` is in force, so the bytes arrive intact on every
    platform, and file paths travel the same way for the same reason.

    One argument per line is literal in an argument file, so a value containing
    a newline -- a model-written description, typically -- would be read as a
    second argument and then as a filename, leaving the tag truncated while
    exiftool still reports files updated. Those values are spilled to their own
    file and passed as ``-TAG<=FILE``, which exiftool reads whole.

    stdout and stderr come back decoded as UTF-8 rather than by locale, for the
    same reason the input is written as UTF-8 -- unless *text* is False, which
    returns them as bytes. ``exiftool -b`` writes binary on stdout (an embedded
    JPEG thumbnail, say), and decoding that as text corrupts it silently: the
    call succeeds, the unit tests that mock it still pass, and the thumbnail is
    rubbish.
    """
    with tempfile.TemporaryDirectory(prefix="pyimgtag-exiftool-") as tmpdir:
        tmp = Path(tmpdir)
        lines: list[str] = []

        for index, arg in enumerate(args[1:]):
            tag, sep, value = arg.partition("=")
            # Only a plain assignment can be spilled: '+=' and '-=' are list
            # operators and '<=' is already the file form, so rewriting any of
            # them would change what the argument means.
            if sep and "\n" in value and not tag.endswith(("+", "-", "<")):
                value_file = tmp / f"value{index}"
                # newline="" disables the translation that would turn every
                # \n into \r\n on Windows -- exiftool reads this file whole,
                # so the CR would land in the description itself.
                value_file.write_text(value, encoding="utf-8", newline="")
                lines.append(f"{tag}<={value_file}")
            else:
                lines.append(arg)

        argfile = tmp / "args.txt"
        argfile.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")

        proc = subprocess.run(  # noqa: S603  # nosec B603 B607
            [
                args[0],
                # How to decode the argument file's contents...
                "-charset",
                "UTF8",
                # ...and how to treat the file names inside it. A separate
                # setting with a separate default: on Windows exiftool
                # otherwise takes file names to be in the system code page and
                # opens them through the ANSI API, so a path it received
                # perfectly well still resolves to a file that is not there.
                # UTF8 switches it to the wide-character calls.
                "-charset",
                "filename=utf8",
                "-@",
                str(argfile),
            ],
            capture_output=True,
            timeout=timeout,
        )

    if not text:
        return proc

    return subprocess.CompletedProcess(
        proc.args,
        proc.returncode,
        proc.stdout.decode("utf-8", "replace"),
        proc.stderr.decode("utf-8", "replace"),
    )
