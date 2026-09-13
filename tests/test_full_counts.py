"""A display cap must never become a verdict or a count.

`Report.summary()` truncates its findings list so a person can read the JSON.
That is a courtesy, and the defect class is what happens when something *else*
reads it: a gate branches on sixty of eighty-two errors, a total is quoted from
a slice, and both fail in the reassuring direction — the report looks complete
because the number it shows is the number it kept.

The sibling project shipped exactly that: a preflight reading a display-capped
list reported 60 of 82 errors. These tests are the answer here, and they need no
renderer, which is the point — the invariant is about arithmetic, not layout, so
it is checked where it is cheap rather than behind a Word render.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "revayat-novel" / "scripts"))

import docqa  # noqa: E402
import qa  # noqa: E402


def _report(errors: int = 0, warnings: int = 0, code: str = "text-missing") -> qa.Report:
    report = qa.Report()
    for index in range(errors):
        report.add(qa.ERROR, code, f"block{index}", "missing")
    for index in range(warnings):
        report.add(qa.WARNING, code, f"block{index}", "suspicious")
    return report


# --------------------------------------------------------------------------- #
# The summary's own contract
# --------------------------------------------------------------------------- #

def test_the_default_summary_truncates_and_says_by_how_much():
    """The cap is fine. Hiding it is not."""
    summary = _report(errors=82).summary()

    assert len(summary["findings"]) == 60
    assert summary["truncated"] == 22
    # The counts a gate is allowed to believe come from the full set.
    assert summary["errors"] == 82
    assert summary["by_code"]["text-missing"] == 82


def test_no_limit_returns_every_finding():
    """What anything that branches on the list has to ask for."""
    summary = _report(errors=82, warnings=5).summary(limit=None)

    assert len(summary["findings"]) == 87
    assert summary["truncated"] == 0, (
        "nothing was dropped, so claiming a dropped count would be its own lie")


def test_a_limit_larger_than_the_findings_drops_nothing():
    summary = _report(errors=3).summary(limit=60)
    assert len(summary["findings"]) == 3
    assert summary["truncated"] == 0


# --------------------------------------------------------------------------- #
# The document verdict
# --------------------------------------------------------------------------- #

def test_the_document_verdict_counts_past_the_cap():
    """Eighty-two errors are eighty-two errors in a report that prints sixty."""
    findings = _report(errors=82).summary(limit=None)["findings"]
    outcome = docqa.document_verdict(findings)

    assert outcome["errors"] == 82
    assert len(outcome["findings"]) == docqa.SHOWN_FINDINGS
    assert outcome["findings_truncated"] == 82 - docqa.SHOWN_FINDINGS
    assert outcome["ok"] is False


def test_a_real_finding_past_the_cap_still_fails_the_document():
    """The verdict may not depend on which check happened to run last.

    Sixty machine-dependent warnings — which deliberately do *not* fail a
    document — followed by one real error. Decided on the printed slice, this
    document passes: the error is the sixty-first item and the slice is sixty
    long. Decided on the full list, it fails, which is the truth.
    """
    findings = (_report(warnings=60, code="font-fallback").findings
                + _report(errors=1, code="text-missing").findings)

    outcome = docqa.document_verdict(findings)

    assert outcome["ok"] is False, (
        "the error fell off the end of the display slice and the document passed")
    assert outcome["errors"] == 1
    assert outcome["findings_truncated"] == 1
    # And the dropped one is the error itself, which is why the count matters:
    # the published list here is sixty warnings and nothing else.
    assert all(f["severity"] == qa.WARNING for f in outcome["findings"])


def test_machine_dependent_findings_alone_do_not_fail_a_document():
    """The other half of the same contract, so neither test drifts alone."""
    findings = _report(warnings=61, code="font-fallback").findings
    outcome = docqa.document_verdict(findings)

    assert outcome["ok"] is True
    assert outcome["warnings"] == 61
    assert outcome["findings_truncated"] == 1


@pytest.mark.parametrize("code", sorted(docqa.MACHINE_DEPENDENT_CODES))
def test_every_machine_dependent_code_is_a_warning(code):
    """Why the ordering argument above is not the thing keeping this correct.

    `summary` emits errors before warnings, so an error can only be cut once
    there are already sixty of them — and a document with sixty errors fails
    either way. That accident is what made the old truncated verdict *look*
    sound. It holds only while these codes are warnings, nobody enforced that,
    and the verdict no longer relies on it.
    """
    report = qa.Report()
    report.add(qa.WARNING, code, "page0001", "a fallback face was used")
    assert report.summary()["warnings"] == 1
