"""Shared stdlib installer CLI; native wrappers only select a Python interpreter."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from install_files import (
    AGENTS, STATE, admit, destination_for, disjoint, path_kind, payload_snapshot,
    pointer_bytes,
)
from install_transaction import installation_lock, publish

ROOT = Path(__file__).absolute().parent.parent
SOURCE = ROOT / "skills" / "revayat-novel"
INVENTORY = ROOT / "install" / "payload.json"
INVENTORY_SHA256 = "29a6b3b821d08d194af4ac67ef00d164a164c38ef2f849c778c353f41133189d"


def verified_snapshot(source: Path, inventory: Path) -> dict:
    admit(inventory)
    if hashlib.sha256(inventory.read_bytes()).hexdigest() != INVENTORY_SHA256:
        raise ValueError("Payload inventory integrity differs from shipped installer")
    return payload_snapshot(source, inventory)


def consent(destination: Path, force: bool) -> bool:
    if force or path_kind(destination) is None:
        return True
    if not sys.stdin.isatty():
        raise ValueError("Replacing an existing installation requires --force in automation")
    try:
        answer = input(f"Replace existing {destination.name}? [y/N] ")
    except (EOFError, OSError):
        return False
    return answer.strip().casefold() in ("y", "yes")


def plan(base: Path, agent: str, scope: str, force: bool):
    source = admit(SOURCE)
    inventory = admit(INVENTORY)
    snapshot = verified_snapshot(source, inventory)
    destinations = []
    names = list(AGENTS) if agent == "all" else [agent]
    for name in names:
        destination = destination_for(base, name, scope)
        home = destination.parent.parent
        admit(home, base)
        if agent == "all" and path_kind(home) is None:
            continue
        for path in (home, destination.parent):
            if path_kind(path) not in (None, "tree"):
                raise ValueError("Skill parent is not a directory")
        admit(destination, base)
        disjoint(source, destination)
        if path_kind(destination) not in (None, "tree"):
            raise ValueError("Skill destination is not a directory")
        if consent(destination, force):
            destinations.append(destination)
    pointer = None
    if any(p.parent.parent.name in (".opencode", ".agents", "opencode")
           for p in destinations):
        pointer_path = admit(base / "AGENTS.md", base)
        if path_kind(pointer_path) not in (None, "file"):
            raise ValueError("Instruction pointer is not a regular file")
        original = pointer_path.read_bytes() if pointer_path.exists() else b""
        pointer_destinations = []
        for name in ("opencode", "antigravity"):
            path = destination_for(base, name, scope)
            admit(path, base)
            if path in destinations or path_kind(path) == "tree":
                pointer_destinations.append(path.as_posix())
        pointer = (original, pointer_bytes(original, pointer_destinations))
    def check_source():
        if verified_snapshot(source, inventory) != snapshot:
            raise ValueError("Source payload changed during staging")
    return destinations, snapshot, pointer, check_source


def _logging(state: Path):
    logger = logging.getLogger("installer")
    logger.setLevel(logging.INFO)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S_UTC")
    path = admit(state / f"installer_{stamp}_{uuid.uuid4().hex[:8]}.log", state)
    try:
        handler = logging.FileHandler(path, mode="x", encoding="utf-8")
        formatter = logging.Formatter("[%(asctime)s UTC] [%(levelname)s] [installer] %(message)s",
                                      datefmt="%Y-%m-%d %H:%M:%S")
        formatter.converter = time.gmtime
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        return logger, handler
    except OSError:
        print("WARNING: Installer file logging unavailable; using console status", file=sys.stderr)
        return logger, None


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="strict")
    parser = argparse.ArgumentParser(description="Recoverable Revayat Novel installation")
    parser.add_argument("--agent", choices=[*AGENTS, "all"], default="all")
    parser.add_argument("--scope", choices=("user", "project"), default="user")
    parser.add_argument("--path", default=os.getcwd())
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    logger = None
    handler = None
    start = time.monotonic()
    try:
        home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or str(Path.home())
        base = admit(Path(args.path if args.scope == "project" else home))
        if path_kind(base) != "tree":
            raise ValueError("Installation base must be an existing directory")
        disjoint(admit(SOURCE), base / STATE)
        # Initialize protected logging in the same validated lock domain.
        with installation_lock(base) as state:
            logger, handler = _logging(state)
        logger.info("start scope=%s agent=%s python=%s", args.scope, args.agent,
                    sys.version.split()[0])
        result = publish(base, [], {}, prepare=lambda: plan(base, args.agent, args.scope, args.force),
                         observer=lambda event, index: logger.info("event=%s participant=%s", event, index))
        code = 2 if result.get("cleanup_pending") else 0
        logger.info("finish committed=%s cleanup_pending=%s exit=%s duration=%.3f",
                    result.get("committed"), result.get("cleanup_pending"), code,
                    time.monotonic() - start)
        print(json.dumps(result, ensure_ascii=False))
        return code
    except (OSError, ValueError, KeyboardInterrupt) as exc:
        # Exception bodies may contain instruction/payload text. Log type/status only.
        if logger:
            logger.error("refused error_type=%s exit=1 duration=%.3f", type(exc).__name__,
                         time.monotonic() - start)
        reason = str(exc) if type(exc) is ValueError else type(exc).__name__
        print(f"Installation refused: {reason}. Existing content and recovery evidence retained.",
              file=sys.stderr)
        return 1
    finally:
        if handler:
            logger.removeHandler(handler)
            handler.flush()
            handler.close()


if __name__ == "__main__":
    raise SystemExit(main())
