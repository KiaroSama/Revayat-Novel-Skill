"""One UTF-8 file per CLI invocation, without manuscript text or arguments."""

from contextvars import ContextVar
from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import sys
import traceback
import uuid

_ACTIVE = ContextVar("revayat_novel_log", default=False)


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
        if handle is not None:
            try:
                handle.close()
            except OSError:
                print("The execution log could not be closed cleanly.", file=sys.stderr)
        _ACTIVE.reset(token)
