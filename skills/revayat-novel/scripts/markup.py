"""The inline markup language, and the footnote tokens inside it.

The only syntax a translator ever sees — `**bold**`, `*italic*`, `` `verbatim` ``
and `[[fn:fn0007]]` — plus the one traversal that knows which of those is real
content and which is an example of the notation.

Its own module because it is its own responsibility and because everything
depends on it: the builder splits runs with it, the typography pass rewrites only
the prose spans it returns, QA counts emphasis with it, and merge resolves
footnote tokens through it. `bookir` imports and re-exports every name — `ir.
parse_markup` is how every caller and every test already reaches them — and
nothing here imports `bookir`, so the two cannot cycle.

**`parse_markup` and `render_spans` are exact inverses**, and that is load-bearing
rather than tidy: emphasis parity in QA, run splitting in the builder and the
Persian typography pass all depend on it. `tests/test_bookir.py` guards it.
"""

from __future__ import annotations

import re
from itertools import groupby
from typing import Any, Iterable

# --------------------------------------------------------------------------- #
# Inline markup
# --------------------------------------------------------------------------- #
#
# The only inline syntax a translator ever sees:
#
#   **bold**            ***bold italic***       *italic*
#   `verbatim`          kept byte-for-byte, forced LTR in the DOCX
#   [[fn:fn0007]]       footnote marker; must survive translation
#
# Anything else is literal. A lone ``*`` in prose is escaped as ``\*``.

#: A canonical footnote id: ``fn`` and **at least** four digits.
#:
#: One definition, used by everything that allocates, recognises or parses one.
#: It used to be written out three times — here, in :data:`_INLINE`, and as the
#: ``:04d`` in :func:`make_footnote` — and the three disagreed at the boundary:
#: note 10000 was allocated as ``fn10000`` and then matched by nothing, so the
#: marker survived into the book as literal text and printed as
#: ``[[fn:fn10000]]`` in the middle of a sentence.
_FOOTNOTE_ID = r"fn\d{4,}"

FOOTNOTE_TOKEN = re.compile(rf"\[\[fn:({_FOOTNOTE_ID})\]\]")
#: Any footnote token, including the ``tr-NN`` form a translator writes before
#: merge allocates it a book-wide id. Kept separate from the canonical pattern
#: on purpose: a ``tr-NN`` left in a finished book is a defect, and
#: :func:`validate_book` should still catch it.
ANY_FOOTNOTE_TOKEN = re.compile(r"\[\[fn:([A-Za-z0-9_-]+)\]\]")
_INLINE = re.compile(
    rf"(?P<token>\[\[fn:{_FOOTNOTE_ID}\]\])"
    r"|(?P<code>(?<!\\)`(?P<code_body>[^`]*)`)"
    r"|(?P<bi>(?<!\\)\*\*\*(?P<bi_body>(?:[^*\\]|\\.)+?)\*\*\*)"
    r"|(?P<bold>(?<!\\)\*\*(?P<bold_body>(?:[^*\\]|\\.)+?)\*\*)"
    r"|(?P<italic>(?<!\\)\*(?P<italic_body>(?:[^*\\]|\\.)+?)\*)"
)


#: Invisible characters that carry no meaning in prose. U+200C (ZWNJ) is
#: deliberately absent: in Persian it is a real orthographic character.
_INVISIBLE = str.maketrans({
    "﻿": "",   # BOM / zero-width no-break space, common as a word joiner
    "​": "",   # zero-width space
    "‍": "",   # zero-width joiner
    "­": "",   # soft hyphen
    "ـ": "",   # Arabic tatweel
    " ": " ",  # no-break space
})


def normalise_source(text: str) -> str:
    """Strip invisible noise that extractors leave in prose."""
    return re.sub(r"[ \t]{2,}", " ", (text or "").translate(_INVISIBLE)).strip()


def escape_markup(text: str) -> str:
    """Escape characters that would otherwise be read as markup."""
    return text.replace("\\", "\\\\").replace("*", "\\*").replace("`", "\\`")


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)


def render_markup(spans: Iterable[tuple[str, bool, bool]]) -> str:
    """Join ``(text, bold, italic)`` spans into inline markup.

    Adjacent spans sharing a style are merged first so that a sentence split
    across three same-styled PDF spans does not become ``*a**b**c*``.
    """
    merged: list[list[Any]] = []
    for text, bold, italic in spans:
        if not text:
            continue
        if merged and merged[-1][1] == bold and merged[-1][2] == italic:
            merged[-1][0] += text
            continue
        merged.append([text, bold, italic])

    out: list[str] = []
    for text, bold, italic in merged:
        # Emphasis must wrap visible characters, not the surrounding spaces:
        # "*text *" renders literally in most parsers.
        stripped = text.strip()
        if not stripped or not (bold or italic):
            out.append(escape_markup(text))
            continue
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        marker = "***" if (bold and italic) else ("**" if bold else "*")
        out.append(f"{lead}{marker}{escape_markup(stripped)}{marker}{trail}")
    return "".join(out)


def parse_markup(text: str) -> list[dict[str, Any]]:
    """Inverse of :func:`render_markup`.

    Returns a list of ``{"text", "bold", "italic", "verbatim", "footnote"}``
    spans. A footnote span has ``footnote`` set and empty ``text``.
    """
    spans: list[dict[str, Any]] = []

    def push(chunk: str, *, bold: bool = False, italic: bool = False,
             verbatim: bool = False, footnote: str | None = None) -> None:
        if footnote is None and not chunk:
            return
        spans.append({
            "text": chunk,
            "bold": bold,
            "italic": italic,
            "verbatim": verbatim,
            "footnote": footnote,
        })

    def nest(body: str, *, bold: bool = False, italic: bool = False) -> None:
        """An emphasised run is parsed by the same grammar, then styled.

        This used to push the body as one literal span, which is how a footnote
        inside emphasis disappeared: ``*واژه [[fn:fn0001]]*`` produced a single
        italic span whose *text* contained the marker, so `write_markup` — which
        only makes a note from a footnote span — had nothing to make. Meanwhile
        `footnote_refs` still counted the marker by regex, so parity passed and
        the note was simply gone from the DOCX.

        Verbatim does not recurse, deliberately: a marker inside backticks is an
        example of a marker and has to stay literal.
        """
        for span in parse_markup(body):
            spans.append({**span,
                          "bold": span["bold"] or bold,
                          "italic": span["italic"] or italic})

    cursor = 0
    for match in _INLINE.finditer(text):
        push(_unescape(text[cursor:match.start()]))
        if match.group("token"):
            push("", footnote=FOOTNOTE_TOKEN.match(match.group("token")).group(1))
        elif match.group("code") is not None:
            push(match.group("code_body"), verbatim=True)
        elif match.group("bi") is not None:
            nest(match.group("bi_body"), bold=True, italic=True)
        elif match.group("bold") is not None:
            nest(match.group("bold_body"), bold=True)
        else:
            nest(match.group("italic_body"), italic=True)
        cursor = match.end()
    push(_unescape(text[cursor:]))
    return spans


def render_spans(spans: Iterable[dict[str, Any]]) -> str:
    """Exact inverse of :func:`parse_markup`.

    Lets a text transform (the Persian typography pass) rewrite only the prose
    spans while emphasis, verbatim runs and footnote tokens are carried through
    untouched — the markup cannot be damaged by a regex that never sees it.
    """
    def content(span: dict[str, Any]) -> str:
        """One span's own text, with no emphasis wrapper — the group adds that."""
        if span.get("footnote"):
            return f"[[fn:{span['footnote']}]]"
        if span.get("verbatim"):
            return f"`{span['text']}`"
        return escape_markup(span["text"])

    out: list[str] = []
    # Consecutive spans sharing a style are wrapped **once**, because emphasis
    # nests: a footnote or a verbatim run inside an italic phrase is now its own
    # span carrying `italic`, and one wrapper per span would emit
    # `*واژه *[[fn:fn0001]]` where the source read `*واژه [[fn:fn0001]]*`.
    for (bold, italic), group in groupby(
            spans, key=lambda s: (bool(s.get("bold")), bool(s.get("italic")))):
        inner = "".join(content(span) for span in group)
        if not inner:
            continue
        if bold and italic:
            out.append(f"***{inner}***")
        elif bold:
            out.append(f"**{inner}**")
        elif italic:
            out.append(f"*{inner}*")
        else:
            out.append(inner)
    return "".join(out)


def verbatim_spans(text: str) -> list[str]:
    """The verbatim runs of ``text``, in order, as their exact contents.

    :func:`emphasis_signature` counts them, and a count is not a content check:
    ```ABC-123``` becoming ```XYZ-999``` keeps every number the
    signature reports while changing the one thing a verbatim span exists to
    protect. A code identifier, a filename or a command is not translatable, so
    it has to come back byte for byte.
    """
    return [span["text"] for span in parse_markup(text) if span.get("verbatim")]


def plain_text(text: str) -> str:
    """Markup stripped — what a reader would actually see."""
    return "".join(span["text"] for span in parse_markup(text))


def footnote_refs(text: str, *, include_local: bool = False) -> list[str]:
    """Footnote ids this text actually refers to, in reading order.

    Markup-aware, because a raw scan and the parser disagreed about one thing
    that matters: a marker shown *inside a code span* is an example of the
    notation, not a reference to a note. `[[fn:fn0001]]` in backticks was counted
    as a reference, so the integrity gate demanded a note for it; and a literal
    `[[fn:tr-example]]` in a worksheet's own instructions was read as a
    translator note with no body, which refused a perfectly good reply.

    ``include_local`` adds the ``tr-NN`` form a translator writes before merge
    allocates a book-wide id. Those are not part of the canonical token grammar,
    so they arrive as ordinary text and are picked out of the prose spans only —
    never out of a verbatim one.
    """
    found: list[str] = []
    for span in parse_markup(text or ""):
        if span.get("footnote"):
            found.append(span["footnote"])
        elif include_local and not span.get("verbatim"):
            found += ANY_FOOTNOTE_TOKEN.findall(span.get("text") or "")
    return found


def rewrite_footnote_refs(text: str, mapping: dict[str, str]) -> str:
    """Repoint footnote tokens through ``mapping``, outside protected spans.

    The substitution used to run over the raw string, so a worksheet's own
    example — a literal `[[fn:tr-example]]` inside backticks — was rewritten into
    a real allocated id the moment any reply offered a body for that name. A
    documented example became a live reference to a footnote.
    """
    if not mapping:
        return text
    spans = parse_markup(text or "")
    for span in spans:
        name = span.get("footnote")
        if name:
            span["footnote"] = mapping.get(name, name)
        elif not span.get("verbatim") and span.get("text"):
            span["text"] = ANY_FOOTNOTE_TOKEN.sub(
                lambda found: f"[[fn:{mapping.get(found.group(1), found.group(1))}]]",
                span["text"])
    return render_spans(spans)


def emphasis_signature(text: str) -> tuple[int, int, int]:
    """``(bold, italic, verbatim)`` span counts — compared source vs. target."""
    bold = italic = verbatim = 0
    for span in parse_markup(text or ""):
        if span["footnote"]:
            continue
        if span["verbatim"]:
            verbatim += 1
        elif span["bold"] and span["italic"]:
            bold += 1
            italic += 1
        elif span["bold"]:
            bold += 1
        elif span["italic"]:
            italic += 1
    return bold, italic, verbatim
