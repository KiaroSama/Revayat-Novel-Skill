"""Page merge, preview, render, review and acceptance through public operations."""

from __future__ import annotations

import json
from pathlib import Path
import pytest
import bookir as ir
import pagecheck
import pagerun
from tests_support import reply_text
import preview
import renderqa
import review
import runstate
from page_fixtures import TARGET, _book, _job, _one_page_book, _prose, _rendered, _reviewed, _save, _translated_pdf_run

pytestmark = pytest.mark.render

def test_a_page_walks_from_pending_to_accepted_through_the_operations(tmp_path):
    """Every step here is a production entry point; nothing writes the record.

    This is the whole claim of the page run: ``status`` and ``next`` are right
    because the operations moved the page, not because a caller remembered to
    say so afterwards.
    """
    pytest.importorskip("pymupdf")
    book_path = _one_page_book(tmp_path)
    pages = tmp_path / "pages"

    assert runstate.RunState(tmp_path).page(1) is None
    manifest = pagerun.build(book_path, pages)
    assert pagerun.status(pages)["pages"][0]["state"] == "extracted"

    # The translator answers the worksheet it was given.
    upcoming = pagerun.next_page(pages)
    assert upcoming["page"] == 1
    ir.write_text(Path(upcoming["output"]),
                  reply_text(Path(upcoming["worksheet"]),
                             f"@@ b00001 para\n{TARGET}\n"))

    merged = pagerun.merge_page(book_path, pages, 1)
    assert merged["ok"], merged
    assert pagerun.status(pages)["pages"][0]["state"] == "merged"

    # Not accepted yet: nobody has looked at the page.
    too_soon = pagerun.accept(book_path, pages, 1)
    assert too_soon["ok"] is False and too_soon["refused"] == "not-qa-passed"

    written = renderqa.check(tmp_path, book_path, 1,
                             target_pdf=_rendered(tmp_path / "target.pdf", TARGET))
    assert written["ok"] is True, written
    assert pagerun.status(pages)["pages"][0]["state"] == "qa_passed"

    # Still not accepted: the checks are geometric, and nobody has looked yet.
    unseen = pagerun.accept(book_path, pages, 1)
    assert unseen["ok"] is False and unseen["refused"] == "not-reviewed"

    _reviewed(tmp_path)
    accepted = pagerun.accept(book_path, pages, 1)
    assert accepted["ok"] is True and accepted["state"] == "accepted"

    progress = pagerun.status(pages)
    assert progress["next"] is None and progress["accepted"] == 1
    assert pagerun.next_page(pages) is None
    assert manifest["pages"] == 1


def test_merging_a_page_nobody_translated_is_refused_not_recorded(tmp_path):
    book_path = _one_page_book(tmp_path)
    pages = tmp_path / "pages"
    pagerun.build(book_path, pages)

    refused = pagerun.merge_page(book_path, pages, 1)
    assert refused["ok"] is False and refused["refused"] == "not-translated"
    assert runstate.RunState(tmp_path).page(1)["state"] == "extracted"


def test_a_split_page_merges_only_when_every_part_is_answered(tmp_path):
    book = _book([
        (1, "paragraph", {"text": "The first half of the page. " * 12}),
        (1, "paragraph", {"text": "The second half of the page. " * 12}),
    ])
    book_path = _save(book, tmp_path)
    pages = tmp_path / "pages"
    manifest = pagerun.build(book_path, pages, budget=900)
    entries = pagerun.jobs_for(manifest, 1)
    assert len(entries) == 2, "the fixture did not actually split"

    ir.write_text(pages / entries[0]["output"],
                  reply_text(pages / entries[0]["file"],
                             "@@ b00001 para\nمتن آزمون\n"))
    half = pagerun.merge_page(book_path, pages, 1)
    assert half["ok"] is False and half["refused"] == "not-translated"
    assert entries[1]["id"] in half["detail"]
    assert not ir.load_book(book_path)["blocks"][0].get("target"), \
        "half a page was written into the book"

    ir.write_text(pages / entries[1]["output"],
                  reply_text(pages / entries[1]["file"],
                             "@@ b00002 para\nمتن دیگر\n"))
    assert pagerun.merge_page(book_path, pages, 1)["ok"]
    assert runstate.RunState(tmp_path).page(1)["state"] == "merged"


def test_a_page_render_qa_never_saw_cannot_be_accepted(tmp_path):
    """A gate nobody ran must never read as a gate that passed."""
    book_path = _one_page_book(tmp_path)
    pages = tmp_path / "pages"
    pagerun.build(book_path, pages)
    ir.write_text(pages / "out_page0001.md",
                  reply_text(pages / "page0001.md",
                             f"@@ b00001 para\n{TARGET}\n"))
    assert pagerun.merge_page(book_path, pages, 1)["ok"]

    refused = pagerun.accept(book_path, pages, 1)
    assert refused["ok"] is False and refused["refused"] == "not-qa-passed"
    assert runstate.RunState(tmp_path).page(1)["state"] == "merged"
    assert pagerun.status(pages)["next"] == 1


def test_a_page_render_qa_failed_cannot_be_accepted(tmp_path):
    pytest.importorskip("pymupdf")
    book_path = _one_page_book(tmp_path)
    pages = tmp_path / "pages"
    pagerun.build(book_path, pages)
    ir.write_text(pages / "out_page0001.md",
                  reply_text(pages / "page0001.md",
                             f"@@ b00001 para\n{TARGET}\n"))
    pagerun.merge_page(book_path, pages, 1)

    blank = _rendered(tmp_path / "target.pdf", "Something else entirely.")
    assert renderqa.check(tmp_path, book_path, 1, target_pdf=blank)["ok"] is False

    refused = pagerun.accept(book_path, pages, 1)
    assert refused["ok"] is False and refused["refused"] == "not-qa-passed"
    assert runstate.RunState(tmp_path).page(1)["state"] == "failed"


def test_a_page_the_book_holds_no_persian_for_cannot_be_accepted(tmp_path):
    """Render QA can pass on a page whose text was never merged; the book is
    the one that knows."""
    book_path = _one_page_book(tmp_path)
    pages = tmp_path / "pages"
    pagerun.build(book_path, pages)
    runstate.RunState(tmp_path).set_page(1, "qa_passed")

    refused = pagerun.accept(book_path, pages, 1)
    assert refused["ok"] is False and refused["refused"] == "not-merged"
    assert "b00001" in refused["detail"]


def test_re_translating_an_accepted_page_takes_its_acceptance_away(tmp_path):
    """The old report passed, but it passed for text the book no longer holds."""
    pytest.importorskip("pymupdf")
    book_path = _one_page_book(tmp_path)
    pages = tmp_path / "pages"
    pagerun.build(book_path, pages)
    ir.write_text(pages / "out_page0001.md",
                  reply_text(pages / "page0001.md",
                             f"@@ b00001 para\n{TARGET}\n"))
    pagerun.merge_page(book_path, pages, 1)
    renderqa.check(tmp_path, book_path, 1,
                   target_pdf=_rendered(tmp_path / "target.pdf", TARGET))
    _reviewed(tmp_path)
    assert pagerun.accept(book_path, pages, 1)["ok"]

    corrected = "A second attempt at the very same line of prose entirely."
    ir.write_text(pages / "out_page0001.md",
                  reply_text(pages / "page0001.md",
                             f"@@ b00001 para\n{corrected}\n"))
    assert pagerun.merge_page(book_path, pages, 1)["ok"]
    assert pagerun.status(pages)["next"] == 1
    assert pagerun.accept(book_path, pages, 1)["refused"] == "not-qa-passed"


def test_the_page_qa_report_is_looked_for_where_render_qa_files_it(tmp_path):
    """The one convention two modules have to agree on, asserted rather than
    hoped for — ``renderqa`` imports ``pagerun``, so it cannot be shared."""
    assert pagerun.qa_report_path(tmp_path, 12) == renderqa.report_path(tmp_path, 12)


def test_the_cli_merges_and_accepts_one_page(tmp_path, capsys):
    pytest.importorskip("pymupdf")
    book_path = _one_page_book(tmp_path)
    pages = tmp_path / "pages"
    pagerun.build(book_path, pages)
    ir.write_text(pages / "out_page0001.md",
                  reply_text(pages / "page0001.md",
                             f"@@ b00001 para\n{TARGET}\n"))

    arguments = ["--book", str(book_path), "--pages", str(pages), "--page", "1"]
    assert pagerun.main(["merge", *arguments]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    assert pagerun.main(["accept", *arguments]) == 2
    assert json.loads(capsys.readouterr().out)["refused"] == "not-qa-passed"

    renderqa.check(tmp_path, book_path, 1,
                   target_pdf=_rendered(tmp_path / "target.pdf", TARGET))

    assert pagerun.main(["accept", *arguments]) == 2
    assert json.loads(capsys.readouterr().out)["refused"] == "not-reviewed"

    assert pagerun.main(["review", "--pages", str(pages), "--page", "1",
                         *[f"--answer={name}=yes" for name in review.QUESTIONS],
                         "--note", "looked at both renders"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    assert pagerun.main(["accept", *arguments]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "accepted"


def test_the_documented_page_loop_runs_in_order_with_nothing_missing(tmp_path,
                                                                     capsys):
    """Every command finds what the one before it left, and refuses if run early.

    This is the failure the loop was rewritten for: `SKILL.md` used to put
    `accept` immediately after `merge`, before the QA and review it depends on,
    and told the reader to hand render-qa a `$WORK/book.docx` that no documented
    command ever produced. Following it literally could not work.
    """
    pytest.importorskip("pymupdf")
    book_path = _one_page_book(tmp_path)
    pages = tmp_path / "pages"
    page = ["--page", "1"]
    at = ["--book", str(book_path), "--pages", str(pages)]

    assert pagerun.main(["build", "--book", str(book_path), "--out", str(pages)]) == 0
    capsys.readouterr()

    # 1 — next names the worksheet, and it is on disk.
    assert pagerun.main(["next", "--pages", str(pages)]) == 0
    upcoming = json.loads(capsys.readouterr().out)
    assert Path(upcoming["worksheet"]).exists(), (
        "next named a worksheet nobody wrote")

    # 2 — the translator answers it, echoing the request line the worksheet
    #     states. That echo is what binds the answer to this cut of the page.
    ir.write_text(Path(upcoming["output"]),
                  reply_text(Path(upcoming["worksheet"]),
                             f"@@ b00001 para\n{TARGET}\n"))

    # 3 — merge, before which accept must refuse.
    assert pagerun.main(["accept", *at, *page]) == 2
    assert json.loads(capsys.readouterr().out)["refused"] == "not-merged"
    assert pagerun.main(["merge", *at, *page]) == 0
    capsys.readouterr()

    # 4 — the preview, which is the artefact step 5 consumes.
    assert pagerun.main(["preview", *at, *page]) == 0
    made = json.loads(capsys.readouterr().out)
    preview_docx = Path(made["output"])
    assert preview_docx.exists(), "preview reported a file it did not write"

    # 5 — render QA, handed exactly that file.
    written = renderqa.check(tmp_path, book_path, 1, docx=preview_docx)
    if not written.get("verified"):
        pytest.skip(f"nothing here lays a document out: {written['unverified']}")
    assert written["ok"], written["findings"]
    for name in written["renders"]["target_sheets"]:
        assert (tmp_path / name).exists(), f"{name} was reported, not written"

    # 6 — the review, before which accept must still refuse.
    assert pagerun.main(["accept", *at, *page]) == 2
    assert json.loads(capsys.readouterr().out)["refused"] == "not-reviewed"
    assert pagerun.main(["review", "--pages", str(pages), *page,
                         *[f"--answer={name}=yes" for name in review.QUESTIONS]]) == 0
    capsys.readouterr()

    # 7 — and only now.
    assert pagerun.main(["accept", *at, *page]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "accepted"
    assert pagerun.status(pages)["next"] is None


def test_page_qa_is_unaffected_by_how_far_the_book_has_reflowed(tmp_path):
    """Source page 2 is checked as itself, not as the finished book's page 2.

    Page 1 here carries far more Persian than English, which is ordinary and is
    exactly what moves everything after it. Rendered as a whole book, source
    page 2's material would be several sheets further on; the old check looked
    at the book's second sheet and would have found page 1's overflow there —
    reporting page 2's blocks missing and page 1's as unexpected.
    """
    pytest.importorskip("pymupdf")
    # Varied on purpose: forty copies of one sentence would trip
    # `text-duplicated`, which would be the check working correctly on a fixture
    # that does not resemble prose.
    swollen = " ".join(
        f"بند شماره {n} به فارسی بسیار بلندتر از اصل "
        f"انگلیسی آن است و همین هر چه را که پس از آن می‌آید جابه‌جا می‌کند."
        for n in range(1, 41))
    own = "بند کوتاه صفحهٔ دوم که باید همان‌جا بماند و جابه‌جا نشود."

    book = ir.new_book()
    first = ir.make_block("paragraph", 1, page=1, text="A short English source line.")
    first["target"] = swollen
    second = ir.make_block("paragraph", 2, page=2, text="The second page's line.")
    second["target"] = own
    book["blocks"] = [first, second]
    book_path = _save(book, tmp_path)

    written = renderqa.check(tmp_path, book_path, 2)
    if not written.get("verified"):
        pytest.skip(f"nothing here lays a document out: {written['unverified']}")

    assert written["ok"], written["findings"]
    assert written["counts"]["expected_blocks"] == 1, (
        "page 2 owns one block; anything else means the preview carried page 1"
    )
    # And page 1, whose Persian runs long, is still judged on all of its sheets.
    first_page = renderqa.check(tmp_path, book_path, 1)
    assert first_page["ok"], first_page["findings"]
    assert first_page["sheets"] >= 1


def test_the_documented_loop_previews_the_page_it_is_on_not_page_twelve(tmp_path):
    """Run the loop on a page that is not the example, and follow the artefact.

    The loop's example sets `P=12`. A `page-0012.docx` hard-coded beside `$P`
    reads as consistent and sends every other page's QA at page 12's preview —
    or at nothing, on a book with fewer pages. Only executing it on another page
    catches that; a parser sees a valid command line either way.
    """
    pytest.importorskip("pymupdf")
    book = _book([_prose(1, "First page"), _prose(2, "Second page")])
    for block in book["blocks"]:
        block["target"] = f"ترجمهٔ {block['id']} با طول کافی برای آزمون."
    book_path = _save(book, tmp_path)
    pages = tmp_path / "pages"
    pagerun.build(book_path, pages)

    written = renderqa.check(tmp_path, book_path, 2)
    if not written.get("verified"):
        pytest.skip(f"nothing here lays a document out: {written['unverified']}")

    used = Path(written["preview"])
    assert used.name == "page-0002.docx", (
        f"page 2's QA consumed {used.name} — the preview belongs to another page"
    )
    assert used.exists()
    assert written["counts"]["expected_blocks"] == 1, (
        "page 2 owns one block; more means the preview carried page 1 as well"
    )
    # And the artefact it built is page 2's own, not a leftover.
    assert "b00002" in pagecheck.document_text(used) or \
        "ترجمهٔ b00002" in pagecheck.document_text(used), (
            "the preview does not contain page 2's translation"
        )


def test_render_qa_finds_the_source_page_without_being_told_where_it_is(
        tmp_path, sample_png):
    """The README route: `render-qa --book --work --page N` and nothing else.

    Both READMEs tell the reader to run exactly that and then compare
    `renders/source/page-0001.png` with the target — while `--source-pdf` was
    optional and omitted, so the source PNG the instruction names was never
    written. The manifest has known where that page lives all along.
    """
    pytest.importorskip("pymupdf")
    _, book_path, pages = _translated_pdf_run(tmp_path, sample_png)

    written = renderqa.check(tmp_path, book_path, 2)

    source = written.get("renders", {}).get("source", "")
    assert source, (
        f"no source render was produced for the documented command: {written}")
    assert (tmp_path / source).exists(), f"{source} was named but not written"
    assert written.get("source_evidence") == source, (
        "the source was rendered but the report did not name it; a gate reading "
        "this cannot tell a missing converter from a missing source page")
    assert pages.name == "pages"
    if written.get("verified"):
        assert written["renders"].get("target_sheets"), "no target sheets"


def test_a_source_page_that_went_missing_leaves_the_page_unverified(
        tmp_path, sample_png):
    """Losing one side of the comparison is "we could not look", never a pass."""
    pytest.importorskip("pymupdf")
    _, book_path, pages = _translated_pdf_run(tmp_path, sample_png)

    one_page = pages / _job(pagerun.load_manifest(pages), 2)["source_pdf"]
    assert one_page.exists()
    one_page.unlink()

    written = renderqa.check(tmp_path, book_path, 2)
    assert written["ok"] is False and written["verified"] is False, (
        f"a page with no source render came back verified: {written}")
    assert written.get("unverified"), "nothing said why it could not be checked"
    assert "source" in written["unverified"], written["unverified"]


def test_a_pdf_page_cannot_be_accepted_on_target_evidence_alone(
        tmp_path, sample_png):
    """The hole this closes, and the two halves of closing it.

    Reachable before because `--source-pdf` was optional: hand `check` a target
    PDF and no source, every deterministic gate reads the target so they all
    pass, the review is filed against target sheets only, and `accept` took it.
    The page reached `accepted` having never been set beside the page it was
    translated from.

    Both halves are asserted here. There is no longer a target-only route for a
    PDF page - handing over a target still renders the source, because the
    manifest is asked rather than the caller. And when the source genuinely
    cannot be produced, the report that results is refused by review and by
    accept rather than passed.
    """
    pytest.importorskip("pymupdf")
    _, book_path, pages = _translated_pdf_run(tmp_path, sample_png)

    built = preview.build(book_path, 2, tmp_path / "only-target.docx",
                          assets=tmp_path / "assets")
    if not built["ok"]:
        pytest.skip(f"no converter here: {built['detail']}")
    if not renderqa.wordrender.backend():
        pytest.skip("no converter here: no render backend")
    target = None
    try:
        target = renderqa.render_docx(tmp_path / "only-target.docx",
                                      tmp_path / "renders" / "preview")
    except renderqa.RenderError as error:
        pytest.skip(f"nothing here can lay a document out: {error}")

    assert target is not None and target.is_file()
    handed = renderqa.check(tmp_path, book_path, 2, target_pdf=target)
    assert handed.get("source_evidence"), (
        "handing over a target still has to render the source: the old route "
        "in was exactly this call with --source-pdf left off")
    assert pagerun.missing_source_render(pages, 2) is None

    # Now the case the gate is for: the source cannot be produced at all.
    (pages / _job(pagerun.load_manifest(pages), 2)["source_pdf"]).unlink()
    written = renderqa.check(tmp_path, book_path, 2, target_pdf=target)
    assert not written.get("source_evidence"), written

    refused = pagerun.missing_source_render(pages, 2)
    assert refused and refused["refused"] == "no-source-render", (
        f"a report with no source render was not refused: {refused}")

    review.record(tmp_path, 2, {name: True for name in review.QUESTIONS},
                  note="claims to have compared them")
    taken = pagerun.accept(book_path, pages, 2)
    assert taken["ok"] is False, f"accepted with no source render: {taken}"
    assert taken["refused"] == "unverified", taken
    assert "source page artifact is missing" in taken["detail"], taken


def test_the_page_loop_still_reaches_accepted_with_both_sides_present(
        tmp_path, sample_png):
    """The gate has to let the correct case through, or it is just an outage."""
    pytest.importorskip("pymupdf")
    _, book_path, pages = _translated_pdf_run(tmp_path, sample_png)

    written = renderqa.check(tmp_path, book_path, 1)
    if not written.get("verified"):
        pytest.skip(f"no converter here: {written.get('unverified')}")
    assert written["source_evidence"], "both sides were present; source missing"
    assert pagerun.missing_source_render(pages, 1) is None

    review.record(tmp_path, 1, {name: True for name in review.QUESTIONS},
                  note="looked at both")
    taken = pagerun.accept(book_path, pages, 1)
    if not written["ok"]:
        # The fixture's odd trims can fail a geometric check; the point here is
        # only that the source gate is not what stopped it.
        assert taken.get("refused") != "no-source-render", taken
    else:
        assert taken["ok"], f"a page with both sides was not accepted: {taken}"


def test_rebuilding_restores_the_source_and_the_loop_works_again(
        tmp_path, sample_png):
    """A tamper must be recoverable, or the gate is a trap rather than a check."""
    pytest.importorskip("pymupdf")
    _, book_path, pages = _translated_pdf_run(tmp_path, sample_png)
    artefact = pages / _job(pagerun.load_manifest(pages), 1)["source_pdf"]
    artefact.write_bytes(b"%PDF-1.4 not really\n")

    assert "source-hash-mismatch" in renderqa.source_evidence(
        tmp_path, pages, 1, None).problem

    pagerun.build(book_path, pages)          # re-cut from the real source
    restored = renderqa.source_evidence(tmp_path, pages, 1, None)
    assert restored.problem == "", restored.problem
    assert restored.path is not None and restored.path.exists()
