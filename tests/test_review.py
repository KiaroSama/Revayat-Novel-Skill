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


# --------------------------------------------------------------------------- #
# The comparison sheet: a convenience, never the evidence
# --------------------------------------------------------------------------- #

def test_a_landscape_source_sits_beside_a_portrait_target(tmp_path):
    """The case the sheet exists for: two shapes, one common height.

    Measured (spike 010): a 612x396 source beside a 396x612 target comes out
    2494x1138 and 26 KB, both legible, neither distorted.
    """
    pytest.importorskip("PIL.Image")
    from PIL import Image

    source = tmp_path / "source.png"
    target = tmp_path / "target.png"
    Image.new("RGB", (1224, 792), (250, 250, 250)).save(source)   # landscape
    Image.new("RGB", (792, 1224), (250, 250, 250)).save(target)   # portrait

    made = review.compare([("source", source), ("target 1/1", target)],
                          tmp_path / "compare.png", height=400)
    assert made["ok"], made
    with Image.open(tmp_path / "compare.png") as composed:
        assert composed.height == (400 + review.COMPARE_LABEL_BAND
                                   + 2 * review.COMPARE_PAD)
        assert composed.width > 400, "the panels were not placed side by side"


def test_too_many_panels_sends_the_reviewer_to_the_files_instead(tmp_path):
    """Six panels is a strip nobody can read. The sheet says so rather than
    silently not existing."""
    pytest.importorskip("PIL.Image")
    from PIL import Image

    panels = []
    for index in range(6):
        path = tmp_path / f"p{index}.png"
        Image.new("RGB", (400, 600), (250, 250, 250)).save(path)
        panels.append((f"sheet {index}", path))

    made = review.compare(panels, tmp_path / "compare.png")
    assert made["ok"] is False
    assert made["panels"] == 6
    assert "ceiling" in made["detail"]
    assert not (tmp_path / "compare.png").exists()


def test_a_missing_panel_is_drawn_as_absent_not_skipped(tmp_path):
    """`evidence_digest` counts a missing file as ABSENT; the sheet agrees."""
    pytest.importorskip("PIL.Image")
    from PIL import Image

    present = tmp_path / "present.png"
    Image.new("RGB", (400, 600), (250, 250, 250)).save(present)

    made = review.compare([("source", tmp_path / "gone.png"),
                           ("target 1/1", present)],
                          tmp_path / "compare.png", height=200)
    assert made["ok"], made
    assert made["panels"] == 2, "the missing panel was skipped instead of drawn"


def test_composing_a_sheet_cannot_stop_a_page_being_reported(tmp_path, monkeypatch):
    """Producing evidence must never be what stops a page being reported on."""
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(review, "_panels", boom)
    made = review.compare([("source", tmp_path / "a.png")], tmp_path / "c.png")
    assert made["ok"] is False
    assert "boom" in made["detail"]


def test_the_sheet_is_not_part_of_the_review_identity(tmp_path):
    """The digest stays over the panels.

    The composition depends on Pillow's resize filter and its default bitmap
    font, neither pinned across versions, so a digest over the sheet would stale
    every standing review on a Pillow upgrade.
    """
    pytest.importorskip("PIL.Image")
    from PIL import Image

    source = tmp_path / "source.png"
    target = tmp_path / "target.png"
    for path in (source, target):
        Image.new("RGB", (400, 600), (250, 250, 250)).save(path)

    before = review.evidence_digest([source, target])
    review.compare([("source", source), ("target 1/1", target)],
                   tmp_path / "compare.png", height=200)
    after = review.evidence_digest([source, target])
    assert before == after, "composing a sheet changed the review identity"
