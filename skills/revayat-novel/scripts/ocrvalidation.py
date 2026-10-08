"""OCR source/output inventory, roles and fresh/cache artifact verification."""
from __future__ import annotations

import json
import re
import sys
import uuid
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import bookir as ir
import runstate
from adapters import ExtractError

ENVELOPE_LIMIT = 4 * 1024 * 1024


def _json(path: Path) -> dict:
    try:
        if path.stat().st_size > ENVELOPE_LIMIT:
            raise ExtractError("OCR envelope exceeds the 4 MiB limit")
    except OSError as error:
        raise ExtractError(f"OCR envelope unreadable: {type(error).__name__}") from None

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ExtractError("OCR envelope has duplicate keys")
            result[key] = value
        return result

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (OSError, UnicodeError, ValueError) as error:
        raise ExtractError(f"OCR envelope unreadable: {type(error).__name__}") from None
    if not isinstance(value, dict):
        raise ExtractError("OCR envelope must be a JSON object")
    return value


def load_page_roles(path: Path | None, source: Path, pages: int) -> dict[int, str]:
    """Strict operator assignment bound to the complete original source inventory."""
    if path is None:
        return {}
    envelope = _json(Path(path))
    if set(envelope) != {"version", "source_sha256", "pages"} or type(envelope["version"]) is not int or envelope["version"] != 1:
        raise ExtractError("OCR page roles require version 1, source_sha256 and pages")
    if envelope["source_sha256"] != ir.sha256_file(source):
        raise ExtractError("OCR page roles source hash mismatch")
    entries = envelope["pages"]
    if not isinstance(entries, list) or len(entries) != pages:
        raise ExtractError("OCR page roles must cover the exact source page inventory")
    roles = {}
    for item in entries:
        if not isinstance(item, dict) or set(item) != {"page", "role"}:
            raise ExtractError("OCR page role entry requires page and role")
        number, role = item["page"], item["role"]
        if type(number) is not int or not 1 <= number <= pages or number in roles:
            raise ExtractError("OCR page roles have duplicate or invalid page indices")
        if not isinstance(role, str) or role not in {"text", "image", "blank"}:
            raise ExtractError("OCR page role must be text, image or blank")
        roles[number] = role
    return roles


def pdf_inventory(path: Path) -> list[dict[str, Any]]:
    """Source-owned native text and decoded draw digests; never recognition guesses."""
    import pymupdf
    try:
        with pymupdf.open(path) as document:
            ir.check_page_count(len(document), str(path))
            if not len(document):
                raise ExtractError("OCR PDF has no pages")
            inventory = []
            for page in document:
                text = page.get_text("text").strip()
                images = page.get_image_info(hashes=True)
                inventory.append({
                    "text": text,
                    "images": tuple(item["digest"].hex() for item in images),
                    "draws": tuple((item["digest"].hex(), tuple(round(float(v), 3)
                        for v in item["bbox"]), tuple(round(float(v), 3)
                        for v in item["transform"])) for item in images),
                    "size": (float(page.mediabox.width), float(page.mediabox.height)),
                    "crop": tuple(float(v) for v in page.cropbox),
                    "rotation": int(page.rotation),
                    "drawing_count": len(page.get_drawings()),
                })
            return inventory
    except ExtractError:
        raise
    except Exception as error:
        raise ExtractError(f"OCR PDF unreadable: {type(error).__name__}") from None


def options(kind: str, language: str, deskew: bool | None, roles: Path | None) -> dict:
    return {"kind": kind, "language": language,
            "deskew": deskew if deskew is not None else kind == "scanned",
            "roles_sha256": runstate.file_hash(roles)}


def make_proof(source: Path, converter_source: Path, destination: Path,
               invocation: str, command: list[str], settings: dict, pages: int) -> dict:
    try:
        engine_version = version("ocrmypdf")
    except PackageNotFoundError:
        engine_version = "external-unverified"
    executable = Path(command[0]).absolute()
    module = (command[:3] == [sys.executable, "-m", "ocrmypdf"])
    script = (executable.parent == Path(sys.executable).absolute().parent
              and executable.name.lower() in {"ocrmypdf", "ocrmypdf.exe"})
    indexed_engine = engine_version == "17.13.0" and (module or script)
    return {"version": 1, "source_sha256": ir.sha256_file(source),
            "converter_source_sha256": ir.sha256_file(converter_source),
            "output_sha256": ir.sha256_file(destination), "pages": pages,
            "engine": "ocrmypdf", "engine_version": engine_version,
            "indexed_engine": indexed_engine,
            "invocation": invocation, "options": settings,
            "command_sha256": ir.sha256_bytes(json.dumps(command).encode("utf-8"))}


def _bound_proof(proof: dict | None, source: Path, converter_source: Path,
                 destination: Path, settings: dict, pages: int) -> bool:
    if not isinstance(proof, dict):
        return False
    return (proof.get("version") == 1 and proof.get("engine") == "ocrmypdf"
            and proof.get("indexed_engine") is True
            and proof.get("engine_version") == "17.13.0" and proof.get("pages") == pages
            and isinstance(proof.get("invocation"), str)
            and re.fullmatch(r"[0-9a-f]{32}", proof["invocation"]) is not None
            and proof.get("options") == settings
            and proof.get("source_sha256") == ir.sha256_file(source)
            and proof.get("converter_source_sha256") == ir.sha256_file(converter_source)
            and proof.get("output_sha256") == ir.sha256_file(destination)
            and isinstance(proof.get("command_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", proof["command_sha256"]) is not None)


def validate_output(source: Path, destination: Path, *, page_roles: Path | None = None,
                    converter_source: Path | None = None, proof: dict | None = None,
                    settings: dict | None = None, allow_unknown: bool = False) -> dict:
    original = pdf_inventory(source)
    output = pdf_inventory(destination)
    if len(output) != len(original):
        raise ExtractError("OCR page inventory mismatch")
    roles = load_page_roles(page_roles, source, len(original))
    converted = pdf_inventory(converter_source) if converter_source and converter_source != source else original
    if len(converted) != len(original):
        raise ExtractError("OCR converter input page inventory mismatch")
    controlled = _bound_proof(proof, source, converter_source or source,
                              destination, settings or {}, len(original))
    pages, unknown, ocr_pages = [], [], []
    for index, (before, after) in enumerate(zip(original, output)):
        number = index + 1
        candidate = converted[index]
        native = bool(before["text"])
        # Native text-bearing pages skip OCR entirely, including short titles.
        if native and after["text"] != before["text"]:
            raise ExtractError(f"OCR native text/page order mismatch on page {number}")
        exact_shape = all(abs(a - b) <= 0.5 for a, b in zip(before["size"], after["size"]))
        swapped_shape = controlled and all(abs(a - b) <= 0.5 for a, b in zip(before["size"], reversed(after["size"])))
        if not (exact_shape or swapped_shape):
            raise ExtractError(f"OCR page geometry mismatch on page {number}")
        if native and (before["crop"] != after["crop"] or before["rotation"] != after["rotation"]):
            raise ExtractError(f"OCR native page geometry changed on page {number}")
        if not native and not controlled and (before["crop"] != after["crop"] or before["rotation"] != after["rotation"]):
            raise ExtractError(f"OCR page geometry unverified on page {number}")
        if candidate["images"] and not after["images"]:
            raise ExtractError(f"OCR source raster missing on page {number}")
        exact_images = candidate["draws"] == after["draws"]
        if not exact_images:
            # If the payload identifies another source page, this is reorder,
            # not an allowed deskew derivative. Do not trust the engine receipt.
            foreign = any(after["images"] and after["images"] == other["images"]
                          and other["images"] != candidate["images"] for other in converted)
            if foreign or native:
                raise ExtractError(f"OCR image/page order mismatch on page {number}")
            if not controlled:
                raise ExtractError(f"OCR page mapping unverified on page {number}")
        mapping = "native-text" if native else "source-draws" if exact_images else "engine-indexed"
        role = roles.get(number)
        origin = "operator" if role else "native" if native else "recognized" if after["text"] else "unknown"
        if native and role in {"image", "blank"}:
            raise ExtractError(f"OCR role contradicts native text on page {number}")
        if role == "blank" and (before["text"] or before["images"] or before["drawing_count"]):
            raise ExtractError(f"OCR blank role contradicts source content on page {number}")
        if role is None:
            role = "text" if native or after["text"] else "blank" if not before["images"] and not before["drawing_count"] else "unknown"
            if role == "blank":
                origin = "native"
        if role == "text" and not after["text"]:
            raise ExtractError(f"OCR required text missing on page {number}")
        if role == "unknown":
            unknown.append(number)
        recognized = not native and bool(after["text"])
        if recognized:
            ocr_pages.append(number)
        pages.append({"page": number, "role": role, "origin": origin,
                      "native_text": native, "recognized": recognized, "mapping": mapping})
    if unknown and not allow_unknown:
        raise ExtractError("OCR page roles required for silent raster/vector pages: "
                           + ", ".join(str(n) for n in unknown[:20]))
    return {"complete": not unknown, "unknown_pages": unknown, "pages": pages,
            "ocr_pages": ocr_pages, "characters": sum(len(item["text"]) for item in output),
            "source_sha256": ir.sha256_file(source), "roles_sha256": runstate.file_hash(page_roles)}


def _usable_ocr_output(destination: Path) -> tuple[bool, str]:
    """Legacy single-file structural seam; production additionally validates source."""
    try:
        inventory = pdf_inventory(destination)
    except (ExtractError, OSError) as error:
        return False, str(error)
    characters = sum(len(item["text"]) for item in inventory)
    if not characters:
        return False, "the output PDF has no text layer at all"
    return True, f"{characters} characters across {len(inventory)} pages"


def read_native_pdf(args, source: Path, out_dir: Path, asset_dir: Path, report: dict,
                    *, probe_pdf, ocr_inputs, run_ocr, quarantine):
    probe = probe_pdf(source)
    report["probe"] = probe
    read_from = source
    role_path = Path(args.ocr_page_roles) if getattr(args, "ocr_page_roles", None) else None
    inventory = pdf_inventory(source)
    load_page_roles(role_path, source, len(inventory))
    state = runstate.RunState(out_dir)
    stale, reason = state.is_stale("extract", ocr_inputs(args))
    reusable = not (stale or args.force_ocr)
    if stale and state.recorded("extract") is not None:
        report["cache"] = {"rebuilt": reason}
    if args.clean_scan != "off" and probe["kind"] != "digital":
        import scan_clean
        try:
            cleaned = out_dir / "cleaned.pdf"
            if cleaned.exists() and reusable:
                report["clean_scan"] = {"reused": str(cleaned)}
            else:
                report["clean_scan"] = scan_clean.clean_pdf(source, cleaned,
                    force=args.clean_scan == "force", ghost_threshold=args.ghost_threshold)
            if report["clean_scan"].get("cleaned") or report["clean_scan"].get("reused"):
                read_from = cleaned
        except scan_clean.Unavailable as error:
            report["clean_scan"] = {"skipped": str(error)}
    from_ocr = probe["kind"] != "digital" and args.ocr != "off"
    if from_ocr:
        ocr_pdf = out_dir / "ocr.pdf"
        receipt = out_dir / "ocr.pdf.proof.json"
        actual_kind = "mixed" if any(item["text"] for item in inventory) else "scanned"
        settings = options(actual_kind, args.ocr_lang, args.deskew, role_path)
        reused = False
        if ocr_pdf.exists() and reusable:
            try:
                proof = _json(receipt) if receipt.exists() else None
                coverage = validate_output(source, ocr_pdf, page_roles=role_path,
                    converter_source=read_from, proof=proof, settings=settings)
                report["ocr"] = {"reused": str(ocr_pdf), "coverage": coverage, "proof": proof}
                reused = True
            except (ExtractError, OSError):
                report["cache"] = {"rebuilt": "OCR artifact coverage/proof changed"}
        if not reused:
            quarantined = quarantine(out_dir)
            candidate = out_dir / f"ocr.{uuid.uuid4().hex}.pdf.new"
            try:
                report["ocr"] = run_ocr(read_from, candidate, kind=actual_kind,
                    language=args.ocr_lang, deskew=args.deskew, timeout=args.ocr_timeout,
                    page_roles=role_path, validation_source=source)
                # No injected converter may replace a prior artifact before validation.
                coverage = validate_output(source, candidate, page_roles=role_path,
                    converter_source=read_from, proof=report["ocr"].get("proof"), settings=settings)
                candidate.replace(ocr_pdf)
            except BaseException:
                if candidate.exists():
                    candidate.replace(candidate.with_name(candidate.name + ".failed"))
                raise
            report["ocr"].update(coverage=coverage, output=str(ocr_pdf))
            if report["ocr"].get("proof"):
                ir.write_text(receipt, json.dumps(report["ocr"]["proof"], ensure_ascii=False))
            if quarantined:
                report["ocr"]["quarantined"] = quarantined
            report["ocr"]["probe_after"] = probe_pdf(ocr_pdf)
        read_from = ocr_pdf
    else:
        coverage = validate_output(source, source, page_roles=role_path, allow_unknown=True)
        if probe["kind"] != "digital":
            report["ocr"] = {"skipped": "--ocr off", "warning": "source coverage remains unverified"}
    from read_pdf import read_pdf
    book = read_pdf(str(read_from), asset_dir,
        lang_source=args.source_lang or "en", lang_target=args.target_lang,
        max_pages=args.max_pages, ocr_text=from_ocr,
        ocr_pages=coverage["ocr_pages"] if from_ocr else [],
        page_roles={item["page"]: item["role"] for item in coverage["pages"]})
    book["source"].update(original_path=str(source), probe=probe, ocr_coverage=coverage)
    return book
