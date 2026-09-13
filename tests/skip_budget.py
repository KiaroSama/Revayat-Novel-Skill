"""What is allowed to skip, decided from the run's own structured result.

A suite reporting `952 passed, 27 skipped` is green, and the 27 are invisible.
They are also load-bearing: on a hosted runner the whole render-and-accept path
and the whole OCR tier skip themselves, by design, because a missing external
tool is not a defect. The problem is that *a new kind of skip looks exactly the
same*. A fixture that starts skipping because an import moved, a test that skips
because the probe it asks now answers differently — both arrive as a slightly
larger number in a line nobody reads.

So every skip has to name a cause this project already knows about. An
unclassified skip fails the build, and the per-cause counts are printed, so the
shape of a run is in the log rather than in one summed integer.

Two profiles, because the two kinds of job mean different things by a skip:

* ``bare`` — a runner with no LibreOffice, no Word, no Tesseract. Skips are
  expected; unclassified ones are not.
* ``full`` — the job that installs those tools precisely so nothing skips. Here
  any skip at all is the hole the job exists to close.

Deliberately not a count budget. A hand-maintained "no more than 27" drifts the
moment a test is added, and the number it would have to be is unknowable from
here — it differs per platform and per installed tool. Two bounds that need no
magic number do the work instead: a skip must be classifiable, and a run must
not skip more than it passes. The `full` profile covers what a count cap was
reaching for, exactly and without arithmetic.

    python tests/skip_budget.py --junit results.xml --profile bare
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

#: Cause name -> what its skip messages look like. Matched case-insensitively
#: against the `message` attribute pytest writes, which is the skip reason for
#: `pytest.skip` and `skipif(reason=…)`, and `could not import 'x': …` for
#: `importorskip`. Verified against a real junit document rather than assumed.
CAUSES: dict[str, re.Pattern[str]] = {
    # `wordrender.backend()` is empty: no Word, no LibreOffice. Every phrasing
    # the suite uses for it — they differ because they report different probes.
    "no render backend": re.compile(
        r"lay (a|the) document out"
        r"|lays a document out"
        r"|cannot lay out here"
        r"|no renderer here"
        r"|no converter here"
        r"|cannot read a page back",
        re.I),
    # The OCR tier: OCRmyPDF, Tesseract, Ghostscript, or the Persian pack.
    "no OCR toolchain": re.compile(
        r"tesseract|ocrmypdf|ghostscript|language pack", re.I),
    # A declared dependency is not importable. This should never fire in CI,
    # where requirements.txt is installed first; it is classified so a
    # contributor running a subset in a bare environment gets a readable answer
    # rather than an unclassified-skip failure.
    #
    # The module names are enumerated on purpose. `could not import` on its own
    # would also swallow `importorskip("numpy")` added by a future test against
    # something nothing declares, which is precisely a skip that should be
    # unclassified and loud.
    "declared dependency not importable": re.compile(
        r"could not import '(pymupdf|fitz|PIL|docx|bs4|lxml)\b"
        # The same condition, phrased by hand: "pymupdf is not installed",
        # "Pillow not installed". Still an enumeration, for the reason above.
        r"|\b(pymupdf|pillow|PIL|lxml|docx|bs4)\b[^\n]{0,24}not installed",
        re.I),
    # The installer tests need a real shell of the right kind.
    "no usable shell": re.compile(r"no PowerShell|no bash here|pwsh ignored", re.I),
    # Only on the oldest declared pillow, where the default font is the built-in
    # bitmap face: a synthetic page drawn with it has no anti-aliased grey, so a
    # measurement about removing grey has nothing to measure. Reached by
    # supported-range.yml and by nothing else.
    "fixture cannot pose the question": re.compile(
        r"no anti-aliased grey", re.I),
}


def classify(message: str) -> str | None:
    for cause, pattern in CAUSES.items():
        if pattern.search(message):
            return cause
    return None


def read(junit: Path) -> tuple[int, int, list[tuple[str, str]]]:
    """``(tests, passed, [(test id, skip message)])`` from a junit document."""
    root = ET.parse(junit).getroot()
    cases = root.iter("testcase")
    total = 0
    failed = 0
    skips: list[tuple[str, str]] = []
    for case in cases:
        total += 1
        skipped = case.find("skipped")
        if skipped is not None:
            where = f"{case.get('classname', '?')}::{case.get('name', '?')}"
            skips.append((where, skipped.get("message") or skipped.text or ""))
            continue
        if case.find("failure") is not None or case.find("error") is not None:
            failed += 1
    return total, total - failed - len(skips), skips


def check(junit: Path, profile: str) -> list[str]:
    total, passed, skips = read(junit)
    problems: list[str] = []

    # A run that collected nothing has no skips either, and would otherwise be
    # the easiest way to pass this check. It is the failure mode most worth
    # catching: an import error at collection time empties the whole suite.
    if total == 0:
        return [f"{junit} records no tests at all; the run collected nothing"]

    grouped: dict[str, list[str]] = {}
    for where, message in skips:
        cause = classify(message)
        if cause is None:
            problems.append(f"unclassified skip: {where}\n    reason: {message}")
        else:
            grouped.setdefault(cause, []).append(where)

    print(f"{total} tests, {passed} passed, {len(skips)} skipped ({profile} profile)")
    for cause, where in sorted(grouped.items()):
        print(f"  {len(where):3}  {cause}")

    if profile == "full" and skips:
        problems.append(
            f"{len(skips)} test(s) skipped on the `full` profile, whose whole "
            f"purpose is that none do:\n    "
            + "\n    ".join(f"{w} — {m}" for w, m in skips))

    if len(skips) > passed:
        problems.append(
            f"{len(skips)} skipped against {passed} passed: this run skipped more "
            f"than it ran, which is an environment failure, not a test profile")

    if problems and profile != "full":
        problems.append(
            "If a new cause is legitimate, add it to CAUSES in this file with a "
            "comment saying which probe produces it. Do not widen an existing "
            "pattern to swallow it — the point is that the causes stay named.")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", required=True, type=Path,
                        help="the --junitxml document the run wrote")
    parser.add_argument("--profile", required=True, choices=("bare", "full"))
    args = parser.parse_args(argv)

    if not args.junit.is_file():
        print(f"::error::no junit document at {args.junit}; the run wrote none, "
              f"so its skips cannot be checked")
        return 2

    problems = check(args.junit, args.profile)
    for problem in problems:
        print(f"::error::{problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
