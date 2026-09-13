"""What a reader sees, and which approval covers it.

Four stages each walked the *source* to decide what text exists — the worksheet
cut, the bilingual review, the blind Persian pass and the typography fixer. They
agreed about body paragraphs, and nothing noticed that published prose with no
English original was in none of them. Measured on a book with a translated
title, a translated byline and a translator's own footnote:

    inventory units: ['b00001', 'b00002']
    meta.title_target        revision moved: False   verdict still ok: True
    meta.author_target       revision moved: False   verdict still ok: True
    translator note target   revision moved: False   verdict still ok: True

The title page is the first thing a reader meets. It was reviewed by nobody,
covered by no revision, and `falint fix` never touched its typography.

So this file asks one question of every published surface: change it on its own,
and does the approval that covers it become stale? Plus the two edges that make
the answer trustworthy — a unit that still owes a translation cannot disappear
from the required set, and a unit whose source is *only* a literal is exempt
rather than pending, because it is meant to survive byte for byte.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bookir as ir  # noqa: E402
import falint  # noqa: E402
import fluency  # noqa: E402
import meaning  # noqa: E402
import merge as merging  # noqa: E402
import published  # noqa: E402
import qa  # noqa: E402
import signoff  # noqa: E402
from tests_support import png_bytes, review_reply  # noqa: E402

SOURCE = ["She did not refuse the invitation.",
          "The house stood at the end of a long lane."]
TARGET = ["او دعوت را رد نکرد.", "خانه در انتهای کوچه‌ای بلند بود."]
TITLE = "خانه‌ای در انتهای کوچه"
AUTHOR = "نویسندهٔ آزمون"


@pytest.fixture()
def book_path(tmp_path: Path) -> Path:
    """One of each published surface, all of them translated.

    A body paragraph, an illustration's caption, a note that came with the book,
    a note the translator added, a running head, and the title page. Every one of
    them prints; the point of the fixture is that they are not all the same shape.
    """
    book = ir.new_book(source_path="sample.epub", source_format="epub",
                       title="A House at the End of the Lane",
                       author="A Test Author")
    for index, text in enumerate(SOURCE, start=1):
        book["blocks"].append(ir.make_block("paragraph", index, text=text))
    book["blocks"].append(ir.make_block(
        "image", 3, asset="fig.png", alt="A red rectangle",
        width_pt=120.0, height_pt=80.0))
    book["blocks"][0]["text"] += "[[fn:fn0001]]"
    book["footnotes"] = [
        ir.make_footnote(1, anchor_block="b00001",
                         text="A note that came with the book."),
        ir.make_footnote(2, anchor_block="b00001", text="", origin="translator"),
    ]
    book["sections"] = [{
        "index": 0, "start_block": None, "footers": {},
        "headers": {"default": {"paragraphs": [
            {"align": None,
             "pieces": [{"id": "rh0001", "text": "Chapter One",
                         "target": None}]}]}},
    }]
    # A real picture on disk, so the gate has an asset to check rather than a
    # missing one to refuse.
    assets = tmp_path / "assets"
    assets.mkdir(exist_ok=True)
    picture = png_bytes(120, 80)
    (assets / "fig.png").write_bytes(picture)
    book["blocks"][2]["sha256"] = ir.sha256_bytes(picture)
    path = tmp_path / "book.json"
    ir.save_book(book, path)

    # Written where a merge writes them, so the inventory is exercised through
    # the same resolver the pipeline uses.
    book = ir.load_book(path)
    resolve = merging.addressing(book)
    persian = dict(zip(["b00001", "b00002"], TARGET))
    # Both markers carried into the Persian, the way a translator carries them:
    # a lost marker is its own QA error and would drown out what this file asks.
    persian["b00001"] += "[[fn:fn0001]][[fn:fn0002]]"
    persian["b00003#alt"] = "مستطیلی سرخ"
    persian["fn0001"] = "یادداشتی که با کتاب آمده است."
    persian["fn0002"] = "یادداشت مترجم."
    persian[published.TITLE_ID] = TITLE
    persian[published.AUTHOR_ID] = AUTHOR
    for unit_id, text in persian.items():
        container, field = resolve(unit_id)
        container[field] = text
    for _unit_id, _kind, piece, _section in ir.iter_running_pieces(book):
        piece["target"] = "فصل یکم"
    ir.save_book(book, path)
    return path


def _approve(book_path: Path, review_dir: Path) -> dict:
    for sheet_id in meaning.write_sheets(book_path, review_dir)["sheets"]:
        ir.write_text(review_dir / f"out_{sheet_id}.md",
                      review_reply(review_dir / f"{sheet_id}.md",
                                   f"!! reviewed {sheet_id}\n"))
    return meaning.record(review_dir, book_path)


def _blind(book_path: Path, out: Path, review_dir: Path) -> dict:
    for sheet_id in fluency.write_sheets(book_path, out, review_dir)["sheets"]:
        ir.write_text(out / f"out_{sheet_id}.md",
                      review_reply(out / f"{sheet_id}.md",
                                   f"!! reviewed {sheet_id}\n"))
    return fluency.record(out, book_path)


def _meaning_ok(book_path: Path, review_dir: Path) -> bool:
    book = ir.load_book(book_path)
    return bool(meaning.verdict(review_dir,
                                meaning.revision(meaning.pairs(book))).get("ok"))


# --------------------------------------------------------------------------- #
# The inventory itself
# --------------------------------------------------------------------------- #

def test_every_published_surface_is_in_the_inventory_and_typed(book_path):
    units = {unit["id"]: unit for unit in published.units(ir.load_book(book_path))}

    assert set(units) == {
        published.TITLE_ID, published.AUTHOR_ID,
        "rh0001", "b00001", "b00002", "b00003#alt",
        "fn0001", "fn0002",
    }, sorted(units)
    assert units[published.TITLE_ID]["part"] == "metadata"
    assert units["rh0001"]["part"] == "running"
    assert units["b00003#alt"]["part"] == "figure"
    assert units["fn0001"]["part"] == "note"
    # The distinction the brief asks for by name: a translator's addition is
    # classified as one instead of being handed an invented English source.
    assert units["fn0001"]["origin"] == "source"
    assert units["fn0002"]["origin"] == "translator"
    assert units["fn0002"]["source"] is None, (
        "a fabricated source side would be compared against as if the author "
        "had written it")


def test_a_translator_addition_is_not_asked_to_match_a_source(book_path, tmp_path):
    """The sheet says what it is, rather than showing an empty source box."""
    out = tmp_path / "review"
    meaning.write_sheets(book_path, out, per_sheet=40)
    text = (out / "sheet_0001.md").read_text(encoding="utf-8")

    assert "fn0002" in text
    assert "[no source — the translator added this]" in text
    assert "یادداشت مترجم." in text


def test_the_inventory_is_the_one_merge_writes_through(book_path):
    """One addressing rule. Two would drift towards a field nothing writes."""
    book = ir.load_book(book_path)
    resolve = merging.addressing(book)

    for unit in published.units(book):
        slot = resolve(unit["id"])
        assert slot is not None, f"{unit['id']} is published and unaddressable"
        container, field = slot
        assert container is unit["container"] and field == unit["field"]


# --------------------------------------------------------------------------- #
# Each field, on its own, against the approval that covers it
# --------------------------------------------------------------------------- #

#: ``(name, mutation)`` — one published field each, changed the way an operator
#: would change it. Every one of these used to leave both approvals reading `ok`.
FIELDS = [
    pytest.param(lambda b: b["meta"].__setitem__("title_target", "عنوانی دیگر"),
                 id="title"),
    pytest.param(lambda b: b["meta"].__setitem__("author_target", "کسی دیگر"),
                 id="author"),
    pytest.param(lambda b: b["footnotes"][1].__setitem__("target", "چیز دیگری."),
                 id="translator-note"),
    pytest.param(lambda b: b["footnotes"][0].__setitem__("target", "یادداشتی دیگر."),
                 id="source-note"),
    pytest.param(lambda b: b["blocks"][2].__setitem__("target_alt", "مربعی آبی"),
                 id="caption"),
    pytest.param(lambda b: b["sections"][0]["headers"]["default"]["paragraphs"][0]
                 ["pieces"][0].__setitem__("target", "فصل دوم"),
                 id="running-head"),
    pytest.param(lambda b: b["blocks"][0].__setitem__("target", "متن دیگری."),
                 id="body"),
]


@pytest.mark.parametrize("change", FIELDS)
def test_changing_one_published_field_makes_the_meaning_approval_stale(
        book_path, tmp_path, change):
    review_dir = tmp_path / "review"
    assert _approve(book_path, review_dir)["ok"] is True
    assert _meaning_ok(book_path, review_dir) is True

    book = ir.load_book(book_path)
    change(book)
    ir.save_book(book, book_path)

    assert _meaning_ok(book_path, review_dir) is False, (
        "the approval survived a change to published prose, so it certifies "
        "text that is not in the book")


@pytest.mark.parametrize("change", FIELDS)
def test_changing_one_published_field_makes_the_blind_pass_stale(
        book_path, tmp_path, change):
    review_dir, out = tmp_path / "review", tmp_path / "fluency"
    assert _approve(book_path, review_dir)["ok"] is True
    assert _blind(book_path, out, review_dir)["ok"] is True
    assert fluency.verdict(out, book_path, review_dir)["ok"] is True

    book = ir.load_book(book_path)
    change(book)
    ir.save_book(book, book_path)

    answer = fluency.verdict(out, book_path, review_dir)
    assert answer["ok"] is False
    # Either refusal is honest here — the Persian moved, and the meaning review
    # of the moved book is stale too — but silence is not.
    assert answer["refused"] in ("stale-review", "meaning-unconfirmed"), answer


# --------------------------------------------------------------------------- #
# Pending and exempt: the two edges
# --------------------------------------------------------------------------- #

def test_a_unit_owing_a_translation_cannot_disappear_from_the_required_set(
        book_path, tmp_path):
    """It is kept off the blind sheets on purpose, and named anyway.

    A reviewer with no source cannot translate it, so showing it invites exactly
    that — and filtering it out of the inventory is how one vanished from
    everything that asks.
    """
    review_dir, out = tmp_path / "review", tmp_path / "fluency"
    book = ir.load_book(book_path)
    book["meta"]["title_target"] = None
    ir.save_book(book, book_path)

    assert [unit["id"] for unit in published.pending(ir.load_book(book_path))] \
        == [published.TITLE_ID]
    assert _approve(book_path, review_dir)["ok"] is True
    blind = _blind(book_path, out, review_dir)
    assert blind["ok"] is True, "the sheets themselves are fine; the book is not"
    assert published.TITLE_ID not in json.dumps(blind.get("proposed") or [])

    answer = fluency.verdict(out, book_path, review_dir)
    assert answer["ok"] is False
    assert answer["refused"] == "pending-units"
    assert answer["pending"] == [published.TITLE_ID]

    codes = [code for code, _unit, _detail in signoff.problems(
        book_path, review_dir=review_dir, fluency_dir=out)]
    assert signoff.PENDING in codes


def test_a_source_that_is_only_a_literal_is_exempt_not_pending(book_path):
    """`` `literal_token` `` is *supposed* to survive byte for byte."""
    book = ir.load_book(book_path)
    book["blocks"][1]["text"] = "`literal_token`"
    book["blocks"][1]["target"] = None
    ir.save_book(book, book_path)

    units = {unit["id"]: unit for unit in published.units(ir.load_book(book_path))}
    assert units["b00002"]["literal"] is True
    assert [unit["id"] for unit in published.pending(ir.load_book(book_path))] == []


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #

def test_typography_now_reaches_the_title_page(book_path):
    """The first line a reader meets, and `falint` had never seen it."""
    book = ir.load_book(book_path)
    book["meta"]["title_target"] = 'خانه‌ای "در انتهای" کوچه ۱۲۳'
    book["meta"]["author_target"] = "نویسنده 123"
    ir.save_book(book, book_path)

    book = ir.load_book(book_path)
    changed = falint.fix_book(book)

    assert published.TITLE_ID in changed["changed"], (
        "the title page keeps whatever punctuation the hand-edit left in it")
    assert published.AUTHOR_ID in changed["changed"]
    assert book["meta"]["author_target"] == "نویسنده ۱۲۳"


def test_the_gate_reports_an_unasked_semantic_review_rather_than_passing(
        book_path, tmp_path, capsys):
    """A gate nobody ran must never read as a gate that passed."""
    assert qa.main(["check", "--book", str(book_path)]) == 0
    report = json.loads(capsys.readouterr().out)
    codes = {finding["code"] for finding in report["findings"]}
    assert signoff.UNVERIFIED in codes, report
    assert report["ok"] is True, "not asking is a warning by default"

    # `--strict` is what publication work uses, and there it blocks.
    assert qa.main(["check", "--book", str(book_path), "--strict"]) == 1
    strict = json.loads(capsys.readouterr().out)
    assert strict["ok"] is False
    assert any(finding["code"] == signoff.UNVERIFIED
               and finding["severity"] == qa.ERROR
               for finding in strict["findings"]), strict


def test_the_gate_refuses_a_review_that_no_longer_describes_the_book(
        book_path, tmp_path, capsys):
    review_dir, out = tmp_path / "review", tmp_path / "fluency"
    assert _approve(book_path, review_dir)["ok"] is True
    assert _blind(book_path, out, review_dir)["ok"] is True

    assert qa.main(["check", "--book", str(book_path),
                    "--review", str(review_dir), "--fluency", str(out),
                    "--strict"]) == 0
    capsys.readouterr()

    # One word of the title, changed after both approvals.
    book = ir.load_book(book_path)
    book["meta"]["title_target"] = "خانه‌ای در انتهای خیابان"
    ir.save_book(book, book_path)

    assert qa.main(["check", "--book", str(book_path),
                    "--review", str(review_dir), "--fluency", str(out)]) == 1
    report = json.loads(capsys.readouterr().out)
    rejected = [finding for finding in report["findings"]
                if finding["code"] == signoff.REJECTED]
    assert {finding["unit"] for finding in rejected} == {"meaning", "fluency"}, report
    assert all(finding["severity"] == qa.ERROR for finding in rejected)
