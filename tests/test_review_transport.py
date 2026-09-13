"""A review reply has to prove which sheet, which revision and which stage.

`out_sheet_0001.md` is a reusable name, so the answer to a superseded question
sits at exactly the path the new question expects — same name, same sheet id, same
units — and until this module existed nothing in it said which text it was written
against. Measured before the fix, on a book whose target had its negation
inverted: sheets regenerated at the new revision, the previous approval file still
on disk, `meaning.record` reporting `ok: true`. The review passed on the strength
of a file nobody had reread.

Five ways a review used to pass without being one, all reproduced against the
unfixed tree and all closed here:

1. an old clean approval recorded against new text,
2. one reply claiming two sheets while the second reply was empty,
3. a fluency `++` edit filed into the meaning stage, read as "no findings" — and
   a meaning `??` finding filed into the fluency stage, ignored the same way,
4. `--per-sheet -1`, which wrote no sheets for a nonempty book and let both
   stages approve it (`--per-sheet 0` raised a bare `ValueError` from `range`),
5. two different replacements for one unit, taken last-write-wins.

Every test here watches a refusal. A genuine no-findings approval still passes,
which is the other half of the contract: this must not become a stage that can
only say no.
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
import reviewsheet  # noqa: E402
from tests_support import review_reply  # noqa: E402

SOURCE = ["She did not refuse the invitation.",
          "The house stood at the end of a long lane.",
          "He smiled, then left without a word.",
          "Nobody mentioned the letter again."]
TARGET = ["او دعوت را رد نکرد.",
          "خانه در انتهای کوچه‌ای بلند بود.",
          "لبخند زد و بی‌آنکه چیزی بگوید رفت.",
          "کسی دیگر از نامه حرفی نزد."]


@pytest.fixture()
def translated(tmp_path: Path) -> Path:
    book = ir.new_book(source_path="s.epub", source_format="epub",
                       title="T", author="A")
    for index, text in enumerate(SOURCE, start=1):
        book["blocks"].append(ir.make_block("paragraph", index, text=text))
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    book = ir.load_book(path)
    resolve = merging.addressing(book)
    for block, target in zip(book["blocks"], TARGET):
        container, field = resolve(block["id"])
        container[field] = target
    ir.save_book(book, path)
    return path


def _reply(out_dir: Path, sheet_id: str, body: str) -> None:
    ir.write_text(out_dir / f"out_{sheet_id}.md",
                  review_reply(out_dir / f"{sheet_id}.md", body))


def _approve(out_dir: Path, sheets: list[str]) -> None:
    for sheet_id in sheets:
        _reply(out_dir, sheet_id, f"!! reviewed {sheet_id}\n")


def _manifest(out_dir: Path) -> dict:
    return json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# The token: what makes two questions different questions
# --------------------------------------------------------------------------- #

BASE = dict(stage="meaning", sheet_id="sheet_0001", revision="meaning1:abc",
            unit_ids=["b00001", "b00002"], policy="rubrics")


@pytest.mark.parametrize("field, value", [
    ("stage", "fluency"),
    ("sheet_id", "sheet_0002"),
    ("revision", "meaning1:def"),
    ("unit_ids", ["b00002", "b00001"]),      # order is part of the question
    ("unit_ids", ["b00001"]),
    ("policy", "different rubrics"),
])
def test_every_part_of_the_request_moves_the_token(field, value):
    assert reviewsheet.token(**BASE) != reviewsheet.token(**{**BASE, field: value})


def test_the_same_request_produces_the_same_token():
    """Otherwise every regeneration would discard every reply."""
    assert reviewsheet.token(**BASE) == reviewsheet.token(**BASE)


def test_the_token_is_tagged_with_the_formula_that_made_it():
    assert reviewsheet.token(**BASE).startswith(reviewsheet.REVIEW_VERSION + ":")


def test_the_review_line_round_trips():
    value = reviewsheet.token(**BASE)
    line = reviewsheet.request_line("meaning", "sheet_0001", value)
    assert reviewsheet.request_of(f"# a sheet\n{line}\nmore text") == (
        "meaning", "sheet_0001", value)
    assert reviewsheet.request_of("nothing here") == ("", "", "")


# --------------------------------------------------------------------------- #
# Bounds, checked before anything is written
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("value", [-1, 0, -100])
def test_a_non_positive_sheet_size_is_refused(value):
    assert reviewsheet.bounded("--per-sheet", value)


@pytest.mark.parametrize("value", ["2", 2.5, None, True])
def test_a_sheet_size_that_is_not_a_whole_number_is_refused(value):
    assert reviewsheet.bounded("--per-sheet", value)


def test_a_usable_sheet_size_passes():
    assert reviewsheet.bounded("--per-sheet", 1) == ""
    assert reviewsheet.bounded("--per-sheet", 20) == ""


@pytest.mark.parametrize("size", [-1, 0])
def test_neither_stage_writes_sheets_for_a_bad_size(translated, tmp_path, size):
    """`range(0, n, -1)` yields nothing, and both stages then approved the book."""
    out = tmp_path / "review"
    said = meaning.write_sheets(translated, out, per_sheet=size)
    assert said["ok"] is False and said["refused"] == "bad-per-sheet"
    assert not (out / "manifest.json").exists(), "a refusal still wrote a manifest"

    rev = meaning.revision(meaning.pairs(ir.load_book(translated)))
    assert meaning.verdict(out, rev)["ok"] is False, (
        "a nonempty book was approved with no sheets written")


def test_the_fluency_stage_refuses_a_bad_size_too(translated, tmp_path):
    mean = tmp_path / "meaning"
    _approve(mean, meaning.write_sheets(translated, mean)["sheets"])
    assert meaning.record(mean, translated)["ok"] is True

    said = fluency.write_sheets(translated, tmp_path / "flu", mean, per_sheet=-1)
    assert said["ok"] is False and said["refused"] == "bad-per-sheet"


# --------------------------------------------------------------------------- #
# An old approval cannot answer new text
# --------------------------------------------------------------------------- #

def test_an_old_approval_is_refused_after_the_text_changes(translated, tmp_path):
    """The measured defect: a target whose negation was inverted, still approved."""
    out = tmp_path / "review"
    first = meaning.write_sheets(translated, out)
    _approve(out, first["sheets"])
    assert meaning.record(out, translated)["ok"] is True

    book = ir.load_book(translated)
    book["blocks"][0]["target"] = "او دعوت را پذیرفت."      # the negation inverted
    ir.save_book(book, translated)

    second = meaning.write_sheets(translated, out)
    assert second["revision"] != first["revision"]

    again = meaning.record(out, translated)
    assert again["ok"] is False, (
        "the previous approval was accepted for text it never described")


def test_a_superseded_reply_is_kept_rather_than_overwritten(translated, tmp_path):
    """A reviewer's argument about the previous text is evidence, not rubbish."""
    out = tmp_path / "review"
    first = meaning.write_sheets(translated, out)
    for sheet_id in first["sheets"]:
        _reply(out, sheet_id,
               f"?? b00001 sense\nAn argument worth keeping.\n"
               f"!! reviewed {sheet_id}\n")
    meaning.record(out, translated)

    book = ir.load_book(translated)
    book["blocks"][0]["target"] = "متن تازه‌ای که کسی ندیده است."
    ir.save_book(book, translated)
    second = meaning.write_sheets(translated, out)

    assert second["superseded"], "nothing was filed, so the old reply is still live"
    kept = list((out / "superseded").rglob("out_*.md"))
    assert kept, "the superseded reply was not kept anywhere"
    assert any("worth keeping" in path.read_text(encoding="utf-8")
               for path in kept)
    assert not (out / "out_sheet_0001.md").exists(), (
        "the old reply is still at the path the new sheet reads")


def test_a_reply_echoing_a_foreign_token_version_is_refused(translated, tmp_path):
    out = tmp_path / "review"
    sheets = meaning.write_sheets(translated, out)["sheets"]
    for sheet_id in sheets:
        line = reviewsheet.request_line("meaning", sheet_id, "rev9:0123456789abcdef")
        ir.write_text(out / f"out_{sheet_id}.md",
                      f"{line}\n!! reviewed {sheet_id}\n")
    said = meaning.record(out, translated)
    assert said["ok"] is False
    assert any("cannot be compared" in problem for problem in said["problems"])


def test_a_reply_with_no_echoed_line_is_refused(translated, tmp_path):
    out = tmp_path / "review"
    sheets = meaning.write_sheets(translated, out)["sheets"]
    for sheet_id in sheets:
        ir.write_text(out / f"out_{sheet_id}.md", f"!! reviewed {sheet_id}\n")
    said = meaning.record(out, translated)
    assert said["ok"] is False
    assert any("echoes no review line" in problem for problem in said["problems"])


# --------------------------------------------------------------------------- #
# Each reply answers its own sheet
# --------------------------------------------------------------------------- #

def test_one_reply_cannot_discharge_another_sheet(translated, tmp_path):
    """Claims used to be unioned, so the second reply could be empty."""
    out = tmp_path / "review"
    sheets = meaning.write_sheets(translated, out, per_sheet=2)["sheets"]
    assert len(sheets) >= 2

    one, two = sheets[0], sheets[1]
    _reply(out, one, f"!! reviewed {one}\n!! reviewed {two}\n")
    ir.write_text(out / f"out_{two}.md", "")

    said = meaning.record(out, translated)
    assert said["ok"] is False
    assert any("also claims" in problem for problem in said["problems"])
    assert any("the reply is empty" in problem for problem in said["problems"])


def test_a_finding_about_another_sheets_unit_is_refused(translated, tmp_path):
    out = tmp_path / "review"
    sheets = meaning.write_sheets(translated, out, per_sheet=2)["sheets"]
    owned = _manifest(out)["owned"]
    foreign = owned[sheets[1]][0]

    _reply(out, sheets[0],
           f"?? {foreign} sense\nAbout a unit this sheet never showed.\n"
           f"!! reviewed {sheets[0]}\n")
    _reply(out, sheets[1], f"!! reviewed {sheets[1]}\n")

    said = meaning.record(out, translated)
    assert said["ok"] is False
    assert any("belongs to another sheet" in problem for problem in said["problems"])


def test_a_reply_answering_a_different_sheet_is_refused(translated, tmp_path):
    out = tmp_path / "review"
    sheets = meaning.write_sheets(translated, out, per_sheet=2)["sheets"]
    requests = _manifest(out)["requests"]

    # The envelope of sheet two, written into sheet one's reply file.
    line = reviewsheet.request_line("meaning", sheets[1], requests[sheets[1]])
    ir.write_text(out / f"out_{sheets[0]}.md",
                  f"{line}\n!! reviewed {sheets[0]}\n")
    _reply(out, sheets[1], f"!! reviewed {sheets[1]}\n")

    said = meaning.record(out, translated)
    assert said["ok"] is False
    assert any("not meaning/" + sheets[0] in problem
               for problem in said["problems"])


# --------------------------------------------------------------------------- #
# Each stage reads its own grammar, and refuses the other's
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("record, owns, offending", [
    ("++ b00001 calque", "meaning", True),
    ("?? b00001 sense", "fluency", True),
    ("@@ b00001 para", "meaning", True),
    ("@@ b00001 para", "fluency", True),
    ("?? b00001 sense", "meaning", False),
    ("++ b00001 calque", "fluency", False),
    ("!! reviewed sheet_0001", "meaning", False),
    ("!! reviewed sheet_0001", "fluency", False),
])
def test_a_stage_names_the_control_records_it_does_not_own(record, owns, offending):
    found = reviewsheet.foreign_controls(f"{record}\nsome body text\n", owns=owns)
    assert bool(found) is offending, found


def test_a_fluency_edit_filed_into_the_meaning_stage_is_refused(translated, tmp_path):
    """It used to parse as nothing and report a clean approval."""
    out = tmp_path / "review"
    sheets = meaning.write_sheets(translated, out)["sheets"]
    for sheet_id in sheets:
        _reply(out, sheet_id,
               f"++ b00001 calque\nمتن روان‌تر.\n!! reviewed {sheet_id}\n")
    said = meaning.record(out, translated)
    assert said["ok"] is False
    assert any("does not read" in problem for problem in said["problems"])


def test_a_meaning_finding_filed_into_the_fluency_stage_is_refused(
        translated, tmp_path):
    mean = tmp_path / "meaning"
    _approve(mean, meaning.write_sheets(translated, mean)["sheets"])
    assert meaning.record(mean, translated)["ok"] is True

    flu = tmp_path / "fluency"
    sheets = fluency.write_sheets(translated, flu, mean)["sheets"]
    for sheet_id in sheets:
        _reply(flu, sheet_id,
               f"?? b00001 sense\nThe negation flipped.\n!! reviewed {sheet_id}\n")
    said = fluency.record(flu, translated)
    assert said["ok"] is False
    assert any("does not read" in problem for problem in said["problems"])


def test_two_replacements_for_one_unit_are_a_conflict(translated, tmp_path):
    """Last-write-wins silently discards a reviewer's judgement."""
    mean = tmp_path / "meaning"
    _approve(mean, meaning.write_sheets(translated, mean)["sheets"])
    meaning.record(mean, translated)

    flu = tmp_path / "fluency"
    sheets = fluency.write_sheets(translated, flu, mean)["sheets"]
    for sheet_id in sheets:
        _reply(flu, sheet_id,
               f"++ b00001 calque\nیک جانشین.\n"
               f"++ b00001 flow\nجانشینی دیگر.\n"
               f"!! reviewed {sheet_id}\n")
    said = fluency.record(flu, translated)
    assert said["ok"] is False
    assert any("different replacements" in problem for problem in said["problems"])


def test_several_findings_for_one_unit_are_allowed(translated, tmp_path):
    """Diagnosis is not replacement: two rubrics may both be true of one unit."""
    out = tmp_path / "review"
    sheets = meaning.write_sheets(translated, out)["sheets"]
    for sheet_id in sheets:
        _reply(out, sheet_id,
               f"?? b00001 sense\nThe negation is reversed.\n"
               f"?? b00001 register\nAnd the voice is wrong.\n"
               f"!! reviewed {sheet_id}\n")
    said = meaning.record(out, translated)
    # Recorded, not refused: these are findings, and `blocking` is what acts.
    assert said["ok"] is False, "a meaning finding has to block"
    assert said.get("refused") != "incomplete", said
    assert len(said["findings"]) == 2


def test_a_genuine_no_findings_approval_still_passes(translated, tmp_path):
    """The other half of the contract: this cannot become a stage that only says no."""
    out = tmp_path / "review"
    sheets = meaning.write_sheets(translated, out)["sheets"]
    _approve(out, sheets)
    said = meaning.record(out, translated)
    assert said["ok"] is True and said["findings"] == []


# --------------------------------------------------------------------------- #
# The sheets have to cover the book
# --------------------------------------------------------------------------- #

def test_a_unit_on_no_sheet_is_reported(translated, tmp_path):
    out = tmp_path / "review"
    sheets = meaning.write_sheets(translated, out, per_sheet=2)["sheets"]
    _approve(out, sheets)

    manifest = _manifest(out)
    dropped = manifest["owned"][sheets[1]].pop()
    ir.write_text(out / "manifest.json",
                  json.dumps(manifest, ensure_ascii=False, indent=1))

    said = meaning.record(out, translated)
    assert said["ok"] is False
    assert said["refused"] == "incomplete-coverage"
    assert any(dropped in problem for problem in said["problems"])


def test_a_unit_on_two_sheets_is_reported():
    problems = reviewsheet.coverage_problems(
        {"sheet_0001": ["b1", "b2"], "sheet_0002": ["b2", "b3"]},
        ["b1", "b2", "b3"])
    assert any("more than one sheet" in problem for problem in problems)


def test_a_sheet_owning_a_unit_the_book_does_not_have_is_reported():
    problems = reviewsheet.coverage_problems(
        {"sheet_0001": ["b1", "ghost"]}, ["b1"])
    assert any("not a reviewable unit" in problem for problem in problems)


def test_complete_coverage_reports_nothing():
    assert reviewsheet.coverage_problems(
        {"sheet_0001": ["b1"], "sheet_0002": ["b2"]}, ["b1", "b2"]) == []
