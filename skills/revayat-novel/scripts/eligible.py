"""One fail-closed, read-only eligibility decision for schedulers and merge."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import bookir as ir
import provenance
import reviewstate
import worksheet
from worksheet import classify  # noqa: F401

RETRYABLE = ("missing", "empty", "malformed", "partial", "invalid", "wrong-request",
             "unbound", "stale-source", "unverified")


def _book_and_glossary(out_dir, manifest):
    def beside(recorded):
        if not recorded:
            return None
        direct = Path(recorded)
        if direct.is_absolute() and direct.exists():
            return direct
        fallback = Path(out_dir).parent / direct.name
        if fallback.exists():
            return fallback
        return direct if direct.is_absolute() else None
    return beside(manifest.get("book", "")), beside(manifest.get("glossary", ""))


def _validate_manifest(manifest):
    reviewstate.require(isinstance(manifest, dict), "worksheet manifest must be an object")
    reviewstate.require(manifest.get("schema") in ("revayat-novel/chunks@1", "revayat-novel/pagerun@1"),
                        "unsupported worksheet manifest schema; rebuild")
    reviewstate.require(isinstance(manifest.get("chunks"), list), "manifest requires a job list")
    reviewstate.require(isinstance(manifest.get("book"), str) and isinstance(manifest.get("glossary", ""), str),
                        "invalid manifest dependency paths")
    seen = set()
    for entry in manifest["chunks"]:
        _validate_entry(entry)
        if manifest["schema"] == "revayat-novel/pagerun@1":
            for field in ("page", "part", "parts", "payload_chars"):
                reviewstate.require(type(entry.get(field)) is int and entry[field] >= 1,
                                    f"invalid page job {field}")
        reviewstate.require(entry["id"] not in seen, "duplicate job id")
        seen.add(entry["id"])
    return manifest


def _validate_entry(entry):
    reviewstate.require(isinstance(entry, dict), "job must be an object")
    for field in ("id", "file", "output"):
        value = entry.get(field)
        reviewstate.require(isinstance(value, str) and bool(value) and Path(value).name == value
                            and value not in (".", ".."), f"invalid job {field}")
    for field in ("block_ids", "unit_ids"):
        reviewstate.require(reviewstate.strings(entry.get(field)) and len(entry[field]) == len(set(entry[field])),
                            f"invalid job {field}")
    reviewstate.require(type(entry.get("units")) is int and entry["units"] == len(entry["unit_ids"]),
                        "job count disagrees with its units")
    neighbours = entry.get("neighbour_ids")
    if neighbours is not None:
        reviewstate.require(isinstance(neighbours, dict) and all(reviewstate.strings(neighbours.get(k, []))
                            for k in ("before", "after")), "invalid context dependencies")
    kinds = entry.get("unit_kinds")
    if kinds is not None:
        reviewstate.require(isinstance(kinds, dict) and all(isinstance(v, str) for v in kinds.values()), "invalid unit kinds")
    spans = entry.get("unit_spans")
    if spans is not None:
        reviewstate.require(isinstance(spans, list), "invalid unit spans")
        for span in spans:
            reviewstate.require(isinstance(span, dict) and all(isinstance(span.get(k), str)
                                for k in ("id", "kind", "owner")), "invalid source span")
            reviewstate.require(all(type(span.get(k)) is int and span[k] >= 0 for k in ("offset", "length")),
                                "invalid source span bounds")
        reviewstate.require([s["id"] for s in spans] == entry["unit_ids"], "spans disagree with ordered unit ids")


def read_manifest(out_dir):
    return _validate_manifest(reviewstate.object_file(Path(out_dir) / "manifest.json"))


def envelope(out_dir, entry, reply):
    if reply is None:
        return "", ""
    try:
        sheet = (Path(out_dir) / entry["file"]).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "unverified", "the worksheet is unreadable; restore it or rebuild"
    wanted = entry.get("request")
    if not isinstance(wanted, str) or not wanted:
        return "unverified", "the manifest has no request identity; rebuild"
    if worksheet.request_of(sheet) != wanted or provenance.request_token(sheet) != wanted:
        return "wrong-request", "worksheet and manifest requests disagree with the worksheet payload; rebuild"
    echoed = worksheet.request_of(reply)
    if not echoed:
        return "unbound", "this reply carries no request line; re-answer or explicitly revalidate"
    lines = [line for line in reply.splitlines() if line.strip().startswith("<!-- revayat-novel: request ")]
    if len(lines) != 1 or echoed != wanted:
        return "wrong-request", f"this reply answers request {echoed} and the worksheet now asks {wanted}"
    return "", ""


def freshness(entry, *, book, glossary, glossary_named, book_path=None,
              manifest=None, source_prints=None, out_dir=None):
    if book is None:
        return "unverified", "the book is missing or unreadable; restore it or rebuild"
    if glossary_named and glossary is None:
        return "unverified", "the named glossary is missing or unreadable; restore it or rebuild"
    recorded = entry.get("source_sha256")
    if not isinstance(recorded, str) or not recorded:
        return "unverified", "no source digest was recorded; rebuild"
    spans, kinds = entry.get("unit_spans"), entry.get("unit_kinds")
    if spans is None or kinds is None or set(kinds) != set(entry["unit_ids"]):
        return "unverified", "the cut or unit kinds cannot be rechecked; rebuild"
    form = recorded.partition(":")[0]
    try:
        if form == provenance.SOURCE_DIGEST_VERSION:
            current = provenance.source_fingerprint(book, entry["block_ids"], spans,
                glossary=glossary, neighbours=entry.get("neighbour_ids"))
        elif form == "page-request1" and book_path is not None:
            import pageidentity
            current = pageidentity.request_fingerprint(book, entry, glossary=glossary,
                book_path=book_path, neighbour_chars=(manifest or {}).get("neighbour_chars", 600),
                source_prints=source_prints, out_dir=out_dir)
        else:
            return "unverified", f"unknown or legacy source digest {form}; rebuild to revalidate it"
    except (OSError, ValueError, KeyError, TypeError) as error:
        return "unverified", f"cannot recheck source dependencies: {error}; rebuild"
    if current != recorded:
        return "stale-source", "the source, page, glossary, voice or context changed since this worksheet was written; rebuild"
    return "", ""


def candidate_problems(book, entry, transport, companions=()):
    """Apply to an isolated candidate and ask the production note graph."""
    import merge
    import notegraph
    import segments

    replies = [(entry, transport), *companions]
    if not (book.get("footnotes") or any(reply["notes"] or any(
            ir.footnote_refs(text, include_local=True) for text in reply["answered"].values())
            for _, reply in replies)):
        return []
    candidate = copy.deepcopy(book)
    answered, touched = {}, set()
    for job, reply in replies:
        notes, mapping, retired = merge.adopt_translator_notes(candidate, reply["entries"],
            reply=job["id"], expected=set(job["unit_ids"]), allocated=[])
        candidate["footnotes"] = [n for n in candidate.get("footnotes", []) if n["id"] not in retired] + notes
        answered.update(merge._resolve_local_tokens(reply["answered"], mapping))
        touched.update(mapping.values())
    merge.apply_units(candidate, segments.rejoin(answered))
    merge._anchor_notes(candidate, [n for n in candidate["footnotes"] if n["id"] in touched])
    return notegraph.problems(candidate)


def eligibility(out_dir, entry, *, book=None, glossary=None, glossary_named=False,
                book_path=None, manifest=None, source_prints=None, revalidate_unbound=False,
                check_candidate=True):
    identity = entry.get("id", "unknown") if isinstance(entry, dict) else "unknown"
    try:
        _validate_entry(entry)
        output = Path(out_dir) / entry["output"]
        reply = output.read_text(encoding="utf-8") if output.exists() else None
        transport = worksheet.verdict(reply, entry["unit_ids"], entry.get("unit_kinds") or {})
    except (OSError, UnicodeError, reviewstate.Refused) as error:
        return {"id": identity, "state": "unverified", "detail": str(error),
                "usable": False, "retryable": True}
    state, detail, revalidated = transport["state"], "", False
    candidate = []
    if state in ("answered", worksheet.NOTHING_TO_TRANSLATE):
        if entry["unit_ids"]:
            check, detail = envelope(out_dir, entry, reply)
            if check == "unbound" and revalidate_unbound:
                revalidated, check, detail = True, "", ""
            if check:
                state = check
        if state in ("answered", worksheet.NOTHING_TO_TRANSLATE):
            check, why = freshness(entry, book=book, glossary=glossary, glossary_named=glossary_named,
                                  book_path=book_path, manifest=manifest, source_prints=source_prints, out_dir=out_dir)
            if check:
                state, detail = check, why
        if state == "answered" and check_candidate:
            problems = candidate_problems(book, entry, transport)
            if problems:
                candidate = problems
                state, detail = "invalid", "; ".join(problems)
                transport["problems"].extend(problems)
    return {"id": identity, "state": state, "detail": detail,
            "usable": state in ("answered", worksheet.NOTHING_TO_TRANSLATE),
            "retryable": state in RETRYABLE, "transport": transport,
            "candidate_problems": candidate, "revalidated": revalidated}


def every(out_dir, manifest=None, *, book_path=None, book=None, glossary=None,
          glossary_path=None, revalidate_unbound=False) -> list[dict[str, Any]]:
    manifest = read_manifest(out_dir) if manifest is None else _validate_manifest(manifest)
    named_book, named_glossary = _book_and_glossary(out_dir, manifest)
    named_book = Path(book_path) if book_path is not None else named_book
    named_glossary = Path(glossary_path) if glossary_path is not None else named_glossary
    if book is None and named_book is not None:
        try:
            book = ir.load_book(named_book)
        except (OSError, ValueError, UnicodeError):
            book = None
    if glossary is None and named_glossary is not None:
        import glossary as gl
        try:
            glossary = gl.load(named_glossary) if named_glossary.is_file() else None
        except (OSError, ValueError, UnicodeError):
            glossary = None
    prints = None
    if book is not None and (book.get("source") or {}).get("format") == "pdf" and any("page" in e for e in manifest["chunks"]):
        import sourcepages
        reference = sourcepages.reference_pdf(book, named_book)
        if reference is not None:
            prints = sourcepages.page_fingerprints(reference, {e["page"] for e in manifest["chunks"] if "page" in e})
    import segments

    jobs = manifest["chunks"]
    split_owners = [set(segments.segments_by_owner(job["unit_ids"])) for job in jobs]
    proofs = [eligibility(out_dir, entry, book=book, glossary=glossary,
                        glossary_named=bool(manifest.get("glossary") or glossary_path),
                        book_path=named_book, manifest=manifest, source_prints=prints,
                        revalidate_unbound=revalidate_unbound,
                        check_candidate=not split_owners[index]) for index, entry in enumerate(jobs)]
    pending = {index for index, owners in enumerate(split_owners) if owners}
    while pending:
        group = {pending.pop()}
        owners = set().union(*(split_owners[index] for index in group))
        while related := {index for index in pending if split_owners[index] & owners}:
            group.update(related)
            pending.difference_update(related)
            owners.update(*(split_owners[index] for index in related))
        # A split owner cannot be committed until all its pieces are ready.
        # The transport checks still apply while its sibling replies are pending.
        if not all(proofs[index]["usable"] for index in group):
            continue
        members = sorted(group)
        first, *rest = members
        problems = candidate_problems(book, jobs[first], proofs[first]["transport"],
            [(jobs[index], proofs[index]["transport"]) for index in rest])
        if problems:
            for index in members:
                proofs[index].update(state="invalid", detail="; ".join(problems),
                    usable=False, retryable=True, candidate_problems=problems)
                proofs[index]["transport"]["problems"].extend(problems)
    return proofs
