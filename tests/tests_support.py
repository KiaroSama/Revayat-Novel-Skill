"""Helpers shared by the pytest fixtures and the standalone end-to-end script.

Kept as a plain importable module rather than living in ``conftest.py`` so that
``e2e_pipeline.py`` can use it without pytest being involved.
"""

from __future__ import annotations

import contextlib
import struct
import zlib
from pathlib import Path


def png_bytes(width: int, height: int, rgb: tuple[int, int, int] = (200, 60, 60)) -> bytes:
    """A minimal valid PNG.

    Generating fixtures beats committing them: the suite stays fast, the
    repository stays small, and no third-party book content is vendored in.
    """

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


@contextlib.contextmanager
def building(what: str, path):
    """Name a shared session fixture in its own failure.

    pytest **caches a session fixture's exception**: one transient failure is
    re-raised for every later request without retrying, so a single unlucky
    ``mktemp``, rasterise or ``save`` becomes a setup error on every test that
    asked for it. This project has one recorded run of ``2 failed, 744 passed, 64
    errors`` at 2.4x the normal peak memory that has never reproduced, and 64 is
    about one module's worth of tests — see ``.ai/BUGS.md``.

    The amplification is not preventable here; being unable to read it off the
    output was. Without this, those errors are anonymous tracebacks in modules
    that have nothing wrong with them. With it, every one of them names the
    fixture, the path and the underlying OS error.

    ``pytest.importorskip`` stays outside the guard, and ``Skipped`` derives from
    ``BaseException`` rather than ``Exception``, so a missing optional dependency
    still skips instead of erroring.
    """
    try:
        yield
    except Exception as error:                      # noqa: BLE001 — re-raised below
        raise RuntimeError(
            f"the shared session fixture {what!r} could not be built at {path}: "
            f"{type(error).__name__}: {error}. Because it is session-scoped, every "
            f"test that requested it reports a setup error from this one cause — "
            f"fix this, not them."
        ) from error


def reply_text(worksheet_path: Path, body: str) -> str:
    """``body`` carrying the request line its worksheet asked for.

    What a translator actually does: copy the `request` line out of the worksheet
    into the reply. Merge compares it with the live request, which is the only
    thing that can tell an answer to *this* cut from an answer to the cut this one
    replaced — the filename, the id list and the parent block's text are identical
    across a recut.

    A worksheet with no request line (an older working directory, or the page
    route before it carried one) yields the body unchanged, so a test about
    something else does not have to care.
    """
    import worksheet  # noqa: PLC0415 — the scripts directory is on sys.path

    path = Path(worksheet_path)
    if not path.is_file():
        # A test that writes its own manifest has no worksheet on disk, and its
        # entries carry no request either, so there is nothing to echo.
        return body
    token = worksheet.request_of(path.read_text(encoding="utf-8"))
    return f"{worksheet.request_line(token)}\n{body}" if token else body
