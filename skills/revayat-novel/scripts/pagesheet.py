"""What a page worksheet shows a translator, and what it does not.

The page route's half of the same seam `chunksheet` is on the chunk route: this
file decides what goes on the page a translator reads, `pagerun` decides which
pages exist and what happens to the answers. `pagerun` re-exports both names.

Every piece of continuity here is **bounded**, and each bound has its own reason:
the neighbour prose because it grows with the book rather than with the page, the
term table and the voice cards because they grow with the glossary, the OCR notes
because they grow with how badly the scan read. An unbounded "just add context" is
how a page job silently becomes the whole-book job the route exists to replace.
"""

from __future__ import annotations

from typing import Any

import bookir as ir
import glossary as gl

#: Hard maximum characters of adjacent-page prose shown per side. Neighbour text
#: is for resolving a pronoun, not for translating; without a ceiling the two
#: neighbours of a dense page can outweigh the page itself.
NEIGHBOUR_CHARS = 600

#: Ceilings on the rest of the continuity state. The two OCR ceilings live in
#: `pageidentity` with `ocr_state`, which is what applies them.
MAX_TERMS = 24
MAX_VOICE_CARDS = 6

def neighbour_context(book: dict[str, Any], jobs: list[dict[str, Any]],
                      index: int, limit: int = NEIGHBOUR_CHARS) -> tuple[str, str]:
    """``(before, after)`` prose from the adjacent pages, hard-capped.

    Read-only by contract: whatever comes back is rendered under a header that
    says not to translate it, and the blocks it came from are owned by *their*
    page, never by this one.
    """
    lookup = ir.blocks_by_id(book)

    def blob(ids: list[str]) -> str:
        return " ".join(
            ir.plain_text(lookup[i].get("text") or "")
            for i in ids
            if i in lookup and lookup[i]["type"] in ir.TEXT_TYPES
        ).strip()

    before = blob(jobs[index - 1]["block_ids"])[-limit:] if index > 0 else ""
    after = (blob(jobs[index + 1]["block_ids"])[:limit]
             if index + 1 < len(jobs) else "")
    return before, after


def render_worksheet(
    book: dict[str, Any],
    glossary: dict[str, Any],
    job: dict[str, Any],
    units: list[tuple[str, str, str]],
    *,
    total: int,
    previous_tail: str,
    next_head: str,
) -> str:
    lookup = ir.blocks_by_id(book)
    source_blob = "\n".join(text for _, _, text in units)
    page = job["page"]

    lines: list[str] = [
        f"<!-- revayat-novel page worksheet {page:04d}/{total:04d} "
        f"| page {page} | units {len(units)} | {len(source_blob)} source chars -->",
        "<!-- Reply with the same @@ headers, in the same order, Persian text "
        "underneath each. Do not add, drop, merge or reorder headers. -->",
        "",
    ]

    entries = gl.entries_for_text(glossary, source_blob)[:MAX_TERMS]
    table = gl.render_term_table(entries, glossary.get("policy", {}),
                                 block_ids=job["block_ids"])
    if table:
        lines += ["## Names — use these exact forms", "", table, ""]

    # The book's register, on every page worksheet too. A page run is the route
    # where a translator sees least of the book, so it is the route where an
    # unstated register drifts fastest.
    voice = str((glossary.get("policy") or {}).get("book_voice") or "").strip()
    if voice:
        lines += ["## This book's voice — the narration, not the characters", "",
                  voice, ""]

    cards = gl.render_voice_cards(glossary, source_blob).splitlines()[:MAX_VOICE_CARDS]
    if cards:
        lines += ["## Character voices", "", *cards, ""]

    uncertain = (job.get("ocr") or {}).get("uncertain") or []
    if uncertain:
        lines += ["## Read poorly by OCR — translate the sense, flag the word, "
                  "do not invent one", ""]
        lines += [f"- {note['block']}: " + "، ".join(f"«{w}»" for w in note["words"])
                  for note in uncertain]
        lines.append("")

    if previous_tail or next_head:
        lines += ["## Surrounding pages — context only, do not translate or output",
                  ""]
        if previous_tail:
            lines += [f"Before (page {page - 1}): …{previous_tail}", ""]
        if next_head:
            lines += [f"After (page {page + 1}): {next_head}…", ""]

    lines += [f"## Translate — page {page}", ""]
    for unit_id, kind, text in units:
        block = lookup.get(unit_id.split("#")[0])
        if block is not None and block["type"] == "image":
            lines.append(
                f"<!-- illustration {block['asset']} is anchored here; the "
                f"picture itself needs nothing from you. The alt header below "
                f"is its caption text and does need translating. -->"
            )
        lines.append(f"@@ {unit_id} {kind}")
        lines.append(text)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
