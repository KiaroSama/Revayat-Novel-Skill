"""What a reviewer saw, filed so a gate can weigh it - and refused when it cannot.

The deterministic checks answer geometric questions. This stage answers the ones
that are only visible as pixels, so the whole value of it is that it cannot be
satisfied by accident: an unanswered question is not a pass, a typo is not a
pass, and a verdict about a page that has since been re-rendered describes a
page that no longer exists.
"""

from __future__ import annotations

import json

import pytest

import review
import runstate


def _rendered(work_dir, page: int = 1, digest: str = "a" * 64) -> str:
    """Put a page in the state a reviewer would find it in."""
    runstate.RunState(work_dir).set_page(page, "rendered",
                                         hashes={"render": digest})
    return digest


def test_a_full_answer_sheet_is_filed_against_the_render_it_describes(tmp_path):
    digest = _rendered(tmp_path)
    filed = review.record(tmp_path, 1, dict.fromkeys(review.QUESTIONS, True),
                          note="both renders look like the same page")

    assert filed["ok"] is True
    assert filed["render_sha256"] == digest
    assert review.verdict(tmp_path, 1)["ok"] is True

    on_disk = json.loads(review.review_path(tmp_path, 1).read_text(encoding="utf-8"))
    assert on_disk["schema"] == review.SCHEMA
    assert set(on_disk["answers"]) == set(review.QUESTIONS)


def test_a_question_nobody_answered_is_not_a_question_nobody_minded(tmp_path):
    """The cheapest way to fake a review is to answer four of five questions."""
    _rendered(tmp_path)
    answers = dict.fromkeys(review.QUESTIONS, True)
    answers.pop("figure-placement")

    filed = review.record(tmp_path, 1, answers)
    assert filed["ok"] is False and filed["refused"] == "incomplete"
    assert "figure-placement" in filed["detail"]
    assert not review.review_path(tmp_path, 1).exists(), (
        "a partial answer sheet must not be on disk at all: a later reader "
        "would find a review file and take the page as looked at"
    )


def test_a_rejection_says_which_questions_failed(tmp_path):
    _rendered(tmp_path)
    answers = dict.fromkeys(review.QUESTIONS, True)
    answers["script-integrity"] = False

    filed = review.record(tmp_path, 1, answers, note="letters are disconnected")
    assert filed["ok"] is False and filed["failed"] == ["script-integrity"]

    seen = review.verdict(tmp_path, 1)
    assert seen["ok"] is False and seen["refused"] == "review-rejected"
    assert "script-integrity" in seen["detail"]
    assert "letters are disconnected" in seen["detail"]


def test_a_review_does_not_survive_the_page_being_rendered_again(tmp_path):
    """Otherwise "reviewed" is a sticker, not evidence."""
    _rendered(tmp_path, digest="a" * 64)
    assert review.record(tmp_path, 1, dict.fromkeys(review.QUESTIONS, True))["ok"]
    assert review.verdict(tmp_path, 1)["ok"] is True

    _rendered(tmp_path, digest="b" * 64)
    stale = review.verdict(tmp_path, 1)
    assert stale["ok"] is False and stale["refused"] == "stale-review"


def test_a_page_nobody_rendered_cannot_be_reviewed(tmp_path):
    filed = review.record(tmp_path, 1, dict.fromkeys(review.QUESTIONS, True))
    assert filed["ok"] is False and filed["refused"] == "not-rendered"


def test_an_unreviewed_page_refuses_rather_than_defaults(tmp_path):
    seen = review.verdict(tmp_path, 7)
    assert seen["ok"] is False and seen["refused"] == "not-reviewed"
    for name in review.QUESTIONS:
        assert name in seen["detail"], "the refusal never says what to answer"


def test_a_damaged_review_is_not_a_pass(tmp_path):
    _rendered(tmp_path)
    review.review_path(tmp_path, 1).parent.mkdir(parents=True, exist_ok=True)
    review.review_path(tmp_path, 1).write_text("{ not json", encoding="utf-8")

    seen = review.verdict(tmp_path, 1)
    assert seen["ok"] is False and seen["refused"] == "unreadable-review"


@pytest.mark.parametrize("text", ["hierarchy=maybe", "hierarchy=", "spelling=yes",
                                  "hierarchy"])
def test_an_answer_that_is_not_yes_or_no_is_refused(text):
    """A typo must fail loudly; silently reading as `no` hides a real pass too."""
    with pytest.raises(ValueError):
        review.parse_answer(text)


@pytest.mark.parametrize("word", ["yes", "Y", "TRUE", "ok", "pass"])
def test_the_ordinary_ways_of_writing_yes_are_understood(word):
    assert review.parse_answer(f"hierarchy={word}") == ("hierarchy", True)


@pytest.mark.parametrize("word", ["no", "N", "false", "fail"])
def test_the_ordinary_ways_of_writing_no_are_understood(word):
    assert review.parse_answer(f"hierarchy={word}") == ("hierarchy", False)


def test_every_question_asks_for_something_geometry_cannot_answer(tmp_path):
    """A question a deterministic check already answers wastes the reviewer.

    Not a text match on the wording — a check that each question is *stated*,
    because a question with no explanation is one a reviewer answers from habit.
    """
    for name, asked in review.QUESTIONS.items():
        assert asked.endswith("?") or "?" in asked, f"{name} is not a question"
        assert len(asked) > 60, f"{name} is too terse to answer honestly"
