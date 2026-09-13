"""The one place a name's introduction is demanded, reading the plan that placed it.

`naming.plan` decides which block carries a locked name's original spelling, and
`naming.enforce_first_mentions` writes it there. This is the gate that proves it
happened — and it is a separate file because `qa` was over the ceiling with it
inside, not because the question is separate: it reads the same plan, deliberately.

The defect that shaped it: this gate used to ask `first_block_id`, the block where
the *entity* first appears in the source. When that block's Persian is a nickname
the parenthetical cannot go there, so the pass placed it in the next block that
uses the canonical form and the gate demanded the pinned one — three identical
runs leaving the same error, with nothing able to satisfy both.
"""

from __future__ import annotations

import re
from typing import Any

import bookir as ir
import glossary as gl
import naming
import published
from findings import ERROR, Report

#: The literal-only exemption, the same function `qa` and `published` use: a
#: source whose every span is verbatim has nothing to translate, so it is not
#: somewhere an introduction can be placed either.
_prose = published.prose

#: The original spelling a first-mention form carries, e.g. "(Elizabeth Bennet)".
_PARENTHETICAL = re.compile(r"\([^()]{2,}\)")


def _check_per_chapter(record: dict[str, Any], introduction: str, name: str,
                       targets: list[tuple[str, str]], report: Report) -> None:
    """Under ``first_per_chapter``, every chapter that can introduce her does.

    Reads `naming.plan`: the chapter grouping, which chapters name her and which
    block owns the introduction in each all come from the plan the enforcement
    pass placed by. This branch used to ask for `first_block_id` instead, so in
    the pinned block's chapter it demanded a paragraph whose Persian is a
    nickname — where the parenthetical cannot go — while the pass correctly put
    it in the next block that uses the canonical form. Three identical passes
    left the same error.
    """
    placements: dict[str, list[tuple[str, int]]] = {}
    for place in record.get("places") or []:
        for block_id in place["blocks"]:
            count = dict(targets).get(block_id, "").count(introduction)
            if count:
                placements.setdefault(place["group"], []).append((block_id, count))

    for place in record.get("places") or []:
        chapter = place["group"]
        here = placements.get(chapter, [])
        total = sum(count for _, count in here)
        if total == 0:
            if place["names_her"]:
                report.add(ERROR, "first-mention-missing", name,
                           f"{introduction} never appears in chapter {chapter}, "
                           f"which names her; policy is 'first_per_chapter', so "
                           f"re-run merge with --glossary")
            continue
        if total > 1:
            where = ", ".join(f"{block_id}x{count}" if count > 1 else block_id
                              for block_id, count in here[:4])
            report.add(ERROR, "first-mention-repeated", name,
                       f"{introduction} appears {total} times in chapter "
                       f"{chapter} ({where}); it belongs once per chapter")
            continue
        if place["owner"] and here[0][0] != place["owner"]:
            report.add(ERROR, "first-mention-misplaced", name,
                       f"{introduction} is in {here[0][0]} but this chapter's "
                       f"introduction belongs in {place['owner']}")


def _check_first_mentions(book: dict[str, Any], glossary: dict[str, Any],
                          report: Report) -> None:
    """The original spelling belongs in exactly one place.

    Chunks are translated in parallel by agents that cannot see each other, so
    left to their own judgement every one of them answers "yes, this is the
    first mention" and «الیزابت بنت (Elizabeth Bennet)» is repeated through the
    whole book. The worksheet already names the chunk that owns the
    introduction; this is the gate that proves it was obeyed.

    "Exactly one place" is what the policy says it is: once in the book, or once
    in every chapter that names her. The gate has to read the policy the same way
    the enforcement pass does, because a gate that disagrees with the pass that
    produced the book is not a gate.
    """
    try:
        plan = naming.plan(glossary, book)
        policy = plan["policy"]
    except ValueError as error:
        # Reported, not raised: this is a gate, and a glossary nobody can
        # interpret is exactly the kind of thing it exists to say out loud.
        report.add(ERROR, "glossary-policy", "glossary", str(error))
        return

    # Counted over the **prose**, with verbatim runs removed, because that is the
    # text the enforcement pass is allowed to touch: it masks literals through
    # `falint.mask_literals` before inserting anything. Counting the raw target
    # instead made the two disagree — a technical passage quoting «علی (Ali)»
    # inside backticks read as a placement, so the gate demanded the removal of
    # something the pass had correctly left alone, and no amount of re-running
    # could satisfy both.
    targets = [(block["id"], _prose(block.get("target") or ""))
               for block in ir.iter_text_blocks(book)]

    for entry in glossary.get("entries", []):
        match = _PARENTHETICAL.search(entry.get("first_form") or "")
        if not match:
            continue
        # Deliberately not gated on `locked`. Locking governs whether the
        # enforcement pass may rewrite the text — a destructive act that should
        # only touch a name the translator has confirmed. Reporting costs
        # nothing, and a name introduced three times is a defect whether or not
        # anyone has ticked the box yet.
        introduction = match.group(0)
        name = entry.get("id") or introduction
        # The same resolver the enforcement pass uses. Asking for
        # `first_block_id` instead is what made the gate demand an
        # introduction in a block whose Persian is a nickname, while the
        # pass correctly put it in the first block that names her — two
        # answers to one question, and no re-run could satisfy both.
        # The plan, not a second opinion about it. Asking `first_block_id`
        # instead is what made the gate demand an introduction in a block whose
        # Persian is a nickname while the pass correctly put it in the first
        # block that can carry one.
        record = (plan.get("entries") or {}).get(
            entry.get("id") or entry.get("source") or gl.canonical(entry)) or {}
        place = naming.place_for(record, "book") or {}
        owner = place.get("owner", "")

        # Counted, not merely located. A block-level list cannot tell one
        # introduction from three inside the same paragraph, which is exactly
        # what a chunk repeating itself produces.
        placements = [(block_id, target.count(introduction))
                      for block_id, target in targets if introduction in target]
        total = sum(count for _, count in placements)

        if policy == "never":
            if total:
                report.add(ERROR, "first-mention-forbidden", name,
                           f"policy is 'never' but {introduction} appears "
                           f"{total} time(s)")
            continue

        if policy == "first_per_chapter":
            _check_per_chapter(record, introduction, name, targets, report)
            continue

        if total == 0:
            report.add(ERROR, "first-mention-missing", name,
                       f"{introduction} never appears; re-run merge with "
                       f"--glossary, which places it once in "
                       f"{owner or 'the block that first mentions the name'}")
            continue

        if total > 1:
            where = ", ".join(f"{block_id}x{count}" if count > 1 else block_id
                              for block_id, count in placements[:4])
            report.add(ERROR, "first-mention-repeated", name,
                       f"{introduction} appears {total} times ({where}); it "
                       f"belongs once, in {owner or placements[0][0]}")
            continue

        placed_in = placements[0][0]
        if owner and placed_in != owner:
            report.add(ERROR, "first-mention-misplaced", name,
                       f"{introduction} is in {placed_in} but the first mention "
                       f"of this name is in {owner}")
