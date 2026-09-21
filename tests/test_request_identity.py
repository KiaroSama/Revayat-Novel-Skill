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
* `source_sha256` — the `units3:` digest over the units **as cut**, recomputable
  by merge from the spans the manifest records. It answers "would this worksheet
  be a different question today", which the token cannot: the units' own text at
  absolute offsets within their owner, the **live** kind and heading level, the
  term table and voice cards the units call for, and the context window from the
  neighbouring blocks. Earlier forms are reported unverifiable rather than
  recomputed, because `units2` covered neither the live kind nor the dependencies
  and `units:` gave every segment of a paragraph the same value.

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
import glossary as gl  # noqa: E402
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


# --------------------------------------------------------------------------- #
# R4-01 — the spans are a partition of their owner, not three copies of its head
# --------------------------------------------------------------------------- #
#
# `unit_records` accumulates offsets per owner, and `build` used to call it once
# per worksheet. Measured on a 2700-character paragraph at budget 1400: three
# segments, every one recorded `offset: 0`, so the spans covered characters
# 0-1008 three times and 1692 characters were checked by nothing. A same-length
# edit past the first segment left every recorded slice identical and the stale
# reply merged clean.

#: 300 numbered words, so a same-length substitution is trivial and every
#: position is identifiable: `word0290` and `NEVER290` are the same width.
NUMBERED = " ".join(f"word{n:04d}" for n in range(300)) + "."


def _numbered_book(tmp_path: Path) -> Path:
    book = ir.new_book(source_path="sample.epub", source_format="epub",
                       title="A Small Book", author="Test Author")
    book["blocks"].append(ir.make_block("paragraph", 1, text=NUMBERED))
    path = tmp_path / "numbered.json"
    ir.save_book(book, path)
    return path


def _spans_by_owner(chunks: Path) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    for entry in _manifest(chunks)["chunks"]:
        for span in entry.get("unit_spans") or []:
            found.setdefault(span["owner"], []).append(span)
    return found


def test_the_recorded_spans_partition_every_owner(tmp_path):
    """Ordered, gap-free, non-overlapping, and covering the owner exactly."""
    book_path = _numbered_book(tmp_path)
    chunks = tmp_path / "cut"
    report = chunking.build(book_path, chunks, glossary_path=None, budget=1400)
    assert len(report["chunks"]) >= 3, (
        "the paragraph was not split, so this measures nothing")

    for owner, spans in _spans_by_owner(chunks).items():
        ordered = sorted(spans, key=lambda s: s["offset"])
        assert [s["offset"] for s in ordered] == sorted(
            s["offset"] for s in spans), f"{owner}: spans are not ordered"
        cursor = 0
        for span in ordered:
            assert span["offset"] == cursor, (
                f"{owner}: {span['id']} starts at {span['offset']}, expected "
                f"{cursor} — offsets restarted per worksheet, so the spans "
                f"describe the head of the paragraph over and over")
            cursor += span["length"]
        assert cursor == len(NUMBERED), (
            f"{owner}: the spans cover {cursor} of {len(NUMBERED)} characters; "
            f"the remainder is verified by nothing")


def test_a_same_length_edit_in_a_late_segment_is_caught(tmp_path):
    """The consequence of the offsets, and the reason they are worth recording.

    `word0290` lives in the last segment. Same width, so no span length changes
    and no id moves; only the text under a late offset does.
    """
    book_path = _numbered_book(tmp_path)
    chunks = tmp_path / "cut"
    chunking.build(book_path, chunks, glossary_path=None, budget=1400)
    _answer_all(chunks)

    clean = merging.merge(book_path, chunks)
    assert clean["stale"] == [], f"an untouched book reported stale: {clean}"

    book = ir.load_book(book_path)
    mutated = NUMBERED.replace("word0290", "NEVER290")
    assert len(mutated) == len(NUMBERED) and mutated != NUMBERED
    book["blocks"][0]["text"] = mutated
    ir.save_book(book, book_path)

    after = merging.merge(book_path, chunks)
    assert after["stale"], (
        "a same-length edit inside a late segment merged clean: the recorded "
        "spans never looked at that part of the paragraph")
    assert after["ok"] is False


def test_an_incoherent_cut_is_refused_rather_than_recorded(tmp_path):
    """A digest over spans that skip half a paragraph reads as 'unchanged'."""
    whole = {"b00001": "0123456789"}
    assert chunking.span_problems(
        [{"id": "b00001#1", "owner": "b00001", "kind": "para",
          "offset": 0, "length": 10}], whole) == []

    gap = chunking.span_problems(
        [{"id": "b00001#1", "owner": "b00001", "kind": "para",
          "offset": 0, "length": 4},
         {"id": "b00001#2", "owner": "b00001", "kind": "para",
          "offset": 6, "length": 4}], whole)
    assert any("belong to no unit" in problem for problem in gap), gap

    overlap = chunking.span_problems(
        [{"id": "b00001#1", "owner": "b00001", "kind": "para",
          "offset": 0, "length": 8},
         {"id": "b00001#2", "owner": "b00001", "kind": "para",
          "offset": 4, "length": 6}], whole)
    assert any("claim the same text" in problem for problem in overlap), overlap

    short = chunking.span_problems(
        [{"id": "b00001#1", "owner": "b00001", "kind": "para",
          "offset": 0, "length": 4}], whole)
    assert any("checked by nothing" in problem for problem in short), short


# --------------------------------------------------------------------------- #
# R4-01 — freshness reads the live book, not the manifest it is compared against
# --------------------------------------------------------------------------- #

def _built(tmp_path: Path, **build) -> tuple[Path, Path]:
    """Built and answered, with merge verified clean through the same glossary.

    Merging without the glossary the build used cannot recompute the term table,
    which merge reports as unverifiable rather than stale — so the helper passes
    it, otherwise every test here would measure that refusal instead.
    """
    book_path = _book(tmp_path)
    chunks = tmp_path / "cut"
    chunking.build(book_path, chunks, budget=1500, **build)
    _answer_all(chunks)
    glossary_path = build.get("glossary_path")
    clean = merging.merge(book_path, chunks, glossary_path=glossary_path)
    assert clean["stale"] == [], clean
    return book_path, chunks


def test_turning_a_paragraph_into_a_heading_makes_its_answer_stale(tmp_path):
    """The kind was read from the record, so the manifest agreed with itself.

    A chapter title keeping a paragraph's translation is the visible result.
    """
    book_path, chunks = _built(tmp_path, glossary_path=None)

    book = ir.load_book(book_path)
    book["blocks"][0]["type"] = "heading"
    book["blocks"][0]["level"] = 1
    ir.save_book(book, book_path)

    after = merging.merge(book_path, chunks)
    assert after["stale"], (
        "the unit is a heading now and its answer was written for a paragraph")


def test_moving_a_heading_to_another_level_makes_its_answer_stale(tmp_path):
    book = ir.new_book(source_path="s.epub", source_format="epub")
    heading = ir.make_block("heading", 1, level=1, text="Chapter One")
    book["blocks"].append(heading)
    book["blocks"].append(ir.make_block("paragraph", 2, text=LONG))
    book_path = tmp_path / "book.json"
    ir.save_book(book, book_path)

    chunks = tmp_path / "cut"
    chunking.build(book_path, chunks, glossary_path=None, budget=1500)
    _answer_all(chunks)
    assert merging.merge(book_path, chunks)["stale"] == []

    book = ir.load_book(book_path)
    book["blocks"][0]["level"] = 3
    ir.save_book(book, book_path)

    assert merging.merge(book_path, chunks)["stale"], (
        "`kind_of` encodes the level, so a level change is a different question")


def test_editing_the_neighbouring_context_makes_an_answer_stale(tmp_path):
    """The window a pronoun was resolved against is part of the question."""
    book = ir.new_book(source_path="s.epub", source_format="epub")
    # Each paragraph is longer than the budget, so `split_blocks` closes a chunk
    # on every one of them and the middle chunk has a neighbour on each side.
    # Three short paragraphs would land in one chunk and this would measure
    # nothing — which is how the first draft of this test passed vacuously.
    for index in range(1, 4):
        book["blocks"].append(ir.make_block(
            "paragraph", index,
            text=f"Paragraph {index} about Anna, who walked to the station. "
                 * 62))
    book_path = tmp_path / "book.json"
    ir.save_book(book, book_path)

    chunks = tmp_path / "cut"
    # 3000, not 1500: a middle chunk carries a context window on *both* sides,
    # which is 900 characters of overhead before any prose, and the budget has to
    # leave room for a segment on top of it.
    report = chunking.build(book_path, chunks, glossary_path=None, budget=3000)
    assert len(report["chunks"]) >= 2, "one worksheet has no neighbours"
    _answer_all(chunks)
    assert merging.merge(book_path, chunks)["stale"] == []

    # The manifest records which blocks the window came from, so merge can ask
    # the live book what it would show today.
    neighbours = _manifest(chunks)["chunks"][0].get("neighbour_ids") or {}
    assert neighbours.get("after"), "no neighbour was recorded to change"
    target = neighbours["after"][0]

    book = ir.load_book(book_path)
    for block in book["blocks"]:
        if block["id"] == target:
            block["text"] = "Entirely different text next door. " + LONG
    ir.save_book(book, book_path)

    assert merging.merge(book_path, chunks)["stale"], (
        "the context window moved and every old answer stayed live")


def test_approving_an_alias_after_the_build_makes_the_answer_stale(tmp_path):
    """The term table is part of what the worker was told."""
    glossary_path = tmp_path / "glossary.json"
    glossary = gl.new_glossary()
    ir.write_text(glossary_path,
                  json.dumps(glossary, ensure_ascii=False, indent=1) + "\n")

    book_path, chunks = _built(tmp_path, glossary_path=glossary_path)

    locked = gl.load(glossary_path)
    locked["entries"].append({
        "id": "anna", "source": "Anna", "target": "آنا",
        "aliases": [], "status": "locked", "kind": "person",
    })
    ir.write_text(glossary_path,
                  json.dumps(locked, ensure_ascii=False, indent=1) + "\n")

    after = merging.merge(book_path, chunks, glossary_path=glossary_path)
    assert after["stale"], (
        "a name was locked after the worksheet was written, so the translator "
        "was never told the form the book now requires")


def test_merging_resolves_the_glossary_recorded_by_the_build(tmp_path):
    """Omitting an argument does not discard an available recorded dependency."""
    glossary_path = tmp_path / "glossary.json"
    ir.write_text(glossary_path,
                  json.dumps(gl.new_glossary(), ensure_ascii=False, indent=1))
    book_path, chunks = _built(tmp_path, glossary_path=glossary_path)

    blind = merging.merge(book_path, chunks)
    assert blind["ok"] and not blind["unverified_freshness"], blind
    assert blind["stale"] == [], (
        "a merge that could not recompute the term table accused the reply of "
        "being stale")


def test_a_digest_from_a_superseded_formula_is_unverified_not_compared(tmp_path):
    """`units2` covered neither the live kind nor the dependencies."""
    book_path, chunks = _built(tmp_path, glossary_path=None)

    manifest = _manifest(chunks)
    for entry in manifest["chunks"]:
        entry["source_sha256"] = "units2:" + "0" * 64
    ir.write_text(chunks / "manifest.json",
                  json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")

    after = merging.merge(book_path, chunks)
    assert after["unverified_freshness"], (
        "an older formula was recomputed as though it were the current one")
    assert after["stale"] == [], (
        "not comparable is not the same as changed; one rebuild clears it")
