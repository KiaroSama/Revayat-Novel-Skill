"""A repair budget has to count something a repair does not change.

Both review stages bounded their loops with a round counter, and cleared it
whenever the content revision moved. A repair is what moves the content
revision. So the counter was cleared by the very thing it was counting, and the
cap could not be reached. Measured on a one-unit book whose negation stayed
inverted through five real rewrites, before `repairlog` existed:

    attempt 1: round=1 history=1 rev=9d741dc0 repair_ok=True
    attempt 2: round=1 history=1 rev=8fc83bd1 repair_ok=True
    attempt 3: round=1 history=1 rev=41e1b251 repair_ok=True
    attempt 4: round=1 history=1 rev=db444ddf repair_ok=True
    attempt 5: round=1 history=1 rev=dabe0297 repair_ok=True

The fluency stage had the same shape one level down: applying an edit moves the
Persian, so every applied pass came back as round 1 and another blind rewrite of
the same sentence was permitted, however many had been applied already.

What is asked here is the policy, not the plumbing: real changing repairs stop,
A→B→A oscillations stop, a fix closes its own episode, an unrelated later issue
keeps its own budget, and "no progress" and "budget exhausted" stay separately
reachable — because a cap small enough to answer every case makes the specific
refusal dead code, which this project has already shipped once.

The per-stage suites keep their own cases; this one is about the shared rule, on
both stages, through the real commands.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bookir as ir  # noqa: E402
import fluency  # noqa: E402
import meaning  # noqa: E402
import merge as merging  # noqa: E402
import repairlog  # noqa: E402
from tests_support import review_reply  # noqa: E402

SOURCE = [
    "She did not refuse the invitation.",
    "The house stood at the end of a long lane.",
]
#: The first one is wrong in the way the reviewer keeps reporting; the second is
#: an ordinary correct rendering, so a later finding about it is a *different*
#: issue rather than the same one coming back.
TARGET = [
    "او دعوت را رد نکرد.",
    "خانه در انتهای کوچه‌ای بلند بود.",
]
#: Two wordings to alternate between, which is the oscillation the policy has to
#: notice: each one is a real edit, and the pair makes no progress.
FIRST = TARGET[0]
SECOND = "او دعوت را پذیرفت."


@pytest.fixture()
def translated(tmp_path: Path) -> Path:
    book = ir.new_book(source_path="sample.epub", source_format="epub",
                       title="A Small Book", author="Test Author")
    for index, text in enumerate(SOURCE, start=1):
        book["blocks"].append(ir.make_block("paragraph", index, text=text))
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    # Through the real door, so the review reads what a merge actually writes.
    book = ir.load_book(path)
    resolve = merging.addressing(book)
    for block, target in zip(book["blocks"], TARGET):
        container, field = resolve(block["id"])
        container[field] = target
    # The title page is published prose too: a book whose metadata has no
    # Persian is unfinished, and the inventory now says so.
    book["meta"]["title_target"] = "کتابی کوچک"
    book["meta"]["author_target"] = "نویسندهٔ آزمون"
    ir.save_book(book, path)
    return path


def _reply(out_dir: Path, sheet_id: str, body: str) -> None:
    ir.write_text(out_dir / f"out_{sheet_id}.md",
                  review_reply(out_dir / f"{sheet_id}.md", body))


def _review(book_path: Path, out: Path, body: str) -> dict:
    """One full meaning round: fresh sheets, one reply per sheet, recorded."""
    for sheet_id in meaning.write_sheets(book_path, out)["sheets"]:
        _reply(out, sheet_id, body + f"!! reviewed {sheet_id}\n")
    return meaning.record(out, book_path)


def _retarget(book_path: Path, unit_id: str, text: str) -> None:
    """A real edit, written where a merge writes it."""
    book = ir.load_book(book_path)
    container, field = merging.addressing(book)(unit_id)
    container[field] = text
    ir.save_book(book, book_path)


# --------------------------------------------------------------------------- #
# The meaning stage
# --------------------------------------------------------------------------- #

def test_five_real_repairs_do_not_buy_five_first_rounds(translated, tmp_path):
    """The observation, as a test: each rewrite is an attempt, and they run out."""
    out = tmp_path / "review"
    rewrites = ["او دعوت را پذیرفت.", "او دعوت را قبول کرد.",
                "او دعوت را رد کرد.", "او دعوت را نپذیرفت."]

    allowed = 0
    for attempt, rewrite in enumerate(rewrites, start=1):
        _review(translated, out, f"?? b00001 sense\nStill inverted ({attempt}).\n")
        request = meaning.repair_requests(out)
        if not request["ok"]:
            break
        allowed += 1
        _retarget(translated, "b00001", rewrite)

    assert allowed == repairlog.MAX_ATTEMPTS - 1, (
        "a finite budget has to be reachable by repairs that really change the text")
    assert request["refused"] == "rounds-exhausted", request
    assert request["escalate"][0]["issue"] == "b00001/sense"
    assert len(request["escalate"][0]["arguments"]) == repairlog.MAX_ATTEMPTS


def test_an_oscillation_stops_before_the_budget_does(translated, tmp_path):
    """A→B→A: two wordings, each a real edit, and no progress between them.

    Reported as an oscillation rather than as an exhausted budget, and that
    distinction is the whole point of keeping both: "the next attempt will put
    back something already rejected" tells an operator what to do, and "you have
    had three goes" does not.
    """
    out = tmp_path / "review"
    _review(translated, out, "?? b00001 sense\nInverted (A).\n")
    assert meaning.repair_requests(out)["ok"] is True

    _retarget(translated, "b00001", SECOND)
    _review(translated, out, "?? b00001 sense\nNow a clause is gone (B).\n")
    assert meaning.repair_requests(out)["ok"] is True

    _retarget(translated, "b00001", FIRST)
    _review(translated, out, "?? b00001 sense\nBack to inverted (A).\n")

    request = meaning.repair_requests(out)
    assert request["ok"] is False
    assert request["refused"] == "oscillating", request
    assert "already rejected" in request["detail"]


def test_no_progress_and_an_exhausted_budget_are_different_answers(translated,
                                                                   tmp_path):
    """Both reachable, from the same starting state, by different behaviour."""
    stalled = tmp_path / "stalled"
    _review(translated, stalled, "?? b00001 sense\nInverted.\n")
    _review(translated, stalled, "?? b00001 sense\nStill inverted.\n")
    assert meaning.repair_requests(stalled)["refused"] == "no-new-evidence"

    moving = tmp_path / "moving"
    for attempt, rewrite in enumerate(["او دعوت را پذیرفت.",
                                       "او دعوت را قبول کرد."], start=1):
        _review(translated, moving, f"?? b00001 sense\nWrong, try {attempt}.\n")
        _retarget(translated, "b00001", rewrite)
    _review(translated, moving, "?? b00001 sense\nWrong again.\n")
    assert meaning.repair_requests(moving)["refused"] == "rounds-exhausted"


def test_a_fix_closes_its_episode_and_returns_the_budget(translated, tmp_path):
    """A repair that worked is not half a loop; it is a closed episode.

    And when the same issue comes back later it is a recurrence with its own
    budget — but every wording already rejected travels with it, so closing an
    episode cannot be used to launder an oscillation.
    """
    out = tmp_path / "review"
    _review(translated, out, "?? b00001 sense\nInverted.\n")
    _retarget(translated, "b00001", SECOND)

    fixed = _review(translated, out, "")
    assert fixed["resolved"] == ["b00001/sense"], fixed
    assert fixed["episodes"]["b00001/sense"]["state"] == "closed"
    assert meaning.repair_requests(out)["ok"] is True

    _retarget(translated, "b00001", "او دعوت را نپذیرفت.")
    again = _review(translated, out, "?? b00001 sense\nA new problem here.\n")
    episode = again["episodes"]["b00001/sense"]
    assert episode["attempts"] == 1 and episode["recurrences"] == 1, episode
    assert episode["superseded"][0]["superseded_because"] == "recurred"
    assert meaning.repair_requests(out)["ok"] is True

    # The laundering attempt: put back the wording the closed episode rejected.
    _retarget(translated, "b00001", FIRST)
    _review(translated, out, "?? b00001 sense\nInverted again.\n")
    request = meaning.repair_requests(out)
    assert request["ok"] is False
    assert request["refused"] == "oscillating", request


# --------------------------------------------------------------------------- #
# The fluency stage, through apply and a fresh meaning approval each time
# --------------------------------------------------------------------------- #

def _settled(book_path: Path, meaning_dir: Path) -> dict:
    """Re-run the bilingual review and approve it, which licenses a pass."""
    return _review(book_path, meaning_dir, "")


def _pass(book_path: Path, out: Path, meaning_dir: Path, target: str) -> dict:
    """One blind pass proposing one replacement, recorded."""
    sheets = fluency.write_sheets(book_path, out, meaning_dir)["sheets"]
    for sheet_id in sheets:
        body = (f"++ b00001 calque\n{target}\n" if sheet_id == sheets[0] else "")
        _reply(out, sheet_id, body + f"!! reviewed {sheet_id}\n")
    return fluency.record(out, book_path)


def test_applying_an_edit_does_not_buy_another_blind_pass(translated, tmp_path):
    """Five real fluency edits, each followed by a fresh meaning approval.

    That sequence used to be permitted for ever: `apply` moved the Persian, the
    round counter reset on the moved revision, and the next pass was round 1
    again. The episode counts proposals about the unit instead, so the third one
    about the same unit and rubric is where it stops.
    """
    review_dir, out = tmp_path / "meaning", tmp_path / "fluency"
    assert _settled(translated, review_dir)["ok"] is True

    smoothings = ["او دعوت را نپذیرفت.", "او دعوت را رد نمی‌کرد.",
                  "او دعوت را پس نزد.", "او از دعوت سر باز نزد."]
    applied = 0
    for target in smoothings:
        recorded = _pass(translated, out, review_dir, target)
        if not recorded["ok"]:
            break
        assert fluency.apply_edits(translated, out)["ok"] is True
        applied += 1
        # The book moved, so the bilingual review of it is stale by
        # construction. Re-running it is what the stage requires — and it used to
        # be what reset the budget.
        assert _settled(translated, review_dir)["ok"] is True

    assert applied == repairlog.MAX_ATTEMPTS - 1, (
        "applying an edit must not return the budget it spent")
    assert recorded["refused"] == "rounds-exhausted", recorded
    assert recorded["edits"] == [], "a refused pass must not offer edits to apply"
    assert recorded["refused_edits"], "and must keep them as evidence"


def test_smoothing_that_puts_back_a_rejected_wording_is_an_oscillation(translated,
                                                                      tmp_path):
    """B→A on the Persian side, through two real applications."""
    review_dir, out = tmp_path / "meaning", tmp_path / "fluency"
    assert _settled(translated, review_dir)["ok"] is True
    before = FIRST

    assert _pass(translated, out, review_dir, SECOND)["ok"] is True
    assert fluency.apply_edits(translated, out)["ok"] is True
    assert _settled(translated, review_dir)["ok"] is True

    # Back to the wording the first pass replaced: a real edit that undoes the
    # previous real edit.
    recorded = _pass(translated, out, review_dir, before)
    assert recorded["ok"] is True, "the second proposal is not itself a loop"
    assert fluency.apply_edits(translated, out)["ok"] is True
    assert _settled(translated, review_dir)["ok"] is True

    third = _pass(translated, out, review_dir, SECOND)
    assert third["ok"] is False
    assert third["refused"] == "oscillating", third
    refused = fluency.apply_edits(translated, out)
    assert refused["ok"] is False and refused["refused"] == "oscillating"
