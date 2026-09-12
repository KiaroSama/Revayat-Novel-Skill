"""Lay a document out once per version of it, not once per question asked.

The documented document-QA flow is `check` → `review` → `check`: the first check
produces the pages a reviewer looks at, the second confirms the review still
describes them. Both rendered the whole book, so a finished novel was laid out
twice — minutes of Word on a 300-page book, for a document that did not change
between the two calls.

A render is a pure function of the document and the renderer, so the second one
was never a different answer. These tests pin that: the same document renders
once, a changed document renders again, and the reuse is keyed on content rather
than on a filename existing.

Fixtures are local on purpose. Importing helpers from another test module is a
pattern this project has one unexplained whole-suite failure around, recorded in
`.ai/TESTING_NOTES.md`, and a twelve-line book builder is not worth that risk.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bookir as ir  # noqa: E402
import renderqa  # noqa: E402
import wordrender  # noqa: E402
from build_docx import Builder, add_arguments  # noqa: E402

pymupdf = pytest.importorskip("pymupdf")

PERSIAN = "متن فارسی یکتا برای آزمون کش رندر است و باید روی صفحه بیاید."


def _built(tmp_path: Path, text: str = PERSIAN, name: str = "book.fa.docx") -> Path:
    """One built document, the smallest thing Word will lay out."""
    book = ir.new_book(lang_source="en", lang_target="fa-IR")
    block = ir.make_block("paragraph", 1, page=1, bbox=[72, 90, 320, 140],
                          text="An English paragraph here.")
    block["target"] = text
    book["blocks"] = [block]
    book_path = tmp_path / f"{name}.book.json"
    ir.save_book(book, book_path)

    parser = argparse.ArgumentParser()
    add_arguments(parser)
    options = parser.parse_args(["--book", "x", "--out", "y",
                                 "--font", "Tahoma", "--no-toc"])
    docx = tmp_path / name
    Builder(ir.load_book(book_path), tmp_path, options).build(docx)
    return docx


@pytest.fixture
def counted(monkeypatch):
    """Every real lay-out, counted, with the real renderer still doing the work.

    For the one test whose claim is about the documented flow end to end. The
    cache-logic tests use `stubbed` instead: they ask how many times the renderer
    was *called*, and laying a document out for real to answer that is 16 seconds
    apiece for evidence a stub gives exactly.

    Patched on the `wordrender` module rather than on a name `renderqa` imported,
    because `render_docx` resolves the attribute at call time — a module alias is
    patchable, a `from … import` alias bound at import is not.
    """
    calls: list[str] = []
    original = wordrender.render

    def counting(docx, out_dir, **kwargs):
        calls.append(Path(docx).name)
        return original(docx, out_dir, **kwargs)

    monkeypatch.setattr(wordrender, "render", counting)
    return calls


@pytest.fixture
def stubbed(monkeypatch):
    """Counted lay-outs that produce a real one-page PDF without Word.

    The decision under test is whether `render_docx` *calls* the renderer, and
    the cache it consults is entirely in `renderqa`: the document's own hash, the
    backend name, the sidecar, the PDF's existence. None of that is faked here —
    only the minutes Word spends paginating, which is external and slow and
    cannot change the answer to "how many calls".

    The file written is a genuine PDF so anything downstream can still open it.
    """
    calls: list[str] = []

    def stub(docx, out_dir, **_kwargs):
        docx, out_dir = Path(docx), Path(out_dir)
        if not docx.is_file():
            raise wordrender.RenderError(f"{docx} is not there")
        calls.append(docx.name)
        out_dir.mkdir(parents=True, exist_ok=True)
        produced = out_dir / (docx.stem + ".pdf")
        doc = pymupdf.open()
        page = doc.new_page(width=300, height=400)
        page.insert_text((40, 60), f"stub render {len(calls)}", fontsize=11,
                         fontname="helv")
        doc.save(str(produced))
        doc.close()
        return produced, "word"

    monkeypatch.setattr(wordrender, "render", stub)
    return calls


def _render(tmp_path: Path, docx: Path) -> Path:
    return renderqa.render_docx(docx, tmp_path / "renders")


@pytest.mark.parametrize("backend", ["word", "libreoffice", ""])
def test_the_same_document_is_laid_out_once(tmp_path, stubbed, monkeypatch,
                                            backend):
    """The whole point: asking twice about an unchanged document costs one render.

    The backend name is pinned rather than inherited from the machine, because
    `wordrender.backend()` returns `""` where neither Word nor LibreOffice is
    installed — every hosted CI runner — and the first version of this cache built
    its key as `f"{backend}\\n{hash}"`. With an empty backend that key began with
    the newline `_cached_key` strips, so the stored key never equalled the computed
    one and nothing was ever reused. It passed here, on a machine with Word, and
    failed on all nine CI platforms at once. Pinning the value is what makes that
    case reachable without uninstalling Word.
    """
    monkeypatch.setattr(wordrender, "backend", lambda: backend)
    docx = _built(tmp_path)

    first = _render(tmp_path, docx)
    assert first.exists()
    assert len(stubbed) == 1, stubbed

    second = _render(tmp_path, docx)

    assert second == first
    assert len(stubbed) == 1, f"the document was laid out again: {stubbed}"
    assert second.exists()


def test_a_changed_document_is_laid_out_again(tmp_path, stubbed):
    """The negative control, and the one that matters for correctness: a reused
    render of a document that has moved on is a picture of something else."""
    docx = _built(tmp_path)
    _render(tmp_path, docx)
    assert len(stubbed) == 1

    # Same path, different content — which is exactly how a rebuild arrives.
    _built(tmp_path, text="متن تازه‌ای که هیچ‌کس ندیده است.")

    _render(tmp_path, docx)

    assert len(stubbed) == 2, "a rebuilt document reused the old render"


def test_a_render_made_by_another_backend_is_not_reused(tmp_path, stubbed,
                                                        monkeypatch):
    """`render_docx` documents that the key covers the renderer as well as the
    document, and it has to: Word and LibreOffice do not paginate identically, so
    handing back the other one's PDF answers a layout question with the wrong
    layout. Nothing proved the claim until this test."""
    monkeypatch.setattr(wordrender, "backend", lambda: "word")
    docx = _built(tmp_path)
    _render(tmp_path, docx)
    assert len(stubbed) == 1

    monkeypatch.setattr(wordrender, "backend", lambda: "libreoffice")
    _render(tmp_path, docx)

    assert len(stubbed) == 2, (
        "the other backend's render was reused; the two do not paginate alike")


def test_a_second_document_does_not_borrow_the_first_render(tmp_path, stubbed):
    """Two documents in one directory keep their own renders."""
    one = _built(tmp_path, name="one.docx")
    two = _built(tmp_path, text="سند دوم با متن دیگر.", name="two.docx")

    first = _render(tmp_path, one)
    second = _render(tmp_path, two)

    assert len(stubbed) == 2, stubbed
    assert first != second
    assert first.exists() and second.exists()


def test_a_render_whose_pdf_was_deleted_is_made_again(tmp_path, stubbed):
    """The record is not the artefact. A cache that trusts its own note over the
    file being there hands the next stage a path to nothing."""
    docx = _built(tmp_path)
    produced = _render(tmp_path, docx)
    assert len(stubbed) == 1

    produced.unlink()

    again = _render(tmp_path, docx)

    assert len(stubbed) == 2, "the missing PDF was not rebuilt"
    assert again.exists()


def test_the_documented_document_qa_flow_lays_the_book_out_once(tmp_path, counted):
    """`check` → `review` → `check`, which is what the skill tells an operator to
    run, and the reason this cache exists rather than a test-only fixture."""
    import docqa
    import review

    docx = _built(tmp_path)
    book_path = tmp_path / f"{docx.name}.book.json"

    first = docqa.check_document(tmp_path, book_path, docx)
    # Only a machine that cannot lay a document out is a reason to skip. The
    # first pass is *expected* to come back unverified — nobody has looked yet,
    # which is the gate working — and treating any `unverified` as a broken
    # machine is the mistake `test_docqa._laid_out` records in its own comment:
    # it quietly turned five tests into no tests.
    reason = str(first.get("unverified", ""))
    if any(tool in reason for tool in ("LibreOffice", "Word", "PyMuPDF")):
        pytest.skip(f"nothing here can lay a document out: {reason}")
    assert len(counted) == 1, counted

    filed = review.record(tmp_path, review.DOCUMENT,
                          dict.fromkeys(review.QUESTIONS, True),
                          render=docqa.visual_hash(tmp_path))
    assert filed["ok"], filed

    second = docqa.check_document(tmp_path, book_path, docx)

    assert second["ok"] is True, second
    assert second["verified"] is True, second
    assert len(counted) == 1, (
        f"the documented flow laid the book out {len(counted)} times")
