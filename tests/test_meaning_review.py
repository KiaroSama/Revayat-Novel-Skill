"""The bilingual review has to be able to refuse, and has to stop asking.

A semantic review is the one gate in this pipeline whose answer comes from a
model rather than from arithmetic, which makes every way of accidentally passing
it worth a test of its own:

* a sheet nobody reported on,
* a findings file with no findings and no claim to have read anything,
* a verdict that outlived the translation it was made against,
* a verdict whose digest this reader cannot even recompute,
* a style note quietly escalated into a demand to rewrite a faithful sentence,
* and a repair loop that asks for the same fix for ever.

Each one is checked by watching it be refused, not by watching a pass.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bookir as ir  # noqa: E402
import meaning  # noqa: E402
from tests_support import review_reply  # noqa: E402
import merge as merging  # noqa: E402

SOURCE = [
    "He smiled, then left without a word.",
    "She did not refuse the invitation.",
    "The house stood at the end of a long lane.",
]
TARGET = [
    "لبخند زد و بی‌آنکه چیزی بگوید رفت.",
    "او دعوت را رد نکرد.",
    "خانه در انتهای کوچه‌ای بلند بود.",
]


@pytest.fixture()
def translated(tmp_path: Path) -> Path:
    """A small book with both sides present, as merge leaves it."""
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
    """A reply echoing its sheet's review line, which the transport requires.

    Without the echo nothing says which sheet or which revision the reply answers,
    and `record` refuses it — so every fixture here would be measuring that
    refusal. `_bare_reply` exists for the tests that want exactly that.
    """
    ir.write_text(out_dir / f"out_{sheet_id}.md",
                  review_reply(out_dir / f"{sheet_id}.md", body))


def _bare_reply(out_dir: Path, sheet_id: str, body: str) -> None:
    """A reply with no echoed review line — used to prove it is refused."""
    ir.write_text(out_dir / f"out_{sheet_id}.md", body)


def test_the_sheet_shows_both_sides_of_every_unit(translated, tmp_path):
    out = tmp_path / "review"
    report = meaning.write_sheets(translated, out)
    # Three paragraphs *and* the title page: the review reads the published
    # inventory, and the title and byline print before any of the prose does.
    assert report["units"] == len(SOURCE) + 2
    text = (out / f"{report['sheets'][0]}.md").read_text(encoding="utf-8")
    assert "A Small Book" in text and "کتابی کوچک" in text, (
        "the title page is not on the sheet, so nobody reviews the first thing "
        "a reader sees")
    for source, target in zip(SOURCE, TARGET):
        assert source in text, "the source side is missing from the sheet"
        assert target in text, "the translation side is missing from the sheet"
    # The rubrics travel with their contrastive pair, or they are read as
    # "say something about the prose".
    for name, rubric in meaning.RUBRICS.items():
        assert name in text and rubric["bad"] in text


def test_the_review_sheet_cannot_be_mistaken_for_a_worksheet(translated, tmp_path):
    """Fed to merge, a findings file must answer no units rather than some."""
    import worksheet as ws

    out = tmp_path / "review"
    meaning.write_sheets(translated, out)
    findings = "?? b00001 sense\nThe negation is reversed.\n!! reviewed sheet_0001\n"
    entries, problems = ws.read_reply(findings)
    assert entries == [], (
        "the findings grammar parses as worksheet headers, so a reviewer's notes "
        "could be merged into the book as translations")
    assert problems == []


def test_a_sheet_nobody_reported_on_is_refused(translated, tmp_path):
    out = tmp_path / "review"
    meaning.write_sheets(translated, out, per_sheet=2)
    report = meaning.record(out, translated)
    assert report["ok"] is False
    assert report["refused"] == "incomplete"
    assert any("nobody reported" in problem for problem in report["problems"])


def test_silence_is_not_approval(translated, tmp_path):
    """An empty findings file with no `!! reviewed` claim does not pass."""
    out = tmp_path / "review"
    sheets = meaning.write_sheets(translated, out)["sheets"]
    for sheet_id in sheets:
        _reply(out, sheet_id, "I had a look and it seems fine.\n")
    report = meaning.record(out, translated)
    assert report["ok"] is False
    # The refusal moved into the shared transport, which names the sheet it is
    # refusing rather than listing the unclaimed ones at the end.
    assert any("nothing says this sheet was read" in problem
               for problem in report["problems"]), report


def test_a_clean_review_passes_and_binds_to_the_revision(translated, tmp_path):
    out = tmp_path / "review"
    sheets = meaning.write_sheets(translated, out)["sheets"]
    for sheet_id in sheets:
        _reply(out, sheet_id, f"!! reviewed {sheet_id}\n")
    written = meaning.record(out, translated)
    assert written["ok"] is True
    assert written["blocking"] == []

    rev = meaning.revision(meaning.pairs(ir.load_book(translated)))
    assert written["revision"] == rev
    assert meaning.verdict(out, rev)["ok"] is True


def test_a_review_does_not_survive_the_translation_changing(translated, tmp_path):
    out = tmp_path / "review"
    sheets = meaning.write_sheets(translated, out)["sheets"]
    for sheet_id in sheets:
        _reply(out, sheet_id, f"!! reviewed {sheet_id}\n")
    meaning.record(out, translated)

    book = ir.load_book(translated)
    book["blocks"][0]["target"] = "متن دیگری که هیچ‌کس ندیده است."
    ir.save_book(book, translated)

    rev = meaning.revision(meaning.pairs(ir.load_book(translated)))
    answer = meaning.verdict(out, rev)
    assert answer["ok"] is False
    assert answer["refused"] == "stale-review"


def test_a_review_does_not_survive_the_source_changing(translated, tmp_path):
    """A re-extracted page invalidates the review as surely as a re-translation."""
    out = tmp_path / "review"
    sheets = meaning.write_sheets(translated, out)["sheets"]
    for sheet_id in sheets:
        _reply(out, sheet_id, f"!! reviewed {sheet_id}\n")
    before = meaning.record(out, translated)["revision"]

    book = ir.load_book(translated)
    book["blocks"][0]["text"] = "He frowned, then left without a word."
    ir.save_book(book, translated)
    after = meaning.revision(meaning.pairs(ir.load_book(translated)))

    assert after != before
    assert meaning.verdict(out, after)["refused"] == "stale-review"


def test_a_digest_this_reader_cannot_recompute_is_neither_fresh_nor_stale(
        translated, tmp_path):
    out = tmp_path / "review"
    sheets = meaning.write_sheets(translated, out)["sheets"]
    for sheet_id in sheets:
        _reply(out, sheet_id, f"!! reviewed {sheet_id}\n")
    meaning.record(out, translated)

    path = meaning.sidecar_path(out)
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["revision"] = "meaning9:" + "0" * 64
    ir.write_text(path, json.dumps(stored, ensure_ascii=False, indent=1) + "\n")

    rev = meaning.revision(meaning.pairs(ir.load_book(translated)))
    answer = meaning.verdict(out, rev)
    assert answer["refused"] == "unverified-digest", (
        "a digest in a formula this reader does not know was treated as an "
        "answer; both of the two guesses are wrong and one of them passes")


def test_an_unreviewed_translation_is_not_a_clean_one(tmp_path):
    rev = f"{meaning.DIGEST_VERSION}:" + "0" * 64
    answer = meaning.verdict(tmp_path / "review", rev)
    assert answer["ok"] is False
    assert answer["refused"] == "not-reviewed"


# --------------------------------------------------------------------------- #
# Meaning blocks; style does not
# --------------------------------------------------------------------------- #

def test_a_meaning_finding_blocks_and_asks_for_that_unit(translated, tmp_path):
    out = tmp_path / "review"
    sheets = meaning.write_sheets(translated, out)["sheets"]
    for sheet_id in sheets:
        _reply(out, sheet_id,
               "?? b00002 sense\n"
               "The source says she did not refuse; the translation says she "
               "refused.\n"
               f"!! reviewed {sheet_id}\n")
    written = meaning.record(out, translated)
    assert written["ok"] is False
    assert written["blocking"] == ["b00002/sense"]

    request = meaning.repair_requests(out)
    assert request["ok"] is True
    assert request["units"] == ["b00002"]


def test_a_style_note_is_recorded_and_asks_for_nothing(translated, tmp_path):
    """The restraint: nothing here tells a translator to rewrite for smoothness."""
    out = tmp_path / "review"
    sheets = meaning.write_sheets(translated, out)["sheets"]
    for sheet_id in sheets:
        _reply(out, sheet_id,
               "?? b00003 fluency\n"
               "The relative clause keeps English order.\n"
               "?? b00001 register\n"
               "Slightly more formal than the source.\n"
               f"!! reviewed {sheet_id}\n")
    written = meaning.record(out, translated)
    assert written["ok"] is True, (
        "a style note blocked the translation; a faithful sentence must not be "
        "sent back to be made smoother")
    assert written["blocking"] == []
    assert written["style_notes"] == ["b00001/register", "b00003/fluency"]
    assert meaning.repair_requests(out)["units"] == []


def test_every_rubric_declares_a_severity_and_both_kinds_exist():
    """Otherwise a rubric added later has no severity and silently never blocks."""
    kinds = {rubric["kind"] for rubric in meaning.RUBRICS.values()}
    assert kinds == {"meaning", "style"}
    for name, rubric in meaning.RUBRICS.items():
        for field in ("kind", "ask", "why", "bad", "good"):
            assert rubric.get(field), f"{name} is missing {field}"


def test_a_finding_with_no_argument_or_an_unknown_rubric_is_refused(translated,
                                                                   tmp_path):
    out = tmp_path / "review"
    sheets = meaning.write_sheets(translated, out)["sheets"]
    for sheet_id in sheets:
        _reply(out, sheet_id,
               "?? b00001 sense\n"
               f"!! reviewed {sheet_id}\n"
               "?? b00002 vibes\nSomething feels off.\n")
    report = meaning.record(out, translated)
    assert report["ok"] is False
    assert any("no argument given" in problem for problem in report["problems"])
    assert any("not a rubric" in problem for problem in report["problems"])


def test_a_finding_about_a_unit_this_book_does_not_have_is_refused(translated,
                                                                  tmp_path):
    out = tmp_path / "review"
    sheets = meaning.write_sheets(translated, out)["sheets"]
    for sheet_id in sheets:
        _reply(out, sheet_id,
               "?? b99999 omission\nA clause is missing.\n"
               f"!! reviewed {sheet_id}\n")
    report = meaning.record(out, translated)
    assert report["ok"] is False
    assert any("no such unit" in problem for problem in report["problems"])


def test_sheets_written_before_the_book_changed_are_refused(translated, tmp_path):
    out = tmp_path / "review"
    sheets = meaning.write_sheets(translated, out)["sheets"]
    book = ir.load_book(translated)
    book["blocks"][0]["target"] = "چیز دیگری."
    ir.save_book(book, translated)
    for sheet_id in sheets:
        _reply(out, sheet_id, f"!! reviewed {sheet_id}\n")
    report = meaning.record(out, translated)
    assert report["refused"] == "stale-sheets"


# --------------------------------------------------------------------------- #
# Repair is bounded
# --------------------------------------------------------------------------- #

def _round(out: Path, translated: Path, body: str) -> dict:
    for sheet_id in json.loads(
            (out / "manifest.json").read_text(encoding="utf-8"))["sheets"]:
        _reply(out, sheet_id, body + f"!! reviewed {sheet_id}\n")
    return meaning.record(out, translated)


def test_the_same_finding_twice_escalates_instead_of_asking_again(translated,
                                                                 tmp_path):
    out = tmp_path / "review"
    meaning.write_sheets(translated, out)
    body = "?? b00002 sense\nThe negation is still reversed.\n"

    first = _round(out, translated, body)
    assert first["round"] == 1
    assert meaning.repair_requests(out)["ok"] is True

    second = _round(out, translated, body)
    assert second["round"] == 2
    assert second["history"] == [["b00002/sense"], ["b00002/sense"]]
    request = meaning.repair_requests(out)
    assert request["ok"] is False
    # Named exactly. Accepting either refusal here is what let the more useful
    # one sit behind the round cap as dead code.
    assert request["refused"] == "no-new-evidence"
    assert request["units"] == []


def _repair(translated: Path, unit: int, text: str) -> None:
    """A real repair: the target changes, which is what moves the revision."""
    book = ir.load_book(translated)
    book["blocks"][unit]["target"] = text
    ir.save_book(book, translated)


def test_the_budget_counts_arguments_about_one_unit_not_rounds(translated,
                                                              tmp_path):
    """Three real rewrites of one sentence, each still wrong, and it stops.

    The budget used to be a round counter cleared whenever the revision moved,
    and a repair is what moves it — so five rewrites of the same wrong sentence
    all reported round 1 and a sixth was still permitted. It counts arguments
    about one ``(unit, rubric)`` now, and a rewrite is an argument rather than a
    fresh start.
    """
    out = tmp_path / "review"
    for attempt, rewrite in enumerate(
            ["او دعوت را پذیرفت.", "او دعوت را قبول کرد.", "او دعوت را رد کرد."],
            start=1):
        meaning.write_sheets(translated, out)
        recorded = _round(out, translated,
                          f"?? b00002 sense\nStill inverted, try {attempt}.\n")
        assert recorded["episodes"]["b00002/sense"]["attempts"] == attempt
        request = meaning.repair_requests(out)
        if attempt < 3:
            assert request["ok"] is True, request
            _repair(translated, 1, rewrite)

    assert request["ok"] is False
    assert request["refused"] == "rounds-exhausted"
    # The arguments travel with the escalation: "look at the glossary entry" is
    # not actionable without the three arguments that were actually made.
    assert len(request["escalate"][0]["arguments"]) == 3, request


def test_an_unrelated_later_issue_starts_with_its_own_budget(translated, tmp_path):
    """One exhausted issue does not exhaust the next one.

    This is what a whole-review round cap got wrong in the other direction: a
    problem found in a *different* unit after the first was repaired spent a
    budget it had nothing to do with, and the review stopped asking for a repair
    nobody had asked for yet.
    """
    out = tmp_path / "review"
    meaning.write_sheets(translated, out)
    _round(out, translated, "?? b00001 omission\nA clause is missing.\n")
    assert meaning.repair_requests(out)["ok"] is True

    _repair(translated, 0, "لبخند زد و بی‌آنکه چیزی بگوید رفت، بی‌درنگ.")
    meaning.write_sheets(translated, out)
    second = _round(out, translated, "?? b00003 sense\nThe lane became a street.\n")

    assert second["resolved"] == ["b00001/omission"], second
    assert second["episodes"]["b00003/sense"]["attempts"] == 1
    request = meaning.repair_requests(out)
    assert request["ok"] is True, request
    assert request["units"] == ["b00003"]


def test_a_corrected_translation_continues_the_episode(translated, tmp_path):
    """A rewrite earns another attempt, not a fresh budget.

    This assertion used to be the opposite — ``round == 1`` after a real repair,
    "the repair budget was spent against text that has since changed" — and that
    is the defect: every repair changed the text, so every repair reset the
    budget and the cap was unreachable. A corrected translation is the *second
    attempt* at the same issue; it is allowed, and it counts.
    """
    out = tmp_path / "review"
    meaning.write_sheets(translated, out)
    _round(out, translated, "?? b00002 sense\nReversed.\n")

    _repair(translated, 1, "او دعوت را نپذیرفت.")
    meaning.write_sheets(translated, out)

    again = _round(out, translated, "?? b00002 sense\nStill not right.\n")
    episode = again["episodes"]["b00002/sense"]
    assert episode["attempts"] == 2, episode
    assert len(set(episode["wordings"])) == 2, "the fixture did not really repair"
    assert meaning.repair_requests(out)["ok"] is True


def test_a_corrected_source_is_a_different_argument(translated, tmp_path):
    """Re-extracting the source is a new book, and a new episode.

    The line the brief draws: a *target* edit continues the episode, because that
    is the repair loop; a *source* correction means the earlier argument was
    about text the book no longer contains. The superseded episode is archived
    rather than deleted, so the reset is visible to whoever reads it next.
    """
    out = tmp_path / "review"
    meaning.write_sheets(translated, out)
    _round(out, translated, "?? b00002 sense\nReversed.\n")
    _repair(translated, 1, "او دعوت را نپذیرفت.")
    meaning.write_sheets(translated, out)
    _round(out, translated, "?? b00002 sense\nStill not right.\n")

    book = ir.load_book(translated)
    book["blocks"][1]["text"] = "She turned the invitation down flat."
    ir.save_book(book, translated)
    meaning.write_sheets(translated, out)

    again = _round(out, translated, "?? b00002 sense\nAbout the new source now.\n")
    episode = again["episodes"]["b00002/sense"]
    assert episode["attempts"] == 1, episode
    assert len(episode["superseded"]) == 1, episode
    assert episode["superseded"][0]["superseded_because"] == "source-changed"
    assert episode["superseded"][0]["attempts"] == 2, "the history was discarded"
    assert meaning.repair_requests(out)["ok"] is True


def test_writing_the_sheets_exits_zero(translated, tmp_path, capsys):
    """The recipe is four shell commands, so a success that exits 2 stops it.

    `write_sheets` had no `ok` key, and `main` read the exit code through
    `report.get("ok", report.get("verdict", {}).get("ok"))` — two silent defaults
    stacked, so a missing key fell through to a `verdict` that was also absent
    and became `None`. Writing the sheets therefore reported failure every single
    time it succeeded, and an agent running the step under `set -e` never reached
    the review.
    """
    out = tmp_path / "review"
    assert meaning.main(["sheets", "--book", str(translated), "--out", str(out)]) == 0
    capsys.readouterr()
    assert (out / "sheet_0001.md").exists()


def test_every_stage_report_carries_the_key_its_exit_code_is_read_from(
        translated, tmp_path, capsys):
    """One assertion per action, so the next one added cannot omit it silently."""
    out = tmp_path / "review"
    assert "ok" in meaning.write_sheets(translated, out)
    _reply(out, "sheet_0001", "!! reviewed sheet_0001\n")
    assert "ok" in meaning.record(out, translated)
    assert "ok" in meaning.verdict(
        out, meaning.revision(meaning.pairs(ir.load_book(translated))))
    assert "ok" in meaning.repair_requests(out)

    for action in (["record"], ["status"]):
        assert meaning.main(
            action + ["--book", str(translated), "--out", str(out)]) == 0
        capsys.readouterr()
