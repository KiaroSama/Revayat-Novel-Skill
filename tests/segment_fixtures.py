"""Explicit current request identities for hand-authored segment contracts."""

import json

import bookir as ir
import provenance


def write_segment_manifest(book_path, chunks, manifest):
    book = ir.load_book(book_path)
    parts = [("b00001#1", "para", "Part one "),
             ("b00001#2", "para", "and part two together.")]
    assert "".join(text for _, _, text in parts) == book["blocks"][0]["text"]
    records = {item["id"]: item for item in provenance.unit_records(parts)}
    for entry in manifest["chunks"]:
        units = [unit for unit in parts if unit[0] in entry["unit_ids"]]
        entry["unit_spans"] = [records[unit[0]] for unit in units]
        entry["source_chars"] = sum(len(unit[2]) for unit in units)
        entry["source_sha256"] = provenance.source_fingerprint(
            book, entry["block_ids"], entry["unit_spans"], glossary=None, neighbours=None)
        payload = "\n".join(f"@@ {unit} {kind}\n{text}\n" for unit, kind, text in units)
        entry["request"] = provenance.request_token(payload)
        ir.write_text(chunks / entry["file"], provenance.request_line(entry["request"]) + "\n" + payload)
    ir.write_text(chunks / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
