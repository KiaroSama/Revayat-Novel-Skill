"""Source-page artifacts, geometry, provenance and visual identity."""

from __future__ import annotations

import json
from pathlib import Path
import pytest
import bookir as ir
import pagerun
import preview
import renderqa
import review
from page_fixtures import _book, _book_from_pdf, _built, _job, _mark, _mixed_pdf, _odd_pdf, _prose, _save, _translated_pdf_run

def test_each_source_page_becomes_a_pdf_of_exactly_that_one_page(tmp_path, sample_png):
    pdf = _odd_pdf(tmp_path / "source.pdf", sample_png)
    pymupdf = pytest.importorskip("pymupdf")
    manifest, pages = _built(_book_from_pdf(pdf, 3), tmp_path)

    for number in (1, 2, 3):
        entry = _job(manifest, number)
        assert entry["source_pdf"] == f"source/page-{number:04d}.pdf"
        assert entry["source_pdf_page"] == number
        split = pages / entry["source_pdf"]
        with pymupdf.open(str(split)) as one:
            assert one.page_count == 1


def test_the_split_page_is_the_original_page_and_not_a_picture_of_it(
        tmp_path, sample_png):
    """Copied with its resources: the text layer and the embedded image stream
    both survive, which a rasterised split would destroy."""
    pymupdf = pytest.importorskip("pymupdf")
    pdf = _odd_pdf(tmp_path / "source.pdf", sample_png)
    manifest, pages = _built(_book_from_pdf(pdf, 3), tmp_path)

    with pymupdf.open(str(pdf)) as source:
        for number in (1, 2, 3):
            with pymupdf.open(str(pages / _job(manifest, number)["source_pdf"])) as one:
                original, split = source[number - 1], one[0]
                assert split.get_text() == original.get_text()
                assert len(split.get_images(full=True)) == \
                    len(original.get_images(full=True))
    # …and the picture really was there to be kept.
    with pymupdf.open(str(pages / _job(manifest, 1)["source_pdf"])) as one:
        assert one[0].get_images(full=True)


def test_rotation_and_page_box_survive_the_split(tmp_path, sample_png):
    pymupdf = pytest.importorskip("pymupdf")
    pdf = _odd_pdf(tmp_path / "source.pdf", sample_png)
    manifest, pages = _built(_book_from_pdf(pdf, 3), tmp_path)

    with pymupdf.open(str(pdf)) as source:
        for number in (1, 2, 3):
            with pymupdf.open(str(pages / _job(manifest, number)["source_pdf"])) as one:
                original, split = source[number - 1], one[0]
                assert split.rotation == original.rotation
                assert tuple(split.mediabox) == tuple(original.mediabox)
                assert tuple(split.cropbox) == tuple(original.cropbox)
                assert tuple(split.rect) == tuple(original.rect)

    # The rotated page is the one that would silently come back upright, and
    # the narrow one the one that would come back A4.
    with pymupdf.open(str(pages / _job(manifest, 2)["source_pdf"])) as turned:
        assert turned[0].rotation == 90
    with pymupdf.open(str(pages / _job(manifest, 3)["source_pdf"])) as narrow:
        assert (narrow[0].rect.width, narrow[0].rect.height) == (200, 800)


def test_the_manifest_hash_is_the_hash_of_the_file_on_disk(tmp_path, sample_png):
    pdf = _odd_pdf(tmp_path / "source.pdf", sample_png)
    manifest, pages = _built(_book_from_pdf(pdf, 3), tmp_path)

    for number in (1, 2, 3):
        entry = _job(manifest, number)
        assert entry["source_pdf_sha256"] == \
            ir.sha256_file(pages / entry["source_pdf"])
    hashes = {_job(manifest, n)["source_pdf_sha256"] for n in (1, 2, 3)}
    assert len(hashes) == 3, "three different pages hashed the same"


def test_a_stranger_in_the_page_pdf_directory_stops_the_run_by_name(
        tmp_path, sample_png):
    """Another stage packages this tree. Nothing here may be overwritten, and
    nothing that is not ours may be swept up with it."""
    pytest.importorskip("pymupdf")
    pdf = _odd_pdf(tmp_path / "source.pdf", sample_png)
    book_path = _save(_book_from_pdf(pdf, 3), tmp_path)
    pages = tmp_path / "pages"

    pagerun.build(book_path, pages)
    theirs = pages / "source" / "package.manifest"
    theirs.write_text("someone else's work", encoding="utf-8")

    with pytest.raises(pagerun.SourceCollision) as refusal:
        pagerun.build(book_path, pages)
    assert "package.manifest" in str(refusal.value)
    assert theirs.read_text(encoding="utf-8") == "someone else's work"

    theirs.unlink()
    assert pagerun.build(book_path, pages)["chunks"], "the refusal was not the end"


def test_the_next_job_names_its_own_page_and_the_whole_book_separately(
        tmp_path, sample_png):
    """Two different files, and handing render QA the wrong one renders the
    wrong page: it indexes ``--source-pdf`` by page number."""
    pytest.importorskip("pymupdf")
    pdf = _odd_pdf(tmp_path / "source.pdf", sample_png)
    _, pages = _built(_book_from_pdf(pdf, 3), tmp_path)

    upcoming = pagerun.next_page(pages)
    assert Path(upcoming["page_pdf"]) == pages / "source" / "page-0001.pdf"
    assert Path(upcoming["reference_pdf"]) == pdf
    assert Path(upcoming["page_pdf"]).exists()


def test_a_book_with_no_pdf_behind_it_simply_has_no_page_pdfs(tmp_path):
    """EPUB and DOCX have no printed page to compare against, and saying so is
    the answer — not a path to a file nobody wrote."""
    manifest, pages = _built(_book([_prose(1, "One"), _prose(2, "Two")]), tmp_path)
    assert manifest["reference_pdf"] == ""
    assert all(entry["source_pdf"] == "" for entry in manifest["chunks"])
    assert not (pages / "source").exists()


def test_the_manifest_names_the_pdf_the_book_was_actually_read_from(
        tmp_path, sample_pdf):
    """A born-digital book has no ``ocr.pdf`` at all, so a hardcoded name is
    wrong for it; a mixed one has two candidates, so a name is a coin toss."""
    pytest.importorskip("pymupdf")
    from read_pdf import read_pdf

    book = read_pdf(str(sample_pdf), tmp_path / "assets")
    manifest, _ = _built(book, tmp_path)
    assert Path(manifest["reference_pdf"]) == sample_pdf
    assert pagerun.status(tmp_path / "pages")["reference_pdf"] == str(sample_pdf)


def test_the_ocr_copy_is_the_reference_when_that_is_what_was_read(
        tmp_path, sample_png):
    """The mixed case: an original beside an OCR-normalised copy. Whichever the
    extractor opened is the one the manifest has to name."""
    pytest.importorskip("pymupdf")
    original = _odd_pdf(tmp_path / "original.pdf", sample_png)
    normalised = _odd_pdf(tmp_path / "ocr.pdf", sample_png)

    manifest, _ = _built(_book_from_pdf(normalised, 3), tmp_path)
    assert Path(manifest["reference_pdf"]) == normalised
    assert Path(manifest["reference_pdf"]) != original


def test_the_reference_is_found_again_from_another_directory(
        tmp_path, sample_png, monkeypatch):
    """The path is stored as it was typed; a resume from elsewhere must not
    decide the book has no source."""
    pytest.importorskip("pymupdf")
    _odd_pdf(tmp_path / "source.pdf", sample_png)
    monkeypatch.chdir(tmp_path)
    book = _book_from_pdf(Path("source.pdf"), 3)
    ir.save_book(book, tmp_path / "book.json")

    monkeypatch.chdir(tmp_path.parent)
    manifest = pagerun.build(tmp_path / "book.json", tmp_path / "pages")
    assert Path(manifest["reference_pdf"]) == tmp_path / "source.pdf"
    assert _job(manifest, 1)["source_pdf_sha256"]


def test_each_source_page_is_laid_out_on_its_own_paper(tmp_path):
    """`book["page"]` is the dominant shape and the wrong paper for the rest.

    A preview built from the dominant setup puts the landscape plate on a
    portrait sheet, and then render QA reports the page it just mis-built.
    """
    pytest.importorskip("pymupdf")
    from read_pdf import read_pdf

    book = read_pdf(str(_mixed_pdf(tmp_path / "mixed.pdf")), tmp_path / "assets")
    lookup = ir.blocks_by_id(book)
    owners = {job["page"]: job for job in pagerun.owners(book)}

    shapes = {page: (round(g["width_pt"]), round(g["height_pt"]))
              for page, job in owners.items()
              for g in [pagerun.geometry(book, job["block_ids"], lookup, page)]}

    assert shapes[1] == (396, 612), shapes
    assert shapes[2] == (612, 396), (
        f"the landscape page came back as {shapes[2]} — it was given the "
        f"book's dominant portrait geometry"
    )
    assert shapes[3] == (468, 612), shapes

    # And the preview a reviewer actually looks at uses it.
    only = preview.page_book(book, 2)
    assert (round(only["page"]["width_pt"]), round(only["page"]["height_pt"])) \
        == (612, 396)


def test_a_new_illustration_invalidates_the_page_it_is_on(tmp_path):
    """Prose unchanged, picture replaced: the accepted page must not survive.

    This is the shape that made the gate worth widening. Every translatable
    word is identical, so a text-only digest reports nothing — and the page
    keeps an `accepted` state carrying a visual review of an image that is no
    longer in the book.
    """
    pymupdf = pytest.importorskip("pymupdf")
    from read_pdf import read_pdf

    def paint(path: Path, colour: tuple[float, float, float]) -> Path:
        doc = pymupdf.open()
        page = doc.new_page(width=396, height=612)
        page.insert_text((60, 100), "The prose that never changes at all.",
                         fontsize=11, fontname="helv")
        page.draw_rect(pymupdf.Rect(60, 200, 300, 400), color=colour, fill=colour)
        doc.save(str(path))
        doc.close()
        return path

    source = paint(tmp_path / "book.pdf", (1, 0, 0))
    book_path = _save(read_pdf(str(source), tmp_path / "assets"), tmp_path)
    pages = tmp_path / "pages"

    assert pagerun.build(book_path, pages)["invalidated"] == []
    assert pagerun.build(book_path, pages)["invalidated"] == [], (
        "an unchanged book invalidated itself on rebuild — every page of every "
        "run would be thrown away and re-translated"
    )
    _mark(tmp_path, 1, "accepted")

    paint(source, (0, 0, 1))          # same words, same file, new picture
    assert pagerun.build(book_path, pages)["invalidated"] == [1], (
        "the picture changed and the page was not invalidated — an accepted "
        "page still carries a review of an image the book no longer has"
    )


def test_a_page_fingerprint_is_reproducible(tmp_path):
    """The property the whole invalidation rests on.

    The obvious identity — the hash of the split one-page PDF — is not
    reproducible: PyMuPDF stamps what it writes, and splitting one unchanged
    source three times measured three different hashes. Keying a page on that
    invalidates the entire book on every rebuild.
    """
    pymupdf = pytest.importorskip("pymupdf")

    def make(path: Path, width: int = 396, colour=(1, 0, 0)) -> Path:
        doc = pymupdf.open()
        page = doc.new_page(width=width, height=612)
        page.insert_text((60, 100), "Identical prose.", fontsize=11, fontname="helv")
        page.draw_rect(pymupdf.Rect(60, 200, 300, 400), color=colour, fill=colour)
        doc.save(str(path))
        doc.close()
        return path

    same = pagerun.page_fingerprint(make(tmp_path / "a.pdf"), 1)
    assert same, "no fingerprint at all"
    assert pagerun.page_fingerprint(make(tmp_path / "b.pdf"), 1) == same, (
        "two identical sources fingerprinted differently — every rebuild would "
        "invalidate every page"
    )
    assert pagerun.page_fingerprint(make(tmp_path / "c.pdf", colour=(0, 0, 1)), 1)         != same, "a repainted plate did not change the fingerprint"
    assert pagerun.page_fingerprint(make(tmp_path / "d.pdf", width=612), 1) != same,         "a different trim did not change the fingerprint"


def test_a_crop_that_moves_changes_the_page_a_reader_sees(tmp_path):
    """The escape `page.rect` cannot see, because it normalises the origin.

    Two files, one content stream, one 400x600 rect, one rotation — and crop
    boxes at x=0 and x=50. `page.rect` reports `(0, 0, 400, 600)` for both,
    which is why hashing width and height alone let a visibly different page
    through. Measured before the fix: identical fingerprints, different pixels.
    """
    pymupdf = pytest.importorskip("pymupdf")

    def make(path: Path, crop, media=(500, 600)) -> Path:
        doc = pymupdf.open()
        page = doc.new_page(width=media[0], height=media[1])
        page.insert_text((60, 100), "Identical prose.", fontsize=11, fontname="helv")
        page.set_cropbox(pymupdf.Rect(*crop))
        doc.save(str(path))
        doc.close()
        return path

    left = make(tmp_path / "left.pdf", (0, 0, 400, 600))
    moved = make(tmp_path / "moved.pdf", (50, 0, 450, 600))

    def seen(path: Path) -> bytes:
        document = pymupdf.open(str(path))
        try:
            return document[0].get_pixmap(dpi=72).samples
        finally:
            document.close()

    with pymupdf.open(str(left)) as a, pymupdf.open(str(moved)) as b:
        assert a[0].rect.width == b[0].rect.width
        assert a[0].rect.height == b[0].rect.height
        assert a[0].rotation == b[0].rotation

    assert seen(left) != seen(moved), (
        "the fixture is wrong: these two are supposed to render differently"
    )
    assert pagerun.page_fingerprint(left, 1) != pagerun.page_fingerprint(moved, 1), (
        "a crop box moved across the media left the fingerprint unchanged — an "
        "accepted page can now change visibly and stay accepted"
    )

    # A media box widened under an unchanged crop is the other half of the pair.
    wider = make(tmp_path / "wider.pdf", (0, 0, 400, 600), media=(700, 600))
    assert pagerun.page_fingerprint(wider, 1) != pagerun.page_fingerprint(left, 1), (
        "a changed media box left the fingerprint unchanged"
    )


def test_an_annotation_drawn_over_a_page_is_part_of_it(tmp_path):
    """Annotations are not in the content stream, and they are on the paper.

    Found while proving the crop-box fix, by asking what else renders without
    touching a stream. A highlight left on a scan measured the same stream hash
    and different pixels — the same escape, through another door.
    """
    pymupdf = pytest.importorskip("pymupdf")

    def make(path: Path, *, highlight: bool) -> Path:
        doc = pymupdf.open()
        page = doc.new_page(width=400, height=600)
        page.insert_text((60, 100), "Identical prose.", fontsize=11, fontname="helv")
        if highlight:
            page.add_highlight_annot(pymupdf.Rect(55, 85, 300, 108))
        doc.save(str(path))
        doc.close()
        return path

    plain = make(tmp_path / "plain.pdf", highlight=False)
    noted = make(tmp_path / "noted.pdf", highlight=True)

    def streams(path: Path) -> bytes:
        document = pymupdf.open(str(path))
        try:
            page = document[0]
            return b"".join(document.xref_stream(x) or b""
                            for x in page.get_contents())
        finally:
            document.close()

    assert streams(plain) == streams(noted), (
        "the fixture is wrong: the highlight was supposed to leave the content "
        "stream alone, which is the whole point of the test"
    )
    assert pagerun.page_fingerprint(plain, 1) != pagerun.page_fingerprint(noted, 1), (
        "an annotation drawn over the page left the fingerprint unchanged"
    )


def test_a_moved_crop_invalidates_its_own_page_and_no_other(tmp_path):
    """Local damage stays local: the point of a per-page identity.

    A source identity that changes for every page whenever one page changes is
    the failure this whole design exists to avoid — it throws away a resumable
    run's progress on every rebuild.
    """
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "book.pdf"

    def make(second_crop) -> Path:
        doc = pymupdf.open()
        for index, crop in enumerate((None, second_crop, None)):
            page = doc.new_page(width=500, height=600)
            page.insert_text((60, 100), f"Page {index + 1}.", fontsize=11,
                             fontname="helv")
            page.set_cropbox(pymupdf.Rect(*(crop or (0, 0, 400, 600))))
        if path.exists():
            path.unlink()
        doc.save(str(path))
        doc.close()
        return path

    make(None)
    before = [pagerun.page_fingerprint(path, n) for n in (1, 2, 3)]
    make((50, 0, 450, 600))
    after = [pagerun.page_fingerprint(path, n) for n in (1, 2, 3)]

    assert after[1] != before[1], "the page whose crop moved was not invalidated"
    assert [after[0], after[2]] == [before[0], before[2]], (
        f"a change to page 2 invalidated its neighbours too: {before} -> {after}"
    )


def test_a_pdf_whose_source_moved_is_refused_a_page_run(tmp_path, sample_png):
    """Losing the source file is not a new source format.

    Before this, `reference_pdf` returned None, `build` carried on, and the
    manifest came out with an empty `reference_pdf` and an empty `source_pdf`
    on every entry — which every later gate read as "a format that has no
    source pages", exactly what DOCX and EPUB look like. The book then walked
    all the way to `accepted` with nothing ever compared against it.
    """
    pytest.importorskip("pymupdf")
    pdf = _odd_pdf(tmp_path / "source.pdf", sample_png)
    book_path = _save(_book_from_pdf(pdf, 3), tmp_path)
    pdf.unlink()

    with pytest.raises(pagerun.SourceUnavailable) as refused:
        pagerun.build(book_path, tmp_path / "pages")
    assert "PDF" in str(refused.value)
    assert not (tmp_path / "pages" / "manifest.json").exists(), (
        "a manifest was written for a run that cannot compare anything")


def test_a_non_pdf_book_still_needs_no_source_page(tmp_path):
    """The gate must not fire on a book that never had a source page."""
    book = _book([_prose(1, "First"), _prose(2, "Second")])
    book["source"]["format"] = "epub"
    book_path = _save(book, tmp_path)

    manifest = pagerun.build(book_path, tmp_path / "pages")
    assert manifest["source_format"] == "epub"
    assert manifest["reference_pdf"] == ""
    assert pagerun.needs_source_page(manifest) is False
    assert pagerun.missing_source_render(tmp_path / "pages", 1) is None


def test_the_source_requirement_survives_an_empty_path(tmp_path, sample_png):
    """`source_format` is the authority, not whether a path is filled in."""
    pytest.importorskip("pymupdf")
    _, _, pages = _translated_pdf_run(tmp_path, sample_png)

    manifest = pagerun.load_manifest(pages)
    assert manifest["source_format"] == "pdf"
    assert pagerun.needs_source_page(manifest) is True

    # Exactly the state the old code produced: a PDF run, every path blanked.
    hollow = json.loads(json.dumps(manifest))
    hollow["reference_pdf"] = ""
    for entry in hollow["chunks"]:
        entry["source_pdf"] = ""
        entry["source_pdf_sha256"] = ""
    assert pagerun.needs_source_page(hollow) is True, (
        "blanking the paths made the source requirement disappear")

    (pages / "manifest.json").write_text(
        json.dumps(hollow, ensure_ascii=False), encoding="utf-8")
    refused = pagerun.missing_source_render(pages, 1)
    assert refused and refused["refused"] in {"no-source-render", "no-render-qa"}


def test_an_older_manifest_with_no_source_format_is_still_read_as_pdf(
        tmp_path, sample_png):
    """A manifest written before the field existed must not lose the gate."""
    pytest.importorskip("pymupdf")
    _, _, pages = _translated_pdf_run(tmp_path, sample_png)
    manifest = pagerun.load_manifest(pages)
    del manifest["source_format"]
    assert pagerun.needs_source_page(manifest) is True, (
        "an older PDF manifest stopped requiring a source page")


def test_an_explicit_source_pdf_cannot_replace_the_manifest_artefact(
        tmp_path, sample_png):
    """Any readable PDF could stand in as a manifested page's evidence.

    The report then said a source had been rendered, and nothing downstream
    could tell it was a different book.
    """
    pytest.importorskip("pymupdf")
    _, book_path, pages = _translated_pdf_run(tmp_path, sample_png)
    impostor = _odd_pdf(tmp_path / "someone-elses.pdf", sample_png)

    origin = renderqa.source_evidence(tmp_path, pages, 1, impostor)
    assert origin.path is not None
    assert origin.path != impostor, "the override replaced the manifest's page"
    assert origin.path == pages / _job(pagerun.load_manifest(pages), 1)["source_pdf"]
    assert origin.sha256, "the manifest's recorded hash was not carried"


def test_replacing_the_source_artefact_is_caught_by_its_hash(tmp_path, sample_png):
    """A file at the right path is not the file the run committed to."""
    pymupdf = pytest.importorskip("pymupdf")
    _, book_path, pages = _translated_pdf_run(tmp_path, sample_png)
    artefact = pages / _job(pagerun.load_manifest(pages), 1)["source_pdf"]

    assert renderqa.source_evidence(tmp_path, pages, 1, None).problem == ""

    # A different, perfectly readable one-page PDF at exactly the right path.
    other = _odd_pdf(tmp_path / "other.pdf", sample_png)
    with pymupdf.open(str(other)) as book, pymupdf.open() as one:
        one.insert_pdf(book, from_page=1, to_page=1)
        artefact.unlink()
        one.save(str(artefact))

    tampered = renderqa.source_evidence(tmp_path, pages, 1, None)
    assert "source-hash-mismatch" in tampered.problem, tampered.problem
    assert tampered.path is None

    written = renderqa.check(tmp_path, book_path, 1)
    assert written["verified"] is False and written["ok"] is False
    assert "source-hash-mismatch" in written.get("unverified", "")
    assert not written.get("source_evidence")

    review.record(tmp_path, 1, dict.fromkeys(review.QUESTIONS, True),
                  note="claims to have compared them")
    refused = pagerun.missing_source_render(pages, 1)
    assert refused and refused["refused"] == "no-source-render"
    taken = pagerun.accept(book_path, pages, 1)
    assert taken["ok"] is False, f"accepted against a swapped source: {taken}"


def test_a_deleted_source_artefact_is_named_missing_not_mismatched(
        tmp_path, sample_png):
    """`source-missing` and `source-hash-mismatch` are different diagnoses."""
    pytest.importorskip("pymupdf")
    _, _, pages = _translated_pdf_run(tmp_path, sample_png)
    (pages / _job(pagerun.load_manifest(pages), 2)["source_pdf"]).unlink()

    gone = renderqa.source_evidence(tmp_path, pages, 2, None)
    assert "source-missing" in gone.problem, gone.problem
    assert gone.required is True


def test_a_run_with_no_manifest_still_honours_an_explicit_source(
        tmp_path, sample_png):
    """Synthetic fixtures and one-off diagnostics keep working."""
    pytest.importorskip("pymupdf")
    pdf = _odd_pdf(tmp_path / "loose.pdf", sample_png)
    origin = renderqa.source_evidence(tmp_path, tmp_path / "nowhere", 2, pdf)
    assert origin.path == pdf
    assert origin.index == 1, "a multi-page file still needs the page index"
    assert origin.required is False
