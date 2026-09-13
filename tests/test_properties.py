"""Properties that must hold for every input, not only the ones someone thought of.

The rest of the suite is example-based, and examples are chosen by the same mind
that wrote the code — so they agree with it. Ten invariants here are stated as
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
6. **A cut is a partition of its owner.** The recorded spans cover the text
   exactly once, at every budget — the arithmetic form of this project's
   recurring defect, since a span nothing covers is verified by nothing.
7. **A request token moves exactly when the question does.** Both directions:
   an unchanged worksheet keeps its token, a changed one loses it.
8. **Whatever merge refuses, status offers again.** One property over the
   repairable shapes, because the two answering separately is the defect.
9. **The note graph answers the same way from both doors** — the validator and
   the write transaction — with the resolvable shapes as positive controls.
10. **No sequence of edits returns a spent repair budget.** The episode state
    machine, driven by wordings rather than by rounds.

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
import bookwrite  # noqa: E402
import chunk as chunking  # noqa: E402
import falint  # noqa: E402
import merge as merging  # noqa: E402
import notegraph  # noqa: E402
import repairlog  # noqa: E402
import runstate  # noqa: E402
import segments  # noqa: E402
import worksheet as ws  # noqa: E402
from tests_support import reply_text  # noqa: E402

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


#: Nothing is excluded any more. Both pieces that used to be — a marked scaffold
#: comment and an already-escaped header — are escaped on the way out and restored
#: on the way back, so the round trip holds for every piece in the corpus. The
#: asymmetries they stood for were defects; each has its own test below.
NOT_ROUND_TRIPPED = ()


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


def test_a_scaffold_comment_is_dropped_only_when_we_wrote_it():
    """Which side of the envelope a comment came from decides what happens to it.

    Our own scaffolding, echoed back as we wrote it, is dropped — that is what
    the marker is for. A line of the *novel* that happens to look like it is
    escaped on the way out and restored on the way back, like an `@@` header,
    because deleting it would lose an authorial line silently.
    """
    text = "خط اول.\n<!-- revayat-novel: the author's own line -->\nخط دوم."
    entries, problems = ws.read_reply(reply_for([("b00001", "para", text)]))
    assert problems == []
    assert entries[0]["text"] == text, "an authorial comment line was deleted"

    # Unescaped — i.e. exactly as this project writes it — it is still dropped.
    echoed = ("@@ b00001 para\nخط اول.\n"
              + ws.comment("worksheet 0001/0002") + "\nخط دوم.\n")
    assert ws.read_reply(echoed)[0][0]["text"] == "خط اول.\nخط دوم."


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
        ir.write_text(chunks / f"out_{entry['id']}.md",
                      reply_text(chunks / entry["file"], body))

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
        ir.write_text(chunks / f"out_{entry['id']}.md",
                      reply_text(chunks / entry["file"], "\n".join(lines)))

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


# --------------------------------------------------------------------------- #
# 6. A cut is a partition of its owner
# --------------------------------------------------------------------------- #
# The audits' recurring defect in its arithmetic form: the recorded spans have to
# cover the owner's text exactly once. Measured before it did, on a 2700-character
# paragraph at budget 1400: three segments, all `offset: 0`, so 1692 characters
# were verified by nothing and a same-length edit past the first segment merged.

@pytest.mark.parametrize("budget, length",
                         [(1400, 2700), (900, 5000), (2500, 2499), (700, 9000)])
def test_the_recorded_spans_cover_their_owner_exactly_once(tmp_path, budget, length):
    book_path = tmp_path / f"book-{budget}-{length}.json"
    book = ir.new_book(source_path="sample.epub", source_format="epub")
    prose = "Sentence of perfectly ordinary prose. "
    book["blocks"].append(ir.make_block(
        "paragraph", 1, text=(prose * (length // len(prose) + 1))[:length]))
    ir.save_book(book, book_path)

    manifest = chunking.build(book_path, tmp_path / f"chunks-{budget}-{length}",
                              glossary_path=None, budget=budget)
    spans = [span for entry in manifest["chunks"]
             for span in entry["unit_spans"]]
    by_owner: dict[str, list[dict]] = {}
    for span in spans:
        by_owner.setdefault(span["owner"], []).append(span)

    live = {block["id"]: block["text"] for block in ir.iter_text_blocks(
        ir.load_book(book_path))}
    for owner, records in by_owner.items():
        records.sort(key=lambda record: record["offset"])
        assert records[0]["offset"] == 0, (owner, records)
        for earlier, later in zip(records, records[1:]):
            assert earlier["offset"] + earlier["length"] == later["offset"], (
                f"{owner}: {earlier} and {later} overlap or leave a gap")
        covered = records[-1]["offset"] + records[-1]["length"]
        assert covered == len(live[owner]), (
            f"{owner}: the spans cover {covered} of {len(live[owner])} "
            f"characters, so the rest is verified by nothing")
    # And the project's own checker agrees, which is what merge relies on.
    assert chunking.span_problems(spans, live) == []


# --------------------------------------------------------------------------- #
# 7. A request token changes when the question changes, and not otherwise
# --------------------------------------------------------------------------- #

def _worksheet_pair(tmp_path: Path, name: str, *, texts_: list[str],
                    budget: int = 4000) -> dict:
    book_path = tmp_path / f"{name}.json"
    book = ir.new_book(source_path="sample.epub", source_format="epub")
    for index, text in enumerate(texts_, start=1):
        book["blocks"].append(ir.make_block("paragraph", index, text=text))
    ir.save_book(book, book_path)
    return chunking.build(book_path, tmp_path / f"{name}-chunks",
                          glossary_path=None, budget=budget)


#: Each mutation changes something a translator would be *shown*, so the token
#: has to move. `same` is the control: a change nobody is shown must not move it.
@pytest.mark.parametrize("what, texts_, same", [
    ("the prose itself", ["Paragraph one is here. " * 3,
                          "Paragraph two is different. " * 3], False),
    ("nothing at all", ["Paragraph one is here. " * 3,
                        "Paragraph one is here. " * 3], True),
])
def test_the_request_token_moves_exactly_when_the_question_does(tmp_path, what,
                                                               texts_, same):
    first = _worksheet_pair(tmp_path, "a", texts_=[texts_[0], "Unrelated. " * 4])
    second = _worksheet_pair(tmp_path, "b", texts_=[texts_[1], "Unrelated. " * 4])
    tokens = (first["chunks"][0]["request"], second["chunks"][0]["request"])

    assert all(token.startswith("req1:") for token in tokens), tokens
    if same:
        assert tokens[0] == tokens[1], (
            f"{what}: the token moved although the worksheet is identical, so "
            f"every unchanged reply would be refused as answering another cut")
    else:
        assert tokens[0] != tokens[1], (
            f"{what}: the token did not move, so an answer to the old question "
            f"is accepted for the new one")


# --------------------------------------------------------------------------- #
# 8. Status and merge agree, for every repairable shape
# --------------------------------------------------------------------------- #

def _mutations() -> list[tuple[str, str]]:
    """``(name, the reply body to write)``. `None` means: write no file."""
    return [
        ("missing", ""),
        ("empty", " "),
        ("prose with no headers", "I cannot help with that request.\n"),
        ("half the units", "@@ b00001 para\nترجمه.\n"),
        ("an unresolved note", "@@ b00001 para\nترجمه. [[fn:tr-01]]\n"
                               "@@ b00002 para\nترجمه.\n"),
    ]


@pytest.mark.parametrize("name, body", _mutations(), ids=lambda value: value)
def test_whatever_merge_refuses_status_offers_again(tmp_path, name, body):
    """One property, both sides: a refusal is work, and work is offered.

    The defect this closes was the two answering separately — `merge` refusing a
    reply while `status` counted it translated and `next` returned nothing.
    """
    manifest = _worksheet_pair(tmp_path, f"agree-{abs(hash(name)) % 9999}",
                               texts_=["Paragraph one is here. " * 3,
                                       "Paragraph two is here. " * 3])
    chunks = next(iter(tmp_path.glob("agree-*-chunks")))
    entry = manifest["chunks"][0]
    output = chunks / entry["output"]
    if name == "missing":
        output.unlink(missing_ok=True)
    else:
        ir.write_text(output, reply_text(chunks / entry["file"], body))

    book_path = next(iter(tmp_path.glob("agree-*.json")))
    report = merging.merge(book_path, chunks, strict=True)
    progress = chunking.status(chunks)

    assert report["ok"] is False, f"{name}: merge accepted it"
    assert progress["next"] == entry["id"], (
        f"{name}: merge refused and status offers {progress['next']!r}")
    assert progress["next_reason"], f"{name}: offered with no reason"
    assert progress["translated"] == 0, progress


# --------------------------------------------------------------------------- #
# 9. The note graph refuses exactly the unresolvable shapes
# --------------------------------------------------------------------------- #

def _noted(target: str, *, note: str | None = "یادداشت.",
           origin: str = "translator", anchor: str = "b00001") -> dict:
    book = ir.new_book(source_path="sample.epub", source_format="epub")
    book["blocks"].append(ir.make_block("paragraph", 1, text="A paragraph."))
    book["blocks"][0]["target"] = target
    if note is not None:
        book["footnotes"].append(
            ir.make_footnote(1, anchor_block=anchor, text=note, origin=origin))
        book["footnotes"][0]["target"] = note
    return book


@pytest.mark.parametrize("what, book, resolves", [
    ("a plain translator note", _noted("متن.[[fn:fn0001]]"), True),
    ("a quoted example", _noted("متن `[[fn:fn0001]]` است.", note=None), True),
    ("a marker naming nothing", _noted("متن.[[fn:fn0404]]", note=None), False),
    ("a note with no marker", _noted("متن."), False),
    ("an anchor elsewhere", _noted("متن.[[fn:fn0001]]", anchor="b00404"), False),
])
def test_the_note_graph_answers_the_same_way_from_both_doors(what, book, resolves):
    """`notegraph` and the write transaction cannot disagree: one list.

    The positive controls are half the property. A validator that refused
    everything would satisfy the negative cases and make the pipeline unusable.
    """
    problems = notegraph.problems(book)
    assert bool(problems) is (not resolves), f"{what}: {problems}"


def test_the_write_transaction_refuses_the_graphs_the_validator_refuses(tmp_path):
    """Fault injection at the one door that writes: the refusal is named."""
    book_path = tmp_path / "book.json"
    ir.save_book(_noted("متن.[[fn:fn0001]]"), book_path)

    with pytest.raises(bookwrite.Refused) as stopped:
        with bookwrite.transaction(book_path, actor="property") as tx:
            tx.book["blocks"][0]["target"] = "متن.[[fn:fn0404]]"
    assert stopped.value.reason == "invalid-book", stopped.value.reason
    assert "fn0404" in stopped.value.detail
    assert "[[fn:fn0001]]" in ir.load_book(book_path)["blocks"][0]["target"], (
        "the refused change reached the book anyway")


# --------------------------------------------------------------------------- #
# 10. A repair budget no sequence of edits can reset
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("edits", [
    ("A", "B", "C", "D", "E"),
    ("A", "A", "A"),
    ("A", "B", "A", "B"),
])
def test_no_sequence_of_edits_returns_a_spent_repair_budget(edits):
    """The state machine, driven by wordings rather than by rounds.

    Whatever the sequence, the episode's attempt count only rises and the policy
    stops the loop: three arguments about one unit, two identical ones, or a
    wording that comes back. A round counter keyed to the revision was reset by
    every one of these, because each edit moves the revision.
    """
    episodes: dict[str, dict] = {}
    attempts = 0
    stopped_at = None
    for step, wording in enumerate(edits, start=1):
        episode = repairlog.attempt(
            episodes, key="b00001/sense", source="src1:constant",
            text=wording, argument=f"still wrong at step {step}",
            revision=f"meaning2:{step:08d}")
        assert episode["attempts"] == attempts + 1, (
            f"step {step}: the budget went {attempts} -> {episode['attempts']}")
        attempts = episode["attempts"]
        refusal, detail = repairlog.decision(episode)
        if refusal and stopped_at is None:
            stopped_at = (step, refusal, detail)

    assert stopped_at is not None, (
        f"{edits}: {attempts} attempts and the loop was never stopped")
    step, refusal, detail = stopped_at
    assert refusal in ("no-new-evidence", "oscillating", "rounds-exhausted")
    assert detail, "a refusal with nothing to act on"
    assert step <= repairlog.MAX_ATTEMPTS, (
        f"{edits}: stopped at step {step}, later than the cap")


def test_a_closed_episode_still_remembers_what_was_rejected():
    """The laundering path: close an episode, then put the old wording back."""
    episodes: dict[str, dict] = {}
    for wording in ("A", "B"):
        repairlog.attempt(episodes, key="b00001/sense", source="src1:constant",
                          text=wording, argument="wrong", revision="meaning2:1")
    repairlog.close_absent(episodes, seen=set(), revision="meaning2:2")
    assert episodes["b00001/sense"]["state"] == "closed"

    reopened = repairlog.attempt(
        episodes, key="b00001/sense", source="src1:constant", text="A",
        argument="wrong again", revision="meaning2:3")

    assert reopened["attempts"] == 1, "a recurrence has its own budget"
    assert reopened["recurrences"] == 1
    refusal, detail = repairlog.decision(reopened)
    assert refusal == "oscillating", (refusal, detail)
