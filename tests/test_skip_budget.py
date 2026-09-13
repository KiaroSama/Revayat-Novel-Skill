"""The skip budget has to fail for the reason it claims to.

A check over skips is itself a check nobody watches, and this project has twice
shipped a gate that compared a value against something derived from the same
value. So two things are proved here: the enforcer rejects what it says it
rejects, and — the part a hand-written fixture cannot fake — every skip reason
the real suite writes is classified by reading `tests/*.py` and feeding those
literal reasons through it. Widen the suite's vocabulary without widening
`CAUSES` and this fails, which is the whole point of naming causes.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

import skip_budget  # noqa: E402


def junit(path: Path, *, cases: list[tuple[str, str | None]]) -> Path:
    """A junit document in pytest's own shape: skips carry `message`."""
    body = []
    for name, skip in cases:
        if skip is None:
            body.append(f'<testcase classname="tests.test_x" name="{name}" time="0.1" />')
        else:
            body.append(
                f'<testcase classname="tests.test_x" name="{name}" time="0.1">'
                f'<skipped type="pytest.skip" message="{skip}">at some line</skipped>'
                f"</testcase>")
    document = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuites name="pytest tests"><testsuite name="pytest">'
        + "".join(body)
        + "</testsuite></testsuites>")
    path.write_text(document, encoding="utf-8")
    return path


def test_the_full_profile_rejects_even_a_legitimate_skip(tmp_path):
    """On the job that installs the tools, a skip is the hole it exists to close."""
    report = junit(tmp_path / "r.xml", cases=[
        ("a", None), ("b", None),
        ("c", "nothing on this machine can lay a document out"),
    ])
    problems = skip_budget.check(report, "full")
    assert problems, "a skip passed the profile that forbids every skip"
    assert "skipped on the `full` profile" in problems[0]
    # And the same document is fine where skips are expected.
    assert skip_budget.check(report, "bare") == []


def test_the_bare_profile_accepts_the_causes_it_knows(tmp_path):
    report = junit(tmp_path / "r.xml", cases=[
        ("a", None), ("b", None), ("c", None), ("d", None),
        ("e", "nothing on this machine can lay a document out"),
        ("f", "Tesseract is not installed"),
        ("g", "no PowerShell on this machine"),
    ])
    assert skip_budget.check(report, "bare") == []


def test_an_unclassified_skip_fails_and_names_the_test(tmp_path):
    report = junit(tmp_path / "r.xml", cases=[
        ("a", None), ("b", None),
        ("surprise", "the fixture decided not to today"),
    ])
    problems = skip_budget.check(report, "bare")
    assert any("unclassified skip" in p and "surprise" in p for p in problems)


def test_a_run_that_collected_nothing_is_not_a_clean_run(tmp_path):
    """The easiest way to have no skips is to have no tests."""
    report = junit(tmp_path / "r.xml", cases=[])
    problems = skip_budget.check(report, "bare")
    assert problems and "collected nothing" in problems[0]


def test_skipping_more_than_it_runs_fails_without_a_magic_number(tmp_path):
    report = junit(tmp_path / "r.xml", cases=[
        ("a", None),
        ("b", "no renderer here: no backend"),
        ("c", "no converter here: none"),
    ])
    problems = skip_budget.check(report, "bare")
    assert any("skipped more" in p for p in problems)


def test_a_failure_is_not_counted_as_a_pass(tmp_path):
    """Otherwise the skipped-versus-passed bound reads a red run as healthy."""
    report = tmp_path / "r.xml"
    report.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuites><testsuite name="pytest">'
        '<testcase classname="t" name="broken"><failure message="boom">t</failure></testcase>'
        '<testcase classname="t" name="skipped1">'
        '<skipped type="pytest.skip" message="no renderer here">x</skipped></testcase>'
        "</testsuite></testsuites>", encoding="utf-8")
    total, passed, skips = skip_budget.read(report)
    assert (total, passed, len(skips)) == (2, 0, 1)


def test_an_absent_junit_document_is_an_error_not_a_pass(tmp_path):
    assert skip_budget.main(["--junit", str(tmp_path / "nope.xml"),
                             "--profile", "bare"]) == 2


#: `pytest.skip("…")`, `pytest.skip(f"…")` and `reason="…"`, first literal only.
#: A reason built by implicit concatenation across lines contributes its first
#: segment, which is the part that names the cause.
_REASON = re.compile(
    r"""(?:pytest\.skip\(|reason\s*=\s*)\s*f?(['"])(.+?)\1""", re.S)
#: `importorskip("pymupdf")` — the message pytest writes is
#: `could not import 'pymupdf': …`, so the target is what has to classify.
_IMPORT = re.compile(r"""importorskip\(\s*(['"])(.+?)\1""")


def _reasons_the_suite_writes() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted(TESTS.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8")
        for _, literal in _REASON.findall(text):
            if "\n" in literal:
                continue
            found.append((path.name, literal))
        for _, module in _IMPORT.findall(text):
            found.append((path.name, f"could not import '{module}': No module named x"))
    return found


def test_the_suite_writes_skip_reasons_this_file_knows_about():
    """Read out of `tests/*.py`, so the allowlist cannot quietly fall behind."""
    reasons = _reasons_the_suite_writes()
    assert len(reasons) > 10, (
        f"only {len(reasons)} skip reasons found in tests/; the scan is broken, "
        f"not the suite")
    unknown = [(where, reason) for where, reason in reasons
               if skip_budget.classify(reason) is None]
    assert not unknown, (
        "these skip reasons exist in the suite but no cause in "
        "tests/skip_budget.py matches them, so a CI run containing one would "
        f"fail the budget: {unknown}")


@pytest.mark.parametrize("reason, cause", [
    ("nothing on this machine lays a document out", "no render backend"),
    ("PyMuPDF cannot read a page back here", "no render backend"),
    ("the Persian language pack is not reachable from here", "no OCR toolchain"),
    ("could not import 'pymupdf': No module named 'pymupdf'",
     "declared dependency not importable"),
    ("no bash here that can open install.sh", "no usable shell"),
])
def test_each_cause_claims_the_reason_it_is_for(reason, cause):
    """A pattern that matches everything names nothing."""
    assert skip_budget.classify(reason) == cause


def test_an_undeclared_import_skip_stays_unclassified():
    """`could not import` must not become a blanket excuse."""
    assert skip_budget.classify(
        "could not import 'numpy': No module named 'numpy'") is None
