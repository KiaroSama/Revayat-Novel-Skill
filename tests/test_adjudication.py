"""Independent-review intake preserves pending uncertainty and exact case coverage."""

import json
import os
from pathlib import Path
import sys

import bookir as ir
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evaluation"))
import adjudicate  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def test_adjudication_prepares_all28_cases_as_pending_with_benchmark_identity(tmp_path):
    out = tmp_path / "pending.json"
    process = ir.run_bounded([sys.executable, str(ROOT / "evaluation/adjudicate.py"),
                              "prepare", "--out", str(out)], 10,
                             env={**os.environ, "REVAYAT_NOVEL_LOG_DIR": str(tmp_path / "logs")})
    assert process.returncode == 0, process.stderr.decode("utf-8")
    matrix = json.loads(out.read_text(encoding="utf-8"))
    cases = json.loads((ROOT / "evaluation/cases.json").read_text(encoding="utf-8"))
    assert len(matrix["cases"]) == 28
    assert [c["id"] for c in matrix["cases"]] == [c["id"] for c in cases["cases"]]
    assert matrix["benchmark_sha256"] == ir.sha256_file(ROOT / "evaluation/cases.json")
    assert all(c["status"] == "pending" and c["reviews"] == [] for c in matrix["cases"])
    assert [c["rubric"] for c in matrix["cases"]] == [c["rationale"] for c in cases["cases"]]
    assert all(row["accepted_controls"] == len(case["accepts"]) and row["wrong_controls"] == len(case["fails"])
               for row, case in zip(matrix["cases"], cases["cases"]))
    assert "overall" not in matrix
    assert len(list((tmp_path / "logs").glob("adjudicate_*.log"))) == 1


@pytest.mark.parametrize("damage", ["digest", "missing", "duplicate", "foreign", "language", "fake-approved"])
def test_intake_refuses_stale_incomplete_or_unsupported_review(damage):
    matrix = adjudicate.prepare(ROOT / "evaluation/cases.json")
    if damage == "digest":
        matrix["benchmark_sha256"] = "b" * 64
    elif damage == "missing":
        matrix["cases"].pop()
    elif damage == "duplicate":
        matrix["cases"][1]["id"] = matrix["cases"][0]["id"]
    elif damage == "foreign":
        matrix["cases"][0]["id"] = "foreign-01"
    elif damage == "language":
        matrix["cases"][0]["source_language"] = "ja"
    else:
        matrix["cases"][0]["status"] = "accept"
    with pytest.raises(ValueError):
        adjudicate.validate(matrix, ROOT / "evaluation/cases.json")


def test_pending_and_disputed_evidence_are_retained_without_aggregate():
    matrix = adjudicate.prepare(ROOT / "evaluation/cases.json")
    assert adjudicate.validate(matrix, ROOT / "evaluation/cases.json") == matrix
    row = matrix["cases"][0]
    # Synthetic metadata exercises intake, never claims actual human approval.
    evidence = {"reviewer_id": "synthetic-reviewer-1", "qualification": "synthetic metadata only",
                "independent": True, "source_language": "en", "reviewed_utc": "2026-10-08T20:00:00Z",
                "evidence_ref": "synthetic-record", "evidence_sha256": "a" * 64,
                "verdict": "accept", "notes": "Synthetic accepted comparison control"}
    row["reviews"] = [evidence, dict(evidence, reviewer_id="synthetic-reviewer-2", verdict="reject")]
    row["status"], row["pending_reason"] = "disputed", None
    assert adjudicate.validate(matrix, ROOT / "evaluation/cases.json")["cases"][0]["status"] == "disputed"
    assert "overall" not in matrix


@pytest.mark.parametrize("damage", ["independence", "identity", "qualification", "timestamp", "evidence", "verdict-type"])
def test_review_metadata_is_typed_complete_and_bounded(damage):
    matrix = adjudicate.prepare(ROOT / "evaluation/cases.json")
    row = matrix["cases"][0]
    evidence = {"reviewer_id": "synthetic-1", "qualification": "synthetic only", "independent": True,
                "source_language": "en", "reviewed_utc": "2026-10-08T20:00:00Z",
                "evidence_ref": "synthetic-record", "evidence_sha256": "a" * 64,
                "verdict": "unknown", "notes": "Synthetic incomplete human evidence"}
    row["reviews"] = [evidence]
    if damage == "independence":
        evidence["independent"] = False
    elif damage == "identity":
        row["reviews"].append(dict(evidence))
    elif damage == "qualification":
        evidence.pop("qualification")
    elif damage == "timestamp":
        evidence["reviewed_utc"] = "2026-99-99T20:00:00Z"
    elif damage == "evidence":
        evidence["evidence_sha256"] = "not-a-hash"
    else:
        evidence["verdict"] = []
    with pytest.raises(ValueError):
        adjudicate.validate(matrix, ROOT / "evaluation/cases.json")
