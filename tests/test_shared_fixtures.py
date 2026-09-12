"""A shared session fixture has to name itself when it fails.

This project has one recorded run of `2 failed, 744 passed, 64 errors` at 2.4x the
normal peak memory that has never reproduced. Four causes were tested and rejected
with evidence; what was never explained is why *64*. The mechanism is here rather
than in any module that reported an error: `conftest.py`'s fixtures are
`scope="session"`, pytest caches a session fixture's exception, and every later
request re-raises it without retrying. One transient failure while rasterising at
200 DPI or writing a PDF therefore errors every test that asked for that fixture —
about one module's worth at a time — in modules with nothing wrong with them.

The amplification cannot be removed without rebuilding those fixtures per test,
which is the cost they exist to avoid. What can be removed is the anonymity, and
these tests hold that: the failure names the fixture, the path and the original
error, and a missing optional dependency still skips rather than erroring.

Full account: `.ai/BUGS.md`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tests_support import building  # noqa: E402


def test_a_failing_shared_fixture_names_itself_the_path_and_the_cause():
    """The three things a reader needs and a bare traceback does not give: which
    fixture, which file, and what actually went wrong."""
    with pytest.raises(RuntimeError) as blamed:
        with building("scanned_pdf", Path("tmp/scan/scanned.pdf")):
            raise OSError(28, "No space left on device")

    message = str(blamed.value)
    assert "scanned_pdf" in message, message
    assert "scanned.pdf" in message, message
    assert "OSError" in message, message
    assert "No space left on device" in message, message
    # And it has to say why one cause produced many errors, or the reader starts
    # debugging the modules that reported them.
    assert "session-scoped" in message, message


def test_the_original_exception_is_kept_as_the_cause():
    """`raise ... from error`, so the traceback still reaches the real failure.
    A guard that replaces the cause trades one anonymous error for another."""
    original = MemoryError("cannot allocate pixmap")

    with pytest.raises(RuntimeError) as blamed:
        with building("mixed_pdf", Path("tmp/mixed/mixed.pdf")):
            raise original

    assert blamed.value.__cause__ is original


def test_a_skip_is_not_turned_into_an_error():
    """`pytest.importorskip` must keep skipping: the OCR and renderer tiers depend
    on it. `Skipped` derives from `BaseException`, not `Exception`, so the guard
    cannot catch it — asserted here rather than assumed, because the whole value
    of the guard is that it catches everything else."""
    with pytest.raises(pytest.skip.Exception):
        with building("sample_pdf", Path("tmp/pdf/sample.pdf")):
            pytest.skip("pymupdf is not installed")


def test_a_fixture_that_builds_cleanly_is_left_alone():
    """The guard is invisible on the happy path — no wrapping, no swallowing, and
    the value the body produced comes straight back."""
    produced = []
    with building("sample_png", Path("tmp/assets/fig.png")):
        produced.append("built")

    assert produced == ["built"]
