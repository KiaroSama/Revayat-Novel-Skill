"""The benchmark has to grade the cases it claims to grade.

A benchmark nobody checks is a file of opinions. Three things are asserted: the
cases are well formed (every accepted rendering passes its own case, every
recorded wrong one fails it), the grader tells "not seen" from "wrong", and it
refuses to produce an overall number — because an average over these axes is not a
fact about a translation, and a single number is what invites tuning against the
benchmark instead of the book.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation"))

import score  # noqa: E402

CASES = score.load_cases()


def test_the_benchmark_has_cases_across_several_axes():
    axes = {case["axis"] for case in CASES["cases"]}
    assert len(CASES["cases"]) >= 8, "too few cases to be a smoke test"
    assert len(axes) >= 6, f"only {len(axes)} axes: {sorted(axes)}"
    assert CASES["schema"].startswith("revayat-novel/benchmark")


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["id"])
def test_every_case_is_well_formed(case):
    """Each case names one axis, several acceptable renderings and a rationale."""
    assert case["accepts"], f"{case['id']} accepts nothing, so it can only fail"
    assert len({score.normalise(text) for text in case["accepts"]}) == len(case["accepts"]), (
        f"{case['id']} lists the same rendering twice under different punctuation")
    assert case.get("rationale"), f"{case['id']} says nothing about why it exists"
    for wrong in case.get("fails", []):
        assert wrong.get("why"), f"{case['id']} has a wrong rendering with no reason"


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["id"])
def test_each_accepted_rendering_passes_its_own_case(case):
    for accepted in case["accepts"]:
        graded = score.grade_one(case, accepted)
        assert graded["verdict"] == "pass", (accepted, graded)


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["id"])
def test_each_recorded_wrong_rendering_fails_its_own_case(case):
    """These are the ones structural QA cannot see, so the benchmark must."""
    for wrong in case.get("fails", []):
        graded = score.grade_one(case, wrong["text"])
        assert graded["verdict"] == "fail", (wrong["text"], graded)
        assert graded["note"] == wrong["why"]


def test_punctuation_and_letter_forms_do_not_change_a_verdict():
    """A translator's comma is not a translation decision.

    Arabic yeh and kaf are folded too: `falint` normalises them, so a pass that
    depended on which form arrived would be grading the typography pass.
    """
    case = CASES["cases"][0]
    accepted = case["accepts"][0]
    loosened = accepted.replace("،", ",").replace("ی", "ي") + "  "
    assert score.grade_one(case, loosened)["verdict"] == "pass"


def test_an_unseen_rendering_is_unknown_rather_than_wrong():
    """The honest third verdict. A new Persian sentence may be perfectly good."""
    case = CASES["cases"][0]
    graded = score.grade_one(case, "این جمله تازه است و این پرونده آن را ندیده است.")
    assert graded["verdict"] == "unknown"
    assert "add it to" in graded["note"], graded


def test_no_answer_at_all_is_unknown_not_a_pass():
    case = CASES["cases"][0]
    assert score.grade_one(case, "")["verdict"] == "unknown"


def test_the_report_has_no_overall_number():
    """And says so, so nobody has to search for one."""
    report = score.grade({case["id"]: case["accepts"][0] for case in CASES["cases"]})

    assert report["overall"] is None
    assert "deliberately absent" in report["overall_note"]
    assert all(item["verdict"] == "pass" for item in report["cases"])
    assert set(report["by_axis"]) == {case["axis"] for case in CASES["cases"]}


def test_the_cli_exits_non_zero_only_on_a_known_wrong_rendering(tmp_path, capsys):
    """`unknown` must not fail a build: it is a question, not a defect."""
    unseen = tmp_path / "unseen.json"
    unseen.write_text(json.dumps({CASES["cases"][0]["id"]: "جمله‌ای تازه."}),
                      encoding="utf-8")
    assert score.main(["--answers", str(unseen)]) == 0
    capsys.readouterr()

    wrong_case = next(case for case in CASES["cases"] if case.get("fails"))
    wrong = tmp_path / "wrong.json"
    wrong.write_text(
        json.dumps({wrong_case["id"]: wrong_case["fails"][0]["text"]},
                   ensure_ascii=False), encoding="utf-8")
    assert score.main(["--answers", str(wrong)]) == 1
