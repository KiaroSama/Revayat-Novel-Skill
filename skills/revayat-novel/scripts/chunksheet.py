"""What a chunk worksheet shows a translator, and what it does not.

Split out of `chunk` so both have room, and the seam is a real one: this file
decides what goes *on* the page a translator reads — the term table, the book's
voice, the character cards, the bounded neighbour context, the units themselves —
while `chunk` decides how the book is cut into pages and what happens to the
answers. `chunk` re-exports both names, because the CLI and the tests reach them
there.

Two rules the rendering carries and the cut does not:

* **only the blocks this worksheet actually carries** decide the term table. A run
  that renders over the budget becomes several worksheets sharing one `ids`, and
  handing each the whole run told every one of them it owned the name's first
  appearance — so four worksheets all asked for the same introduction, the
  duplication the glossary pass exists to prevent, asked for in the prompt.
* **the neighbour window is bounded and hashed.** It is context, never content:
  what it shows is part of the request digest, so a change to the text next door
  invalidates this worksheet rather than silently changing what was asked.
"""

from __future__ import annotations

from typing import Any

import bookir as ir
import glossary as gl
from provenance import CONTEXT_CHARS, translatable_units
from worksheet import comment, escape_payload

def neighbour_context(book: dict[str, Any], chunks: list[list[str]], index: int) -> tuple[str, str]:
    def blob(ids: list[str]) -> str:
        lookup = ir.blocks_by_id(book)
        return " ".join(
            ir.plain_text(lookup[i].get("text") or "")
            for i in ids if i in lookup and lookup[i]["type"] in ir.TEXT_TYPES
        ).strip()

    previous_tail = blob(chunks[index - 1])[-CONTEXT_CHARS:] if index > 0 else ""
    next_head = blob(chunks[index + 1])[:CONTEXT_CHARS] if index + 1 < len(chunks) else ""
    return previous_tail, next_head


def render_worksheet(
    book: dict[str, Any],
    glossary: dict[str, Any],
    ids: list[str],
    *,
    index: int,
    total: int,
    previous_tail: str,
    next_head: str,
    units: list[tuple[str, str, str]] | None = None,
) -> str:
    """``units`` overrides what the block run offers.

    The builder needs that: a unit longer than the budget is cut into segments,
    and a run of units that renders over the budget is split into several
    worksheets, so what a worksheet carries is no longer simply "every unit of
    these blocks". Left out, the behaviour is exactly what it always was.
    """
    lookup = ir.blocks_by_id(book)
    units = translatable_units(book, ids) if units is None else units
    source_blob = "\n".join(text for _, _, text in units)

    lines: list[str] = [
        comment(f"worksheet {index:04d}/{total:04d} | units {len(units)} "
                f"| {len(source_blob)} source chars"),
        comment("Reply with the same @@ headers, in the same order, Persian "
                "text underneath each. Do not add, drop, merge or reorder "
                "headers."),
        # Kept to one line on purpose: every worksheet pays for it out of the
        # budget, and a small job pays proportionally most.
        comment("Copy the `request` line above into your reply, unchanged."),
        "",
    ]

    # The blocks *this worksheet* carries, not every block in the run. A run
    # that renders over the budget becomes several worksheets sharing one
    # ``ids``, and handing each the whole run told every one of them that it
    # owned the name's first appearance — so four worksheets all said
    # "introduce this name here", which is the duplication the glossary pass
    # exists to prevent, asked for in the prompt.
    carried = [block_id for block_id in ids
               if any(unit_id == block_id or unit_id.startswith(f"{block_id}#")
                      for unit_id, _, _ in units)]

    table = gl.render_term_table(
        gl.entries_for_text(glossary, source_blob), glossary.get("policy", {}),
        block_ids=carried,
    )
    if table:
        lines += ["## Names — use these exact forms", "", table, ""]

    # The book's own register, before the characters who speak in it. One
    # sentence, on every worksheet: a translator who is not told picks a register
    # per chunk, and a book whose narration turns administrative halfway through
    # is exactly what `meaning`'s `register` rubric and `fluency`'s internal
    # consistency question keep finding.
    voice = str((glossary.get("policy") or {}).get("book_voice") or "").strip()
    if voice:
        lines += ["## This book's voice — the narration, not the characters", "",
                  voice, ""]

    cards = gl.render_voice_cards(glossary, source_blob)
    if cards:
        lines += ["## Character voices", "", cards, ""]

    if previous_tail or next_head:
        lines += ["## Surrounding text — context only, do not translate or output", ""]
        if previous_tail:
            lines += [f"Before: …{previous_tail}", ""]
        if next_head:
            lines += [f"After: {next_head}…", ""]

    lines += ["## Translate", ""]
    for unit_id, kind, text in units:
        block = lookup.get(unit_id.split("#")[0])
        if block is not None and block["type"] == "image":
            lines.append(comment(
                f"illustration {block['asset']} is anchored here; the picture "
                f"itself needs nothing from you. The alt header below is its "
                f"caption text and does need translating."))
        lines.append(f"@@ {unit_id} {kind}")
        lines.append(escape_payload(text))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
