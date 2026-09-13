"""Book-wide naming and voice consistency.

A chapter is translated by an agent with a fresh context, so nothing stops
chapter 3 calling him «جان» and chapter 12 calling him «جون». The glossary is
the shared state that prevents it: every name is decided once, locked, and
injected into every chunk that mentions it.

Two kinds of state live here:

* **entries** — one canonical Persian form per named entity, plus the aliases
  and nicknames that must *not* be flattened into the canonical form;
* **voices** — a short character card (register, speech habits) so a sardonic
  character does not become polite in chapter 9.

``scan`` proposes candidates from the source text; a human or the agent fills
in the Persian. ``check`` is the deterministic gate that later verifies the
translation actually honoured them.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import bookir as ir
import falint
import runstate

# The store and the candidate scan live in `glossentry`; everything here is the
# half that makes a book honour them. Re-exported because `gl.make_entry`,
# `gl.canonical` and `gl.scan` are how every caller and every test reach them —
# a name a test imports is public whatever it looks like inside the file.
from glossentry import (  # noqa: F401
    GLOBAL_TOP_N,
    SCHEMA,
    _CONTRACTION,
    _NAME_RUN,
    _POSSESSIVE,
    _SENTENCE_START,
    _STOPWORDS,
    _guess_category,
    _strip_possessive,
    _trim_stopwords,
    alias_accepted,
    alias_map,
    allocate_ids,
    canonical,
    count_frequencies,
    load,
    make_entry,
    new_glossary,
    save,
    scan,
    surface_forms,
)



# --------------------------------------------------------------------------- #
# Per-chunk term table
# --------------------------------------------------------------------------- #

def entries_for_text(glossary: dict[str, Any], text: str, *,
                     top_n: int = GLOBAL_TOP_N) -> list[dict[str, Any]]:
    """Entries mentioned in ``text``, plus the most frequent names book-wide."""
    hit: list[dict[str, Any]] = []
    hit_ids: set[str] = set()
    for entry in glossary["entries"]:
        for form in surface_forms(entry):
            if re.search(rf"\b{re.escape(form)}\b", text):
                hit.append(entry)
                hit_ids.add(entry["id"])
                break

    frequent = sorted(glossary["entries"], key=lambda e: -int(e.get("frequency", 0)))[:top_n]
    for entry in frequent:
        if entry["id"] not in hit_ids and canonical(entry):
            hit.append(entry)
            hit_ids.add(entry["id"])
    return hit


def _alias_column(entry: dict[str, Any]) -> str:
    """``Lizzy → لیزی`` where the Persian is approved, the bare alias otherwise.

    The column used to list the English aliases alone, under the heading "keep
    distinct" — which tells a translator that "Lizzy" must not become "Elizabeth"
    while never saying what Persian "Lizzy" should be. Left to invent one, each
    parallel chunk invents a different one, and the drift check then rejects all
    of them.
    """
    mapping = alias_map(entry)
    shown = [f"{alias} → {mapping[alias]}" if alias in mapping else alias
             for alias in entry.get("aliases", [])]
    return "، ".join(shown) or "—"


def render_term_table(entries: list[dict[str, Any]], policy: dict[str, Any],
                      *, block_ids: Iterable[str] = ()) -> str:
    """A compact Markdown table for injection into a translation prompt.

    ``block_ids`` are the blocks this chunk covers. The "Use" column then states
    the answer outright — the long form here, the short form there — instead of
    asking the translator to work out whether this is the entity's first
    appearance. It cannot know: it only sees its own chunk. Left to judgement,
    every parallel chunk decides "yes, first mention" and the original spelling
    is repeated throughout the book.
    """
    rows = [entry for entry in entries if canonical(entry)]
    if not rows:
        return ""
    # Under `first_per_chapter` a worksheet cannot be told the whole truth from
    # here: which chapter a block belongs to is a property of the book, and this
    # has only the chunk's block ids. So it asks the chunk that owns the
    # book-wide first mention, exactly as for `first_mention`, and the
    # enforcement pass adds the remaining chapters once every chunk is back —
    # which is the division of labour this module is built on anyway.
    parenthetical = parenthetical_policy(policy)
    here = set(block_ids)

    lines = [
        "| English | Aliases — use exactly these | Use exactly this |",
        "| --- | --- | --- |",
    ]
    for entry in sorted(rows, key=lambda e: -int(e.get("frequency", 0))):
        introduce = (
            parenthetical != "never"
            and bool(entry.get("first_form"))
            and (not here or entry.get("first_block_id") in here)
        )
        form = entry["first_form"] if introduce else canonical(entry)
        note = "  ← first mention, introduce it here" if introduce else ""
        lines.append(
            "| {source} | {aliases} | {form}{note} |".format(
                source=entry["source"],
                aliases=_alias_column(entry),
                form=form,
                note=note,
            )
        )
    return "\n".join(lines)


def voice_entry(glossary: dict[str, Any],
                voice: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """``(the entry this voice card is about, a problem)``.

    A card is keyed by a character name, and a chunk may well name that character
    only by a nickname — so matching the card's own spelling against the text is
    how the one chunk that most needs the card goes without it: the scene where
    "Lizzy" is being sardonic got no card, because the card says "Elizabeth
    Bennet".

    Resolved through the glossary instead: an explicit ``entry`` id if the card
    carries one, otherwise the entry whose English source matches. An ambiguous
    name — two characters sharing a surname — is **not** guessed. It is returned
    as a problem, because a card attached to the wrong character is worse than no
    card, and the fix is an `entry` id the file can state.
    """
    wanted = str(voice.get("entry") or "")
    if wanted:
        for entry in glossary.get("entries", []):
            if entry.get("id") == wanted:
                return entry, ""
        return None, (f"voice card for {voice.get('character', '?')!r} names entry "
                      f"{wanted!r}, which this glossary does not have")

    name = (voice.get("character") or "").strip()
    if not name:
        return None, "a voice card with no character name"
    matches = [entry for entry in glossary.get("entries", [])
               if (entry.get("source") or "").strip() == name]
    if len(matches) == 1:
        return matches[0], ""
    if not matches:
        # Not an error: a card may be about a character with no locked name.
        return None, ""
    return None, (f"voice card for {name!r} matches {len(matches)} glossary "
                  f"entries; add an `entry` id to say which character it is about")


def voice_problems(glossary: dict[str, Any]) -> list[str]:
    """Voice cards that cannot be resolved to one character, with what to do."""
    return [problem for problem in
            (voice_entry(glossary, voice)[1]
             for voice in glossary.get("voices", []))
            if problem]


def render_voice_cards(glossary: dict[str, Any], text: str) -> str:
    prose = prose_of(text)
    cards = []
    for voice in glossary.get("voices", []):
        name = voice.get("character", "")
        entry, _ = voice_entry(glossary, voice)
        # Every approved English form of the character, not only the card's own
        # spelling. An unresolved card falls back to its own name, which is what
        # it always did — a card is guidance, and losing it must not be fatal.
        forms = surface_forms(entry) if entry else ([name] if name else [])
        if any(form and re.search(rf"\b{re.escape(form)}\b", prose)
               for form in forms):
            cards.append(
                f"- **{name}** — register: {voice.get('register', '?')}; "
                f"{voice.get('persian_policy') or voice.get('speech_style', '')}"
            )
    return "\n".join(cards)


# --------------------------------------------------------------------------- #
# First-mention enforcement
# --------------------------------------------------------------------------- #

#: A parenthetical of two characters or more: "(Elizabeth Bennet)".
PARENTHETICAL = re.compile(r"\([^()]{2,}\)")

#: Characters that join a name to what is beside it. Letters and digits are
#: obvious; U+200C is Persian's zero-width non-joiner, which glues the parts of
#: one word together, so a name flanked by it is a fragment, not a mention.
#: U+200C, written as an escape because the character itself is
#: invisible in source and the next reader would not know it is there.
ZWNJ = "‌"
_WORD = re.compile(r"[^\W_]", re.UNICODE)


def _joined(text: str, index: int) -> bool:
    if not 0 <= index < len(text):
        return False
    character = text[index]
    return character == ZWNJ or bool(_WORD.match(character))


def standalone_spans(text: str, needle: str) -> list[tuple[int, int]]:
    """Where ``needle`` stands as its own word in ``text``.

    A plain ``str.replace`` is what makes glossary rewriting dangerous: a short
    name is a substring of longer words, and "«علی» inside «علیرضا»" is exactly
    the corruption that a global replace introduces and nobody reviews. The
    boundary test has to know about the zero-width non-joiner too, or every
    Persian compound reads as a match.
    """
    if not needle:
        return []
    spans: list[tuple[int, int]] = []
    start = text.find(needle)
    while start != -1:
        end = start + len(needle)
        if not _joined(text, start - 1) and not _joined(text, end):
            spans.append((start, end))
        start = text.find(needle, start + 1)
    return spans


#: The values ``policy.original_parenthetical`` may take. They place the
#: original spelling in different numbers of places, so they are not
#: interchangeable and an unknown one cannot be guessed.
PARENTHETICAL_POLICIES = ("first_mention", "first_per_chapter", "never")


def parenthetical_policy(policy: dict[str, Any]) -> str:
    """The validated ``original_parenthetical`` value.

    An unrecognised value is refused rather than quietly read as
    ``first_mention``. Silently falling back is the worst of the three
    behaviours: it rewrites the book to a policy nobody chose, and the gate then
    agrees with it, so there is nothing left to notice the mistake.
    """
    value = (policy or {}).get("original_parenthetical", "first_mention")
    if value not in PARENTHETICAL_POLICIES:
        raise ValueError(
            f"unknown original_parenthetical policy {value!r}; expected one of "
            f"{', '.join(PARENTHETICAL_POLICIES)}"
        )
    return value


def chapters_by_block(book: dict[str, Any]) -> dict[str, str]:
    """Block id -> the id of the heading that opens its chapter.

    :func:`bookir.chapter_key` is what this project means by "a new chapter
    starts here", and the same answer has to serve the enforcement pass and the
    gate or they will disagree about how many introductions a book should have.
    Whatever precedes the first heading is a section of its own, keyed
    ``front``: a name introduced in a preface is introduced again in chapter one,
    which is what a reader who skipped the preface needs.
    """
    chapters: dict[str, str] = {}
    current = "front"
    for block in book.get("blocks", []):
        if ir.chapter_key(block):
            current = block["id"]
        chapters[block["id"]] = current
    return chapters


def mentioned_in_prose(target: str, form: str) -> bool:
    """True when ``form`` stands alone somewhere a rewrite is allowed to touch.

    The gate and the rewrite must mean the same thing by "this names her". A
    name that occurs only inside a verbatim span or a URL is not a place an
    introduction can go, so it must not be a place the gate demands one.
    """
    if not form:
        return False
    for span in ir.parse_markup(target):
        if span["verbatim"] or span["footnote"]:
            continue
        masked, _keep = falint.mask_literals(span["text"])
        if standalone_spans(masked, form):
            return True
    return False


def _flatten_in_prose(target: str, first_form: str,
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


def _introduce_in_prose(target: str, later_form: str,
                        first_form: str) -> str | None:
    """Expand the first standalone prose occurrence, or ``None`` if there is none.

    Eligibility is decided by the project's own span parser, not by a second
    one: emphasis may carry the introduction, verbatim spans and footnote tokens
    may not, and the markup itself is never touched because it is never in the
    text a rule sees. Literals inside a span — URLs, emails, identifiers — are
    masked the way the typography pass masks them, because a name sitting after
    a ``/`` satisfies every word-boundary test and a URL with a parenthetical
    spliced into it is a dead link.

    A name whose words fall in two different styled spans is left alone:
    placing it would mean rewriting the emphasis.
    """
    spans = ir.parse_markup(target)
    for span in spans:
        if span["verbatim"] or span["footnote"]:
            continue
        masked, keep = falint.mask_literals(span["text"])
        found = standalone_spans(masked, later_form)
        if not found:
            continue
        start, end = found[0]
        span["text"] = falint.unmask_literals(
            masked[:start] + first_form + masked[end:], keep
        )
        return ir.render_spans(spans)
    return None


def prose_of(text: str) -> str:
    """``text`` with markup, verbatim spans and protected regions blanked out.

    One resolver, used by placement, by the drift check and by the worksheet
    instruction — because three opinions about "does this block mention the name"
    is how the enforcement pass and the gate came to disagree for ever. With
    `علی` inside backticks, or inside `https://example.com/علی`, or inside
    `علیرضا`, `introduction_owner` said the pinned paragraph was eligible and
    `_place_once` moved on to the next one; QA then demanded the pinned paragraph,
    so three identical passes left the same error and no number of re-runs
    converged.

    Same length as the input, because callers map positions back onto the
    original. A verbatim span keeps its width and loses its content: it is an
    example of the notation, never a mention.
    """
    out: list[str] = []
    for span in ir.parse_markup(text or ""):
        if span.get("footnote"):
            continue
        body = span.get("text") or ""
        out.append(" " * len(body) if span.get("verbatim")
                   else falint.blank_protected(body))
    return "".join(out)


def owed_forms(entry: dict[str, Any], source: str, *,
               distinct: bool = True) -> tuple[list[str], list[str]]:
    """``(the English forms this text really uses, the Persian it therefore owes)``.

    Longest-first and non-overlapping, which is the whole point. "Elizabeth
    Bennet arrived." contains the full name *and* the alias "Elizabeth" inside it,
    so the old union accepted the block on «الیزابت آمد» alone — the surname
    dropped, the check satisfied by a form the source never used on its own.
    Consuming the longest match first leaves nothing for the inner alias to claim.
    """
    prose = prose_of(source)
    consumed: list[tuple[int, int]] = []
    used: list[str] = []
    mapping = alias_map(entry)

    for form in sorted(surface_forms(entry), key=len, reverse=True):
        if not form:
            continue
        for match in re.finditer(rf"\b{re.escape(form)}\b", prose):
            start, end = match.span()
            if any(start < taken_end and taken_start < end
                   for taken_start, taken_end in consumed):
                continue
            consumed.append((start, end))
            used.append(form)

    accepted: list[str] = []
    for form in used:
        if form == entry.get("source"):
            accepted.append(canonical(entry))
            if not distinct:
                accepted += alias_accepted(entry)
        elif form in mapping:
            accepted.append(mapping[form])
            if not distinct:
                accepted.append(canonical(entry))
        else:
            # An alias with no pairing — an older glossary's flat list. Which form
            # it owes is unknown, so every approved form is accepted: guessing
            # strictly would reject a faithful translation on evidence the file
            # does not contain.
            accepted.append(canonical(entry))
            accepted += alias_accepted(entry)
    return sorted(set(used), key=len, reverse=True), accepted


def introduction_owner(entry: dict[str, Any],
                       blocks: list[dict[str, Any]]) -> str:
    """Which block should carry this name's original spelling. One answer, asked
    by the enforcement pass and by the QA gate.

    The scan pins ``first_block_id`` to where the *entity* first appears in the
    **source** — which may be a block whose Persian is a nickname. The
    parenthetical attaches to the canonical form, so it cannot go there, and
    :func:`_place_once` has always fallen back to the first block that actually
    names her. That fallback is right; the defect was that the gate did not know
    about it.

    With "Lizzy/لیزی" in block one and "Elizabeth Bennet/الیزابت بنت" in block two,
    enforcement introduced the name in block two and QA demanded block one, so
    three identical passes left the same error and no number of re-runs converged.
    The rule is now stated once, here: **the pinned block if it is eligible,
    otherwise the first eligible one** — and the nickname is never expanded to
    make a block eligible.
    """
    later_form = canonical(entry)
    if not later_form:
        return ""
    # `standalone_spans` over `prose_of`, not `in`. A substring test called a
    # paragraph eligible when its only «علی» was inside backticks, inside a URL or
    # inside «علیرضا» — and `_place_once`, which is careful, went to the next
    # paragraph. The gate then demanded the pinned one for ever.
    eligible = [block for block in blocks
                if standalone_spans(prose_of(block.get("target") or ""), later_form)]
    pinned = entry.get("first_block_id") or ""
    if any(block["id"] == pinned for block in eligible):
        return pinned
    return eligible[0]["id"] if eligible else ""


def _place_once(group: list[dict[str, Any]], owner_id: str, later_form: str,
                first_form: str) -> dict[str, Any] | None:
    """Introduce the name once inside ``group``, the owning block first.

    Falling back to the first block of the group that actually names her keeps a
    book usable when the owning block was cut or never translated. Returns the
    block it landed in, or ``None`` when the name stands alone nowhere here.
    """
    pinned = next((block for block in group if block["id"] == owner_id), None)
    for block in ([pinned] if pinned else []) + group:
        # The cheap test first: parsing every block's markup for every entry is
        # the one place this pass could get expensive.
        if later_form not in (block.get("target") or ""):
            continue
        rewritten = _introduce_in_prose(block["target"], later_form, first_form)
        if rewritten is not None:
            block["target"] = rewritten
            return block
    return None


def enforce_first_mentions(glossary: dict[str, Any],
                           book: dict[str, Any]) -> dict[str, Any]:
    """Give each locked name its original spelling once, where it belongs.

    Chunks are translated in parallel by agents that cannot see one another, so
    "is this the first mention?" is a question none of them can answer. Every
    one of them answers yes, and the finished book repeats
    «الیزابت بنت (Elizabeth Bennet)» in thirty places. The worksheet asks the
    owning chunk to introduce the name, but asking is not a guarantee — this is
    the pass that makes it true regardless of what came back.

    It is deliberately mechanical and idempotent: flatten every introduction
    down to the later form, then re-introduce exactly one, at the first
    standalone occurrence inside the block the glossary scan already chose. Run
    it twice and the second run changes nothing.

    ``first_per_chapter`` changes only how many places "once" means: the same
    placement runs inside every chapter that names her, because a reader who
    opens at chapter nine never saw chapter one's parenthetical. ``never``
    flattens and places nothing.

    Aliases are left alone. When a book gives a character a nickname with its
    own spelling, that is a translation decision, not a drift to normalise —
    ``policy.keep_aliases_distinct`` says so explicitly.
    """
    policy = parenthetical_policy(glossary.get("policy") or {})
    report: dict[str, Any] = {"policy": policy, "introduced": {}, "flattened": 0,
                              "unplaceable": [], "skipped": 0}

    blocks = [b for b in ir.iter_text_blocks(book)]

    if policy == "first_per_chapter":
        chapters = chapters_by_block(book)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for block in blocks:
            grouped.setdefault(chapters.get(block["id"], "front"), []).append(block)
        # Insertion order is reading order, so the first chapter is placed first
        # and `introduced` names the earliest block for each entry.
        groups = list(grouped.values())
    else:
        groups = [blocks]

    for entry in glossary.get("entries", []):
        first_form = (entry.get("first_form") or "").strip()
        later_form = canonical(entry)
        if not entry.get("locked") or not first_form or not later_form:
            report["skipped"] += 1
            continue
        if first_form == later_form or not PARENTHETICAL.search(first_form):
            # No parenthetical policy for this name; nothing to place.
            report["skipped"] += 1
            continue

        # 1. Flatten. The long form carries its parenthetical with it, so the
        #    replace needs no boundary test — but it still only runs on prose.
        for block in blocks:
            target = block.get("target") or ""
            if first_form not in target:
                continue
            block["target"], replaced = _flatten_in_prose(
                target, first_form, later_form)
            report["flattened"] += replaced

        if policy == "never":
            continue

        # 2. Re-introduce once per group: the whole book, or each chapter.
        key = entry.get("id") or entry.get("source")
        owner_id = entry.get("first_block_id") or ""
        placed = False
        for group in groups:
            landed = _place_once(group, owner_id, later_form, first_form)
            if landed is not None:
                placed = True
                report["introduced"].setdefault(key, landed["id"])
        if not placed:
            report["unplaceable"].append(key)

    return report


def check(glossary: dict[str, Any], book: dict[str, Any]) -> list[dict[str, Any]]:
    """Find places where a translated block ignored a locked name.

    Reports a violation when the source block mentions a locked entity and the
    translated block contains neither its canonical Persian form nor any
    alias's own Persian form — i.e. the name silently drifted.
    """
    violations: list[dict[str, Any]] = []
    locked = [
        entry for entry in glossary["entries"]
        if entry.get("locked") and canonical(entry)
    ]
    if not locked:
        return violations

    for block in ir.iter_text_blocks(book):
        target = (block.get("target") or "").strip()
        if not target:
            continue
        source = block.get("text") or ""
        target_plain = prose_of(target)
        distinct_policy = bool((glossary.get("policy") or {})
                               .get("keep_aliases_distinct", True))
        for entry in locked:
            # One resolver, longest-first and non-overlapping. The union of
            # full-name and inner-alias matches let "Elizabeth Bennet arrived"
            # pass on «الیزابت آمد» alone, because "Elizabeth" is also an alias
            # *inside* the full name — the surname silently dropped, on the very
            # check that exists to catch a dropped name.
            forms, accepted = owed_forms(entry, source, distinct=distinct_policy)
            if not forms:
                continue
            # Boundary-aware, never substring. «علی» sits inside «علیرضا», a
            # different person, and inside «علی‌اکبر», a different name again —
            # U+200C glues word parts, so a form flanked by one is a fragment of
            # a longer word, not an occurrence. A substring match there is the
            # worst failure this gate has: it certifies the wrong man as the
            # right one, on the very check that exists to catch a drifted name.
            #
            # An approved alias target still passes, because it is matched the
            # same way and on its own. So does the first-mention parenthetical
            # «علی (Ali)» — the canonical form stands alone in front of it, which
            # is why there is no separate rule for the untranslated spelling.
            if any(form and standalone_spans(target_plain, form)
                   for form in accepted):
                continue
            violations.append({
                "block": block["id"],
                "entry": entry["id"],
                "source_forms": forms,
                # What *this* block owes, which is not always the canonical form.
                # Reporting the canonical where the source used a nickname is how
                # a reader gets talked into expanding it — the message itself was
                # asking for the drift the check exists to prevent.
                "expected": accepted[0] if accepted else canonical(entry),
                "accepted": sorted(set(accepted)),
                "excerpt": target_plain[:120],
            })
    return violations


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    ir.use_utf8_stdio()
    parser = argparse.ArgumentParser(prog="revayat-novel glossary")
    sub = parser.add_subparsers(dest="action", required=True)

    p_scan = sub.add_parser("scan", help="propose candidate names from book.json")
    p_scan.add_argument("--book", required=True)
    p_scan.add_argument("--out", required=True, help="glossary.json to create or refresh")
    p_scan.add_argument("--min-count", type=int, default=3)
    p_scan.add_argument("--limit", type=int, default=400)

    p_terms = sub.add_parser("terms-for", help="term table for a chunk worksheet")
    p_terms.add_argument("--glossary", required=True)
    p_terms.add_argument("--chunk", required=True)

    p_count = sub.add_parser("count", help="refresh frequencies")
    p_count.add_argument("--glossary", required=True)
    p_count.add_argument("--book", required=True)

    p_check = sub.add_parser("check", help="verify locked names survived translation")
    p_check.add_argument("--glossary", required=True)
    p_check.add_argument("--book", required=True)

    args = parser.parse_args(argv)

    if args.action == "scan":
        book = ir.load_book(args.book)
        out = Path(args.out)
        glossary = load(out)
        known = {e["source"] for e in glossary["entries"]}
        proposals = [
            entry for entry in scan(book, minimum=args.min_count, limit=args.limit)
            if entry["source"] not in known
        ]
        # Renumber so ids stay unique alongside anything already decided.
        allocate_ids(glossary["entries"], proposals)
        glossary["entries"].extend(proposals)
        count_frequencies(glossary, book)
        save(glossary, out)
        # Keyed on the source side of the book, not the file: merge rewrites
        # book.json in place, and a file hash would report the glossary stale
        # against a translation rather than against a re-extraction.
        runstate.RunState(out.parent).record("glossary", {
            "book": runstate.source_digest(book),
            "min_count": str(args.min_count),
        }, {"glossary": runstate.file_hash(out)})
        print(json.dumps({
            "glossary": str(out),
            "existing": len(known),
            "proposed": len(proposals),
            "needs_persian": [e["source"] for e in glossary["entries"] if not canonical(e)][:60],
        }, ensure_ascii=False, indent=1))
        return 0

    if args.action == "terms-for":
        glossary = load(Path(args.glossary))
        text = Path(args.chunk).read_text(encoding="utf-8")
        table = render_term_table(entries_for_text(glossary, text), glossary["policy"])
        cards = render_voice_cards(glossary, text)
        if table:
            print(table)
        if cards:
            print("\n**Character voices in this chunk**\n" + cards)
        return 0

    if args.action == "count":
        path = Path(args.glossary)
        glossary = load(path)
        count_frequencies(glossary, ir.load_book(args.book))
        save(glossary, path)
        print(json.dumps({"entries": len(glossary["entries"])}, ensure_ascii=False))
        return 0

    glossary = load(Path(args.glossary))
    violations = check(glossary, ir.load_book(args.book))
    print(json.dumps({"violations": violations, "count": len(violations)},
                     ensure_ascii=False, indent=1))
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
