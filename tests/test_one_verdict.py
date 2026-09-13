"""One reply, one verdict — the same answer in `status`, `next` and `merge`.

Two sides read every `out_chunkNNNN.md`: `merge`, which writes the book, and
`status`/`next`, which decide what is left to do. While each derived its own
answer from the same file they disagreed in both directions, and each direction
is unrecoverable by retrying:

* `merge` refused an orphan, wrong-kind or missing translator-note body while
  `status` called the job answered and `next` reported nothing outstanding — a
  resume loop with nothing to offer and a merge that can never succeed;
* a zero-unit job (an image-only page, a blank verso) was finished the moment it
  was cut as far as `status` was concerned, and `merge` demanded a reply file for
  it.

So this is a truth table rather than a list of examples. Every row asserts three
things about one reply: the state `worksheet.verdict` gives it, whether `next`
still offers the job, and whether `merge` writes. A row where the last two
disagree is the defect, whichever way round it falls.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bookir as ir  # noqa: E402
import chunk as chunking  # noqa: E402
import merge as merging  # noqa: E402
import worksheet as ws  # noqa: E402
from tests_support import reply_text  # noqa: E402

GOOD = "ترجمهٔ این بند که به اندازهٔ کافی بلند است."


def _book(tmp_path: Path, *, paragraphs: int = 2, image: bool = False) -> Path:
    book = ir.new_book(source_path="sample.epub", source_format="epub",
                       title="A Small Book", author="Test Author")
    index = 0
    for _ in range(paragraphs):
        index += 1
        book["blocks"].append(
            ir.make_block("paragraph", index, text=f"Paragraph number {index}."))
    if image:
        index += 1
        book["blocks"].append(
            ir.make_block("image", index, asset="fig.png", sha256="0" * 64,
                          bbox=None, width_pt=10.0, height_pt=10.0,
                          pixel_width=10, pixel_height=10,
                          alt="", target_alt=None))
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    return path


def _cut(book_path: Path, tmp_path: Path) -> tuple[Path, dict]:
    chunks = tmp_path / "chunks"
    chunking.build(book_path, chunks, glossary_path=None, budget=4000)
    return chunks, json.loads((chunks / "manifest.json").read_text(encoding="utf-8"))


def _headers(entry: dict) -> list[tuple[str, str]]:
    kinds = entry.get("unit_kinds") or {}
    return [(unit_id, kinds.get(unit_id, "para")) for unit_id in entry["unit_ids"]]


def complete(entry: dict) -> str:
    lines: list[str] = []
    for unit_id, kind in _headers(entry):
        lines += [f"@@ {unit_id} {kind}", GOOD, ""]
    return "\n".join(lines)


def duplicated(entry: dict) -> str:
    unit_id, kind = _headers(entry)[0]
    return complete(entry) + f"@@ {unit_id} {kind}\n{GOOD}\n"


def reordered(entry: dict) -> str:
    lines: list[str] = []
    for unit_id, kind in reversed(_headers(entry)):
        lines += [f"@@ {unit_id} {kind}", GOOD, ""]
    return "\n".join(lines)


def wrong_kind(entry: dict) -> str:
    lines: list[str] = []
    for position, (unit_id, kind) in enumerate(_headers(entry)):
        if position == 0:
            kind = "heading1" if kind != "heading1" else "para"
        lines += [f"@@ {unit_id} {kind}", GOOD, ""]
    return "\n".join(lines)


def unclosed_fence(entry: dict) -> str:
    return "```\n" + complete(entry)


def orphan_note(entry: dict) -> str:
    return complete(entry) + "@@ tr-01 footnote\nیادداشتی که هیچ ارجاعی ندارد.\n"


def marker_without_body(entry: dict) -> str:
    lines: list[str] = []
    for position, (unit_id, kind) in enumerate(_headers(entry)):
        body = GOOD + ("[[fn:tr-01]]" if position == 0 else "")
        lines += [f"@@ {unit_id} {kind}", body, ""]
    return "\n".join(lines)


def note_wrong_kind(entry: dict) -> str:
    return marker_without_body(entry) + "@@ tr-01 heading1\nیادداشت مترجم.\n"


#: ``(name, reply writer, expected verdict state, may merge)``. ``None`` as the
#: writer means no reply file at all.
SHAPES = [
    ("no reply at all", None, "missing", False),
    ("an empty file", lambda _entry: "   \n", "empty", False),
    ("a complete reply", complete, "answered", True),
    ("an id answered twice", duplicated, "invalid", False),
    ("headers reordered", reordered, "invalid", False),
    ("a unit answered as the wrong kind", wrong_kind, "invalid", False),
    ("a fence that never closes", unclosed_fence, "invalid", False),
    ("a note body nothing refers to", orphan_note, "invalid", False),
    ("a marker with no body", marker_without_body, "invalid", False),
    ("a note answered as a heading", note_wrong_kind, "invalid", False),
]


@pytest.mark.parametrize("name, write, state, may_merge", SHAPES,
                         ids=[shape[0] for shape in SHAPES])
def test_status_next_and_merge_agree_about_one_reply(tmp_path, name, write,
                                                    state, may_merge):
    book_path = _book(tmp_path)
    chunks, manifest = _cut(book_path, tmp_path)
    entry = manifest["chunks"][0]
    assert len(manifest["chunks"]) == 1, "this table assumes a single worksheet"

    reply = None if write is None else write(entry)
    if reply is not None:
        ir.write_text(chunks / entry["output"],
                      reply_text(chunks / entry["file"], reply))

    # 1. the verdict itself
    got = ws.verdict(reply, list(entry["unit_ids"]), entry.get("unit_kinds") or {})
    assert got["state"] == state, f"{name}: verdict {got['state']!r}, {got['problems']}"

    # 2. what the resume view does with it
    report = chunking.status(chunks)
    offered = report["next"] is not None

    # 3. what merge does with it
    merged = merging.merge(book_path, chunks, strict=True)

    assert merged["ok"] is may_merge, f"{name}: merge said {merged}"
    assert offered is not may_merge, (
        f"{name}: merge {'accepted' if merged['ok'] else 'refused'} this reply "
        f"and next {'offered' if offered else 'did not offer'} the job. One of "
        f"them is wrong: a refused reply must stay schedulable, and an accepted "
        f"one must not be handed out again.")


def test_a_zero_unit_job_is_finished_without_a_reply(tmp_path):
    """An image with no alt text asks for nothing, so there is nothing to answer.

    `status` always knew that. `merge` demanded `out_chunk0001.md` anyway, so the
    run had nothing left to do and could never merge — the deadlock from the other
    direction.
    """
    book_path = _book(tmp_path, paragraphs=0, image=True)
    chunks, manifest = _cut(book_path, tmp_path)
    entry = manifest["chunks"][0]
    assert entry["units"] == 0 and entry["unit_ids"] == []

    assert ws.verdict(None, [], {})["state"] == ws.NOTHING_TO_TRANSLATE
    assert chunking.status(chunks)["next"] is None
    merged = merging.merge(book_path, chunks, strict=True)
    assert merged["ok"] is True, merged
    assert merged["missing_outputs"] == [], (
        "a job that asks for nothing was reported as an unanswered one")
    assert not (chunks / entry["output"]).exists(), (
        "the test wrote a reply; then it is not testing the zero-unit case")


def test_the_verdict_is_side_effect_free(tmp_path):
    """A verdict anyone can ask twice. One that writes cannot be a shared one."""
    book_path = _book(tmp_path)
    chunks, manifest = _cut(book_path, tmp_path)
    entry = manifest["chunks"][0]
    reply = orphan_note(entry)
    ir.write_text(chunks / entry["output"], reply)

    before = sorted((path.name, path.stat().st_mtime_ns)
                    for path in chunks.iterdir() if path.is_file())
    first = ws.verdict(reply, list(entry["unit_ids"]), entry["unit_kinds"])
    second = ws.verdict(reply, list(entry["unit_ids"]), entry["unit_kinds"])
    after = sorted((path.name, path.stat().st_mtime_ns)
                   for path in chunks.iterdir() if path.is_file())

    assert first["state"] == second["state"] == "invalid"
    assert first["problems"] == second["problems"]
    assert before == after, "asking for a verdict changed the working directory"


def test_the_note_graph_is_part_of_the_verdict_not_only_of_merge():
    """The specific disagreement C02 names, at the unit level."""
    expected = ["b00001"]
    kinds = {"b00001": "para"}
    orphan = f"@@ b00001 para\n{GOOD}\n\n@@ tr-01 footnote\nبدنه‌ای بی‌ارجاع.\n"
    assert ws.verdict(orphan, expected, kinds)["state"] == "invalid"
    assert ws.classify(orphan, expected, kinds) == "invalid"

    resolved = (f"@@ b00001 para\n{GOOD}[[fn:tr-01]]\n\n"
                "@@ tr-01 footnote\nیادداشتی که ارجاع دارد.\n")
    assert ws.verdict(resolved, expected, kinds)["state"] == "answered"
