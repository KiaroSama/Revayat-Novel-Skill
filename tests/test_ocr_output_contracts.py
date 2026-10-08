"""OCR artifacts must cover the source, not merely contain some text."""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys

import pymupdf
import pytest
from PIL import Image

import bookir as ir
import extract
from read_pdf import read_pdf


def _source(path):
    buffer = io.BytesIO()
    Image.new("RGB", (30, 40), "navy").save(buffer, format="PNG")
    with pymupdf.open() as document:
        page = document.new_page(width=300, height=400)
        page.insert_text((30, 60), "Native bold text.", fontname="hebo")
        page = document.new_page(width=300, height=400)
        page.insert_image(page.rect, stream=buffer.getvalue())
        document.save(path)


def _converter(monkeypatch, source, *, recognized=True, truncate=False,
               reverse=False, changed=False):
    monkeypatch.setattr(extract, "find_ocrmypdf", lambda: [sys.executable, "-m", "ocrmypdf"])

    def run(command, timeout, **kwargs):
        with pymupdf.open(source) as document:
            if changed:
                buffer = io.BytesIO()
                Image.new("RGB", (30, 40), "white").save(buffer, format="PNG")
                document[1].replace_image(document[1].get_images()[0][0],
                                          stream=buffer.getvalue())
            if recognized:
                document[1].insert_text((30, 60), "Recognized text.", render_mode=3)
            if truncate:
                document.select([0])
            if reverse:
                document.select([1, 0])
            document.save(command[-1])
        return subprocess.CompletedProcess(command, 4, b"", b"structural warning")

    monkeypatch.setattr(ir, "run_bounded", run)


@pytest.mark.parametrize("failure", ["truncated", "silent", "reordered"])
def test_incomplete_output_preserves_previous_artifact(tmp_path, monkeypatch, failure):
    source, output = tmp_path / "source.pdf", tmp_path / "ocr.pdf"
    _source(source)
    output.write_bytes(b"previous artifact")
    _converter(monkeypatch, source, recognized=failure != "silent",
               truncate=failure == "truncated", reverse=failure == "reordered")
    before = source.read_bytes()
    with pytest.raises(extract.ExtractError):
        extract.run_ocr(source, output, kind="mixed", deskew=False)
    assert output.read_bytes() == b"previous artifact"
    assert source.read_bytes() == before


def test_complete_nonzero_artifact_retains_native_mixed_emphasis(tmp_path, monkeypatch):
    source, output = tmp_path / "source.pdf", tmp_path / "ocr.pdf"
    _source(source)
    _converter(monkeypatch, source)
    result = extract.run_ocr(source, output, kind="mixed", deskew=False)
    assert result["exit"] == 4 and result["warning"]
    book = read_pdf(str(output), tmp_path / "assets", ocr_text=True, ocr_pages=[2])
    native = next(block for block in book["blocks"] if block.get("page") == 1)
    assert "**Native bold text.**" in native["text"]
    recognized = next(block for block in book["blocks"] if block.get("page") == 2
                      and block.get("text"))
    assert recognized["text"] == "Recognized text."
    assert result["coverage"]["ocr_pages"] == [2]


def test_explicit_image_role_allows_silent_plate(tmp_path, monkeypatch):
    source, output = tmp_path / "source.pdf", tmp_path / "ocr.pdf"
    _source(source)
    roles = tmp_path / "roles.json"
    roles.write_text(json.dumps({"version": 1, "source_sha256": ir.sha256_file(source),
                     "pages": [{"page": 1, "role": "text"},
                               {"page": 2, "role": "image"}]}), encoding="utf-8")
    _converter(monkeypatch, source, recognized=False)
    result = extract.run_ocr(source, output, kind="mixed", page_roles=roles)
    assert result["coverage"]["pages"][1]["role"] == "image"
    assert result["coverage"]["pages"][1]["origin"] == "operator"


def test_image_role_preserves_artwork_when_ocr_recognizes_a_caption(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    _source(source)
    roles = tmp_path / "roles.json"
    roles.write_text(json.dumps({"version": 1, "source_sha256": ir.sha256_file(source),
                     "pages": [{"page": 1, "role": "text"},
                               {"page": 2, "role": "image"}]}), encoding="utf-8")
    _converter(monkeypatch, source)
    parser = argparse.ArgumentParser()
    extract.add_arguments(parser)
    work = tmp_path / "work"
    args = parser.parse_args([str(source), "--out", str(work), "--clean-scan", "off",
                             "--ocr-page-roles", str(roles)])
    before = source.read_bytes()
    extract.extract(args)
    book = ir.load_book(work / "book.json")
    images = [block for block in book["blocks"]
              if block["type"] == "image" and block["page"] == 2]
    assert len(images) == 1
    assert images[0]["source_role"] == "illustration"
    assert ir.sha256_file(work / "assets" / images[0]["asset"]) == images[0]["sha256"]
    assert any(block.get("text") == "Recognized text." for block in book["blocks"])
    assert book["source"]["ocr_coverage"]["complete"] is True
    assert source.read_bytes() == before


def test_changed_raster_uses_bound_controlled_mapping_receipt(tmp_path, monkeypatch):
    source, output = tmp_path / "source.pdf", tmp_path / "ocr.pdf"
    _source(source)
    _converter(monkeypatch, source, changed=True)
    monkeypatch.setattr("ocrvalidation.version", lambda package: "17.13.0")
    result = extract.run_ocr(source, output, kind="mixed", deskew=True)
    proof = result["proof"]
    assert proof["source_sha256"] == ir.sha256_file(source)
    assert proof["output_sha256"] == ir.sha256_file(output)
    assert proof["pages"] == 2
    assert proof["options"]["deskew"] is True
    assert result["coverage"]["pages"][1]["mapping"] == "engine-indexed"


def test_changed_raster_from_unverified_launcher_is_not_engine_evidence(tmp_path, monkeypatch):
    source, output = tmp_path / "source.pdf", tmp_path / "ocr.pdf"
    _source(source)
    output.write_bytes(b"previous artifact")
    _converter(monkeypatch, source, changed=True)
    monkeypatch.setattr(extract, "find_ocrmypdf", lambda: ["unbound-external-ocr"])
    with pytest.raises(extract.ExtractError, match="mapping unverified"):
        extract.run_ocr(source, output, kind="mixed", deskew=True)
    assert output.read_bytes() == b"previous artifact"


def test_zero_text_approved_plate_and_blank_are_complete(tmp_path, monkeypatch):
    source, output = tmp_path / "plates.pdf", tmp_path / "ocr.pdf"
    buffer = io.BytesIO()
    Image.new("RGB", (30, 40), "navy").save(buffer, format="PNG")
    with pymupdf.open() as document:
        page = document.new_page(width=300, height=400)
        page.insert_image(page.rect, stream=buffer.getvalue())
        document.new_page(width=300, height=400)
        document.save(source)
    roles = tmp_path / "roles.json"
    roles.write_text(json.dumps({"version": 1, "source_sha256": ir.sha256_file(source),
                     "pages": [{"page": 1, "role": "image"},
                               {"page": 2, "role": "blank"}]}), encoding="utf-8")
    _converter(monkeypatch, source, recognized=False)
    result = extract.run_ocr(source, output, kind="scanned", page_roles=roles)
    assert result["coverage"]["complete"] is True
    assert result["coverage"]["characters"] == 0
    assert result["coverage"]["unknown_pages"] == []


@pytest.mark.parametrize("defect", ["foreign-source", "duplicate-page", "invalid-role"])
def test_invalid_role_envelope_refuses_without_replacing_output(tmp_path, monkeypatch, defect):
    source, output = tmp_path / "source.pdf", tmp_path / "ocr.pdf"
    _source(source)
    output.write_bytes(b"previous artifact")
    envelope = {"version": 1, "source_sha256": ir.sha256_file(source),
                "pages": [{"page": 1, "role": "text"}, {"page": 2, "role": "image"}]}
    if defect == "foreign-source":
        envelope["source_sha256"] = "0" * 64
    elif defect == "duplicate-page":
        envelope["pages"][1]["page"] = 1
    else:
        envelope["pages"][1]["role"] = "unknown"
    roles = tmp_path / "roles.json"
    roles.write_text(json.dumps(envelope), encoding="utf-8")
    _converter(monkeypatch, source)
    with pytest.raises(extract.ExtractError):
        extract.run_ocr(source, output, kind="mixed", page_roles=roles)
    assert output.read_bytes() == b"previous artifact"
