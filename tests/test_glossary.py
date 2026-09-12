"""Glossary identity and compliance.

The glossary is shared state: every chunk is translated against it, every gate
reports against it, and an entry's id is the only handle any of that has. So the
two things that must hold are that an id means one entity, and that a match
means the name and not a word that merely starts with it.
"""

from __future__ import annotations

import bookir as ir
import glossary as gl

#: Mid-sentence mentions only, so the scan's sentence-opening filter keeps them.
#: Frequencies are deliberately distinct: Elizabeth Bennet 6, Darcy 5,
#: Netherfield 4, then Lydia 2 and Meryton 2 for a second, looser scan.
SOURCE = [
    "The morning came slowly and Elizabeth Bennet stood by the window.",
    "Then Darcy said nothing to Elizabeth Bennet at all.",
    "At Netherfield the talk was of Elizabeth Bennet and of Darcy.",
    "Nobody at Netherfield mentioned Darcy again that evening.",
    "She walked to Netherfield with Elizabeth Bennet and with Darcy.",
    "A letter from Elizabeth Bennet reached Netherfield before Darcy did.",
    "Then Lydia went down to Meryton without Elizabeth Bennet.",
    "The road to Meryton was long and Lydia complained of it.",
]


def _book(tmp_path):
    book = ir.new_book()
    book["blocks"] = [
        ir.make_block("paragraph", index, page=1, text=text)
        for index, text in enumerate(SOURCE, start=1)
    ]
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    return book, path


# --------------------------------------------------------------------------- #
# Id allocation
# --------------------------------------------------------------------------- #

def test_a_rescan_after_a_deletion_does_not_reuse_an_id(tmp_path):
    """Deleting a false candidate is the documented way to reject one.

    That leaves a gap — `g0001, g0003` is the normal shape of a worked-on
    glossary — and an id derived from the *length* of the list hands the next
    candidate an id that is already taken. Two entries under one id means a
    locked decision can be looked up and the wrong entity found.
    """
    book, book_path = _book(tmp_path)
    out = tmp_path / "glossary.json"

    assert gl.main(["scan", "--book", str(book_path), "--out", str(out),
                    "--min-count", "4"]) == 0
    glossary = gl.load(out)
    assert [entry["id"] for entry in glossary["entries"]] == ["g0001", "g0002", "g0003"]

    # Lock one decision, delete the middle candidate as a reviewer would.
    locked = glossary["entries"][0]
    locked.update({"target": "الیزابت بنت", "later_form": "الیزابت بنت",
                   "locked": True, "notes": "decided"})
    rejected = glossary["entries"].pop(1)
    gl.save(glossary, out)

    assert gl.main(["scan", "--book", str(book_path), "--out", str(out),
                    "--min-count", "2"]) == 0
    after = gl.load(out)

    ids = [entry["id"] for entry in after["entries"]]
    assert len(set(ids)) == len(ids), ids
    assert rejected["id"] not in ids, "a deleted candidate's id must stay spent"

    # Every id resolves to exactly one entry, and the locked one is still itself.
    by_id = {entry["id"]: entry for entry in after["entries"]}
    assert len(by_id) == len(after["entries"])
    assert by_id[locked["id"]]["source"] == locked["source"]
    assert by_id[locked["id"]]["locked"] is True
    assert by_id[locked["id"]]["later_form"] == "الیزابت بنت"

    # And every block a candidate points at is still a real block.
    blocks = ir.blocks_by_id(book)
    for entry in after["entries"]:
        assert not entry["first_block_id"] or entry["first_block_id"] in blocks


def test_the_second_scan_proposes_the_names_the_first_minimum_excluded(tmp_path):
    """Guards the fixture: without new proposals the test above proves nothing."""
    _book_unused, book_path = _book(tmp_path)
    out = tmp_path / "glossary.json"
    gl.main(["scan", "--book", str(book_path), "--out", str(out), "--min-count", "4"])
    first = {entry["source"] for entry in gl.load(out)["entries"]}
    gl.main(["scan", "--book", str(book_path), "--out", str(out), "--min-count", "2"])
    assert {entry["source"] for entry in gl.load(out)["entries"]} > first
