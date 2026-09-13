"""The glossary store, and the candidates a book proposes for it.

What a glossary *is* — entries, ids, aliases, the canonical form — and how `scan`
proposes names from the source. Deliberately separate from the half that makes a
book honour it: this module reads and writes a small JSON document and never
touches a translation.

The dependency points one way. `glossary` imports this and re-exports every name,
because `gl.make_entry` and `gl.canonical` are how every caller and every test
already reach them; nothing here imports `glossary`, so the two cannot cycle.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import bookir as ir

SCHEMA = "revayat-novel/glossary@1"

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
#:
#: A "word" here admits an internal capital, an apostrophe and a hyphen, because
#: real surnames have all three and the previous class — one capital then
#: `[a-z'’-]` — stopped at the second one. Measured: `McDonald` matched only
#: `Mc` and was dropped by the length guard; `Anne-Marie` matched `Anne-` and
#: then split into two competing candidates; `O'Brien`, `MacLeod`, `DeVere`,
#: `LaFontaine` and `Van der Berg` were invisible. Eight of ten real surname
#: shapes never reached the glossary, so no worksheet carried a locked spelling
#: for them — and the drift check cannot report a name it was never given.
#:
#: Loosening it is safe because the pattern is not the filter: a candidate still
#: has to clear `_STOPWORDS`, the length guard, `minimum` occurrences and — the
#: one that does the real work — the mid-sentence rule, which discards anything
#: that only ever opens a sentence.
#:
#: The hyphen branch requires a *following capital*, so `Anne-Marie` stays whole
#: while a dash between words cannot be swallowed and no candidate can end in
#: one. `{1,}` is gone, so a single capital matches; `len(name) < 3` in `scan`
#: still drops `A`, `I` and bare initials.
#: The particle group carries the word *after* the particle. It has to: the
#: group ends by consuming the space, so a trailing `(?:\s+WORD)*` can never
#: attach the next word and `Van der Berg` came back as `Van der` plus a
#: separate `Berg`. The original pattern had the same flaw and it was invisible
#: only because `der` was not in the alternation.
_NAME_WORD = r"[A-Z][A-Za-z'’]*(?:-[A-Z][A-Za-z'’]*)*"
_NAME_RUN = re.compile(
    rf"\b({_NAME_WORD}"
    rf"(?:\s+(?:of|de|van|von|der|den|la|le|du|the)\s+{_NAME_WORD})?"
    rf"(?:\s+{_NAME_WORD})*)\b")
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
            # How this book's *narration* sounds, in one or two sentences of
            # Persian: formal or plain, contemporary or period, spare or
            # ornamented. Empty by default, because an invented house style is
            # worse than none — a translator asked to match a voice nobody chose
            # picks one per chunk, which is the same defect as every chunk
            # deciding for itself where to introduce a name. Filled in, it is
            # rendered into every worksheet, so the twentieth chunk is told what
            # the first was told. The character cards in `voices` are a different
            # question: they are about who is speaking, this is about the prose
            # around them.
            "book_voice": "",
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
        # The aliases' own Persian, when the book uses a short form and the
        # translation should too. Without it the drift check demands the full
        # canonical name wherever the source says only "Ashcroft", which is
        # worse Persian and rejects a faithful translation.
        "alias_targets": [],
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


#: An entry id as :func:`make_entry` writes it.
_ENTRY_ID = re.compile(r"^g(\d+)$")


def allocate_ids(entries: list[dict[str, Any]],
                 proposals: list[dict[str, Any]]) -> None:
    """Number ``proposals`` so no id in ``entries`` is taken twice.

    How many entries there are is not the same question as which ids exist.
    Deleting a false candidate is the documented way to reject one, so
    ``g0001, g0003`` is the normal shape of a glossary somebody has worked on —
    and a next id derived from the *length* of the list hands the next candidate
    ``g0003``, which is already taken. An id is the only handle the worksheets,
    the enforcement pass and the QA findings have on an entry, so two entries
    sharing one means a locked decision can be looked up and the wrong entity
    found.

    A deleted candidate's number stays spent: reusing it would let a reference
    written down before the deletion resolve silently to a different name.
    """
    used = {str(entry.get("id") or "") for entry in entries}
    highest = max(
        (int(match.group(1)) for entry_id in used
         if (match := _ENTRY_ID.match(entry_id))),
        default=0,
    )
    for offset, proposal in enumerate(proposals, start=1):
        proposal["id"] = f"g{highest + offset:04d}"

    ids = [str(entry.get("id") or "") for entry in (*entries, *proposals)]
    duplicated = sorted({entry_id for entry_id in ids if ids.count(entry_id) > 1})
    if duplicated:
        raise ValueError(
            f"glossary ids are not unique: {duplicated}. Entries sharing an id "
            f"cannot be told apart by any later stage; give each one its own."
        )


def surface_forms(entry: dict[str, Any]) -> list[str]:
    return [form for form in [entry.get("source"), *entry.get("aliases", [])] if form]


def alias_map(entry: dict[str, Any]) -> dict[str, str]:
    """``source alias -> approved Persian``, however the entry spells it.

    Two parallel lists could not express this. ``aliases`` is stored
    ``sorted(set(...))`` and ``alias_targets`` was a flat list beside it, so
    position carried no meaning and nothing in the file said *which* Persian
    belonged to "Lizzy". The worksheet could therefore only print "keep these
    distinct" without saying what to keep them as, and the drift check had no way
    to tell a preserved nickname from an expanded one.

    A mapping is accepted now. A bare list is still read — an older glossary
    stays valid — but it can only contribute *accepted* forms, not a pairing, and
    that is exactly the limitation that motivated the change.
    """
    targets = entry.get("alias_targets")
    if isinstance(targets, dict):
        return {str(k): str(v) for k, v in targets.items() if k and v}
    return {}


def alias_accepted(entry: dict[str, Any]) -> list[str]:
    """Every approved Persian form for this entry's aliases, mapped or not."""
    targets = entry.get("alias_targets")
    if isinstance(targets, dict):
        return [str(v) for v in targets.values() if v]
    return [str(form) for form in (targets or ()) if form]


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

