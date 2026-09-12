"""Persian typography — the lint and fix pass over translated text.

Persian written by a translation model is usually *correct* and typographically
wrong: an Arabic yeh instead of a Persian one, a Latin comma in a Persian
sentence, a space where a zero-width non-joiner belongs. None of that is a
translation problem, so none of it should cost a re-translation — it is
mechanical, and this module does it mechanically.

Two safety properties matter more than coverage:

* **Markup cannot be damaged.** Text is decomposed with :func:`bookir.parse_markup`
  first, so emphasis markers, verbatim spans and footnote tokens are carried
  through untouched — no regex ever sees them.
* **Rules are context-gated.** A comma only becomes ``،`` when a Persian letter
  precedes it, so ``e.g., Smith`` inside the same paragraph is left alone. A
  ZWNJ is only inserted for suffix patterns that are unambiguous in Persian
  orthography; ZWNJ carries meaning, so a blind pass would corrupt real words.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import bookir as ir
import runstate

ZWNJ = "‌"

# --- Character-level normalisation -------------------------------------------
_CHAR_MAP = {
    "ي": "ی",  # Arabic yeh    -> Persian yeh
    "ى": "ی",  # alef maksura  -> Persian yeh
    "ك": "ک",  # Arabic kaf    -> Persian keheh
    "ـ": "",        # tatweel/kashida: decorative elongation, never wanted
    " ": " ",       # no-break space
    "​": "",        # zero-width space (not ZWNJ — that one is meaningful)
    "﻿": "",        # stray BOM
}
_ARABIC_INDIC = {chr(0x0660 + n): chr(0x06F0 + n) for n in range(10)}
_LATIN_TO_PERSIAN_DIGIT = {str(n): chr(0x06F0 + n) for n in range(10)}

PERSIAN_LETTER = r"ء-غف-يٮ-ۓۺ-ۿ"
_PERSIAN_CHAR = re.compile(f"[{PERSIAN_LETTER}]")

#: Regions inside prose that must survive untouched.
_PROTECTED = re.compile(
    r"""(?xi)
    (?:https?://|www\.)\S+
    | [\w.+-]+@[\w-]+\.[\w.-]+
    | \b[0-9]{1,4}(?:[.:/-][0-9]{1,4})+\b    # dates, versions, ISBN fragments
    | \b[A-Za-z][\w.'’-]*\b                  # any Latin word
    """
)

# --- Suffixes that take a ZWNJ in correct Persian orthography ----------------
#: Only affixes with no standalone reading of their own. A ZWNJ carries meaning,
#: so inserting one is a rewrite of the word, and a rule that cannot tell an
#: affix from a word corrupts valid Persian silently.
#:
#: «تر» is the absentee: bare, it is also the adjective *wet*, so «موی تر» (wet
#: hair) and «بزرگ تر» (bigger) are the same shape and only the part of speech
#: of the preceding word separates them — which this pass cannot know. It goes
#: to :func:`lint_text` instead, reported for a human to decide. «تری» and
#: «ترین» stay here: neither stands alone after a noun.
_ZWNJ_SUFFIXES = (
    "ها", "های", "هایی", "هایم", "هایت", "هایش", "هایمان", "هایتان", "هایشان",
    "تری", "ترین",
)
_ZWNJ_PREFIXES = ("می", "نمی")

_SUFFIX_SPACE = re.compile(
    rf"([{PERSIAN_LETTER}]{{2,}}) +({'|'.join(_ZWNJ_SUFFIXES)})\b"
)
#: «می» is the verbal prefix *and* the noun *wine*, so the word after it decides:
#: joined only when that word can be a finite verb. Every finite می-form ends in
#: a personal ending (ـم، ـی، ـد، ـیم، ـید، ـند) or, in the third-person past,
#: in the past stem's own ـد/ـت — so its last letter is always one of م/ی/د/ت.
#: «ناب» in «می ناب» is not, and nor is any other adjective the noun takes.
#: ponytail: a last-letter test, not a verb lexicon. It still joins «می» before
#: a non-verb that happens to end in one of those letters; a stem list is the
#: upgrade if that ever shows up in a real book.
_PREFIX_SPACE = re.compile(
    rf"\b({'|'.join(_ZWNJ_PREFIXES)}) +([{PERSIAN_LETTER}]+[میدت])\b"
)

# --- Punctuation --------------------------------------------------------------
_PERSIAN_PUNCT = "،؛؟!:.»…"
_COMMA = re.compile(rf"(?<=[{PERSIAN_LETTER}۰-۹]) *,")
_SEMICOLON = re.compile(rf"(?<=[{PERSIAN_LETTER}۰-۹]) *;")
_QUESTION = re.compile(rf"(?<=[{PERSIAN_LETTER}۰-۹]) *\?")
_ELLIPSIS = re.compile(r"\.{3,}")
_SPACE_BEFORE_PUNCT = re.compile(rf"[ \t]+([{re.escape(_PERSIAN_PUNCT)}])")
_MISSING_SPACE_AFTER = re.compile(rf"([،؛؟!:])(?=[{PERSIAN_LETTER}])")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_QUOTE_PAIR = re.compile(r"[\"“](.+?)[\"”]", re.S)
_GUILLEMET_INNER = re.compile(r"«\s+|\s+»")
#: A standalone run of ASCII digits. Explicitly [0-9], never \d: Python's
#: \d also matches Persian digits, so a second fix pass would try to convert
#: already-converted text and fail. The pass has to be idempotent.
_DIGIT_RUN = re.compile(r"(?<![\w.:/-])[0-9]+(?![\w.:/-])")


class Options:
    """Which transformations run. Everything defaults to publication style."""

    def __init__(
        self,
        *,
        digits: str = "persian",     # persian | keep
        quotes: bool = True,
        ellipsis: bool = True,
        zwnj: bool = True,
        punctuation: bool = True,
    ) -> None:
        self.digits = digits
        self.quotes = quotes
        self.ellipsis = ellipsis
        self.zwnj = zwnj
        self.punctuation = punctuation


# --------------------------------------------------------------------------- #
# Fixing
# --------------------------------------------------------------------------- #

def mask_literals(text: str) -> tuple[str, list[str]]:
    """Replace protected regions with sentinels that no rule can match.

    Public because the typography pass is not the only rewrite that must stay
    out of a URL: the glossary's first-mention pass inserts into prose too, and a
    Persian name after a ``/`` passes every word-boundary test there is. One
    definition of "this is a literal, not a word" for both.
    """
    keep: list[str] = []

    def swap(match: re.Match[str]) -> str:
        keep.append(match.group(0))
        return f"\x00{len(keep) - 1}\x00"

    return _PROTECTED.sub(swap, text), keep


def unmask_literals(text: str, keep: list[str]) -> str:
    """Put back exactly what :func:`mask_literals` took out."""
    return re.sub(r"\x00(\d+)\x00", lambda m: keep[int(m.group(1))], text)


def fix_prose(text: str, options: Options) -> str:
    """Apply Persian typography to one prose span.

    Masking is the *first* thing that happens, before any rule at all. An
    Arabic kaf or an Arabic-Indic digit inside a URL is one of its bytes, not
    its typography: normalising it yields a different and usually dead address,
    and a quote pair inside a query string is punctuation the far end parses.
    Protecting a region only after the character, digit and quote passes have
    run protects text those passes already rewrote — so the order here is the
    guarantee, and a reader must never have to backtick a URL by hand to get it.
    """
    masked, keep = mask_literals(text)

    for source, target in _CHAR_MAP.items():
        masked = masked.replace(source, target)
    for source, target in _ARABIC_INDIC.items():
        masked = masked.replace(source, target)

    if options.quotes:
        masked = _QUOTE_PAIR.sub(lambda m: f"«{m.group(1)}»", masked)

    if options.ellipsis:
        masked = _ELLIPSIS.sub("…", masked)

    if options.punctuation:
        masked = _COMMA.sub("،", masked)
        masked = _SEMICOLON.sub("؛", masked)
        masked = _QUESTION.sub("؟", masked)
        masked = _SPACE_BEFORE_PUNCT.sub(r"\1", masked)
        masked = _MISSING_SPACE_AFTER.sub(r"\1 ", masked)
        masked = _GUILLEMET_INNER.sub(lambda m: m.group(0).strip(), masked)

    if options.zwnj:
        # Repeat: "کتاب ها ی" style chains need more than one pass to settle.
        for _ in range(3):
            replaced = _SUFFIX_SPACE.sub(rf"\1{ZWNJ}\2", masked)
            replaced = _PREFIX_SPACE.sub(rf"\1{ZWNJ}\2", replaced)
            if replaced == masked:
                break
            masked = replaced

    if options.digits == "persian":
        masked = _DIGIT_RUN.sub(
            lambda m: "".join(_LATIN_TO_PERSIAN_DIGIT[d] for d in m.group(0)), masked
        )

    masked = _MULTI_SPACE.sub(" ", masked)
    return unmask_literals(masked, keep)


#: Quote characters a pair can be built from. ASCII ``"`` is both halves, so
#: which one a given mark is depends only on its position in the sequence.
_QUOTE_CHARS = '"“”'


def _quote_positions(text: str) -> list[int]:
    """Where a convertible quote sits. A quote inside a literal is not one.

    ``?q="کتاب"`` in a query string is the address the far end parses, not
    punctuation a Persian publisher has an opinion about.
    """
    literals = [match.span() for match in _PROTECTED.finditer(text)]
    return [index for index, character in enumerate(text)
            if character in _QUOTE_CHARS
            and not any(start <= index < end for start, end in literals)]


def _pair_quotes(spans: list[dict[str, Any]]) -> None:
    """«…» for a quotation whose two halves lie in different spans.

    :func:`fix_prose` sees one span at a time, and that is deliberate — it is
    what keeps every regex away from the markup. But a quotation that wraps
    emphasis, ``"او **خوب** است."``, has its opening and closing quote in two
    different strings, so no per-span rule can pair them, and the commonest line
    in a novel keeps its Latin quotes.

    Pairing happens here instead, on the decomposed spans: no rule sees a marker,
    verbatim spans and footnote tokens are skipped, and a quote inside a
    protected literal is not a quote. Each replacement is one character for one,
    so every index stays valid whatever order they are applied in. An odd quote
    left over is unbalanced, which :func:`lint_text` reports and nothing here
    guesses at.
    """
    found = [(span, index)
             for span in spans if not (span["verbatim"] or span["footnote"])
             for index in _quote_positions(span["text"])]
    for (opening, open_at), (closing, close_at) in zip(found[::2], found[1::2]):
        if opening is closing:
            continue    # a pair inside one span: fix_prose's own rule has it
        for span, index, mark in ((opening, open_at, "«"), (closing, close_at, "»")):
            span["text"] = span["text"][:index] + mark + span["text"][index + 1:]


def fix_text(text: str, options: Options | None = None) -> str:
    """Fix a full marked-up string, leaving markup and verbatim spans alone."""
    if not text:
        return text
    options = options or Options()
    spans = ir.parse_markup(text)
    if options.quotes:
        _pair_quotes(spans)
    for span in spans:
        if span["verbatim"] or span["footnote"]:
            continue
        span["text"] = fix_prose(span["text"], options)
    return ir.render_spans(spans).strip()


# --------------------------------------------------------------------------- #
# Linting
# --------------------------------------------------------------------------- #

_LATIN_SENTENCE = re.compile(r"[A-Za-z][A-Za-z ,'’-]{25,}")
_DOUBLE_PUNCT = re.compile(r"([،؛؟!])\1+")
_ARABIC_LEFTOVER = re.compile(r"[يكىـ٠-٩]")
#: The join the fix pass deliberately refuses to make on its own; see
#: :data:`_ZWNJ_SUFFIXES`.
_BARE_COMPARATIVE = re.compile(rf"[{PERSIAN_LETTER}]{{2,}} +تر\b")


def lint_text(text: str) -> list[dict[str, str]]:
    """Problems a fix pass cannot decide on its own."""
    issues: list[dict[str, str]] = []
    plain = ir.plain_text(text or "")
    if not plain.strip():
        return issues

    def note(code: str, detail: str) -> None:
        issues.append({"code": code, "detail": detail[:160]})

    if _ARABIC_LEFTOVER.search(plain):
        note("arabic-forms", "Arabic yeh/kaf/tatweel or Arabic-Indic digits remain")

    opens, closes = plain.count("«"), plain.count("»")
    if opens != closes:
        note("guillemets", f"unbalanced Persian quotes: {opens} « vs {closes} »")

    if '"' in plain or "“" in plain or "”" in plain:
        note("latin-quotes", "Latin quotation marks in Persian prose")

    if _DOUBLE_PUNCT.search(plain):
        note("double-punctuation", "repeated punctuation mark")

    bare = _BARE_COMPARATIVE.search(plain)
    if bare:
        note("zwnj-comparative",
             f"«{bare.group(0)}»: a comparative needs the ZWNJ, «تر» meaning "
             f"wet must not have it — only a reader can tell which this is")

    persian, latin = ir.script_ratio(plain)
    match = _LATIN_SENTENCE.search(plain)
    if match and latin > 0.30:
        note("untranslated", f"long Latin passage: {match.group(0)[:80]}")
    elif persian == 0 and latin > 0:
        note("untranslated", "no Persian characters at all")

    if re.search(rf"[{PERSIAN_LETTER}][A-Za-z]|[A-Za-z][{PERSIAN_LETTER}]", plain):
        note("script-collision", "Latin and Persian letters with no separating space")

    return issues


# --------------------------------------------------------------------------- #
# Book-level driver
# --------------------------------------------------------------------------- #

def _targets(book: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any], str]]:
    """``(unit id, container, field)`` for every translated string in the book."""
    for block in ir.iter_text_blocks(book):
        if (block.get("target") or "").strip():
            yield block["id"], block, "target"
    for block in book.get("blocks", []):
        if block["type"] == "image" and (block.get("target_alt") or "").strip():
            yield f"{block['id']}#alt", block, "target_alt"
    for note in book.get("footnotes", []):
        if (note.get("target") or "").strip():
            yield note["id"], note, "target"
    # A running head prints on every page, so a stray Latin quotation mark or an
    # unconverted digit in one is the typographical error a reader meets most.
    for unit_id, _, piece, _ in ir.iter_running_pieces(book):
        if (piece.get("target") or "").strip():
            yield unit_id, piece, "target"


def fix_book(book: dict[str, Any], options: Options | None = None) -> dict[str, Any]:
    options = options or Options()
    changed: list[str] = []
    for unit_id, container, field in _targets(book):
        before = container[field]
        after = fix_text(before, options)
        if after != before:
            container[field] = after
            changed.append(unit_id)
    return {"changed": changed, "changed_count": len(changed)}


def lint_book(book: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for unit_id, container, field in _targets(book):
        for issue in lint_text(container[field]):
            findings.append({"unit": unit_id, **issue})
    by_code: dict[str, int] = {}
    for finding in findings:
        by_code[finding["code"]] = by_code.get(finding["code"], 0) + 1
    return {"findings": findings, "count": len(findings), "by_code": by_code}


def main(argv: list[str] | None = None) -> int:
    ir.use_utf8_stdio()
    parser = argparse.ArgumentParser(prog="revayat-novel falint", description=__doc__)
    parser.add_argument("action", choices=["fix", "lint"])
    parser.add_argument("--book", required=True)
    parser.add_argument("--digits", choices=["persian", "keep"], default="persian")
    parser.add_argument("--no-quotes", action="store_true")
    parser.add_argument("--no-zwnj", action="store_true")
    parser.add_argument("--no-ellipsis", action="store_true")
    parser.add_argument("--limit", type=int, default=40, help="findings to print")
    args = parser.parse_args(argv)

    book_path = Path(args.book)
    book = ir.load_book(book_path)

    if args.action == "fix":
        options = Options(
            digits=args.digits,
            quotes=not args.no_quotes,
            zwnj=not args.no_zwnj,
            ellipsis=not args.no_ellipsis,
        )
        report = fix_book(book, options)
        ir.save_book(book, book_path)
        # The source side is the input: typography rewrites targets, so hashing
        # the file would mark this stage stale against its own output.
        runstate.RunState(book_path.parent).record("typography", {
            "book": runstate.source_digest(book),
            "digits": str(args.digits),
        }, {"changed": str(report.get("count", len(report.get("changed", []))))})
        report["changed"] = report["changed"][:args.limit]
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0

    report = lint_book(book)
    report["findings"] = report["findings"][:args.limit]
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
