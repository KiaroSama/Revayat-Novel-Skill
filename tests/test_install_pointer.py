"""Exact owned-region updates leave user bytes outside it untouched."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "install"))


def test_owned_pointer_preserves_unowned_bytes_and_is_stable():
    from install_files import pointer_bytes
    prefix = b"\xef\xbb\xbfOwner\r\n"
    suffix = b"\r\nPrivate tail without newline"
    original = prefix + b"<!-- BEGIN revayat-novel -->\r\nold\r\n<!-- END revayat-novel -->" + suffix
    result = pointer_bytes(original, ["/project/.opencode/skills/revayat-novel"])
    assert result.startswith(prefix) and result.endswith(suffix)
    assert b"old\r\n" not in result
    assert pointer_bytes(result, ["/project/.opencode/skills/revayat-novel"]) == result


@pytest.mark.parametrize("original", [
    b"<!-- BEGIN revayat-novel -->\nprivate", b"<!-- END revayat-novel -->",
    b"<!-- BEGIN revayat-novel -->\n<!-- BEGIN revayat-novel -->\n<!-- END revayat-novel -->",
    b"\xffprivate", b"<!-- BEGIN revayat-novel --> inline\n",
])
def test_malformed_pointer_refuses(original):
    from install_files import pointer_bytes
    with pytest.raises(ValueError):
        pointer_bytes(original, ["/safe/skill"])


@pytest.mark.parametrize("original", [b"", b"owner without newline", b"owner\r\n", bytes.fromhex("efbbbf") + b"owner\n"])
def test_absent_pointer_append_preserves_original_as_prefix(original):
    from install_files import pointer_bytes
    result = pointer_bytes(original, ["/safe/skill"])
    assert result.startswith(original)
    assert pointer_bytes(result, ["/safe/skill"]) == result
