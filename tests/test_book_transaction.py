"""Changing the book is one transaction, and a half-finished one is recoverable.

`ir.write_text` has always replaced a file atomically, and atomic replacement is
not the property a book needs. Four failures measured on this pipeline before
`bookwrite` existed:

* a replacement carrying ``[[fn:fn0099]]`` — a note the book does not have — was
  written straight to disk: ``apply ok: True`` and then
  ``validate_book`` reporting ``unknown footnote ref fn0099`` on the file the
  pipeline had just accepted;
* `fluency apply` wrote the book and *then* its sidecar, so an injected sidecar
  failure left the Persian changed with nothing recording that it had been —
  and the next run applied the same edits again;
* a writer holding an older read overwrote another writer's committed edit, in a
  file that was perfectly well-formed, so nothing noticed;
* a payload line beginning ``~`` or shaped like a comment was dropped from a
  reviewer's replacement without a word.

The tests here are mostly fault injection, because that is the only way to ask
the questions that matter: what is on disk when the *second* write fails, and
does the next run resolve it the same way every time.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bookir as ir  # noqa: E402
import bookwrite  # noqa: E402
import fluency  # noqa: E402
import meaning  # noqa: E402
import merge as merging  # noqa: E402
from tests_support import review_reply  # noqa: E402

SOURCE = ["She did not refuse the invitation.",
          "The lane was long and quiet in the evening."]
TARGET = ["او دعوت را رد نکرد.", "کوچه در عصر بلند و ساکت بود."]
#: Long enough that the length-ratio and duplicate gates are not what refuses it.
SMOOTHED = "کوچه در آن عصر، بلند و ساکت، تا انتها کشیده شده بود."


@pytest.fixture()
def translated(tmp_path: Path) -> Path:
    book = ir.new_book(source_path="sample.epub", source_format="epub",
                       title="A Small Book", author="A Test Author")
    for index, text in enumerate(SOURCE, start=1):
        book["blocks"].append(ir.make_block("paragraph", index, text=text))
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    book = ir.load_book(path)
    resolve = merging.addressing(book)
    for block, target in zip(book["blocks"], TARGET):
        container, field = resolve(block["id"])
        container[field] = target
    book["meta"]["title_target"] = "کتابی کوچک"
    book["meta"]["author_target"] = "نویسندهٔ آزمون"
    ir.save_book(book, path)
    return path


@pytest.fixture()
def settled(translated: Path, tmp_path: Path) -> Path:
    out = tmp_path / "meaning"
    for sheet_id in meaning.write_sheets(translated, out)["sheets"]:
        ir.write_text(out / f"out_{sheet_id}.md",
                      review_reply(out / f"{sheet_id}.md",
                                   f"!! reviewed {sheet_id}\n"))
    assert meaning.record(out, translated)["ok"] is True
    return out


def _propose(book_path: Path, out: Path, meaning_dir: Path, body: str) -> dict:
    sheets = fluency.write_sheets(book_path, out, meaning_dir)["sheets"]
    ir.write_text(out / f"out_{sheets[0]}.md",
                  review_reply(out / f"{sheets[0]}.md",
                               body + f"!! reviewed {sheets[0]}\n"))
    return fluency.record(out, book_path)


def _breaking(monkeypatch, when) -> None:
    """Make `ir.write_text` fail for the paths ``when`` selects."""
    real = ir.write_text

    def guarded(path, text):
        if when(str(path)):
            raise OSError("injected write failure")
        return real(path, text)

    monkeypatch.setattr(ir, "write_text", guarded)


# --------------------------------------------------------------------------- #
# The control/payload boundary
# --------------------------------------------------------------------------- #

def test_persian_that_looks_like_control_survives_the_round_trip():
    """A line beginning `~ ` is Persian a translator may legitimately write."""
    text = "~ خطی که با موج آغاز می‌شود\n<!-- و یک خط شبیه توضیح -->\nو خط سوم"

    edits, _claimed, problems = fluency.read_edits(
        "++ b00001 calque\n" + fluency.escape_payload(text) + "\n")

    assert problems == []
    assert edits[0]["target"] == text, "the reviewer's own lines were rewritten"


def test_an_already_escaped_line_escapes_again():
    """One layer out, one layer in — or the round trip is asymmetric."""
    text = "\\~ a line that is itself in escaped form"
    edits, _claimed, _problems = fluency.read_edits(
        "++ b00001 calque\n" + fluency.escape_payload(text) + "\n")
    assert edits[0]["target"] == text


def test_this_projects_own_scaffolding_is_dropped_and_nothing_else_is():
    """The distinction that was missing: whose comment is it?

    Any line beginning `<!--` used to be discarded, so a replacement carrying a
    comment-shaped line of its own lost it silently. Only this project's marked
    scaffolding is ours to drop.
    """
    edits, claimed, _problems = fluency.read_edits(
        "<!-- revayat-novel: review fluency sheet_0001 rev1:0123456789abcdef -->\n"
        "++ b00001 calque\n"
        "<!-- revayat-novel: context, not under review -->\n"
        "~ همسایه‌ای که ویرایش نمی‌شود\n"
        "<!-- یادداشت خود بازبین -->\n"
        "متن پیشنهادی\n"
        "!! reviewed sheet_0001\n")

    assert claimed == ["sheet_0001"]
    assert edits[0]["target"] == "<!-- یادداشت خود بازبین -->\nمتن پیشنهادی"


def test_the_sheet_escapes_persian_that_would_read_as_a_context_quote(
        translated, settled, tmp_path):
    """End to end: a unit whose Persian starts `~ ` can still be proposed."""
    book = ir.load_book(translated)
    book["blocks"][0]["target"] = "~ او دعوت را رد نکرد."
    ir.save_book(book, translated)
    out = tmp_path / "meaning2"
    for sheet_id in meaning.write_sheets(translated, out)["sheets"]:
        ir.write_text(out / f"out_{sheet_id}.md",
                      review_reply(out / f"{sheet_id}.md",
                                   f"!! reviewed {sheet_id}\n"))
    assert meaning.record(out, translated)["ok"] is True

    flu = tmp_path / "fluency"
    sheets = fluency.write_sheets(translated, flu, out)["sheets"]
    sheet = (flu / f"{sheets[0]}.md").read_text(encoding="utf-8")

    assert "\\~ او دعوت را رد نکرد." in sheet, (
        "the unit's own Persian is on the sheet as a context quote, so a "
        "reviewer copying it back loses the line")


# --------------------------------------------------------------------------- #
# Refusals that must not mutate
# --------------------------------------------------------------------------- #

def test_conflicting_replacements_for_one_unit_are_refused(translated, settled,
                                                           tmp_path):
    out = tmp_path / "fluency"
    before = bookwrite.file_digest(translated)

    recorded = _propose(translated, out, settled,
                        f"++ b00002 calque\n{SMOOTHED}\n\n"
                        f"++ b00002 flow\nکوچه بلند بود و ساکت.\n")

    assert recorded["ok"] is False
    assert recorded["refused"] == "incomplete"
    assert any("different replacements" in problem
               for problem in recorded["problems"])
    assert bookwrite.file_digest(translated) == before


def test_an_undefined_note_marker_refuses_before_anything_is_written(
        translated, settled, tmp_path):
    """Measured: this used to save a book `validate_book` then rejected."""
    out = tmp_path / "fluency"
    before = bookwrite.file_digest(translated)
    assert _propose(translated, out, settled,
                    f"++ b00002 calque\n{SMOOTHED}[[fn:fn0099]]\n")["ok"] is True

    applied = fluency.apply_edits(translated, out)

    assert applied["ok"] is False
    assert applied["refused"] == "invalid-book"
    assert "fn0099" in applied["detail"]
    assert bookwrite.file_digest(translated) == before
    assert ir.validate_book(ir.load_book(translated)) == []


def test_applying_the_same_edit_twice_changes_nothing_the_second_time(
        translated, settled, tmp_path):
    out = tmp_path / "fluency"
    assert _propose(translated, out, settled,
                    f"++ b00002 calque\n{SMOOTHED}\n")["ok"] is True
    assert fluency.apply_edits(translated, out)["ok"] is True
    after = bookwrite.file_digest(translated)

    again = fluency.apply_edits(translated, out)

    assert again["ok"] is False
    assert again["refused"] == "already-applied"
    assert bookwrite.file_digest(translated) == after


# --------------------------------------------------------------------------- #
# Fault injection
# --------------------------------------------------------------------------- #

def test_a_sidecar_failure_leaves_the_book_unchanged_and_recoverable(
        translated, settled, tmp_path, monkeypatch):
    """The half-committed pair, and the recovery that resolves it.

    The book used to be written first, so this left the Persian changed with
    nothing saying it had been applied — and the next run applied it again.
    """
    out = tmp_path / "fluency"
    assert _propose(translated, out, settled,
                    f"++ b00002 calque\n{SMOOTHED}\n")["ok"] is True
    before = bookwrite.file_digest(translated)
    sidecar = fluency.sidecar_path(out)
    recorded = sidecar.read_text(encoding="utf-8")

    _breaking(monkeypatch, lambda path: path.endswith("fluency.json"))
    stopped = fluency.apply_edits(translated, out)

    assert stopped["ok"] is False and stopped["refused"] == "write-failed"
    assert bookwrite.file_digest(translated) == before, "the book moved anyway"
    assert bookwrite.journal_path(translated).is_file(), "nothing to recover from"

    monkeypatch.undo()
    resolved = bookwrite.recover(translated)

    assert resolved == {"recovered": True, "direction": "back",
                        "actor": "fluency.apply", "files": [str(sidecar)]}
    assert sidecar.read_text(encoding="utf-8") == recorded
    assert not bookwrite.journal_path(translated).is_file()

    # And the pass is exactly where it was: the edits apply cleanly now.
    assert fluency.apply_edits(translated, out)["ok"] is True
    assert bookwrite.file_digest(translated) != before


def test_a_book_failure_after_the_sidecar_is_rolled_back(translated, settled,
                                                         tmp_path, monkeypatch):
    """The other order: the side file landed and the commit did not."""
    out = tmp_path / "fluency"
    assert _propose(translated, out, settled,
                    f"++ b00002 calque\n{SMOOTHED}\n")["ok"] is True
    before = bookwrite.file_digest(translated)
    sidecar = fluency.sidecar_path(out)
    recorded = json.loads(sidecar.read_text(encoding="utf-8"))

    _breaking(monkeypatch, lambda path: path.endswith("book.json"))
    stopped = fluency.apply_edits(translated, out)

    assert stopped["ok"] is False and stopped["refused"] == "write-failed"
    assert bookwrite.file_digest(translated) == before
    # The sidecar on disk now claims to have been applied, and the book says
    # otherwise. That is exactly what the journal is for.
    assert json.loads(sidecar.read_text(encoding="utf-8")).get("applied")

    monkeypatch.undo()
    assert bookwrite.recover(translated)["direction"] == "back"
    assert json.loads(sidecar.read_text(encoding="utf-8")) == recorded, (
        "the sidecar was left claiming an edit the book never took")
    assert fluency.apply_edits(translated, out)["ok"] is True


def test_a_journal_failure_stops_before_anything_else(translated, settled,
                                                      tmp_path, monkeypatch):
    out = tmp_path / "fluency"
    assert _propose(translated, out, settled,
                    f"++ b00002 calque\n{SMOOTHED}\n")["ok"] is True
    before = bookwrite.file_digest(translated)
    recorded = fluency.sidecar_path(out).read_text(encoding="utf-8")

    _breaking(monkeypatch, lambda path: path.endswith(".journal.json"))
    stopped = fluency.apply_edits(translated, out)

    assert stopped["ok"] is False and stopped["refused"] == "write-failed"
    assert bookwrite.file_digest(translated) == before
    assert fluency.sidecar_path(out).read_text(encoding="utf-8") == recorded
    assert not bookwrite.journal_path(translated).is_file()
    with bookwrite.transaction(translated, actor="lock-release-control"):
        pass


def test_a_journal_naming_a_state_the_book_is_not_in_is_not_guessed(translated):
    """Neither direction is safe, so recovery refuses and keeps the evidence."""
    journal = bookwrite.journal_path(translated)
    ir.write_text(journal, json.dumps({
        "schema": bookwrite.SCHEMA, "phase": "prepared", "actor": "probe", "at": time.time(),
        "book": {"path": str(translated), "before": "a" * 64, "after": "b" * 64},
        "side": [],
    }))

    with pytest.raises(bookwrite.Refused) as stopped:
        bookwrite.recover(translated)

    assert stopped.value.reason == "unresolved-journal"
    assert journal.is_file(), "the only record of what was in progress was deleted"


# --------------------------------------------------------------------------- #
# Two writers
# --------------------------------------------------------------------------- #

def test_a_writer_holding_an_older_read_does_not_overwrite_the_newer_book(
        translated):
    """The lost update, refused — and the other writer's work still there."""
    before = bookwrite.file_digest(translated)
    mine = ir.load_book(translated)

    other = ir.load_book(translated)
    other["meta"]["title_target"] = "عنوانی که نویسندهٔ دیگر نوشت"
    ir.save_book(other, translated)

    mine["blocks"][0]["target"] = SMOOTHED
    with pytest.raises(bookwrite.Refused) as stopped:
        bookwrite.replace(translated, mine, actor="probe", expect=before)

    assert stopped.value.reason == "lost-update"
    landed = ir.load_book(translated)
    assert landed["meta"]["title_target"] == "عنوانی که نویسندهٔ دیگر نوشت"
    assert landed["blocks"][0]["target"] == TARGET[0]


def test_merge_refuses_rather_than_committing_over_a_book_that_moved(
        translated, tmp_path, monkeypatch):
    """The same guarantee through the real command."""
    import chunk as chunking  # noqa: PLC0415
    from tests_support import reply_text  # noqa: PLC0415

    chunks = tmp_path / "chunks"
    manifest = chunking.build(translated, chunks, glossary_path=None, budget=4000)
    entry = manifest["chunks"][0]
    ir.write_text(chunks / entry["output"], reply_text(
        chunks / entry["file"],
        "".join(f"@@ {unit_id} {entry['unit_kinds'][unit_id]}\nترجمهٔ تازه.\n"
                for unit_id in entry["unit_ids"])))

    # Another writer commits between merge's read and merge's write.
    real = merging.published.slots

    def interleave(book):
        if not getattr(interleave, "done", False):
            interleave.done = True
            other = ir.load_book(translated)
            other["meta"]["title_target"] = "عنوانی از نویسندهٔ دیگر"
            ir.save_book(other, translated)
        return real(book)

    monkeypatch.setattr(merging.published, "slots", interleave)
    report = merging.merge(translated, chunks)

    assert report["ok"] is False
    assert report["refused"] == "lost-update"
    assert ir.load_book(translated)["meta"]["title_target"] \
        == "عنوانی از نویسندهٔ دیگر"


def test_a_held_lock_refuses_and_a_dead_one_is_taken_over(translated,
                                                          monkeypatch):
    monkeypatch.setattr(bookwrite, "WAIT_SECONDS", 0.05)
    held = bookwrite._take_lock(translated, "someone")
    try:
        with pytest.raises(bookwrite.Refused) as stopped:
            with bookwrite.transaction(translated, actor="probe"):
                pass
        assert stopped.value.reason == "locked"
    finally:
        held.release()

    # A released handle, with the diagnostic lock file still present, is free.
    with bookwrite.transaction(translated, actor="probe") as tx:
        tx.book["meta"]["author_target"] = "نویسندهٔ تازه"
    assert ir.load_book(translated)["meta"]["author_target"] == "نویسندهٔ تازه"


def test_a_successful_edit_invalidates_the_review_that_licensed_it(
        translated, settled, tmp_path):
    """Not machinery: the digest covers the text the edit changed.

    The blind pass proposes, `apply` writes, and the bilingual review of the book
    that results is stale *by construction* — which is what makes the final
    source comparison unavoidable rather than a step somebody remembers.
    """
    out = tmp_path / "fluency"
    assert _propose(translated, out, settled,
                    f"++ b00002 calque\n{SMOOTHED}\n")["ok"] is True
    book = ir.load_book(translated)
    assert meaning.verdict(settled,
                           meaning.revision(meaning.pairs(book)))["ok"] is True

    assert fluency.apply_edits(translated, out)["ok"] is True

    book = ir.load_book(translated)
    answer = meaning.verdict(settled, meaning.revision(meaning.pairs(book)))
    assert answer["ok"] is False
    assert answer["refused"] == "stale-review"
    assert fluency.verdict(out, translated, settled)["ok"] is False
