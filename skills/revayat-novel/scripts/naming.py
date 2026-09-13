"""Which block introduces which name, once — computed once, used by both sides.

A locked name is introduced with its original spelling exactly where the policy
says: once in the book, or once in each chapter that names her. Two passes have to
agree about that place — the enforcement pass that writes it and the gate that
checks it — and for three separate reasons they did not:

* the gate's per-chapter branch asked for ``first_block_id``, the block where the
  *entity* first appears in the source. When that block's Persian is a nickname,
  the parenthetical cannot go there: enforcement correctly introduced the name in
  the next block that uses the canonical form, and the gate demanded the pinned
  one. Three identical passes left the same error.
* eligibility was computed on two different views of the text. `prose_of`
  concatenates the markup spans, so ``**الیزابت** بنت`` reads as contiguous and
  the block looked eligible; the writer works inside **one** span at a time and
  could not place anything there. Owner said yes, writer said no, and no re-run
  converged.
* `_place_once`'s own fallback — "the first block of the group that actually names
  her" — was the right rule and lived in the writer, where the gate could not
  reach it. A helper one path never calls is not convergence.

So the plan is a value, not a convention: :func:`plan` returns the groups and the
owning block per entry, and both the writer and the gate read it. The eligibility
rule is :func:`placement_spans`, which is *the writer's own test* — what it can
actually do — so a block is eligible here exactly when the introduction can be
written there.

The pieces that rewrite prose live here with it, because the plan and the writer
have to move together; `glossary` re-exports them for the callers that already
had them.
"""

from __future__ import annotations

from typing import Any

import bookir as ir
import falint
import glossary as gl

#: What the plan calls the group of blocks outside any chapter.
FRONT = "front"


def placement_spans(target: str, form: str) -> list[tuple[int, int]]:
    """Where ``form`` stands alone in text an introduction may be written into.

    One rule, and deliberately the writer's: inside a single markup span, with
    literals masked the way :func:`falint.mask_literals` masks them, skipping
    verbatim spans and footnote tokens. A name whose words fall in two styled
    spans is *not* eligible — placing it would rewrite the emphasis — and the
    point is that the gate now agrees, rather than demanding a placement the
    writer refuses.
    """
    found: list[tuple[int, int]] = []
    offset = 0
    for span in ir.parse_markup(target or ""):
        body = span.get("text") or ""
        if span.get("footnote"):
            continue
        if not span.get("verbatim"):
            masked, _keep = falint.mask_literals(body)
            found += [(offset + start, offset + end)
                      for start, end in gl.standalone_spans(masked, form)]
        offset += len(body)
    return found


def eligible(blocks: list[dict[str, Any]], form: str) -> list[str]:
    """The ids of the blocks an introduction of ``form`` could be written into."""
    return [block["id"] for block in blocks
            if placement_spans(block.get("target") or "", form)]


def _owner(blocks: list[dict[str, Any]], form: str, pinned: str) -> str:
    """The pinned block when it can carry the introduction, else the first that can.

    Stated once, here. The nickname is never expanded to make a block eligible:
    a book that gives a character her own nickname made a translation decision,
    and overwriting it to satisfy a placement rule is the drift this whole area
    exists to prevent.
    """
    usable = eligible(blocks, form)
    if pinned in usable:
        return pinned
    return usable[0] if usable else ""


def owner_of(entry: dict[str, Any], blocks: list[dict[str, Any]]) -> str:
    """Which of ``blocks`` carries this entry's introduction. One group's answer.

    The whole-book case of :func:`plan`, for a caller holding one entry and one
    list of blocks. Both go through :func:`_owner`, so there is one rule.
    """
    return _owner(blocks, gl.canonical(entry), entry.get("first_block_id") or "")


def groups(book: dict[str, Any], policy: str) -> list[tuple[str, list[dict[str, Any]]]]:
    """``(group key, blocks)`` in reading order, as the policy divides the book."""
    blocks = list(ir.iter_text_blocks(book))
    if policy != "first_per_chapter":
        return [("book", blocks)]
    chapters = gl.chapters_by_block(book)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for block in blocks:
        grouped.setdefault(chapters.get(block["id"], FRONT), []).append(block)
    return list(grouped.items())


def plan(glossary: dict[str, Any], book: dict[str, Any]) -> dict[str, Any]:
    """The naming plan: for each entry that owes an introduction, where it goes.

    ``entries`` maps the entry key to ``{introduction, later_form, places}``, and
    ``places`` is one record per group: its key, the owning block id (``""`` when
    the group has nowhere to put it), and whether the group names her at all.
    """
    policy = gl.parenthetical_policy(glossary.get("policy") or {})
    divided = groups(book, policy)
    entries: dict[str, Any] = {}

    for entry in glossary.get("entries") or []:
        first_form = (entry.get("first_form") or "").strip()
        later_form = gl.canonical(entry)
        introduction = gl.PARENTHETICAL.search(first_form)
        if not later_form or not introduction or first_form == later_form:
            continue
        key = entry.get("id") or entry.get("source") or later_form
        pinned = entry.get("first_block_id") or ""
        places = []
        for group_key, blocks in divided:
            owner = _owner(blocks, later_form, pinned)
            places.append({
                "group": group_key,
                "owner": owner,
                # "Does this group name her" is asked with the *placement* rule,
                # not with a substring test: a mention inside a URL or a verbatim
                # span is not somewhere an introduction can go, so it is not
                # somewhere one is demanded either.
                "names_her": bool(owner),
                "blocks": [block["id"] for block in blocks],
            })
        entries[key] = {
            "introduction": introduction.group(0),
            "first_form": first_form,
            "later_form": later_form,
            "locked": bool(entry.get("locked")),
            "places": places,
        }
    return {"policy": policy, "entries": entries}


def place_for(plan_record: dict[str, Any], group_key: str) -> dict[str, Any] | None:
    for place in plan_record.get("places") or []:
        if place["group"] == group_key:
            return place
    return None


# --------------------------------------------------------------------------- #
# Rewriting, which the plan is the plan *for*
# --------------------------------------------------------------------------- #

def flatten_in_prose(target: str, first_form: str,
                     later_form: str) -> tuple[str, int]:
    """Long form down to short form, in prose spans only.

    Returns the rewritten string and how many occurrences were replaced. A
    verbatim span is literal content — an identifier, a command, a quoted
    string — so rewriting inside one changes what the book says the literal is.
    """
    spans = ir.parse_markup(target)
    replaced = 0
    for span in spans:
        if span["verbatim"] or span["footnote"]:
            continue
        replaced += span["text"].count(first_form)
        span["text"] = span["text"].replace(first_form, later_form)
    return (ir.render_spans(spans) if replaced else target), replaced


def introduce_in_prose(target: str, later_form: str,
                       first_form: str) -> str | None:
    """Expand the first eligible occurrence, or ``None`` if there is none.

    Eligibility is :func:`placement_spans` — the same function the plan uses, so
    "the gate demanded a placement the writer could not make" is not a state this
    can reach. Literals inside a span are masked the way the typography pass masks
    them, because a name sitting after a ``/`` satisfies every word-boundary test
    and a URL with a parenthetical spliced into it is a dead link.
    """
    spans = ir.parse_markup(target)
    offset = 0
    for span in spans:
        body = span.get("text") or ""
        if span.get("footnote"):
            continue
        if not span.get("verbatim"):
            masked, keep = falint.mask_literals(body)
            found = gl.standalone_spans(masked, later_form)
            if found:
                start, end = found[0]
                span["text"] = falint.unmask_literals(
                    masked[:start] + first_form + masked[end:], keep)
                return ir.render_spans(spans)
        offset += len(body)
    return None


def enforce_first_mentions(glossary: dict[str, Any],
                           book: dict[str, Any]) -> dict[str, Any]:
    """Give each locked name its original spelling once, where the plan says.

    Chunks are translated in parallel by agents that cannot see one another, so
    "is this the first mention?" is a question none of them can answer. Every one
    of them answers yes, and the finished book repeats
    «الیزابت بنت (Elizabeth Bennet)» in thirty places. The worksheet asks the
    owning chunk to introduce the name; this is the pass that makes it true
    regardless of what came back.

    Mechanical and idempotent: flatten every introduction down to the later form,
    then write exactly one back, in the block :func:`plan` names. Run it twice and
    the second run changes nothing.

    ``first_per_chapter`` changes only how many places "once" means — a reader who
    opens at chapter nine never saw chapter one's parenthetical. ``never``
    flattens and places nothing. Aliases are left alone: a nickname with its own
    spelling is a translation decision, and `policy.keep_aliases_distinct` says so.
    """
    computed = plan(glossary, book)
    policy = computed["policy"]
    report: dict[str, Any] = {"policy": policy, "introduced": {}, "flattened": 0,
                              "unplaceable": [], "skipped": 0}
    blocks = list(ir.iter_text_blocks(book))
    by_id = {block["id"]: block for block in blocks}

    for entry in glossary.get("entries") or []:
        key = (entry.get("id") or entry.get("source")
               or gl.canonical(entry))
        record = computed["entries"].get(key)
        if record is None or not record["locked"]:
            report["skipped"] += 1
            continue

        first_form, later_form = record["first_form"], record["later_form"]
        for block in blocks:
            target = block.get("target") or ""
            if first_form not in target:
                continue
            block["target"], replaced = flatten_in_prose(target, first_form,
                                                         later_form)
            report["flattened"] += replaced

        if policy == "never":
            continue

        # The plan was computed before the flattening, which is what makes it
        # stable: a block already carrying the long form contains the short one
        # inside it, so it is eligible either way.
        placed = False
        for place in record["places"]:
            owner = by_id.get(place["owner"])
            if owner is None:
                continue
            rewritten = introduce_in_prose(owner.get("target") or "", later_form,
                                           first_form)
            if rewritten is None:
                # The plan and the writer read the same rule, so this is a book
                # that changed underneath the plan rather than a disagreement.
                continue
            owner["target"] = rewritten
            placed = True
            report["introduced"].setdefault(key, owner["id"])
        if not placed:
            report["unplaceable"].append(key)

    return report
