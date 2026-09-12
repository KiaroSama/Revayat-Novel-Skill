"""An answer belongs to the worksheet it was written for, not to a filename.

The freshness check merge had compared the manifest's `source_sha256` against
`unit_fingerprint(book, ids)` — the manifest against the book. But `chunk build`
writes that manifest *from* that book, so after a rebuild the two sides are the
same value by construction and the comparison cannot fail. It catches "the book
changed after the build" and is blind to the case that matters: "this answer was
written before the change".

`chunk build`'s own refusal message promised otherwise — *"the out_chunk*.md files
stay on disk, and merge will reject every id that moved"* — so the defect was a
false claim in an error message as well as a hole in the gate.

What closes it is provenance rather than a cleverer comparison: a worksheet records
the revision it was cut at, and a rebuild that changes a worksheet's revision moves
any existing answer to `superseded/` instead of leaving it where the next merge
will read it. The work is preserved and named; it simply stops being able to pass
itself off as an answer to a question nobody asked it.
"""

from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bookir as ir  # noqa: E402
import chunk as chunking  # noqa: E402
import merge as merging  # noqa: E402


def _book(tmp_path: Path, texts: list[str], name: str = "book.json") -> Path:
    book = ir.new_book(source_path="sample.epub", source_format="epub")
    for index, text in enumerate(texts, start=1):
        book["blocks"].append(ir.make_block("paragraph", index, text=text))
    path = tmp_path / name
    ir.save_book(book, path)
    return path


def _answer(chunks: Path, entry: dict, text: str = "ترجمه.") -> None:
    ir.write_text(chunks / entry["output"], "\n".join(
        f"@@ {unit_id} {entry['unit_kinds'][unit_id]}\n{text}\n"
        for unit_id in entry["unit_ids"]))


def _retype(book_path: Path, index: int, text: str) -> None:
    """Change one block's source text in place, as an editor would."""
    book = ir.load_book(book_path)
    book["blocks"][index]["text"] = text
    ir.save_book(book, book_path)


# --------------------------------------------------------------------------- #
# The headline case: a changed source must not keep the old answer
# --------------------------------------------------------------------------- #

def test_an_answer_to_the_old_source_is_not_accepted_after_a_rebuild(tmp_path):
    """The defect, in the order an operator meets it.

    Translate "Ali arrived." Then the editor corrects the source to "Ali never
    arrived." Rebuild, merge — and the book must not end up carrying a positive
    sentence for a negative one. Meaning reversed, `ok=true`, nothing to see.
    """
    book_path = _book(tmp_path, ["Ali arrived.", "The room was quiet."])
    chunks = tmp_path / "chunks"
    manifest = chunking.build(book_path, chunks, glossary_path=None)
    entry = manifest["chunks"][0]
    _answer(chunks, entry, "علی آمد.")

    # The editor changes what the sentence means.
    _retype(book_path, 0, "Ali never arrived.")

    chunking.build(book_path, chunks, glossary_path=None, force=True)
    report = merging.merge(book_path, chunks, strict=True)

    assert not report["ok"], (
        "merge accepted a translation written for the old source: the book now "
        f"says Ali arrived where the source says he did not. {report}")
    assert ir.load_book(book_path)["blocks"][0].get("target") in (None, ""), (
        "the stale translation was written into the book")


def test_the_superseded_answer_is_kept_where_a_human_can_find_it(tmp_path):
    """Refusing is only half of it. Nobody's translation may be deleted to make
    a gate pass — the work moves somewhere named, and the message says where."""
    book_path = _book(tmp_path, ["Ali arrived.", "The room was quiet."])
    chunks = tmp_path / "chunks"
    manifest = chunking.build(book_path, chunks, glossary_path=None)
    entry = manifest["chunks"][0]
    _answer(chunks, entry, "علی آمد.")

    _retype(book_path, 0, "Ali never arrived.")
    chunking.build(book_path, chunks, glossary_path=None, force=True)

    kept = sorted(p for p in (chunks / "superseded").glob("*.md")) \
        if (chunks / "superseded").is_dir() else []
    assert kept, f"the old translation was not preserved anywhere: {list(chunks.iterdir())}"
    assert any("علی آمد." in p.read_text(encoding="utf-8") for p in kept), (
        f"something was filed but not the translation: {[p.name for p in kept]}")


def test_an_unchanged_rebuild_keeps_its_answer_usable(tmp_path):
    """The negative control, and the one that makes the fix safe to live with:
    rebuilding without editing the source must not quarantine good work. A gate
    that fires on every rebuild would be worse than the hole it closes."""
    book_path = _book(tmp_path, ["Ali arrived.", "The room was quiet."])
    chunks = tmp_path / "chunks"
    manifest = chunking.build(book_path, chunks, glossary_path=None)
    for entry in manifest["chunks"]:
        _answer(chunks, entry, "علی آمد.")

    chunking.build(book_path, chunks, glossary_path=None, force=True)
    report = merging.merge(book_path, chunks, strict=True)

    assert report["ok"], f"an unchanged rebuild lost its answers: {report}"
    assert not (chunks / "superseded").exists() or not list(
        (chunks / "superseded").glob("*.md")), (
        "an unchanged rebuild quarantined work it should have left alone")


def test_a_rebuild_after_a_kind_change_supersedes_the_answer(tmp_path):
    """The revision covers the *question*, not only the prose. A paragraph
    re-typed as a heading is a different question — answering it as body text is
    how a chapter title becomes a sentence — so the old answer is superseded."""
    book_path = _book(tmp_path, ["Ali arrived.", "The room was quiet."])
    chunks = tmp_path / "chunks"
    manifest = chunking.build(book_path, chunks, glossary_path=None)
    _answer(chunks, manifest["chunks"][0], "علی آمد.")

    book = ir.load_book(book_path)
    book["blocks"][0]["type"] = "heading"
    book["blocks"][0]["level"] = 1
    ir.save_book(book, book_path)

    chunking.build(book_path, chunks, glossary_path=None, force=True)
    report = merging.merge(book_path, chunks, strict=True)

    assert not report["ok"], (
        f"a paragraph answered as prose was accepted as a heading: {report}")


# --------------------------------------------------------------------------- #
# The check that was there already has to keep working
# --------------------------------------------------------------------------- #

def test_a_source_edited_without_a_rebuild_is_still_caught(tmp_path):
    """This is the case the old comparison *did* catch, and it must survive the
    fix: the source moves, nobody rebuilds, and the manifest's recorded identity
    no longer describes the book."""
    book_path = _book(tmp_path, ["Ali arrived.", "The room was quiet."])
    chunks = tmp_path / "chunks"
    manifest = chunking.build(book_path, chunks, glossary_path=None)
    _answer(chunks, manifest["chunks"][0], "علی آمد.")

    _retype(book_path, 0, "Ali never arrived.")

    report = merging.merge(book_path, chunks, strict=True)

    assert not report["ok"], f"an edited source was merged as fresh: {report}"
    assert report["stale"], f"the staleness was not reported as stale: {report}"


def test_a_manifest_with_no_recorded_identity_is_unverified_not_fresh(tmp_path):
    """A manifest written before this field existed cannot be *proved* fresh.
    It is reported as unverified — a legacy run stays migratable, and silence
    is not taken for evidence."""
    book_path = _book(tmp_path, ["Ali arrived.", "The room was quiet."])
    chunks = tmp_path / "chunks"
    manifest = chunking.build(book_path, chunks, glossary_path=None)
    _answer(chunks, manifest["chunks"][0], "علی آمد.")

    import json
    path = chunks / "manifest.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    for entry in raw["chunks"]:
        entry.pop("source_sha256", None)
        entry.pop("worksheet_sha256", None)
    ir.write_text(path, json.dumps(raw, ensure_ascii=False, indent=1) + "\n")

    report = merging.merge(book_path, chunks, strict=True)

    assert report["unverified_freshness"], (
        f"a legacy manifest was treated as proven fresh: {report}")
