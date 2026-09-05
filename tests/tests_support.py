"""Helpers shared by the pytest fixtures and the standalone end-to-end script.

Kept as a plain importable module rather than living in ``conftest.py`` so that
``e2e_pipeline.py`` can use it without pytest being involved.
"""

from __future__ import annotations

import struct
import zlib


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
