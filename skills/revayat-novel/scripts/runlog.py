"""One UTF-8 file per CLI invocation, without manuscript text or arguments."""

from contextvars import ContextVar
from datetime import datetime, timezone
import os
import logging
from pathlib import Path
import platform
import sys
import traceback
import uuid

_ACTIVE = ContextVar("revayat_novel_log", default=False)
_EVENT = ContextVar("revayat_novel_event", default=None)
_STAGE = ContextVar("revayat_novel_stage", default="pipeline")
STAGES = frozenset({"extract", "web-import", "clean-scan", "ocr-sidecar", "glossary",
                    "chunk", "pages", "render-qa", "doc-qa", "merge", "meaning",
                    "fluency", "falint", "qa", "build", "doctor", "unknown"})
CAUSES = frozenset({"invalid-input", "invalid-state", "unreadable-state", "absent-state",
                    "source-changed", "unsafe-source", "source-limit", "existing-book",
                    "unknown-stage", "unclassified-refusal"})
MODULE_EVENTS = {
    ("read_docx", "DOCX extracted: %d blocks, %d native notes"):
        ("docx-extracted", ("blocks", "notes")),
    ("read_epub", "EPUB extracted: %d blocks, %d notes, %d distinct assets"):
        ("epub-extracted", ("blocks", "notes", "assets")),
    ("read_epub", "Parsed EPUB spine item %d of %d"):
        ("epub-spine", ("item", "items")),
    ("read_epub", "EPUB extraction refused; source content was not complete"):
        ("epub-refused", ()),
    ("webhtml", "Normalized one chapter without duplicating source node identities"):
        ("chapter-normalized", ()),
}


def stage(value):
    safe = value if value in STAGES else "unknown"
    _STAGE.set(safe)
    event("stage", stage=safe)


def event(kind, **metadata):
    sink = _EVENT.get()
    if sink is None or kind not in {"stage", "refused", "module"}:
        return
    fields = []
    if "stage" in metadata:
        fields.append("stage=" + (metadata["stage"] if metadata["stage"] in STAGES else "unknown"))
    if "cause" in metadata:
        fields.append("cause=" + (metadata["cause"] if metadata["cause"] in CAUSES else "unclassified-refusal"))
    if "error_type" in metadata and metadata["error_type"] in {"OSError", "FileNotFoundError", "PermissionError", "UnicodeError", "UnicodeDecodeError", "ValueError", "Refused", "JSONDecodeError"}:
        fields.append("error_type=" + metadata["error_type"])
    if metadata.get("event") in {v[0] for v in MODULE_EVENTS.values()}:
        fields.append("event=" + metadata["event"])
    for key in ("blocks", "notes", "assets", "item", "items"):
        value = metadata.get(key)
        if type(value) is int and 0 <= value <= 1000000:
            fields.append(f"{key}={value}")
    sink("ERROR" if kind == "refused" else "INFO", kind + " " + " ".join(fields))


class SafeModuleEvents(logging.Handler):
    def emit(self, record):
        spec = MODULE_EVENTS.get((record.name, record.msg)) if isinstance(record.msg, str) else None
        if spec is None or not isinstance(record.args, tuple) or len(record.args) != len(spec[1]):
            return
        if any(type(value) is not int or not 0 <= value <= 1000000 for value in record.args):
            return
        event("module", event=spec[0], **dict(zip(spec[1], record.args)))


def execute(function, *, name="revayat-novel", directory=None):
    if _ACTIVE.get():
        return function()
    root = directory or os.environ.get("REVAYAT_NOVEL_LOG_DIR")
    if root is None:
        return function()
    token = _ACTIVE.set(True)
    handle = None
    logging_failed = False
    code = 1
    event_token = None
    stage_token = _STAGE.set("pipeline")
    handler = SafeModuleEvents()
    owned_loggers = []
    try:
        # The using agent sets this to the translation output directory. Never
        # silently put a book's execution history in an agent/global profile.
        try:
            root = Path(root)
            root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S_UTC")
            path = root / f"{name}_{stamp}.log"
            try:
                handle = path.open("x", encoding="utf-8", newline="")
            except FileExistsError:
                handle = path.with_name(f"{path.stem}_{uuid.uuid4().hex}.log").open("x", encoding="utf-8", newline="")
        except (OSError, ValueError):
            print("File logging is unavailable; command results remain on the console.", file=sys.stderr)

        def log(level, message):
            nonlocal logging_failed
            if handle is not None and not logging_failed:
                timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                try:
                    handle.write(f"[{timestamp}] [{level}] [pipeline] {message}\n")
                    handle.flush()
                except OSError:
                    logging_failed = True
                    print("Execution log writing failed; restore logging before continuing translation.", file=sys.stderr)

        event_token = _EVENT.set(log)
        for module in sorted({key[0] for key in MODULE_EVENTS}):
            logger = logging.getLogger(module)
            owned_loggers.append((logger, logger.level))
            logger.setLevel(logging.DEBUG)
            logger.addHandler(handler)
        log("INFO", f"started python={platform.python_version()} platform={sys.platform}")
        try:
            code = int(function() or 0)
            return code
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 1
            raise
        except BaseException as error:
            log("ERROR", f"exception={type(error).__name__}")
            for frame in traceback.extract_tb(error.__traceback__):
                log("ERROR", f"frame={Path(frame.filename).name}:{frame.lineno} function={frame.name}")
            raise
        finally:
            log("INFO" if code == 0 else "ERROR", f"finished exit={code}")
    finally:
        for logger, level in owned_loggers:
            logger.removeHandler(handler)
            logger.setLevel(level)
        handler.close()
        if event_token is not None:
            _EVENT.reset(event_token)
        _STAGE.reset(stage_token)
        if handle is not None:
            try:
                handle.close()
            except OSError:
                print("The execution log could not be closed cleanly.", file=sys.stderr)
        _ACTIVE.reset(token)
