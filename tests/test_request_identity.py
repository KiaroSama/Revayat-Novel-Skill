"""An answer is bound to the request it answers, not to a filename.

`out_chunk0002.md` is a *reusable* name. Rebuild the worksheets and an answer to
the previous cut sits at exactly the path the new cut expects — with the same ids,
the same count and the same parent block text. Measured before this was fixed: one
paragraph recut from a 1400 budget to a 1500 one came back as the **older
generation's** translation, merge reporting `ok: true` and `stale: []`.

Two values now carry the identity, and they answer different questions:

* `request` — a token the worksheet states and the reply echoes. It covers the
  ordered headers and kinds, the exact segment boundaries, the neighbouring
  context and the term table, and it is the only thing that can say *which
  version* a given answer was written for.
* `source_sha256` — the `units2:` digest over the units **as cut**, recomputable
  by merge from the spans the manifest records. It answers "has the book moved
  since the build", which the token cannot.

The other half of the contract is just as load-bearing: a reply that is still
valid stays usable. A budget change that does not touch a unit must not throw away
its translation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bookir as ir  # noqa: E402
import chunk as chunking  # noqa: E402
import merge as merging  # noqa: E402
import worksheet as ws  # noqa: E402
from tests_support import reply_text  # noqa: E402

#: Long enough that one paragraph has to be cut into several segments, and made of
#: distinguishable sentences so a mixed generation is visible in the result.
LONG = " ".join(f"Sentence number {n} of a paragraph that has to be cut."
                for n in range(1, 61))


def _book(tmp_path: Path, first: str = "A short first paragraph about Anna.") -> Path:
    book = ir.new_book(source_path="sample.epub", source_format="epub",
                       title="A Small Book", author="Test Author")
    book["blocks"].append(ir.make_block("paragraph", 1, text=first))
    book["blocks"].append(ir.make_block("paragraph", 2, text=LONG))
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    return path


def _manifest(chunks: Path) -> dict:
    return json.loads((chunks / "manifest.json").read_text(encoding="utf-8"))


def _answer(chunks: Path, entry: dict, mark: str) -> None:
    """A reply whose Persian says which generation wrote it."""
    kinds = entry.get("unit_kinds") or {}
    body = "\n".join(
        f"@@ {unit_id} {kinds.get(unit_id, 'para')}\n"
        f"ترجمهٔ {mark} برای {unit_id} که به اندازهٔ کافی بلند است.\n"
        for unit_id in entry["unit_ids"])
    ir.write_text(chunks / entry["output"],
                  reply_text(chunks / entry["file"], body))


def _answer_all(chunks: Path, mark: str = "یکم") -> None:
    for entry in _manifest(chunks)["chunks"]:
        _answer(chunks, entry, mark)


# --------------------------------------------------------------------------- #
# The token distinguishes generations, and only generations
# --------------------------------------------------------------------------- #

def test_a_recut_changes_the_request_but_an_untouched_unit_keeps_it(tmp_path):
    """Both halves of the contract, in one measurement.

    `chunk0001` carries the short first paragraph and is unaffected by the budget
    change, so its answer must stay usable. The segments of the long paragraph are
    cut differently, so theirs must not.
    """
    book_path = _book(tmp_path)
    first = chunking.build(book_path, tmp_path / "cut", glossary_path=None,
                           budget=1400, force=True)
    second = chunking.build(book_path, tmp_path / "cut", glossary_path=None,
                            budget=1500, force=True)

    before = {entry["id"]: entry for entry in first["chunks"]}
    after = {entry["id"]: entry for entry in second["chunks"]}

    assert before["chunk0001"]["unit_ids"] == after["chunk0001"]["unit_ids"]
    assert before["chunk0001"]["request"] == after["chunk0001"]["request"], (
        "a unit whose question did not change was given a new request token, so "
        "its perfectly good translation would be thrown away")

    assert before["chunk0002"]["unit_ids"] == after["chunk0002"]["unit_ids"], (
        "this test needs a recut that keeps the ids, which is the case the old "
        "fingerprint could not see")
    assert before["chunk0002"]["request"] != after["chunk0002"]["request"]
    assert before["chunk0002"]["source_sha256"] != after["chunk0002"]["source_sha256"]


def test_a_recut_never_merges_one_generation_into_another(tmp_path):
    """The defect itself, end to end.

    Answer every segment at one budget, recut at another into the same directory,
    then merge. The old answers must not be written as though they answered the
    new cut.
    """
    book_path = _book(tmp_path)
    chunks = tmp_path / "cut"
    chunking.build(book_path, chunks, glossary_path=None, budget=1400, force=True)
    _answer_all(chunks, "قدیمی")

    chunking.build(book_path, chunks, glossary_path=None, budget=1500, force=True)
    report = merging.merge(book_path, chunks, strict=True)

    assert report["ok"] is False, (
        "answers written for a different cut of this paragraph were accepted; "
        f"the merged text would mix generations: {report}")
    merged = ir.load_book(book_path)["blocks"][1].get("target")
    assert not merged, "the book was written from a mixed generation"


def test_the_answers_a_rebuild_supersedes_are_kept_to_copy_from(tmp_path):
    """Nobody's translation is deleted to make a gate pass."""
    book_path = _book(tmp_path)
    chunks = tmp_path / "cut"
    chunking.build(book_path, chunks, glossary_path=None, budget=1400, force=True)
    _answer_all(chunks, "قدیمی")

    manifest = chunking.build(book_path, chunks, glossary_path=None, budget=1500,
                              force=True)

    filed = manifest.get("superseded") or []
    assert filed, "a recut superseded nothing, so the old answers are still live"
    kept = sorted((chunks / "superseded").glob("*.md"))
    assert kept, "the superseded answers were not kept anywhere"
    assert any("قدیمی" in path.read_text(encoding="utf-8") for path in kept)


def test_a_late_reply_for_the_previous_cut_is_refused(tmp_path):
    """C01c: a worker that finishes after the rebuild writes to the same path.

    Superseding happens at rebuild time and cannot help here — the file arrives
    afterwards. Only the echoed token separates them.
    """
    book_path = _book(tmp_path)
    chunks = tmp_path / "cut"
    first = chunking.build(book_path, chunks, glossary_path=None, budget=1400,
                           force=True)
    stale_token = next(entry["request"] for entry in first["chunks"]
                       if entry["id"] == "chunk0002")

    second = chunking.build(book_path, chunks, glossary_path=None, budget=1500,
                            force=True)
    entry = next(e for e in second["chunks"] if e["id"] == "chunk0002")
    assert entry["request"] != stale_token

    # The late worker answers the question it was given, at the name it was told.
    kinds = entry.get("unit_kinds") or {}
    body = "\n".join(f"@@ {unit_id} {kinds.get(unit_id, 'para')}\nترجمهٔ دیرهنگام.\n"
                     for unit_id in entry["unit_ids"])
    ir.write_text(chunks / entry["output"],
                  f"{ws.request_line(stale_token)}\n{body}")
    for other in second["chunks"]:
        if other["id"] != entry["id"]:
            _answer(chunks, other, "تازه")

    report = merging.merge(book_path, chunks, strict=True)

    assert report["ok"] is False
    named = " ".join(report["malformed"].get(entry["id"], []))
    assert stale_token in named and entry["request"] in named, named


def test_a_reply_with_no_request_line_is_refused_and_can_be_revalidated(tmp_path):
    """Unbound is not trusted silently, and not lost either.

    Replies written before worksheets carried a token have to be dealt with
    explicitly: `--revalidate-unbound` accepts them on the strength of the source
    digest, and says which ones it did that for.
    """
    book_path = _book(tmp_path)
    chunks = tmp_path / "cut"
    manifest = chunking.build(book_path, chunks, glossary_path=None, budget=4000)
    for entry in manifest["chunks"]:
        kinds = entry.get("unit_kinds") or {}
        ir.write_text(chunks / entry["output"], "\n".join(
            f"@@ {unit_id} {kinds.get(unit_id, 'para')}\nترجمهٔ بدون نشانه.\n"
            for unit_id in entry["unit_ids"]))

    refused = merging.merge(book_path, chunks, strict=True)
    assert refused["ok"] is False
    assert any("no request line" in problem
               for problems in refused["malformed"].values()
               for problem in problems)

    accepted = merging.merge(book_path, chunks, strict=True,
                             revalidate_unbound=True)
    assert accepted["ok"] is True, accepted
    assert accepted["revalidated"] == [entry["id"] for entry in manifest["chunks"]]


def test_revalidating_an_unbound_reply_still_checks_the_source(tmp_path):
    """The escape hatch is not a way past freshness."""
    book_path = _book(tmp_path)
    chunks = tmp_path / "cut"
    manifest = chunking.build(book_path, chunks, glossary_path=None, budget=4000)
    for entry in manifest["chunks"]:
        kinds = entry.get("unit_kinds") or {}
        ir.write_text(chunks / entry["output"], "\n".join(
            f"@@ {unit_id} {kinds.get(unit_id, 'para')}\nترجمهٔ بدون نشانه.\n"
            for unit_id in entry["unit_ids"]))

    book = ir.load_book(book_path)
    book["blocks"][0]["text"] = "A first paragraph that now says something else."
    ir.save_book(book, book_path)

    report = merging.merge(book_path, chunks, strict=True, revalidate_unbound=True)

    assert report["ok"] is False
    assert report["stale"], "a changed source passed on the revalidation path"


# --------------------------------------------------------------------------- #
# Context and policy are part of the question
# --------------------------------------------------------------------------- #

def test_changing_the_preceding_paragraph_invalidates_the_next_answer(tmp_path):
    """C01b. "She smiled" resolves differently when Anna becomes Maria.

    The neighbouring source travels in the worksheet precisely so a translator can
    resolve a pronoun, so it is part of the question — and the answer to the old
    question is not an answer to the new one.
    """
    book_path = _book(tmp_path, first="Anna put the letter down.")
    chunks = tmp_path / "cut"
    first = chunking.build(book_path, chunks, glossary_path=None, budget=1500,
                           force=True)
    before = {entry["id"]: entry["request"] for entry in first["chunks"]}

    book = ir.load_book(book_path)
    book["blocks"][0]["text"] = "Maria put the letter down."
    ir.save_book(book, book_path)
    second = chunking.build(book_path, chunks, glossary_path=None, budget=1500,
                            force=True)
    after = {entry["id"]: entry["request"] for entry in second["chunks"]}

    assert after["chunk0002"] != before["chunk0002"], (
        "the neighbour's name changed in the context this worksheet carries, and "
        "its request token did not move — so an answer that resolved a pronoun "
        "against 'Anna' would be merged for a book that says 'Maria'")


def test_changing_the_glossary_invalidates_the_answers_it_reached(tmp_path):
    """C01a. The term table is part of the question too.

    A glossary-only rebuild renders a different worksheet — a different locked
    spelling, a different instruction about introducing the name — so the answer
    to the previous one is not an answer to this.
    """
    import glossary as gl

    book_path = _book(tmp_path, first="Elizabeth Bennet put the letter down.")
    glossary_path = tmp_path / "glossary.json"
    glossary = gl.new_glossary()
    entry = gl.make_entry(1, "Elizabeth Bennet", category="person", frequency=9)
    entry.update({"target": "الیزابت بنت", "first_form": "الیزابت بنت (Elizabeth Bennet)",
                  "locked": True})
    glossary["entries"] = [entry]
    gl.save(glossary, glossary_path)

    chunks = tmp_path / "cut"
    first = chunking.build(book_path, chunks, glossary_path=glossary_path,
                           budget=1500, force=True)
    before = {item["id"]: item["request"] for item in first["chunks"]}

    entry["target"] = "الیزابت بنِت"          # the locked spelling is corrected
    gl.save(glossary, glossary_path)
    second = chunking.build(book_path, chunks, glossary_path=glossary_path,
                            budget=1500, force=True)
    after = {item["id"]: item["request"] for item in second["chunks"]}

    changed = [job for job in before if before[job] != after.get(job)]
    assert changed, (
        "the locked spelling changed and no request token moved, so every answer "
        "written against the old spelling stays live and the drift check will "
        "reject the book later instead")


def test_the_request_token_is_stable_for_an_unchanged_build(tmp_path):
    """Rebuilding the same book at the same budget asks the same question.

    Otherwise every rebuild would discard every answer, which is a different way
    of losing a translation.
    """
    book_path = _book(tmp_path)
    one = chunking.build(book_path, tmp_path / "a", glossary_path=None, budget=1500)
    two = chunking.build(book_path, tmp_path / "b", glossary_path=None, budget=1500)

    assert ([entry["request"] for entry in one["chunks"]]
            == [entry["request"] for entry in two["chunks"]])
