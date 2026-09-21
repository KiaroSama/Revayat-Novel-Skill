"""The Persian-only pass has to actually be blind, and must not pass on its own.

Two claims carry this stage, and both are checked by watching a refusal rather
than a success:

* **The sheet contains no source.** That is the entire reason the pass exists
  separately from the bilingual review — a reviewer who can see the English reads
  calque word order as sense — so a sheet that leaked one word of source would
  make the stage decorative while still reporting green.
* **A blind edit is a proposal, never an acceptance.** Applying one moves the
  book, which makes the meaning review of it stale by construction, and this
  stage's verdict stays false until that review has been redone against the
  source. The final source comparison is therefore not a step a caller remembers
  — it is the only path to a true verdict, and the test proves there is no other.

Plus the ways a semantic gate accidentally passes, which this stage inherits from
`meaning` and must refuse the same way: a sheet nobody read, silence read as
approval, a verdict that outlived its text, a digest nobody can recompute, an
edit that changes nothing, and a loop that keeps proposing the same rewrite.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bookir as ir  # noqa: E402
import fluency  # noqa: E402
import meaning  # noqa: E402
import merge as merging  # noqa: E402
from tests_support import review_reply  # noqa: E402

SOURCE = [
    "The man he had seen at the station reading a book left.",
    "She did not refuse the invitation.",
    "The house stood at the end of a long lane.",
]
TARGET = [
    "مردی که او را در ایستگاه دیده بود که کتاب می‌خواند رفت.",
    "او دعوت را رد نکرد.",
    "خانه در انتهای کوچه‌ای بلند بود.",
]
#: The same first sentence with Persian's own clause order — a `calque` edit that
#: changes no fact, which is what a fluency edit is supposed to look like.
SMOOTHED = "مردی رفت که او را در ایستگاه، در حال کتاب خواندن، دیده بود."


@pytest.fixture()
def translated(tmp_path: Path) -> Path:
    book = ir.new_book(source_path="sample.epub", source_format="epub",
                       title="A Small Book", author="Test Author")
    for index, text in enumerate(SOURCE, start=1):
        book["blocks"].append(ir.make_block("paragraph", index, text=text))
    path = tmp_path / "book.json"
    ir.save_book(book, path)
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


@pytest.fixture()
def settled(translated: Path, tmp_path: Path) -> Path:
    """A meaning review that passed, which is what licenses a fluency pass."""
    out = tmp_path / "meaning"
    for sheet_id in meaning.write_sheets(translated, out)["sheets"]:
        _reply(out, sheet_id, f"!! reviewed {sheet_id}\n")
    assert meaning.record(out, translated)["ok"] is True
    return out


def _resettle(book_path: Path, meaning_dir: Path) -> dict:
    """Re-run the meaning review against the book as it now stands.

    This is the brief's final source comparison. It is an ordinary re-run of the
    bilingual stage, deliberately: a second implementation of "does this say what
    the source says" would be a second opinion nobody reconciles.
    """
    for sheet_id in meaning.write_sheets(book_path, meaning_dir)["sheets"]:
        _reply(meaning_dir, sheet_id, f"!! reviewed {sheet_id}\n")
    return meaning.record(meaning_dir, book_path)


def _reply(out_dir: Path, sheet_id: str, body: str) -> None:
    """A reply echoing its sheet's review line, which the transport requires."""
    ir.write_text(out_dir / f"out_{sheet_id}.md",
                  review_reply(out_dir / f"{sheet_id}.md", body))


# --------------------------------------------------------------------------- #
# Blindness — the property the whole stage exists for
# --------------------------------------------------------------------------- #

def test_no_sheet_contains_any_source_text(translated, settled, tmp_path):
    """One leaked English sentence makes the pass decorative and still green."""
    out = tmp_path / "fluency"
    report = fluency.write_sheets(translated, out, settled)
    assert report["ok"] is True

    for sheet_id in report["sheets"]:
        text = (out / f"{sheet_id}.md").read_text(encoding="utf-8")
        for source in SOURCE:
            assert source not in text, (
                f"{sheet_id} shows the source; the reviewer can no longer "
                f"answer whether the Persian reads as Persian")
        # Not even a stray English word from the source, which would be enough
        # to anchor a reader's reconstruction of the sentence.
        for word in ("station", "invitation", "lane"):
            assert word not in text
        for target in TARGET:
            assert target in text, "the Persian under review is missing"


def test_the_sheet_carries_persian_neighbours_for_the_adjacency_rubrics(
        translated, settled, tmp_path):
    """`flow` and `register` are questions a unit shown alone cannot be asked."""
    out = tmp_path / "fluency"
    report = fluency.write_sheets(translated, out, settled, per_sheet=1)
    # The title and the byline are published units too, so one unit per sheet is
    # five sheets: they come first, then the three paragraphs.
    assert len(report["sheets"]) == len(TARGET) + 2

    middle = (out / "sheet_0004.md").read_text(encoding="utf-8")
    assert TARGET[1] in middle
    assert TARGET[0] in middle and TARGET[2] in middle, (
        "neither neighbour is on the sheet, so flow cannot be judged")
    # And the context is marked as context, so it is not edited by mistake.
    assert "not under review" in middle

    # Context stays inside one part: the unit before the first paragraph is the
    # byline, and a transition from a title page to a chapter is not one.
    first_prose = (out / "sheet_0003.md").read_text(encoding="utf-8")
    assert TARGET[0] in first_prose
    assert "نویسندهٔ آزمون" not in first_prose, (
        "the byline is quoted as the previous line, so the reviewer is invited "
        "to smooth a transition that does not exist")


def test_an_edit_filed_against_a_context_line_is_not_read_as_its_body(
        translated, settled, tmp_path):
    out = tmp_path / "fluency"
    fluency.write_sheets(translated, out, settled, per_sheet=1)
    edits, claimed, problems = fluency.read_edits(
        f"++ b00002 calque\n~ {TARGET[0]}\n{SMOOTHED}\n!! reviewed sheet_0002\n")
    assert claimed == ["sheet_0002"]
    assert problems == []
    assert edits[0]["target"] == SMOOTHED, (
        "the quoted neighbour was swallowed into the replacement, so applying "
        "this edit would write a different unit's Persian into b00002")


# --------------------------------------------------------------------------- #
# Meaning first
# --------------------------------------------------------------------------- #

def test_sheets_are_refused_while_the_meaning_review_is_open(translated, tmp_path):
    """Smoothing disputed prose gives the translator two defects to find."""
    report = fluency.write_sheets(translated, tmp_path / "fluency",
                                  tmp_path / "never-reviewed")
    assert report["ok"] is False
    assert report["refused"] == "meaning-unsettled"
    assert report["meaning"] == "not-reviewed"
    assert not (tmp_path / "fluency").exists(), (
        "sheets were written anyway, so the refusal only reported a problem")


def test_sheets_are_refused_when_meaning_findings_are_open(translated, tmp_path):
    out = tmp_path / "meaning"
    for sheet_id in meaning.write_sheets(translated, out)["sheets"]:
        _reply(out, sheet_id,
               f"?? b00002 sense\nThe negation is reversed.\n!! reviewed {sheet_id}\n")
    meaning.record(out, translated)

    report = fluency.write_sheets(translated, tmp_path / "fluency", out)
    assert report["ok"] is False
    assert report["refused"] == "meaning-unsettled"
    assert report["meaning"] == "meaning-rejected"


# --------------------------------------------------------------------------- #
# An edit is a proposal
# --------------------------------------------------------------------------- #

def test_a_recorded_edit_that_was_never_applied_does_not_pass(
        translated, settled, tmp_path):
    out = tmp_path / "fluency"
    sheets = fluency.write_sheets(translated, out, settled)["sheets"]
    for sheet_id in sheets:
        _reply(out, sheet_id,
               f"++ b00001 calque\n{SMOOTHED}\n!! reviewed {sheet_id}\n")
    assert fluency.record(out, translated)["ok"] is True

    decided = fluency.verdict(out, translated, settled)
    assert decided["ok"] is False
    assert decided["refused"] == "edits-unapplied"


def test_applying_an_edit_leaves_the_stage_waiting_on_the_source_comparison(
        translated, settled, tmp_path):
    """The load-bearing test: there is no route to green that skips the re-check."""
    out = tmp_path / "fluency"
    sheets = fluency.write_sheets(translated, out, settled)["sheets"]
    for sheet_id in sheets:
        _reply(out, sheet_id,
               f"++ b00001 calque\n{SMOOTHED}\n!! reviewed {sheet_id}\n")
    fluency.record(out, translated)

    applied = fluency.apply_edits(translated, out)
    assert applied["ok"] is True
    assert applied["units"] == ["b00001"]
    # The book really changed, and what it replaced is kept beside the edit.
    sidecar = json.loads(fluency.sidecar_path(out).read_text(encoding="utf-8"))
    assert sidecar["changes"][0]["before"] == TARGET[0]
    assert sidecar["changes"][0]["after"] == SMOOTHED

    waiting = fluency.verdict(out, translated, settled)
    assert waiting["ok"] is False, (
        "the smoothed Persian passed without ever being compared to the source")
    assert waiting["refused"] == "meaning-unconfirmed"
    assert waiting["meaning"] == "stale-review"

    # The final source comparison, and only then does the stage pass.
    assert _resettle(translated, settled)["ok"] is True
    assert fluency.verdict(out, translated, settled)["ok"] is True


def test_the_source_comparison_can_reject_a_smoothing_that_changed_meaning(
        translated, settled, tmp_path):
    """What the re-check is for: a fluent sentence that now says something else."""
    out = tmp_path / "fluency"
    sheets = fluency.write_sheets(translated, out, settled)["sheets"]
    for sheet_id in sheets:
        # Reads beautifully in Persian. Also reverses the negation, which a blind
        # reviewer cannot possibly know.
        _reply(out, sheet_id,
               f"++ b00002 flow\nاو دعوت را پذیرفت.\n!! reviewed {sheet_id}\n")
    fluency.record(out, translated)
    assert fluency.apply_edits(translated, out)["ok"] is True

    for sheet_id in meaning.write_sheets(translated, settled)["sheets"]:
        _reply(settled, sheet_id,
               f"?? b00002 sense\nThe source says she did not refuse; the "
               f"smoothed line says she accepted.\n!! reviewed {sheet_id}\n")
    meaning.record(settled, translated)

    decided = fluency.verdict(out, translated, settled)
    assert decided["ok"] is False
    assert decided["refused"] == "meaning-unconfirmed"
    assert decided["meaning"] == "meaning-rejected"


def test_a_blind_pass_that_found_nothing_passes_without_moving_the_book(
        translated, settled, tmp_path):
    """A real result, and the one case with nothing to re-compare."""
    out = tmp_path / "fluency"
    before = ir.load_book(translated)
    for sheet_id in fluency.write_sheets(translated, out, settled)["sheets"]:
        _reply(out, sheet_id, f"!! reviewed {sheet_id}\n")
    assert fluency.record(out, translated)["ok"] is True

    decided = fluency.verdict(out, translated, settled)
    assert decided["ok"] is True
    assert decided["changed"] == []
    assert ir.load_book(translated)["blocks"] == before["blocks"], (
        "a pass that proposed nothing still rewrote the book")


# --------------------------------------------------------------------------- #
# The refusals a semantic gate needs
# --------------------------------------------------------------------------- #

def test_a_sheet_nobody_read_is_refused(translated, settled, tmp_path):
    out = tmp_path / "fluency"
    fluency.write_sheets(translated, out, settled, per_sheet=1)
    report = fluency.record(out, translated)
    assert report["ok"] is False
    assert report["refused"] == "incomplete"
    assert any("nobody read" in problem for problem in report["problems"])


def test_silence_is_not_approval(translated, settled, tmp_path):
    out = tmp_path / "fluency"
    for sheet_id in fluency.write_sheets(translated, out, settled)["sheets"]:
        _reply(out, sheet_id, "Looked at it, reads fine.\n")
    report = fluency.record(out, translated)
    assert report["ok"] is False
    # The refusal moved into the shared review transport, which names the sheet
    # it is refusing rather than listing the unclaimed ones at the end.
    assert any("nothing says this sheet was read" in problem
               for problem in report["problems"]), report


def test_an_edit_identical_to_what_is_there_is_refused(translated, settled, tmp_path):
    """It would read as a reviewed unit while changing nothing."""
    out = tmp_path / "fluency"
    for sheet_id in fluency.write_sheets(translated, out, settled)["sheets"]:
        _reply(out, sheet_id,
               f"++ b00001 calque\n{TARGET[0]}\n!! reviewed {sheet_id}\n")
    report = fluency.record(out, translated)
    assert report["ok"] is False
    assert any("identical" in problem for problem in report["problems"])


def test_an_edit_with_an_empty_body_is_refused(translated, settled, tmp_path):
    """An empty replacement would delete the unit's Persian."""
    out = tmp_path / "fluency"
    for sheet_id in fluency.write_sheets(translated, out, settled)["sheets"]:
        _reply(out, sheet_id, f"++ b00001 calque\n!! reviewed {sheet_id}\n")
    report = fluency.record(out, translated)
    assert report["ok"] is False
    assert any("no replacement given" in problem for problem in report["problems"])


def test_an_unknown_rubric_is_refused(translated, settled, tmp_path):
    out = tmp_path / "fluency"
    for sheet_id in fluency.write_sheets(translated, out, settled)["sheets"]:
        _reply(out, sheet_id,
               f"++ b00001 prettier\n{SMOOTHED}\n!! reviewed {sheet_id}\n")
    report = fluency.record(out, translated)
    assert report["ok"] is False
    assert any("not a rubric" in problem for problem in report["problems"])


def test_edits_are_refused_once_the_persian_moved_under_them(
        translated, settled, tmp_path):
    out = tmp_path / "fluency"
    for sheet_id in fluency.write_sheets(translated, out, settled)["sheets"]:
        _reply(out, sheet_id,
               f"++ b00001 calque\n{SMOOTHED}\n!! reviewed {sheet_id}\n")
    book = ir.load_book(translated)
    book["blocks"][2]["target"] = "خانه ته کوچه بود."
    ir.save_book(book, translated)

    report = fluency.record(out, translated)
    assert report["ok"] is False
    assert report["refused"] == "stale-sheets"


def test_recorded_edits_are_not_applied_twice(translated, settled, tmp_path):
    out = tmp_path / "fluency"
    for sheet_id in fluency.write_sheets(translated, out, settled)["sheets"]:
        _reply(out, sheet_id,
               f"++ b00001 calque\n{SMOOTHED}\n!! reviewed {sheet_id}\n")
    fluency.record(out, translated)
    assert fluency.apply_edits(translated, out)["ok"] is True

    again = fluency.apply_edits(translated, out)
    assert again["ok"] is False
    assert again["refused"] == "already-applied"


def test_a_digest_this_reader_cannot_recompute_is_refused_not_guessed(
        translated, settled, tmp_path):
    out = tmp_path / "fluency"
    for sheet_id in fluency.write_sheets(translated, out, settled)["sheets"]:
        _reply(out, sheet_id, f"!! reviewed {sheet_id}\n")
    fluency.record(out, translated)

    path = fluency.sidecar_path(out)
    found = json.loads(path.read_text(encoding="utf-8"))
    found["revision"] = "fluency0:" + "0" * 64
    ir.write_text(path, json.dumps(found, ensure_ascii=False) + "\n")

    decided = fluency.verdict(out, translated, settled)
    assert decided["ok"] is False
    assert decided["refused"] == "unverified-digest"


def test_a_pass_does_not_survive_the_persian_changing(translated, settled, tmp_path):
    out = tmp_path / "fluency"
    for sheet_id in fluency.write_sheets(translated, out, settled)["sheets"]:
        _reply(out, sheet_id, f"!! reviewed {sheet_id}\n")
    fluency.record(out, translated)
    assert fluency.verdict(out, translated, settled)["ok"] is True

    book = ir.load_book(translated)
    book["blocks"][1]["target"] = "او دعوت را نپذیرفت."
    ir.save_book(book, translated)

    decided = fluency.verdict(out, translated, settled)
    assert decided["ok"] is False
    assert decided["refused"] == "stale-review"


def test_distinct_blind_reviews_without_progress_stop_the_loop(translated, settled, tmp_path):
    """A fresh review event with unchanged evidence still exhausts the episode."""
    out = tmp_path / "fluency"
    fluency.write_sheets(translated, out, settled)
    proposal = f"++ b00001 calque\n{SMOOTHED}\n!! reviewed sheet_0001\n"
    _reply(out, "sheet_0001", proposal)

    first = fluency.record(out, translated)
    assert first["ok"] is True

    _reply(out, "sheet_0001", "Second reading of the unchanged passage.\n" + proposal)
    second = fluency.record(out, translated)
    assert second["ok"] is False
    assert second["refused"] == "no-new-evidence"
    # The proposals are kept where an escalation can read them, and out of the
    # way of `apply`, which refuses a refused pass by name.
    assert second["refused_edits"], second
    assert second["edits"] == [], second
    assert second["escalate"][0]["arguments"] == [SMOOTHED, SMOOTHED], second
    refused = fluency.apply_edits(translated, out)
    assert refused["ok"] is False and refused["refused"] == "no-new-evidence"


def test_a_book_with_nothing_translated_is_a_translation_gap_not_a_fluency_one(
        tmp_path):
    book = ir.new_book(source_path="s.epub", source_format="epub")
    book["blocks"].append(ir.make_block("paragraph", 1, text="Untranslated."))
    path = tmp_path / "book.json"
    ir.save_book(book, path)

    out = tmp_path / "meaning"
    for sheet_id in meaning.write_sheets(path, out)["sheets"]:
        _reply(out, sheet_id, f"!! reviewed {sheet_id}\n")
    meaning.record(out, path)

    report = fluency.write_sheets(path, tmp_path / "fluency", out)
    assert report["ok"] is False
    assert report["refused"] in {"nothing-translated", "meaning-unsettled"}


# --------------------------------------------------------------------------- #
# The grammar cannot be confused with the other two stages
# --------------------------------------------------------------------------- #

def test_an_edit_file_is_not_read_as_a_worksheet_reply(translated, settled, tmp_path):
    """Handed to merge, it must answer no units rather than some."""
    import worksheet as ws

    body = f"++ b00001 calque\n{SMOOTHED}\n!! reviewed sheet_0001\n"
    entries, problems = ws.read_reply(body)
    assert entries == [], (
        "the edit grammar parses as worksheet headers, so a fluency reply could "
        "be merged into the book as a translation of the wrong units")
    assert problems == []


def test_an_edit_file_is_not_read_as_a_meaning_findings_file():
    body = f"++ b00001 calque\n{SMOOTHED}\n!! reviewed sheet_0001\n"
    findings, claimed, _problems = meaning.read_findings(body)
    assert findings == [], (
        "a fluency edit parses as a meaning finding, so a proposed rewrite could "
        "be filed as a semantic defect against the translator")
    # The shared `!!` claim is deliberate and does no harm: the tagged digests
    # refuse a directory handed to the wrong stage.
    assert claimed == ["sheet_0001"]


def test_a_meaning_findings_file_is_not_read_as_edits():
    body = "?? b00001 sense\nThe negation is reversed.\n!! reviewed sheet_0001\n"
    edits, _claimed, problems = fluency.read_edits(body)
    assert edits == [], (
        "a semantic finding parses as an edit, so its prose argument would be "
        "written into the book as Persian")
    assert any("foreign fluency control" in problem for problem in problems)


# --------------------------------------------------------------------------- #
# The exit codes a shell recipe depends on
# --------------------------------------------------------------------------- #

def test_every_action_exits_zero_on_success_and_two_on_a_refusal(
        translated, settled, tmp_path, capsys):
    """`meaning sheets` exited 2 on success for exactly this reason.

    Its `main` read the code from a key `write_sheets` never returned. Every
    action here is checked both ways, because a stage that reports failure when
    it worked stops the recipe, and one that reports success when it refused is
    worse.
    """
    out = tmp_path / "fluency"
    where = ["--book", str(translated), "--out", str(out)]
    meaning_dir = ["--meaning", str(settled)]

    assert fluency.main(["sheets", *where, *meaning_dir]) == 0
    capsys.readouterr()
    _reply(out, "sheet_0001", f"++ b00001 calque\n{SMOOTHED}\n!! reviewed sheet_0001\n")
    assert fluency.main(["record", *where]) == 0
    capsys.readouterr()
    assert fluency.main(["apply", *where]) == 0
    capsys.readouterr()

    # Refused: the smoothed Persian has not been compared against its source yet.
    assert fluency.main(["status", *where, *meaning_dir]) == 2
    capsys.readouterr()
    assert _resettle(translated, settled)["ok"] is True
    assert fluency.main(["status", *where, *meaning_dir]) == 0
    capsys.readouterr()


def test_every_report_carries_the_key_its_exit_code_is_read_from(
        translated, settled, tmp_path):
    out = tmp_path / "fluency"
    assert "ok" in fluency.write_sheets(translated, out, settled)
    _reply(out, "sheet_0001", f"++ b00001 calque\n{SMOOTHED}\n!! reviewed sheet_0001\n")
    assert "ok" in fluency.record(out, translated)
    assert "ok" in fluency.apply_edits(translated, out)
    assert "ok" in fluency.verdict(out, translated, settled)
