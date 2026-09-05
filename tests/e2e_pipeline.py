#!/usr/bin/env python3
"""Drive every pipeline stage end to end, on a book generated from scratch.

Unit tests cover each module; this covers the *seams* between them — the places
where a change to one stage quietly breaks the next. Every defect this project
has shipped so far lived in a seam: a worksheet id the merger could not parse,
an OCR flag the reader did not know about, an image stream the builder could
not decode.

Needs nothing but the Python dependencies: no OCR engine, no Word, no network.
Run it directly, or let CI run it on Linux, macOS and Windows.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills" / "revayat-novel" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bookir as ir            # noqa: E402
import build_docx              # noqa: E402
import chunk as chunking       # noqa: E402
import falint                  # noqa: E402
import glossary as gl          # noqa: E402
import merge as merging        # noqa: E402
import qa                      # noqa: E402
from tests_support import png_bytes   # noqa: E402

#: Deliberately mentions the same character several times, in and out of
#: sentence-initial position, so the glossary scan has something real to find
#: and the first-mention rule has more than one chunk to choose between.
CHAPTERS = [
    ("Chapter One", [
        "The light moved, and Elizabeth Bennet stood beside the window.",
        "Mr Darcy said nothing at all, which was *itself* a kind of answer.",
        "Later Elizabeth Bennet turned away, because she had not slept.",
    ]),
    ("Chapter Two", [
        "At breakfast Elizabeth Bennet found the room already full of talk.",
        "Nobody mentioned the letter, though **everybody** had read it.",
        "By noon Elizabeth Bennet had gone, and Mr Darcy had gone with her.",
    ]),
]


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def check(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def build_source_book(work: Path) -> Path:
    """A small book with headings, emphasis, an image and a source footnote."""
    assets = work / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    picture = png_bytes(120, 80)
    (assets / "fig.png").write_bytes(picture)

    book = ir.new_book(title="Pride and Prejudice", author="Jane Austen")
    blocks: list[dict] = []
    index = 0
    for title, paragraphs in CHAPTERS:
        index += 1
        blocks.append(ir.make_block("heading", index, level=1, text=title,
                                    font_size_pt=19.0))
        for paragraph in paragraphs:
            index += 1
            blocks.append(ir.make_block("paragraph", index, text=paragraph))
        index += 1
        blocks.append(ir.make_block(
            "image", index, asset="fig.png", sha256=ir.sha256_bytes(picture),
            bbox=None, width_pt=180.0, height_pt=120.0,
            pixel_width=120, pixel_height=80, alt="A red rectangle",
            target_alt=None,
        ))

    blocks[2]["text"] += "[[fn:fn0001]]"
    book["blocks"] = blocks
    book["footnotes"] = [ir.make_footnote(
        1, anchor_block=blocks[2]["id"], text="A note that came with the book."
    )]

    check(ir.validate_book(book) == [], "generated book is not valid")
    path = work / "book.json"
    ir.save_book(book, path)
    return path


def translate(worksheet: str) -> str:
    """Stand in for the translating agent, honouring the worksheet contract."""
    out: list[str] = []
    current: str | None = None
    body: list[str] = []
    counter = 0
    seen_translate = False

    def flush() -> None:
        nonlocal counter
        if current is None:
            return
        source = "\n".join(body).strip()
        spans = ir.parse_markup(source)
        for span in spans:
            if span["footnote"] or span["verbatim"]:
                continue
            # Keep the locked name in the output, the way a real translator
            # would, so the glossary gate is exercised rather than tripped.
            name = "الیزابت بنت " if "Elizabeth Bennet" in span["text"] else ""
            # The unit id goes in the filler so no two units come back with
            # word-for-word identical Persian. A fixed string made every
            # multi-span paragraph in the book identical, which is exactly the
            # pasted-worksheet shape qa's duplicate-translation gate rejects.
            span["text"] = (f"{name}متن فارسی یکتای {current} "
                            f"برای آزمون خط لوله است. ") * 2
        rendered = ir.render_spans(spans).strip()
        # Exercise the translator-footnote path once.
        if counter == 1:
            rendered += "[[fn:tr-01]]"
        out.append(f"@@ {current} x")
        out.append(rendered)
        out.append("")

    for line in worksheet.splitlines():
        if line.strip() == "## Translate":
            seen_translate = True
            continue
        if not seen_translate:
            continue
        match = chunking.HEADER.match(line.strip())
        if match:
            flush()
            current = match.group("id")
            body = []
            counter += 1
            continue
        if current is not None and not line.strip().startswith("<!--"):
            body.append(line)
    flush()

    if counter >= 1:
        out += ["@@ tr-01 footnote", "یادداشتی که مترجم افزوده است.", ""]
    return "\n".join(out)


def main() -> int:
    ir.use_utf8_stdio()
    work = Path(tempfile.mkdtemp(prefix="revayat-novel-e2e-"))
    try:
        print(f"working in {work}")

        # 1. source book -------------------------------------------------- #
        book_path = build_source_book(work)
        source = ir.load_book(book_path)
        print(f"  1 book        : {source['stats']['text_blocks']} text blocks, "
              f"{source['stats']['images']} images")

        # 2. glossary ------------------------------------------------------ #
        glossary_path = work / "glossary.json"
        glossary = gl.new_glossary()
        proposals = gl.scan(source, minimum=2)
        check(any(e["source"] == "Elizabeth Bennet" for e in proposals),
              "glossary scan missed the obvious recurring name")
        for entry in proposals:
            if entry["source"] == "Elizabeth Bennet":
                entry.update({
                    "target": "الیزابت بنت", "later_form": "الیزابت بنت",
                    "first_form": "الیزابت بنت (Elizabeth Bennet)",
                    "locked": True,
                })
        glossary["entries"] = proposals
        gl.save(glossary, glossary_path)
        first = next(e for e in proposals if e["source"] == "Elizabeth Bennet")
        check(bool(first["first_block_id"]), "first mention was not recorded")
        print(f"  2 glossary    : {len(proposals)} names, first mention at "
              f"{first['first_block_id']}")

        # 3. chunk --------------------------------------------------------- #
        chunks = work / "chunks"
        manifest = chunking.build(book_path, chunks, glossary_path=glossary_path,
                                  budget=400)
        check(manifest["chunks"], "no chunks were produced")
        introduced = sum(
            "first mention" in (chunks / entry["file"]).read_text(encoding="utf-8")
            for entry in manifest["chunks"]
        )
        check(introduced == 1,
              f"the first mention must be announced in exactly one chunk, got {introduced}")
        print(f"  3 chunks      : {len(manifest['chunks'])} worksheets, "
              f"first-mention announced once")

        # 4. translate + merge --------------------------------------------- #
        for entry in manifest["chunks"]:
            worksheet = (chunks / entry["file"]).read_text(encoding="utf-8")
            ir.write_text(chunks / entry["output"], translate(worksheet))
        # The glossary is not optional here: merge is where the one first
        # mention is settled, and without it the introduction is never placed.
        report = merging.merge(book_path, chunks, glossary_path=glossary_path)
        check(report["ok"], f"merge failed: {json.dumps(report)[:400]}")
        check(report.get("translator_notes"), "the translator's footnote was dropped")
        placed = report.get("first_mentions", {}).get("introduced", {})
        check(bool(placed), "merge did not place any first mention")
        print(f"  4 merge       : {report['units_applied']} units, "
              f"{sum(len(v) for v in report['translator_notes'].values())} translator "
              f"notes, {len(placed)} name(s) introduced")

        # 5. typography ----------------------------------------------------- #
        translated = ir.load_book(book_path)
        fixed = falint.fix_book(translated)
        ir.save_book(translated, book_path)
        once = json.dumps(translated, ensure_ascii=False)
        falint.fix_book(translated)
        check(json.dumps(translated, ensure_ascii=False) == once,
              "the typography pass is not idempotent")
        print(f"  5 typography  : {fixed['changed_count']} units adjusted, idempotent")

        # 6. QA ------------------------------------------------------------- #
        translated = ir.load_book(book_path)
        summary = qa.check_book(translated, assets=work / "assets",
                                glossary=gl.load(glossary_path)).summary()
        check(summary["ok"], f"QA rejected the book: {json.dumps(summary)[:500]}")
        print(f"  6 QA          : clean ({summary['warnings']} warnings)")

        # 7. build ---------------------------------------------------------- #
        import argparse
        parser = argparse.ArgumentParser()
        build_docx.add_arguments(parser)
        options = parser.parse_args([
            "--book", str(book_path), "--out", "x", "--font", "Tahoma",
            "--heading-size", "source",
        ])
        output = work / "book.fa.docx"
        built = build_docx.Builder(translated, work / "assets", options).build(output)
        check(built["warning_count"] == 0, f"build warnings: {built['warnings']}")
        origins = {note.get("origin") for note in translated["footnotes"]}
        check(origins == {"source", "translator"},
              f"both footnote origins must reach the document, saw {origins}")
        check(built["footnotes"] == len(translated["footnotes"]),
              f"{len(translated['footnotes'])} notes in the book, "
              f"{built['footnotes']} in the document")
        print(f"  7 build       : {built['headings']} headings, {built['images']} images, "
              f"{built['footnotes']} footnotes")

        # 8. verify the package --------------------------------------------- #
        package = qa.check_docx(output, translated).summary()
        check(package["ok"], f"package QA failed: {json.dumps(package)[:500]}")
        with zipfile.ZipFile(output) as archive:
            names = set(archive.namelist())
            document = archive.read("word/document.xml").decode("utf-8")
        check("word/footnotes.xml" in names, "no real footnotes part")
        check("<w:bidi/>" in document, "the document is not right-to-left")
        check("<w:bookmarkStart" in document, "headings carry no bookmarks")
        check('w:val="38"' in document,
              "--heading-size source did not reach the document (19pt = 38 half-points)")
        print(f"  8 package     : verified, {output.stat().st_size // 1024} KB")

        print("\nend-to-end pipeline OK")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
