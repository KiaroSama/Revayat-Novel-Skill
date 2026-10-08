"""Real backend batch equivalence and corrupt-document isolation."""

from __future__ import annotations

import pytest
import wordrender
from render_fixtures import _five_documents

pytestmark = pytest.mark.render

def test_a_batch_renders_every_document_identically_to_one_at_a_time(tmp_path):
    """Identical output is the premise the whole saving rests on.

    Measured in spike 011 on five documents: same page count, same text, same
    embedded fonts, same page width. Re-asserted here because state leaking
    between documents in one session is the correctness risk of batching.
    """
    pytest.importorskip("pymupdf")
    import pymupdf

    if not wordrender.backend():
        pytest.skip(f"no renderer here: {wordrender.unavailable_reason()}")
    docs = _five_documents(tmp_path)

    produced, failed, backend = wordrender.render_many(docs, tmp_path / "batch")
    assert not failed, failed
    assert set(produced) == set(docs), "the batch did not render every document"

    for docx in docs:
        alone, _ = wordrender.render(docx, tmp_path / "alone")
        with pymupdf.open(produced[docx]) as batched, pymupdf.open(alone) as single:
            assert len(batched) == len(single)
            assert ([p.get_text("text") for p in batched]
                    == [p.get_text("text") for p in single])
            assert [round(p.rect.width, 2) for p in batched] == \
                   [round(p.rect.width, 2) for p in single]


def test_one_corrupt_document_does_not_take_the_batch_down(tmp_path):
    """Measured on Word: `com_error` in 0.04s, and the documents after it rendered.

    A batch that loses its tail to one bad page is worse than no batch — the
    per-page design gets that isolation for free and batching must not give it up.

    Asserted on **whichever backend this machine has**, not only Word. It was
    Word-only at first and the `nothing skipped` CI job rejected that, correctly:
    the property is not COM-specific, and LibreOffice batching is the backend this
    project has never measured, so it is the one that most needs saying. Proving it
    there also forced a real fix — that branch reported a failed document in
    neither map, conflating "it failed" with "nobody reached it".
    """
    pytest.importorskip("pymupdf")
    if not wordrender.backend():
        pytest.skip(f"no renderer here: {wordrender.unavailable_reason()}")

    docs = _five_documents(tmp_path, count=3)
    docs[1].write_bytes(b"PK\x03\x04 this is not a document at all")

    produced, failed, _ = wordrender.render_many(docs, tmp_path / "batch")
    assert docs[1] in failed, "the corrupt document was not reported as failed"
    assert docs[0] in produced and docs[2] in produced, (
        f"a bad document took its neighbours down: produced={list(produced)}")
