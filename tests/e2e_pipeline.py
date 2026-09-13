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
import re
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
import fluency                 # noqa: E402
import glossary as gl          # noqa: E402
import meaning                 # noqa: E402
import merge as merging        # noqa: E402
import notegraph               # noqa: E402
import published               # noqa: E402
import qa                      # noqa: E402
import signoff                 # noqa: E402
import worksheet as worksheet_module  # noqa: E402
from tests_support import png_bytes, review_reply   # noqa: E402

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
    # The kind the worksheet asked for, echoed back. Merge checks it, because a
    # heading answered as a paragraph is how a chapter title becomes body text —
    # so a stand-in that invents a kind is not honouring the contract.
    current_kind = "para"
    body: list[str] = []
    counter = 0
    noted = False
    seen_translate = False

    def flush() -> None:
        nonlocal counter, noted
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
        # Exercise the translator-footnote path once per worksheet, and only on
        # prose. The protocol says to write the marker "in the sentence": put it
        # in a footnote's own body or an image caption and there is no paragraph
        # for the note to anchor to, so `qa` rightly reports it orphaned. The
        # first unit of a worksheet is not always prose — a split run can open
        # with a footnote or an `#alt` — so the kind decides, not the position.
        if not noted and current_kind == "para":
            rendered += "[[fn:tr-01]]"
            noted = True
        out.append(f"@@ {current} {current_kind}")
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
            current_kind = match.group("kind")
            body = []
            counter += 1
            continue
        if current is not None and not line.strip().startswith("<!--"):
            body.append(line)
    flush()

    # Only when a marker was actually placed. Emitting the note unconditionally
    # produced a note no sentence points at whenever the worksheet carried no
    # prose to point from — a split run can leave a worksheet holding nothing but
    # an image caption and a source footnote — and `qa` rightly calls that
    # orphaned. A real translator does not file a note for nothing either.
    if noted:
        out += ["@@ tr-01 footnote", "یادداشتی که مترجم افزوده است.", ""]

    # The request line, copied back out of the worksheet exactly as the worksheet
    # asks. Without it merge cannot tell this answer from an answer to the cut
    # this worksheet replaced, and refuses it as unbound — which is the point.
    token = worksheet_module.request_of(worksheet)
    if token:
        out.insert(0, worksheet_module.request_line(token))
    return "\n".join(out)


def _plain(text: str) -> str:
    """Text with the XML tags and the run boundaries taken out.

    Word splits one sentence across several `<w:t>` runs whenever it changes
    anything about the formatting, so a substring search against the raw part
    fails on text that is present and correct.
    """
    return re.sub(r"<[^>]+>", "", text).replace("‌", "")


def _approve(out_dir: Path, sheet_ids: list[str]) -> None:
    """Claim each sheet read, echoing the review line the sheet asks for.

    A reply that echoes nothing cannot say which sheet or which revision it
    answers, so the transport refuses it — and this script did exactly that
    until the refusal was added, which is how it became the stage nobody ran.
    """
    for sheet_id in sheet_ids:
        ir.write_text(out_dir / f"out_{sheet_id}.md",
                      review_reply(out_dir / f"{sheet_id}.md",
                                   f"!! reviewed {sheet_id}\n"))


def _workspace() -> Path:
    """A working directory inside the project, so a kept run stays with it.

    `tempfile.mkdtemp()` put it under the user's temp directory, and this script
    deliberately keeps the directory when a stage fails — which left diagnostic
    copies of a book outside the project it belongs to. `.pytest-tmp` is already
    git-ignored and is where the pytest runs land.
    """
    root = REPO / ".pytest-tmp" / "e2e"
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="run-", dir=root))


def main() -> int:
    ir.use_utf8_stdio()
    work = _workspace()
    passed = False
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
                                  budget=1300)
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

        # 4a. finish the published text ------------------------------------ #
        # Before anybody reads it, because both of these change what a reader
        # sees and therefore what an approval is about. Run after the reviews —
        # which is the order this script used to follow — and the approvals
        # describe text that has since changed.
        translated = ir.load_book(book_path)
        fixed = falint.fix_book(translated)
        once = json.dumps(translated, ensure_ascii=False)
        falint.fix_book(translated)
        check(json.dumps(translated, ensure_ascii=False) == once,
              "the typography pass is not idempotent")
        # The one expected hand-edit, and published prose like any paragraph: the
        # title page is the first thing a reader meets.
        translated["meta"]["title_target"] = "خانه‌ای در انتهای کوچه"
        translated["meta"]["author_target"] = "نویسندهٔ آزمون"
        ir.save_book(translated, book_path)
        pending = published.pending(ir.load_book(book_path))
        check(not pending,
              f"published prose with no Persian: {[u['id'] for u in pending]}")
        print(f"  4a typography : {fixed['changed_count']} units adjusted, "
              f"idempotent, title page translated")

        # 4b. the bilingual review ------------------------------------------ #
        # A stage nothing exercises is a stage that rots, and this one is easy to
        # leave out of the chain because a human answers it. What is mechanical
        # about it is checked here: the sheets carry both sides, a clean report
        # binds to the revision it was made from, and the same report stops being
        # valid the moment the translation moves. The judgement itself is not
        # simulated — a blank report with the sheet claimed read is exactly what
        # "the reviewer found nothing" looks like.
        review_dir = work / "review"
        sheets = meaning.write_sheets(book_path, review_dir)
        check(sheets["units"] > 0, "the review sheets cover no units")
        first = (review_dir / f"{sheets['sheets'][0]}.md").read_text(encoding="utf-8")
        source_one = ir.load_book(book_path)["blocks"][0]
        check((source_one.get("text") or "")[:20] in first,
              "the review sheet does not carry the source side")
        check((source_one.get("target") or "")[:20] in first,
              "the review sheet does not carry the translation side")
        _approve(review_dir, sheets["sheets"])
        filed = meaning.record(review_dir, book_path)
        check(filed.get("ok"), f"the review was refused: {json.dumps(filed)[:300]}")
        check(meaning.verdict(review_dir, sheets["revision"])["ok"],
              "a clean review did not pass its own gate")
        check(any(unit["part"] == "metadata"
                  for unit in published.units(ir.load_book(book_path))),
              "the title page is not in the reviewed inventory")
        print(f"  4b review     : {sheets['units']} pairs, "
              f"{len(sheets['sheets'])} sheet(s), bound to {sheets['revision'][:16]}…")

        # 4c. the blind Persian pass ---------------------------------------- #
        # Part of the documented sequence, and the stage a run is most tempted to
        # skip because the bilingual review already "read" the prose. It cannot
        # have: it could see the English behind every sentence.
        fluency_dir = work / "fluency"
        blind = fluency.write_sheets(book_path, fluency_dir, review_dir)
        check(blind.get("ok"), f"the blind pass was refused: {json.dumps(blind)[:300]}")
        for sheet_id in blind["sheets"]:
            text = (fluency_dir / f"{sheet_id}.md").read_text(encoding="utf-8")
            for block in ir.load_book(book_path)["blocks"]:
                english = (block.get("text") or "").strip()
                check(not english or english[:24] not in text,
                      f"{sheet_id} leaked source text into the blind pass")
        _approve(fluency_dir, blind["sheets"])
        pass_filed = fluency.record(fluency_dir, book_path)
        check(pass_filed.get("ok"),
              f"the blind pass was refused: {json.dumps(pass_filed)[:300]}")
        settled = fluency.verdict(fluency_dir, book_path, review_dir)
        check(settled["ok"], f"the blind pass did not pass: {json.dumps(settled)[:300]}")
        print(f"  4c fluency    : {blind['units']} units read blind, "
              f"{len(blind['sheets'])} sheet(s), no edits proposed")

        # 5. typography already settled ------------------------------------- #
        # The check that step 4a held, which is what the recipe asks for at this
        # point: nothing left to fix, so no approval is about stale text.
        left = falint.lint_book(ir.load_book(book_path))
        check(not left.get("findings"),
              f"typography still needs fixing after the reviews: "
              f"{json.dumps(left)[:300]}")
        print("  5 typography  : nothing left to fix, approvals are about this text")

        # 6. QA, semantic verdicts enforced --------------------------------- #
        translated = ir.load_book(book_path)
        summary = qa.check_book(translated, assets=work / "assets",
                                glossary=gl.load(glossary_path)).summary()
        check(summary["ok"], f"QA rejected the book: {json.dumps(summary)[:500]}")
        # Both note origins, both anchored where they print, and the whole graph
        # resolving — through the production builder below, not only in the IR.
        check(not notegraph.problems(translated),
              f"the footnote graph does not resolve: {notegraph.problems(translated)}")
        gate = signoff.problems(book_path, review_dir=review_dir,
                                fluency_dir=fluency_dir)
        check(not gate, f"the delivery gate refused: {gate}")
        # And the other half of the same gate: not asking is not passing.
        unasked = signoff.problems(book_path, review_dir=None, fluency_dir=None)
        check([code for code, _u, _d in unasked] == [signoff.UNVERIFIED] * 2,
              f"a gate nobody ran must report it: {unasked}")
        print(f"  6 QA          : clean ({summary['warnings']} warnings), "
              f"both semantic verdicts current")

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

        # 9. the shipped content is the approved content --------------------- #
        # The question the whole gate exists to answer, asked of the file that
        # will actually be sent. Checked in both directions: every approved
        # string is in the document, and the approvals still hold for the book it
        # was built from — the no-edit case, which is the one that silently
        # passes when the chain is only asserted rather than compared.
        with zipfile.ZipFile(output) as archive:
            printed = "".join(
                archive.read(part).decode("utf-8")
                for part in ("word/document.xml", "word/footnotes.xml")
                if part in names)
        shipped = _plain(printed)
        for unit in published.units(ir.load_book(book_path)):
            wanted = _plain(ir.plain_text(unit["target"]))
            check(bool(wanted), f"{unit['id']} has no Persian at delivery")
            check(wanted[:60] in shipped,
                  f"{unit['id']} ({unit['part']}) was approved and is not in the "
                  f"document: {unit['target'][:60]!r}")
        after = signoff.approved(book_path, review_dir=review_dir,
                                 fluency_dir=fluency_dir)
        check(not after["problems"],
              f"the approvals no longer hold after the build: {after['problems']}")
        check(after["meaning_revision"] == filed["revision"],
              "the book moved between the approval and the build")
        check(after["fluency_revision"] == pass_filed["revision"],
              "the Persian moved between the blind pass and the build")
        print(f"  9 delivery    : {after['published_units']} published units in the "
              f"file, approved at {after['meaning_revision'][:16]}…")

        print("\nend-to-end pipeline OK")
        passed = True
        return 0
    finally:
        # A failed run keeps its working directory, and says where. Deleting it
        # unconditionally destroyed the only evidence at exactly the moment it
        # was needed: this script reports a failure with no traceback, so
        # without the worksheets, the book and the report there is nothing left
        # to diagnose from — which is how a three-platform CI failure became
        # unreproducible locally.
        if passed:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print(f"\nkept for diagnosis: {work}")


if __name__ == "__main__":
    raise SystemExit(main())
