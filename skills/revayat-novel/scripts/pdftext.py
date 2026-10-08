"""PDF text classification, repeated furniture and paragraph continuity."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

import bookir as ir

FLAG_ITALIC = 1 << 1
FLAG_BOLD = 1 << 4
RUNNING_HEAD_MIN_SHARE = 0.25
MARGIN_BAND = 0.08

#: Fonts an OCR pass writes its invisible text layer in. They carry no styling
#: at all, so anything they seem to say about weight or slope is an artefact.
_OCR_FONTS = ("glyphless", "notoserif-regular", "invisible")


def _style_of(span: dict[str, Any], *, ocr: bool = False) -> tuple[bool, bool]:
    """Bold/italic for a span, from render flags and the font name.

    Flags alone miss synthetic faces (e.g. ``AGaramondPro-BoldItalic`` embedded
    with flags=0), so the font name is consulted as well.

    Recognized-page provenance suppresses synthetic style regardless of the
    renderer's font (OCRmyPDF may use fpdf2 rather than GlyphLessFont). Native
    skip-text pages keep their typography; known OCR fonts remain a guard for
    direct reads whose page provenance is unavailable.
    """
    name = str(span.get("font", "")).lower()
    if ocr or any(marker in name for marker in _OCR_FONTS):
        return False, False
    flags = int(span.get("flags", 0))
    bold = bool(flags & FLAG_BOLD) or "bold" in name or "black" in name or "heavy" in name
    italic = bool(flags & FLAG_ITALIC) or "italic" in name or "oblique" in name
    return bold, italic


def _line_text(line: dict[str, Any]) -> str:
    return "".join(span.get("text", "") for span in line.get("spans", []))


def _margin_line(line: dict[str, Any], page_height: float) -> bool:
    y0, y1 = line["bbox"][1], line["bbox"][3]
    return y1 <= page_height * MARGIN_BAND or y0 >= page_height * (1 - MARGIN_BAND)


def _collect_running_heads(pages: list[dict[str, Any]], page_height: float) -> set[str]:
    """Learn repeated margin labels using each page's unrotated text geometry."""
    counts: Counter[str] = Counter()

    for page in pages:
        seen_on_page: set[str] = set()
        for block in page.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                if not _margin_line(line, float(page.get("height") or page_height)):
                    continue
                key = _normalise_head(_line_text(line))
                if key:
                    seen_on_page.add(key)
        counts.update(seen_on_page)

    threshold = max(3, int(len(pages) * RUNNING_HEAD_MIN_SHARE))
    return {key for key, count in counts.items() if count >= threshold}


def _normalise_head(text: str) -> str:
    """Collapse a margin line to a comparison key.

    Digits become ``#`` so ``Page 12`` and ``Page 13`` collapse together; a
    bare page number therefore normalises to ``#`` and repeats on every page.
    """
    collapsed = re.sub(r"\s+", " ", text).strip()
    if not collapsed or len(collapsed) > 90:
        return ""
    return re.sub(r"\d+", "#", collapsed).lower()


def _body_font_size(pages: list[dict[str, Any]]) -> float:
    """The most common span size — everything larger is a candidate heading."""
    sizes: Counter[float] = Counter()
    for page in pages:
        for block in page.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if len(text) >= 3:
                        sizes[round(float(span.get("size", 0)), 1)] += len(text)
    if not sizes:
        return 10.0
    return sizes.most_common(1)[0][0]


#: Words that open a chapter even when the type size does not change, in the
#: source languages this skill actually sees.
_CHAPTER_WORD = re.compile(
    r"^\s*(?:chapter|part|book|prologue|epilogue|interlude"
    r"|فصل|بخش|پیش‌?گفتار|مقدمه|پس‌?گفتار|درآمد)\b",
    re.I,
)


#: A line narrower than this share of the text column, sitting with equal
#: margins either side, is centred rather than merely justified.
CENTRED_MAX_WIDTH_SHARE = 0.75
#: Margins within this many points of each other read as equal.
CENTRED_TOLERANCE_PT = 12.0
#: Vertical space above a line, relative to the body size, that a designer only
#: leaves before something that starts a new section.
SECTION_GAP_RATIO = 1.6


def _alignment(bbox: list[float], page_width: float, text_left: float,
               text_right: float) -> str:
    """``centre``, ``start`` or ``end``, judged from the two side margins.

    A full-width justified paragraph also has equal margins, so a line only
    counts as centred when it is short enough to have been placed there.
    """
    left, right = bbox[0], bbox[2]
    column = max(text_right - text_left, 1.0)
    if (right - left) / column <= CENTRED_MAX_WIDTH_SHARE and abs(
        (left - text_left) - (text_right - right)
    ) <= CENTRED_TOLERANCE_PT:
        return "centre"
    return "start" if (left - text_left) <= (text_right - right) else "end"


def _style_evidence(group: dict[str, Any], *, body_size: float, page_width: float,
                    text_left: float, text_right: float,
                    gap_before: float | None, starts_page: bool,
                    ocr: bool) -> dict[str, Any]:
    """Everything the source actually told us about how this line was set.

    Recorded whether or not it changes the classification, because the DOCX
    styles are chosen from it and a reader auditing a heading level deserves to
    see the evidence rather than a bare number. Nothing here is inferred: if
    the source did not say, the field is ``None``.
    """
    text = ir.plain_text(group["markup"])
    letters = [c for c in text if c.isalpha()]
    return {
        # Where the evidence came from decides how far it can be trusted: on a
        # scan the size is a per-line estimate, not a typesetter's choice.
        "source": "ocr-layout" if ocr else "pdf-text",
        "font_size_pt": round(group["size"], 2),
        "relative_size": round(group["size"] / body_size, 3) if body_size else None,
        "bold": bool(group.get("bold")),
        "italic": bool(group.get("italic")),
        "all_caps": bool(letters) and all(c.isupper() for c in letters),
        "alignment": _alignment(group["bbox"], page_width, text_left, text_right),
        "space_before_pt": round(gap_before, 1) if gap_before is not None else None,
        "starts_page": starts_page,
        "lines": group.get("line_count", 1),
        "bbox": group["bbox"],
    }


def _heading_level(size: float, body_size: float, text: str, bold: bool,
                   *, ocr: bool = False,
                   evidence: dict[str, Any] | None = None) -> int | None:
    """Classify a short line as a heading, or ``None`` for body text.

    On an OCR layer the size is a per-line *estimate* that jitters, so only an
    unambiguous jump counts and the weaker signals — a bold run-in subheading,
    a modest size bump — are dropped. Trusting them there turned ordinary
    paragraphs into 939 false headings on a real 70-page scan.
    """
    if len(text) > 120 or not text.strip():
        return None
    ratio = size / body_size if body_size else 1.0

    # Corroboration, used only where size alone is not decisive. A short,
    # centred line with clear air above it and no closing punctuation is a
    # heading in every book ever set, whatever its point size — and on a scan,
    # where the size is a guess, it is the *stronger* signal of the two.
    evidence = evidence or {}
    centred = evidence.get("alignment") == "centre"
    airy = (evidence.get("space_before_pt") or 0) >= body_size * SECTION_GAP_RATIO
    unpunctuated = not text.rstrip().endswith((".", "!", "?", "،", "؛"))
    display = (
        centred and unpunctuated and len(text) <= 80
        and evidence.get("lines", 1) <= 3
    )

    if ocr:
        if ratio >= 1.9:
            return 1
        if ratio >= 1.55:
            return 2
        if _CHAPTER_WORD.match(text):
            return 2
        # Size is unreliable here, so placement carries the decision instead.
        if display and (airy or evidence.get("starts_page")):
            return 2
        return None

    if ratio >= 1.8:
        return 1
    if ratio >= 1.45:
        return 2
    if ratio >= 1.18:
        return 3
    # Same size as body, but short, bold and standalone: a run-in subheading.
    if bold and ratio >= 1.0 and len(text) <= 60 and not text.rstrip().endswith((".", "!", "?")):
        return 4
    # "CHAPTER SEVEN" style small-caps headings keep the body size.
    if _CHAPTER_WORD.match(text):
        return 2
    if display and (airy or evidence.get("starts_page")):
        return 2 if evidence.get("starts_page") else 3
    if evidence.get("all_caps") and unpunctuated and len(text) <= 60 and airy:
        return 3
    return None


def _join_lines(lines: list[str]) -> str:
    """Join the lines of a paragraph, undoing end-of-line hyphenation."""
    out = ""
    for raw in lines:
        piece = raw.strip()
        if not piece:
            continue
        if not out:
            out = piece
            continue
        if out.endswith("-") and not out.endswith("--"):
            out = out[:-1] + piece  # word split across a line break
        else:
            out = f"{out} {piece}"
    return out


#: Two lines belong to the same paragraph while their sizes agree this closely.
SIZE_GROUP_TOLERANCE = 0.06

#: In a text layer produced by OCR the "font size" is an *estimate* fitted to
#: each recognised line, not typography. Measured on a real scanned page, spans
#: of one uniform body paragraph ranged 11.5–15.0pt — a ±14% spread. At the
#: born-digital tolerance that splits almost every line into its own block, so
#: OCR gets a much wider one.
OCR_SIZE_GROUP_TOLERANCE = 0.28


def _block_groups(block: dict[str, Any], drop: set[str],
                  tolerance: float = SIZE_GROUP_TOLERANCE,
                  *, ocr: bool = False,
                  page_height: float | None = None) -> list[dict[str, Any]]:
    """Split one PyMuPDF text block into same-size line groups.

    A single PDF text block routinely holds the tail of a paragraph *and* the
    subheading that follows it. Classifying the block as a whole would let the
    subheading's font size swallow the paragraph, so lines are grouped by size
    first and each group is classified on its own.

    Returns dicts of ``{markup, size, bold, bbox}`` in reading order.
    """
    groups: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in block.get("lines", []):
        if (page_height is not None and _margin_line(line, page_height)
                and _normalise_head(_line_text(line)) in drop):
            continue
        spans: list[tuple[str, bool, bool]] = []
        size = 0.0
        bold_line = italic_line = False
        for span in line.get("spans", []):
            text = span.get("text", "")
            if not text:
                continue
            bold, italic = _style_of(span, ocr=ocr)
            bold_line = bold_line or bold
            italic_line = italic_line or italic
            size = max(size, float(span.get("size", 0)))
            spans.append((text, bold, italic))
        if not spans:
            continue

        markup = ir.render_markup(spans)
        bbox = list(line.get("bbox", block.get("bbox", [0, 0, 0, 0])))
        same = (
            current is not None
            and current["size"] > 0
            and abs(size - current["size"]) / current["size"] <= tolerance
        )
        if same:
            current["lines"].append(markup)
            current["size"] = max(current["size"], size)
            current["bold"] = current["bold"] or bold_line
            current["italic"] = current["italic"] or italic_line
            current["bbox"] = _union(current["bbox"], bbox)
        else:
            current = {"lines": [markup], "size": size, "bold": bold_line,
                       "italic": italic_line, "bbox": bbox}
            groups.append(current)

    # Joining happens on already-marked-up strings, which is safe because a
    # marker never straddles a line break here.
    return [
        {
            "markup": _join_lines(group["lines"]),
            "size": group["size"],
            "bold": group["bold"],
            "italic": group["italic"],
            "line_count": len(group["lines"]),
            "bbox": [round(v, 2) for v in group["bbox"]],
        }
        for group in groups
        if _join_lines(group["lines"]).strip()
    ]


def _union(a: list[float], b: list[float]) -> list[float]:
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


#: Sentence-final characters. A paragraph ending in one of these is complete.
#: Includes the Persian question mark, semicolon and ellipsis, so a Persian
#: paragraph is recognised as finished by the same rule as an English one.
_SENTENCE_END = ".!?:;»”\"'*`)]؟؛…،"


def _continues(first: str) -> bool:
    """Does this opening character suggest the previous paragraph ran on?

    In a cased script a lower-case opener is the signal. Persian, Arabic,
    Hebrew and CJK have no case at all, and ``'م'.islower()`` is ``False`` —
    so testing case alone silently disables paragraph merging for every one of
    them, leaving the translator a book of one-line fragments. For a caseless
    opener the decision rests entirely on the previous paragraph having ended
    mid-sentence, which is the stronger half of the signal anyway.
    """
    return not first.isupper()


def _merge_split_paragraphs(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rejoin a paragraph that a line, column or page break cut in half.

    The signal is conservative and needs both halves to agree: the earlier
    paragraph ends mid-sentence *and* the later one starts lower-case. Prose
    that genuinely starts a new paragraph practically always ends the previous
    one with punctuation, so this leaves real paragraph breaks intact — while
    repairing the extractor artefacts that would otherwise reach the
    translator as two half-sentences with no shared context.
    """
    merged: list[dict[str, Any]] = []
    for block in blocks:
        if block["type"] != "paragraph" or not merged:
            merged.append(block)
            continue

        previous = None
        for candidate in reversed(merged):
            if candidate["type"] == "paragraph":
                previous = candidate
                break
            if candidate["type"] == "pagebreak":
                continue  # a page break alone does not end a paragraph
            break  # a heading, image or separator genuinely separates the two

        if previous is None:
            merged.append(block)
            continue

        tail = ir.plain_text(previous.get("text", "")).rstrip()
        head = ir.plain_text(block.get("text", "")).lstrip()
        if tail and head and tail[-1] not in _SENTENCE_END and _continues(head[0]):
            previous["text"] = f"{previous['text'].rstrip()} {block['text'].lstrip()}"
            if block.get("bbox") and previous.get("bbox"):
                previous["bbox"] = _union(previous["bbox"], block["bbox"])
            continue
        merged.append(block)
    return merged
