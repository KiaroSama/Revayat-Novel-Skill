#!/usr/bin/env python3
"""Prepare a pending independent-review matrix; validate evidence without grading prose."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/revayat-novel/scripts"))
import bookir as ir  # noqa: E402
import runlog  # noqa: E402

SCHEMA = "revayat-novel/adjudication@1"
LIMIT = 1024 * 1024


def object_file(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate review JSON key")
            result[key] = value
        return result

    with Path(path).open("rb") as handle:
        raw = handle.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("Review JSON exceeds 1 MiB")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite review value")))
    if not isinstance(value, dict):
        raise ValueError("Review must be an object")
    return value, hashlib.sha256(raw).hexdigest()


def benchmark(path):
    data, digest = object_file(path)
    cases = data.get("cases")
    if (data.get("schema") != "revayat-novel/benchmark@1" or not isinstance(cases, list)
            or not 1 <= len(cases) <= 1000 or any(not isinstance(c, dict) for c in cases)):
        raise ValueError("Invalid benchmark cases")
    ids = [c.get("id") for c in cases]
    if any(not isinstance(i, str) or not re.fullmatch(r"[a-z0-9-]{1,80}", i) for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("Invalid benchmark case identities")
    for case in cases:
        for field in ("axis", "source", "rationale"):
            text(case.get(field), "benchmark " + field, 10000)
        text(case.get("source_language", "en"), "benchmark language", 20)
        if not isinstance(case.get("accepts"), list) or not case["accepts"]:
            raise ValueError("Benchmark requires accepted controls")
        for accepted in case["accepts"]:
            text(accepted, "accepted control", 10000)
        if not isinstance(case.get("fails"), list) or not case["fails"]:
            raise ValueError("Benchmark requires wrong controls")
        for wrong in case["fails"]:
            if not isinstance(wrong, dict):
                raise ValueError("Invalid wrong control")
            text(wrong.get("text"), "wrong control", 10000)
            text(wrong.get("why"), "wrong rationale", 10000)
    return cases, digest


def prepare(cases_path):
    cases, digest = benchmark(cases_path)
    return {"schema": SCHEMA, "benchmark_sha256": digest, "cases": [
        {"id": c["id"], "source_language": c.get("source_language", "en"),
         "axis": c["axis"], "rubric": c["rationale"],
         "accepted_controls": len(c["accepts"]), "wrong_controls": len(c["fails"]),
         "status": "pending", "reviews": [],
         "pending_reason": "Qualified independent human evidence has not been supplied"}
        for c in cases]}


def text(value, label, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise ValueError("Invalid " + label)
    return value


def validate(review, cases_path):
    cases, digest = benchmark(cases_path)
    if set(review) != {"schema", "benchmark_sha256", "cases"} or review["schema"] != SCHEMA or review["benchmark_sha256"] != digest:
        raise ValueError("Review benchmark identity differs")
    rows = review["cases"]
    if not isinstance(rows, list) or len(rows) != len(cases) or any(not isinstance(r, dict) for r in rows):
        raise ValueError("Review must cover every case exactly once")
    if [r.get("id") for r in rows] != [c["id"] for c in cases]:
        raise ValueError("Missing, duplicate, foreign or reordered review case")
    for row, case in zip(rows, cases):
        if set(row) != {"id", "source_language", "axis", "rubric", "accepted_controls",
                        "wrong_controls", "status", "reviews", "pending_reason"}:
            raise ValueError("Invalid review case fields")
        if row["source_language"] != case.get("source_language", "en") or row["axis"] != case["axis"]:
            raise ValueError("Review case language or axis differs")
        if (row["rubric"] != case["rationale"] or type(row["accepted_controls"]) is not int
                or type(row["wrong_controls"]) is not int
                or row["accepted_controls"] != len(case["accepts"])
                or row["wrong_controls"] != len(case["fails"])):
            raise ValueError("Review rubric or control enumeration differs")
        if not isinstance(row["status"], str) or row["status"] not in ("pending", "accept", "reject", "disputed"):
            raise ValueError("Invalid review status")
        reviews = row["reviews"]
        if not isinstance(reviews, list) or len(reviews) > 10:
            raise ValueError("Invalid reviewer evidence list")
        seen = set()
        for evidence in reviews:
            required = {"reviewer_id", "qualification", "independent", "source_language",
                        "reviewed_utc", "evidence_ref", "evidence_sha256", "verdict", "notes"}
            if not isinstance(evidence, dict) or set(evidence) != required:
                raise ValueError("Missing reviewer metadata")
            reviewer = text(evidence["reviewer_id"], "reviewer identity", 120)
            if reviewer in seen or evidence["independent"] is not True:
                raise ValueError("Duplicate or nonindependent reviewer")
            seen.add(reviewer)
            for field in ("qualification", "evidence_ref", "notes"):
                text(evidence[field], field)
            if evidence["source_language"] != row["source_language"]:
                raise ValueError("Reviewer source language differs")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", text(evidence["reviewed_utc"], "review time", 20)):
                raise ValueError("Review time must be UTC ISO8601 seconds")
            datetime.strptime(evidence["reviewed_utc"], "%Y-%m-%dT%H:%M:%SZ")
            if not re.fullmatch(r"[a-f0-9]{64}", text(evidence["evidence_sha256"], "evidence digest", 64)):
                raise ValueError("Invalid evidence digest")
            if not isinstance(evidence["verdict"], str) or evidence["verdict"] not in ("accept", "reject", "unknown"):
                raise ValueError("Invalid independent verdict")
        verdicts = {e["verdict"] for e in reviews}
        expected = ("disputed" if {"accept", "reject"} <= verdicts else
                    next(iter(verdicts)) if len(verdicts) == 1 and "unknown" not in verdicts else "pending")
        if row["status"] != expected:
            raise ValueError("Case status does not follow independent evidence")
        if expected == "pending":
            text(row["pending_reason"], "pending reason")
        elif row["pending_reason"] is not None:
            raise ValueError("Settled case must not claim pending evidence")
    return review


def main(argv=None):
    ir.use_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "validate"))
    parser.add_argument("--cases", type=Path, default=ROOT / "evaluation/cases.json")
    parser.add_argument("--review", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    def invoke():
        try:
            if args.out.exists():
                raise ValueError("Review output exists; preserve it and choose a new filename")
            result = prepare(args.cases) if args.action == "prepare" else validate(
                object_file(args.review)[0] if args.review else {}, args.cases)
            ir.write_text(args.out, json.dumps(result, ensure_ascii=False, indent=1) + "\n")
            print(json.dumps({"ok": True, "cases": len(result["cases"]),
                              "pending": sum(c["status"] == "pending" for c in result["cases"])}))
            return 0
        except (OSError, ValueError, UnicodeError) as error:
            runlog.event("refused", cause="invalid-input", error_type=type(error).__name__)
            print(json.dumps({"ok": False, "refused": "invalid-review", "error_type": type(error).__name__}))
            return 2

    return runlog.execute(invoke, name="adjudicate",
                          directory=os.environ.get("REVAYAT_NOVEL_LOG_DIR") or ROOT / "evaluation/logs")


if __name__ == "__main__":
    raise SystemExit(main())
