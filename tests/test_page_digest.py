"""A page is accepted against what it renders, not against its body prose.

`translation_hash` covered `expectations(...)["texts"]` — the page's paragraphs and
nothing else. So a page that had passed render QA and been looked at could still be
accepted after its **footnote translation**, its **running head**, its **caption**,
its **figure** or its **page width** changed: none of those move the body text, and
every gate above the digest describes an earlier moment.

Ordinary body-target changes were already rejected, and that has to stay rejected —
it is the one half of this check that worked.

The digest is versioned because it grew. A value written by the older formula is
not evidence either way: compared, every such page reads as changed; ignored, every
such page reads as current. It refuses as `unverified-digest` instead, which one
`render-qa` run clears.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bookir as ir  # noqa: E402
import pagerun  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tests_support import png_bytes  # noqa: E402


def _book(tmp_path: Path) -> Path:
    """One page carrying prose, a note, a figure with a caption and a head."""
    book = ir.new_book(lang_source="en", lang_target="fa-IR")
    para = ir.make_block("paragraph", 1, page=1, bbox=[72, 90, 320, 140],
                         text="Ali arrived at dawn.[[fn:fn0001]]")
    para["target"] = "علی سپیده‌دم رسید.[[fn:fn0001]]"
    figure = ir.make_block("image", 2, page=1, bbox=[72, 150, 320, 300],
                           asset="p0001-img001.png")
    figure["alt"] = "A red rectangle."
    figure["target_alt"] = "مستطیلی سرخ."
    figure["width_pt"] = 180
    figure["height_pt"] = 120
    book["blocks"] = [para, figure]
    # A real file, beside book.json where every stage looks for it. The asset's
    # identity is read from the bytes on disk — an earlier version hashed a
    # `asset_sha256` field instead, which nothing in this project writes, so it
    # contributed the empty string for every figure and a replaced picture
    # changed nothing. A fixture that sets that invented key by hand agrees with
    # the bug, which is exactly how it survived.
    assets = tmp_path / "assets"
    assets.mkdir(exist_ok=True)
    (assets / "p0001-img001.png").write_bytes(png_bytes(120, 80))
    note = ir.make_footnote(1, anchor_block=para["id"], text="A source note.")
    note["target"] = "یادداشت منبع."
    book["footnotes"] = [note]
    book["sections"] = [{
        "start_block": para["id"],
        "headers": {"default": {"paragraphs": [
            {"align": "center", "pieces": [
                {"id": "rh0001", "text": "Pride and Prejudice",
                 "target": "غرور و تعصب"}]}]}},
        "footers": {},
    }]
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    return path


def _edit(book_path: Path, change) -> str:
    book = ir.load_book(book_path)
    change(book)
    ir.save_book(book, book_path)
    return pagerun.translation_hash(book_path, 1)


# --------------------------------------------------------------------------- #
# Everything the page renders moves the digest
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("what, change", [
    ("the body translation",
     lambda b: b["blocks"][0].update(target="علی هرگز نرسید.")),
    ("a footnote's translation",
     lambda b: b["footnotes"][0].update(target="یادداشتی دیگر.")),
    ("a caption",
     lambda b: b["blocks"][1].update(target_alt="مستطیلی آبی.")),
    ("the figure's asset name",
     lambda b: b["blocks"][1].update(asset="p0001-img009.png")),
    ("a running head",
     lambda b: b["sections"][0]["headers"]["default"]["paragraphs"][0]
     ["pieces"][0].update(target="غرور و تعصب، ویرایش دوم")),
    ("the page width",
     lambda b: b["page"].update(width_pt=999.0)),
])
def test_a_change_to_anything_rendered_moves_the_digest(tmp_path, what, change):
    """Each of these was invisible. A page accepted after one of them had passed
    QA against a sheet that no longer exists."""
    book_path = _book(tmp_path)
    before = pagerun.translation_hash(book_path, 1)

    after = _edit(book_path, change)

    assert after != before, f"{what} left the digest unchanged"


def test_an_untouched_page_hashes_the_same_twice(tmp_path):
    """The negative control, and the one that makes the digest usable: a digest
    that moved on its own would refuse every page forever."""
    book_path = _book(tmp_path)

    first = pagerun.translation_hash(book_path, 1)
    second = pagerun.translation_hash(book_path, 1)

    assert first == second


def test_an_unrelated_page_does_not_move_this_pages_digest(tmp_path):
    """Only the affected page is invalidated. Rehashing the whole book would make
    every edit anywhere re-open every accepted page."""
    book_path = _book(tmp_path)
    before = pagerun.translation_hash(book_path, 1)

    book = ir.load_book(book_path)
    other = ir.make_block("paragraph", 9, page=2, bbox=[72, 90, 320, 140],
                          text="A paragraph on page two.")
    other["target"] = "بندی در صفحهٔ دو."
    book["blocks"].append(other)
    ir.save_book(book, book_path)

    assert pagerun.translation_hash(book_path, 1) == before


def test_the_digest_says_which_formula_produced_it(tmp_path):
    """Tagged, so a value from an older formula is recognisable as one rather
    than being compared against a digest it cannot match."""
    book_path = _book(tmp_path)

    assert pagerun.translation_hash(book_path, 1).startswith(
        pagerun.PAGE_DIGEST_VERSION + ":")


# --------------------------------------------------------------------------- #
# A digest from an older formula is unverifiable, not fresh and not stale
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("recorded, why", [
    ("", "no digest at all — a page whose QA predates the field"),
    ("a" * 64, "an untagged digest from the first formula"),
    ("page1:" + "b" * 64, "a digest from a superseded version"),
])
def test_an_incomparable_digest_is_reported_as_unverified(tmp_path, recorded, why):
    """Not `translation-changed`: claiming the page moved would be as wrong as
    claiming it is current, and the operator would go looking for an edit nobody
    made. One `render-qa` run records a comparable digest."""
    book_path = _book(tmp_path)
    record = {"hashes": {"translation": recorded}} if recorded else {"hashes": {}}

    refusal, detail = pagerun._translation_moved(book_path, 1, record)

    assert refusal == "unverified-digest", f"{why}: got {refusal!r}"
    assert "render-qa" in detail, detail


def test_a_matching_current_digest_passes(tmp_path):
    book_path = _book(tmp_path)
    record = {"hashes": {"translation": pagerun.translation_hash(book_path, 1)}}

    assert pagerun._translation_moved(book_path, 1, record) == ("", "")


def test_a_moved_current_digest_is_reported_as_changed(tmp_path):
    """The signal that already worked, still working and still named the same —
    an operator reading this message knows to re-render, not to migrate."""
    book_path = _book(tmp_path)
    record = {"hashes": {"translation": pagerun.translation_hash(book_path, 1)}}
    _edit(book_path, lambda b: b["blocks"][0].update(target="علی هرگز نرسید."))

    refusal, detail = pagerun._translation_moved(book_path, 1, record)

    assert refusal == "translation-changed", detail
    assert pagerun.PAGE_DIGEST_VERSION in detail


# --------------------------------------------------------------------------- #
# The asset's identity comes from the file, not from a field
# --------------------------------------------------------------------------- #

def test_replacing_the_picture_under_the_same_name_moves_the_digest(tmp_path):
    """The case a hand-made fixture could not have caught.

    An earlier version hashed `block["asset_sha256"]` — a field nothing in this
    project writes — so every figure contributed the empty string and swapping the
    picture changed nothing. The test passed because it set that same invented key
    itself, which is the shape of a fixture agreeing with the bug. The identity is
    read from the bytes on disk now, so this is the real question.
    """
    book_path = _book(tmp_path)
    before = pagerun.translation_hash(book_path, 1)

    (tmp_path / "assets" / "p0001-img001.png").write_bytes(png_bytes(200, 140,
                                                                    (20, 90, 200)))

    assert pagerun.translation_hash(book_path, 1) != before, (
        "a different picture under the same filename left the page acceptable")


def test_deleting_the_picture_moves_the_digest(tmp_path):
    """A figure that is simply gone must invalidate the page too — the sheet a
    reviewer approved had a picture on it."""
    book_path = _book(tmp_path)
    before = pagerun.translation_hash(book_path, 1)

    (tmp_path / "assets" / "p0001-img001.png").unlink()

    assert pagerun.translation_hash(book_path, 1) != before


def test_an_untouched_picture_does_not_move_the_digest(tmp_path):
    """The negative control: reading the file must be stable, or every page
    re-opens on every check."""
    book_path = _book(tmp_path)

    assert pagerun.translation_hash(book_path, 1) == \
        pagerun.translation_hash(book_path, 1)


def test_the_digest_reads_no_field_the_project_never_writes(tmp_path):
    """A guard on the class of defect, not the instance. Every key the digest
    consults has to be one the real constructors or readers actually produce —
    hashing a misspelled field silently hashes nothing, and the feature quietly
    does not exist."""
    import inspect
    import re

    # Comments and the docstring are stripped first: this function's comments
    # *name* the field that caused the defect, in order to explain it, and a guard
    # that cannot tell an explanation from a lookup would forbid writing the
    # explanation down.
    source = inspect.getsource(pagerun.translation_hash)
    source = source.replace(inspect.getdoc(pagerun.translation_hash) or "", "")
    code = "\n".join(re.sub(r"#.*$", "", line) for line in source.splitlines())

    invented = [name for name in ("asset_sha256", "translation", "drop",
                                  "target_text", "dropped")
                if f'"{name}"' in code or f"'{name}'" in code]

    assert not invented, (
        f"the digest reads field(s) this project does not write: {invented}")
