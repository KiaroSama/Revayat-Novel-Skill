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
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import bookir as ir

SCHEMA = "revayat/glossary@1"

#: How many book-wide frequent names ride along in every chunk's term table.
GLOBAL_TOP_N = 25

#: Words that start a sentence or are otherwise capitalised without being names.
_STOPWORDS = frozenset("""
a an and are as at be been but by for from had has have he her hers him his how
i if in is it its me my no nor not of on or our ours she so than that the their
theirs them then there these they this those to too us was we were what when
where which who whom why will with you your yours
after again against all also am among any because before being below between
both did do does doing down during each few further here into itself more most
other over same some such through under until up very while
mr mrs miss ms dr sir madam lord lady oh ah yes well now just only even still
chapter part book volume prologue epilogue contents preface introduction
january february march april may june july august september october november
december monday tuesday wednesday thursday friday saturday sunday
""".split())

#: A capitalised word, or a run of them ("Elizabeth Bennet", "New York").
_NAME_RUN = re.compile(r"\b([A-Z][a-z'’-]{1,}(?:\s+(?:of|de|van|von|the)\s+)?"
                       r"(?:\s+[A-Z][a-z'’-]{1,})*)\b")
_SENTENCE_START = re.compile(r"(?:^|[.!?…]\s+|[«\"'“]\s*)$")
#: "I'm", "I've", "He'll" — a contraction, not a name. Matched on the whole
#: candidate so "O'Brien" and "D'Arcy" are untouched.
_CONTRACTION = re.compile(r"^[A-Za-z]{1,3}['’](?:m|s|d|t|ll|ve|re|em)$", re.I)
#: Trailing genitive: "Alice's" is the same entity as "Alice".
_POSSESSIVE = re.compile(r"['’]s$|s['’]$")


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #

def new_glossary() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "policy": {
            # Where the original spelling appears beside the Persian name.
            # "first_mention" | "first_per_chapter" | "never"
            "original_parenthetical": "first_mention",
            "lock_canonical": True,
            "keep_aliases_distinct": True,
        },
        "entries": [],
        "voices": [],
    }


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return new_glossary()
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("policy", new_glossary()["policy"])
    data.setdefault("entries", [])
    data.setdefault("voices", [])
    return data


def save(glossary: dict[str, Any], path: Path) -> None:
    ir.write_text(path, json.dumps(glossary, ensure_ascii=False, indent=1) + "\n")


def make_entry(index: int, source: str, *, category: str = "unknown",
               frequency: int = 0, aliases: Iterable[str] = ()) -> dict[str, Any]:
    return {
        "id": f"g{index:04d}",
        "source": source,
        "target": "",          # filled in by the translator agent
        "first_form": "",      # e.g. "الیزابت بنت (Elizabeth Bennet)"
        "later_form": "",      # e.g. "الیزابت بنت"
        "category": category,  # person | place | organisation | thing | term
        "aliases": sorted(set(aliases)),
        "gender": "unknown",
        "locked": False,
        "frequency": frequency,
        # The block this entity first appears in. Chunks are translated in
        # parallel by agents that cannot see each other, so "is this the first
        # mention?" cannot be left to each one's judgement — every chunk would
        # answer yes and the book would repeat the original spelling forever.
        # Deciding it once, here, makes it deterministic.
        "first_block_id": "",
        "notes": "",
    }


def surface_forms(entry: dict[str, Any]) -> list[str]:
    return [form for form in [entry.get("source"), *entry.get("aliases", [])] if form]


def canonical(entry: dict[str, Any]) -> str:
    return (entry.get("later_form") or entry.get("target") or "").strip()


# --------------------------------------------------------------------------- #
# Candidate scan
# --------------------------------------------------------------------------- #

def _strip_possessive(name: str) -> str:
    """``Alice's`` -> ``Alice``; only the final word can carry the genitive."""
    words = name.split()
    if not words:
        return name
    words[-1] = _POSSESSIVE.sub("", words[-1])
    return " ".join(word for word in words if word)


def _trim_stopwords(name: str) -> str:
    """Drop ordinary words that a capitalised run swept up at either end.

    A sentence opening with "Then Elizabeth Bennet arrived" capitalises *Then*,
    so the run reads as one three-word name. Rejecting only runs made entirely
    of stopwords leaves "Then Elizabeth Bennet" competing with the real entity
    and splitting its frequency in two.
    """
    words = name.split()
    while words and words[0].lower() in _STOPWORDS:
        words.pop(0)
    while words and words[-1].lower() in _STOPWORDS:
        words.pop()
    return " ".join(words)


def _guess_category(name: str, contexts: list[str]) -> str:
    blob = " ".join(contexts).lower()
    if re.search(rf"\b(?:in|at|to|from|near|towards?)\s+{re.escape(name.lower())}\b", blob):
        return "place"
    if re.search(rf"{re.escape(name.lower())}\s+(?:said|asked|replied|whispered|cried)", blob):
        return "person"
    if re.search(rf"\b(?:mr|mrs|miss|ms|dr|lord|lady|sir)\.?\s+{re.escape(name.lower())}", blob):
        return "person"
    return "unknown"


def scan(book: dict[str, Any], *, minimum: int = 3, limit: int = 400) -> list[dict[str, Any]]:
    """Propose glossary candidates from the source text.

    Capitalised runs are counted, but a run that only ever appears at the start
    of a sentence is discarded — that is how ordinary words masquerade as names.
    """
    counts: Counter[str] = Counter()
    mid_sentence: Counter[str] = Counter()
    contexts: dict[str, list[str]] = {}
    first_block: dict[str, str] = {}

    for block in ir.iter_text_blocks(book):
        if block.get("type") == "heading":
            continue
        text = ir.plain_text(block.get("text") or "")
        for match in _NAME_RUN.finditer(text):
            name = _trim_stopwords(
                _strip_possessive(re.sub(r"\s+", " ", match.group(1)).strip())
            )
            if len(name) < 3:
                continue
            words = name.split()
            if all(word.lower() in _STOPWORDS for word in words):
                continue
            if len(words) == 1 and (name.lower() in _STOPWORDS or _CONTRACTION.match(name)):
                continue
            counts[name] += 1
            first_block.setdefault(name, block["id"])
            before = text[max(0, match.start() - 40):match.start()]
            # If a leading stopword was trimmed off, the *name* did not open the
            # sentence even though the matched run did — "Then Elizabeth Bennet"
            # is real evidence of a name used mid-sentence.
            trimmed_lead = not match.group(1).startswith(name)
            if trimmed_lead or not _SENTENCE_START.search(before):
                mid_sentence[name] += 1
            contexts.setdefault(name, [])
            if len(contexts[name]) < 5:
                contexts[name].append(text[max(0, match.start() - 60):match.end() + 60])

    # Keep names that occur somewhere other than a sentence opening.
    candidates = {
        name: total for name, total in counts.items()
        if total >= minimum and mid_sentence[name] >= max(1, total // 4)
    }

    # Fold "Elizabeth" into "Elizabeth Bennet" as an alias rather than letting
    # both compete as separate entities.
    multiword = sorted((n for n in candidates if " " in n), key=len, reverse=True)
    aliases: dict[str, set[str]] = {name: set() for name in multiword}
    absorbed: set[str] = set()
    for short in list(candidates):
        if " " in short:
            continue
        for long in multiword:
            if re.search(rf"\b{re.escape(short)}\b", long):
                aliases[long].add(short)
                absorbed.add(short)
                break

    proposals: list[dict[str, Any]] = []
    ordered = sorted(
        (n for n in candidates if n not in absorbed),
        key=lambda n: (-candidates[n], n),
    )[:limit]
    for index, name in enumerate(ordered, start=1):
        entry = make_entry(
            index, name,
            category=_guess_category(name, contexts.get(name, [])),
            frequency=candidates[name] + sum(candidates.get(a, 0) for a in aliases.get(name, ())),
            aliases=aliases.get(name, ()),
        )
        # Earliest mention of the entity or any of its aliases. Block ids are
        # zero-padded and allocated in reading order, so min() is the earliest.
        seen_at = [first_block[form] for form in (name, *aliases.get(name, ()))
                   if form in first_block]
        entry["first_block_id"] = min(seen_at) if seen_at else ""
        entry["notes"] = ""
        proposals.append(entry)
    return proposals


def count_frequencies(glossary: dict[str, Any], book: dict[str, Any]) -> None:
    """Refresh each entry's frequency across the whole source text."""
    corpus = "\n".join(
        ir.plain_text(block.get("text") or "") for block in ir.iter_text_blocks(book)
    )
    for entry in glossary["entries"]:
        total = 0
        for form in surface_forms(entry):
            total += len(re.findall(rf"\b{re.escape(form)}\b", corpus))
        entry["frequency"] = total


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
    parenthetical = policy.get("original_parenthetical", "first_mention")
    here = set(block_ids)

    lines = [
        "| English | Aliases (keep distinct) | Use exactly this |",
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
                aliases="، ".join(entry.get("aliases", [])) or "—",
                form=form,
                note=note,
            )
        )
    return "\n".join(lines)


def render_voice_cards(glossary: dict[str, Any], text: str) -> str:
    cards = []
    for voice in glossary.get("voices", []):
        name = voice.get("character", "")
        if name and re.search(rf"\b{re.escape(name)}\b", text):
            cards.append(
                f"- **{name}** — register: {voice.get('register', '?')}; "
                f"{voice.get('persian_policy') or voice.get('speech_style', '')}"
            )
    return "\n".join(cards)


# --------------------------------------------------------------------------- #
# Compliance check
# --------------------------------------------------------------------------- #

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
        source = ir.plain_text(block.get("text") or "")
        target_plain = ir.plain_text(target)
        for entry in locked:
            forms = [f for f in surface_forms(entry)
                     if re.search(rf"\b{re.escape(f)}\b", source)]
            if not forms:
                continue
            accepted = [canonical(entry)]
            accepted += [a for a in entry.get("alias_targets", []) if a]
            if any(form and form in target_plain for form in accepted):
                continue
            # An untranslated original spelling is acceptable inside the
            # first-mention parenthetical, but not on its own.
            if entry["source"] in target_plain and canonical(entry) in target_plain:
                continue
            violations.append({
                "block": block["id"],
                "entry": entry["id"],
                "source_forms": forms,
                "expected": canonical(entry),
                "excerpt": target_plain[:120],
            })
    return violations


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    ir.use_utf8_stdio()
    parser = argparse.ArgumentParser(prog="revayat glossary")
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
        start = len(glossary["entries"])
        for offset, entry in enumerate(proposals, start=1):
            entry["id"] = f"g{start + offset:04d}"
        glossary["entries"].extend(proposals)
        count_frequencies(glossary, book)
        save(glossary, out)
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
