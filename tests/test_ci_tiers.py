"""Marking a tier must not hide it from the build.

`pytest.ini` names two expensive tiers — `render` and `ocr` — so a contributor can
leave them to CI instead of spending minutes on a real Word render and a real OCR
toolchain locally. That is only safe while **CI runs everything**: the moment a
workflow passes `-m`, a marked test stops being "slow here" and becomes "never
run anywhere", which is the same as deleting it with extra steps.

So three things are asserted from the files themselves: every marker a test uses
is declared, no CI job deselects one, and every suite on disk is still reached.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
WORKFLOWS = ROOT / ".github" / "workflows"

#: `-m <expression>` **after** the word `pytest`, in any quoting a workflow might
#: use. Anchored that way because `python -m pytest` contains `-m` too, and a
#: pattern that matched it called every CI job a deselection — a check that fires
#: on everything is as useless as one that fires on nothing.
_DESELECT = re.compile(r"-m\s*(?:=|\s)\s*['\"]?(?:not\s|\w+\s*(?:and|or)\s|render|ocr)")
#: `pytestmark = pytest.mark.<name>` and `@pytest.mark.<name>`.
_USED = re.compile(r"pytest\.mark\.([a-z_]+)")
#: `    name: description` under pytest.ini's `markers =`.
_DECLARED = re.compile(r"^\s{4}([a-z_]+):", re.M)


def _pytest_ini() -> str:
    return (ROOT / "pytest.ini").read_text(encoding="utf-8")


def test_every_marker_a_test_uses_is_declared():
    """An undeclared marker is a typo that silently selects nothing."""
    declared = set(_DECLARED.findall(_pytest_ini()))
    assert {"render", "ocr"} <= declared, declared

    builtin = {"parametrize", "skipif", "skip", "xfail", "usefixtures", "filterwarnings"}
    used: set[str] = set()
    for path in sorted(TESTS.glob("test_*.py")):
        used |= set(_USED.findall(path.read_text(encoding="utf-8")))

    undeclared = sorted(used - declared - builtin)
    assert not undeclared, (
        f"these markers are used but not declared in pytest.ini: {undeclared}. "
        f"An undeclared marker selects nothing and warns, so a tier named this "
        f"way would be skipped everywhere.")


def test_no_ci_job_deselects_a_tier():
    """The one rule that makes marking safe: CI runs the whole suite.

    `render` and `ocr` are slow on a laptop and free on a runner. If a workflow
    ever passes `-m`, the tier stops being covered at all — and the failure mode
    is invisible, because the jobs still go green.
    """
    offenders = []
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        for line in workflow.read_text(encoding="utf-8").splitlines():
            at = line.find("pytest")
            if at < 0:
                continue
            if _DESELECT.search(line[at:]):
                offenders.append(f"{workflow.name}: {line.strip()}")
    assert not offenders, (
        "these CI steps deselect tests by marker:\n  " + "\n  ".join(offenders)
        + "\nA marked tier must still run somewhere. If a job genuinely needs a "
          "subset, give it its own documented lane instead.")


def test_the_local_command_the_docs_hand_a_contributor_is_the_cheap_one():
    """AGENTS.md has to name the fast command, or nobody benefits from the tiers."""
    guide = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "not render and not ocr" in guide, (
        "AGENTS.md does not tell a contributor how to skip the expensive tiers, "
        "so marking them buys nothing")


def test_the_marked_modules_are_the_expensive_ones():
    """A marker on the wrong module is worse than none: it hides a fast test.

    Checked structurally rather than by timing: a module is in a tier because it
    drives the real renderer or the real OCR toolchain, and that shows in what it
    imports and calls.
    """
    expected = {
        "test_docqa.py": "render",
        "test_renderqa.py": "render",
        "test_pagerun.py": "render",
        "test_page_acceptance.py": "render",
        "test_render_cache.py": "render",
        "test_review.py": "render",
        "test_integration_ocr.py": "ocr",
    }
    for name, mark in expected.items():
        text = (TESTS / name).read_text(encoding="utf-8")
        assert f"pytestmark = pytest.mark.{mark}" in text, (
            f"{name} is an expensive module and carries no {mark} marker")


@pytest.mark.parametrize("tier", ["render", "ocr"])
def test_deselecting_a_tier_still_leaves_a_suite_worth_running(tier):
    """And the cheap run has to be worth something on its own.

    If deselecting a tier left almost nothing, the fast command would be a
    placebo. Counted by collection, not by guess.
    """
    import subprocess

    run = subprocess.run(
        [sys.executable, "-m", "pytest", str(TESTS), "--collect-only", "-q",
         "-p", "no:cacheprovider", "-m", f"not {tier}"],
        capture_output=True, text=True, cwd=ROOT, timeout=300)
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    collected = re.search(r"(\d+)\s*/?\s*(\d+)?\s*tests? collected", run.stdout)
    assert collected, run.stdout[-800:]
    assert int(collected.group(1)) > 400, (
        f"deselecting {tier} leaves only {collected.group(1)} tests")
