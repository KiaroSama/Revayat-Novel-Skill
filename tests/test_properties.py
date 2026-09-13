"""Properties that must hold for every input, not only the ones someone thought of.

The rest of the suite is example-based, and examples are chosen by the same mind
that wrote the code — so they agree with it. Five invariants here are stated as
properties and checked against generated input instead:

1. **Transport round-trips.** A unit's text survives being written into a
   worksheet and read back out of a reply, whatever is in it — including the
   `@@` headers and code fences that are the transport's own grammar.
2. **Every unit is owned exactly once.** Grouping into worksheets and cutting an
   over-long unit into segments never loses a unit, never duplicates one, and
   never reorders them.
3. **The typography pass is idempotent.** Running it twice is running it once.
   It rewrites its own output, so a rule that is not a fixed point compounds.
4. **A merge that fails changes nothing.** The transaction is all-or-nothing
   against an arbitrary mix of good and broken replies.
5. **No page keeps `accepted` once its source moves.** The page state machine,
   driven by random sequences.

Generated, not random: one seed, fixed example counts, and a corpus that
*deliberately* contains the adversarial shapes — a line that looks like a
header, an unclosed fence, Persian with ZWNJ, a word longer than any budget.
`hypothesis` would bring shrinking, and it is not worth a dependency every reader
who runs `pytest tests` would have to install for five properties; a failure here
prints the exact input, which is what shrinking is for. Each group is bounded so
the whole module stays under a second or two — a property tier that costs a
minute is a property tier people stop running.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bookir as ir  # noqa: E402
import chunk as chunking  # noqa: E402
import falint  # noqa: E402
import merge as merging  # noqa: E402
import runstate  # noqa: E402
import segments  # noqa: E402
import worksheet as ws  # noqa: E402

#: One seed, so a failure is reproducible by name rather than by luck.
SEED = 20260913

#: Pieces a generated unit text is built from. The first six are ordinary; the
#: rest are the shapes that have actually broken this transport or its budget.
PIECES = [
    "یک جملهٔ ساده.",
    "پاراگرافی کمی بلندتر که دو جمله دارد. این جملهٔ دوم است.",
    "A line of English prose.",
    "«نقل‌قولی با گیومه.»",
    "می‌خواهم این را نگه دارم.",
    "",                                     # a blank line inside a unit
    "@@ b00042 para",                       # looks exactly like a header
    "@@ tr-01 note",                        # and so does a translator note
    "```",                                  # an unbalanced fence
    "~~~python",                            # the other fence, with a language
    "\\@@ b00007 para",                     # a literal backslash before one
    "<!-- revayat-novel: scaffolding -->",  # our own comment, echoed back
    "Supercalifragilisticexpialidociousandthensome" * 3,   # one long word
    "   leading and trailing spaces   ",
    "دو‌کلمه با نیم‌فاصله",
]


def texts(count: int, *, seed: int = SEED,
          exclude: tuple[str, ...] = ()) -> list[str]:
    """``count`` generated unit texts, each a few pieces joined by newlines."""
    rng = random.Random(seed)
    pool = [piece for piece in PIECES if piece not in exclude]
    made: list[str] = []
    while len(made) < count:
        body = "\n".join(rng.choice(pool) for _ in range(rng.randint(1, 5)))
        if body.strip():
            made.append(body)
    return made


# --------------------------------------------------------------------------- #
# 1. The transport round-trips
# --------------------------------------------------------------------------- #

def reply_for(units: list[tuple[str, str, str]]) -> str:
    """A worksheet reply in the shape `chunk` writes and `merge` reads."""
    lines: list[str] = []
    for unit_id, kind, text in units:
        lines.append(f"@@ {unit_id} {kind}")
        lines.append(ws.escape_payload(text))
        lines.append("")
    return "\n".join(lines)


#: Two pieces are documented to *not* survive, so they are excluded from the
#: round-trip corpus and asserted separately below: our own scaffold comment is
#: dropped on purpose, and a line that is already an escaped header is the one
#: case the escape cannot distinguish from a header it escaped itself.
NOT_ROUND_TRIPPED = ("<!-- revayat-novel: scaffolding -->", "\\@@ b00007 para")


@pytest.mark.parametrize("text", texts(40, exclude=NOT_ROUND_TRIPPED),
                         ids=lambda t: t[:18].replace("\n", "|"))
def test_a_units_text_survives_the_worksheet_and_the_reply(text):
    """The property the `@@` grammar exists for, over generated content."""
    units = [("b00001", "para", text)]
    entries, problems = ws.read_reply(reply_for(units))
    assert problems == [], f"transport complained about ordinary content: {problems}"
    assert len(entries) == 1, (
        f"one unit in, {len(entries)} out — a line in the text was read as a "
        f"header: {[e['id'] for e in entries]}")
    assert entries[0]["id"] == "b00001"
    assert entries[0]["kind"] == "para"
    # The reader strips the unit's outer whitespace, and says so; nothing else
    # about the text may change.
    assert entries[0]["text"] == text.strip()


def test_many_units_keep_their_order_and_their_own_text():
    """Ids, kinds and bodies stay paired however many units share a reply."""
    corpus = texts(24, exclude=NOT_ROUND_TRIPPED)
    units = [(f"b{index:05}", "para" if index % 3 else "heading1", text)
             for index, text in enumerate(corpus, start=1)]
    entries, problems = ws.read_reply(reply_for(units))
    assert problems == []
    assert [entry["id"] for entry in entries] == [unit[0] for unit in units]
    assert [entry["kind"] for entry in entries] == [unit[1] for unit in units]
    assert [entry["text"] for entry in entries] == [unit[2].strip() for unit in units]


def test_the_scaffold_comment_is_dropped_and_the_rest_is_not():
    """Documented asymmetry: our comment goes, the unit's own text stays."""
    text = "خط اول.\n<!-- revayat-novel: scaffolding -->\nخط دوم."
    entries, problems = ws.read_reply(reply_for([("b00001", "para", text)]))
    assert problems == []
    assert entries[0]["text"] == "خط اول.\nخط دوم."


def test_an_already_escaped_header_round_trips_too():
    """A literal `\\@@ …` line is content, and the reader must not unescape it.

    The escape writes `\\@@` in front of a line that *is* a header, and the
    reader strips one backslash off any line that starts with one. A unit whose
    own text already begins that way therefore arrived one backslash short —
    silently, because nothing downstream knows what the line should have been.
    Found by the round-trip property above, not by an example.
    """
    text = "\\@@ b00007 para"
    entries, problems = ws.read_reply(reply_for([("b00001", "para", text)]))
    assert problems == []
    assert entries[0]["text"] == text


# --------------------------------------------------------------------------- #
# 2. Every unit is owned exactly once
# --------------------------------------------------------------------------- #

def render_units(units: list[tuple[str, str, str]]) -> str:
    """A stand-in worksheet renderer with realistic per-unit overhead."""
    head = "<!-- revayat-novel: a preamble of roughly constant size -->\n" * 2
    return head + "\n".join(f"@@ {unit_id} {kind}\n{text}\n"
                            for unit_id, kind, text in units)


@pytest.mark.parametrize("seed", range(6))
def test_grouping_into_worksheets_loses_nothing_and_reorders_nothing(seed):
    rng = random.Random(SEED + seed)
    corpus = texts(rng.randint(3, 12), seed=SEED + seed)
    units = [(f"b{index:05}", "para", text)
             for index, text in enumerate(corpus, start=1)]
    budget = rng.choice((240, 400, 900, 4000))

    jobs = segments.fit_jobs(render_units, units, budget)
    seen = [unit for group, _ in jobs for unit in group]
    assert seen == units, (
        f"grouping at budget {budget} changed the units: {len(seen)} out of "
        f"{len(units)}, order {'kept' if seen == units else 'broken'}")
    # Each group is either within budget or a single unit that cannot be, which
    # is the documented escape — never a group of two that overflows.
    for group, rendered in jobs:
        assert len(rendered) <= budget or len(group) <= 1, (
            f"a group of {len(group)} units renders to {len(rendered)} over a "
            f"budget of {budget}")


@pytest.mark.parametrize("budget", [40, 97, 300, 1000])
def test_a_split_unit_rejoins_to_the_same_words(budget):
    """`split_text` is exact, and `rejoin` preserves the word sequence.

    Not the same bytes: the join puts one space where the cut fell, and says so
    — a reply arrives stripped, so the original separator is unrecoverable. The
    words, and their order, are the part that must not move.
    """
    for text in texts(12, seed=SEED + budget):
        pieces = segments.split_text(text, budget)
        assert "".join(pieces) == text, "split_text is not exact"
        answered = {segments.segment_id("b00001", index): piece
                    for index, piece in enumerate(pieces, start=1)}
        whole = segments.rejoin(answered)
        assert list(whole) == ["b00001"], f"rejoin produced {list(whole)}"
        assert whole["b00001"].split() == text.split()


def test_coverage_is_reported_exactly_when_a_segment_is_missing():
    """The check has to fail for the absence, not for the shape of the ids."""
    rng = random.Random(SEED)
    for _ in range(12):
        count = rng.randint(2, 6)
        ids = [segments.segment_id("b00001", index)
               for index in range(1, count + 1)]
        known = segments.segments_by_owner(ids)
        assert segments.coverage_problems(known, ids) == []
        dropped = rng.randrange(count)
        short = [unit_id for index, unit_id in enumerate(ids) if index != dropped]
        problems = segments.coverage_problems(known, short)
        assert problems, (
            f"{count} segments, one withheld, and coverage reported nothing")


# --------------------------------------------------------------------------- #
# 3. The typography pass is idempotent
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text", texts(25, seed=SEED + 1),
                         ids=lambda t: t[:18].replace("\n", "|"))
def test_running_the_typography_pass_twice_is_running_it_once(text):
    """A rule that is not a fixed point compounds over a book's worth of passes."""
    once = falint.fix_text(text)
    assert falint.fix_text(once) == once, (
        "the typography pass moved on its own output, so a book that is fixed "
        "twice differs from one fixed once")


def test_the_typography_pass_is_idempotent_over_a_whole_book():
    book = ir.new_book(source_path="s.epub", source_format="epub",
                       title="کتاب", author="نویسنده")
    for index, text in enumerate(texts(10, seed=SEED + 2), start=1):
        book["blocks"].append(ir.make_block("paragraph", index, text=text))
    once = falint.fix_book(json.loads(json.dumps(book)))
    twice = falint.fix_book(json.loads(json.dumps(once)))
    assert twice == once


# --------------------------------------------------------------------------- #
# 4. A merge that fails changes nothing
# --------------------------------------------------------------------------- #

def _book_and_chunks(tmp_path: Path, count: int) -> tuple[Path, Path]:
    book = ir.new_book(source_path="sample.epub", source_format="epub",
                       title="A Small Book", author="Test Author")
    for index in range(1, count + 1):
        book["blocks"].append(
            ir.make_block("paragraph", index, text=f"Paragraph number {index}."))
    book_path = tmp_path / "book.json"
    ir.save_book(book, book_path)
    chunks = tmp_path / "chunks"
    chunking.build(book_path, chunks, glossary_path=None, budget=600)
    return book_path, chunks


#: How a reply can be broken. Each one is refused by name elsewhere in the
#: suite; the property here is only that refusing writes nothing.
BREAKAGES = ("drop a unit", "rename a unit", "change the kind", "answer twice",
             "empty answer", "unclosed fence")


@pytest.mark.parametrize("breakage", BREAKAGES)
def test_a_refused_merge_leaves_the_book_byte_identical(tmp_path, breakage):
    book_path, chunks = _book_and_chunks(tmp_path, 8)
    manifest = json.loads((chunks / "manifest.json").read_text(encoding="utf-8"))
    before = book_path.read_bytes()

    for entry in manifest["chunks"]:
        kinds = entry.get("unit_kinds") or {}
        unit_ids = list(entry["unit_ids"])
        lines: list[str] = []
        for position, unit_id in enumerate(unit_ids):
            kind = kinds.get(unit_id, "para")
            if breakage == "drop a unit" and position == 0 and len(unit_ids) > 1:
                continue
            if breakage == "rename a unit" and position == 0:
                unit_id = "b99999"
            if breakage == "change the kind" and position == 0:
                kind = "heading1" if kind != "heading1" else "para"
            lines += [f"@@ {unit_id} {kind}", "ترجمه.", ""]
            if breakage == "answer twice" and position == 0:
                lines += [f"@@ {unit_id} {kind}", "ترجمهٔ دوم.", ""]
        if breakage == "empty answer":
            lines = [line if not line.startswith("ترجمه") else "" for line in lines]
        body = "\n".join(lines)
        if breakage == "unclosed fence":
            body = "```\n" + body
        ir.write_text(chunks / f"out_{entry['id']}.md", body)

    report = merging.merge(book_path, chunks, strict=True)
    assert report["ok"] is False, (
        f"a reply broken by '{breakage}' was accepted; merge reported ok")
    assert book_path.read_bytes() == before, (
        f"merge reported failure for '{breakage}' and still wrote to book.json")


def test_a_good_merge_is_idempotent(tmp_path):
    """Merging the same answers twice is merging them once."""
    book_path, chunks = _book_and_chunks(tmp_path, 6)
    manifest = json.loads((chunks / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["chunks"]:
        kinds = entry.get("unit_kinds") or {}
        lines = []
        for unit_id in entry["unit_ids"]:
            lines += [f"@@ {unit_id} {kinds.get(unit_id, 'para')}", "ترجمه.", ""]
        ir.write_text(chunks / f"out_{entry['id']}.md", "\n".join(lines))

    first = merging.merge(book_path, chunks, strict=True)
    assert first["ok"] is True, first
    after_once = book_path.read_bytes()
    second = merging.merge(book_path, chunks, strict=True)
    assert second["ok"] is True, second
    assert book_path.read_bytes() == after_once


# --------------------------------------------------------------------------- #
# 5. No page keeps `accepted` once its source moves
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("seed", range(8))
def test_the_page_state_machine_holds_under_random_sequences(tmp_path, seed):
    """Four invariants, over a dozen arbitrary transitions per run."""
    rng = random.Random(SEED + seed)
    state = runstate.RunState(tmp_path)
    page = 1
    source = "sha-initial"
    state.note_page_source(page, source)

    for step in range(12):
        if rng.random() < 0.25:
            # The source moved: a corrected scan, a re-extraction.
            source = f"sha-{step}"
            invalidated = state.note_page_source(page, source)
            entry = state.page(page)
            assert invalidated is True
            assert entry["state"] == "pending", (
                f"step {step}: the source changed and the page stayed "
                f"{entry['state']!r} — its translation answers text the book no "
                f"longer contains")
            assert entry["attempts"] == 0
            assert entry["hashes"] == {"source": source}, (
                f"step {step}: a stale hash survived the source change: "
                f"{entry['hashes']}")
            continue

        target = rng.choice(runstate.PAGE_STATES)
        hashes = {}
        if rng.random() < 0.5:
            hashes["translation"] = f"tr-{rng.randrange(3)}"
        before = state.page(page) or {}
        attempts_before = int(before.get("attempts", 0))
        translation_before = (before.get("hashes") or {}).get("translation", "")

        entry = state.set_page(page, target, hashes=hashes,
                               error="boom" if target == "failed" else "")
        assert entry["state"] == target
        if target == "failed":
            changed = hashes.get("translation", translation_before) != translation_before
            expected = 1 if changed else attempts_before + 1
            assert entry["attempts"] == expected, (
                f"step {step}: attempts went {attempts_before} -> "
                f"{entry['attempts']} on a failure")
        elif "translation" in hashes and hashes["translation"] != translation_before:
            assert entry["attempts"] == 0, (
                f"step {step}: a new translation did not earn a fresh attempt "
                f"count")
        else:
            assert entry["attempts"] == attempts_before, (
                f"step {step}: relabelling to {target!r} moved the attempt count")

    # And the record survives a reload: it is a file, not a session.
    assert runstate.RunState(tmp_path).page(page) == state.page(page)


def test_an_unknown_page_state_is_refused_rather_than_stored(tmp_path):
    """Otherwise the set of states is whatever a caller last wrote."""
    state = runstate.RunState(tmp_path)
    with pytest.raises(ValueError, match="unknown page state"):
        state.set_page(1, "nearly-accepted")
    assert state.page(1) is None
