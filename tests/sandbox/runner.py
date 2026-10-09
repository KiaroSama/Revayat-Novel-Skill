"""Own one offline container; admit its controls before starting repository code."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import tempfile
import time
import uuid

CONTROLS = ("environment", "privileges", "network", "resources", "rlimits", "readonly", "scratch")
CASES = ("archive_path", "xml_entity", "native_grid", "native_continuation", "transaction", "artifact")
ENVIRONMENT = {
    "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/scratch/home",
    "TMPDIR": "/scratch/tmp", "XDG_CACHE_HOME": "/scratch/cache",
    "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONHASHSEED": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
}
WALL = 180
IDLE = 30
OUTPUT_LIMIT = 65536
TMPFS = {
    "/scratch": "rw,nosuid,nodev,noexec,size=64m,mode=1777",
    "/dev": "ro,dev,nosuid,noexec,size=1m",
    "/dev/pts": "ro,nosuid,nodev,noexec,size=1m",
    "/dev/shm": "ro,nosuid,nodev,noexec,size=1m",
    "/dev/mqueue": "ro,nosuid,nodev,noexec,size=1m",
}


class Cancelled(Exception):
    """Controlled cancellation still runs owned cleanup."""


def cancel(_signal, _frame):
    # A second termination during cleanup must not interrupt its bounded owner.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    raise Cancelled("cancelled")


def create_command(image, name, target):
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
        raise ValueError("Prepared image must be an exact local image ID")
    if not re.fullmatch(r"revayat-novel-[a-z0-9-]{1,60}", name):
        raise ValueError("Invalid owned container name")
    source = str(Path(target).resolve())
    control = str(Path(__file__).resolve().parent)
    if any(character in source + control for character in (",", "\n", "\r")):
        raise ValueError("Mount path cannot be safely encoded")
    return ["docker", "create", "--name", name, "--label", "revayat-novel-sandbox-validation=true",
            "--read-only", "--network", "none",
            "--user", "65532:65532", "--cpus", "1", "--memory", "768m",
            "--memory-swap", "768m", "--pids-limit", "32", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges=true", "--ulimit", "cpu=60:60",
            "--ulimit", "fsize=8388608:8388608", "--log-driver", "none",
            "--cgroupns", "private", "--ipc", "private",
            *(item for path, options in TMPFS.items() for item in ("--tmpfs", path + ":" + options)),
            "--mount", f"type=bind,source={source},target=/target,readonly",
            "--mount", f"type=bind,source={control},target=/control,readonly",
            *(item for filename in ("hosts", "hostname", "resolv.conf")
              for item in ("--mount", f"type=bind,source={Path(source) / '.network' / filename},target=/etc/{filename},readonly")),
            "--entrypoint", "env", image, "-i",
            *(f"{key}={value}" for key, value in ENVIRONMENT.items()),
            "/usr/local/bin/python", "-B", "-u", "/control/controls.py"]


def validate_result(result, exit_code=0):
    if (type(exit_code) is not int or exit_code != 0 or not isinstance(result, dict)
            or set(result) != {"ok", "controls", "cases"} or result.get("ok") is not True):
        raise ValueError("Sandbox execution did not complete successfully")
    for field, expected in (("controls", CONTROLS), ("cases", CASES)):
        values = result.get(field)
        if not isinstance(values, dict) or set(values) != set(expected) or any(value is not True for value in values.values()):
            raise ValueError("Sandbox proof is absent, incomplete or refused")


def validate_inspect(data, image, name, target):
    if not isinstance(data, list) or len(data) != 1:
        raise ValueError("Container inspection is not unique")
    container = data[0]
    host, config = container["HostConfig"], container["Config"]
    required = {"ReadonlyRootfs": True, "NetworkMode": "none", "NanoCpus": 1000000000,
                "Memory": 805306368, "MemorySwap": 805306368, "PidsLimit": 32,
                "Privileged": False, "PidMode": "", "IpcMode": "private"}
    if container["Image"] != image or container["Name"] != "/" + name or container["State"]["Running"]:
        raise ValueError("Unexpected image, ownership or pre-start state")
    if any(host.get(key) != value for key, value in required.items()):
        raise ValueError("Container resource or namespace configuration differs")
    command = create_command(image, name, target)
    expected_command = command[command.index(image) + 1:]
    if (config.get("User") != "65532:65532" or config.get("Entrypoint") != ["env"]
            or config.get("Cmd") != expected_command or config.get("Volumes")
            or config.get("Tty") or config.get("OpenStdin")):
        raise ValueError("Container identity or trusted entry point differs")
    if host.get("Tmpfs") != TMPFS or host.get("CgroupnsMode") != "private" or host.get("CapAdd"):
        raise ValueError("Additional scratch, namespace or capability configuration refused")
    if host.get("CapDrop") != ["ALL"] or host.get("SecurityOpt") != ["no-new-privileges=true"]:
        raise ValueError("Container privilege protections differ")
    if host.get("Devices") or host.get("DeviceRequests") or host.get("Binds") or host.get("PortBindings"):
        raise ValueError("Additional device, volume or port configuration refused")
    mounts = container["Mounts"]
    expected = {"/target": str(Path(target).resolve()), "/control": str(Path(__file__).resolve().parent)}
    expected.update({f"/etc/{filename}": str(Path(target).resolve() / ".network" / filename)
                     for filename in ("hosts", "hostname", "resolv.conf")})
    bindings = {mount["Destination"]: mount for mount in mounts if mount["Type"] == "bind"}
    if set(bindings) != set(expected) or any(bindings[key]["Source"] != value or bindings[key]["RW"] for key, value in expected.items()):
        raise ValueError("Read-only source mount admission failed")
    if (len(bindings) != len(expected) or len(mounts) > len(expected) + len(TMPFS)
            or any(mount["Type"] not in {"bind", "tmpfs"} for mount in mounts)
            or any(mount["Destination"] not in TMPFS for mount in mounts if mount["Type"] == "tmpfs")):
        raise ValueError("Unexpected persistent volume or additional mount refused")
    limits = {item["Name"]: (item["Soft"], item["Hard"]) for item in host["Ulimits"]}
    if limits.get("cpu") != (60, 60) or limits.get("fsize") != (8388608, 8388608):
        raise ValueError("Container native limits differ")


def bounded(command, *, deadline, idle=IDLE, permitted=(0,)):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Sandbox wall budget exhausted")
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, start_new_session=True)
    chunks, size = [], 0
    last_output = time.monotonic()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map() or process.poll() is None:
                now = time.monotonic()
                if now >= deadline or now - last_output >= idle:
                    raise TimeoutError("Sandbox wall or idle budget exhausted")
                for key, _ in selector.select(min(0.5, deadline - now, idle - (now - last_output))):
                    block = os.read(key.fileobj.fileno(), 4096)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    size += len(block)
                    if size > OUTPUT_LIMIT:
                        raise ValueError("Sandbox output ceiling exceeded")
                    chunks.append(block)
                    last_output = time.monotonic()
        code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        if code not in permitted:
            raise ValueError(f"Owned command failed with exit code {code}")
        return code, b"".join(chunks).decode("utf-8", errors="strict")
    finally:
        # The separate process group owns Docker CLI descendants, not the daemon.
        try:
            os.killpg(process.pid, getattr(signal, "SIGKILL", 9))
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
        process.stdout.close()


def negative_preflights(image, name, target, deadline, logger):
    mutations = {
        "environment": lambda argv: argv.insert(argv.index("/usr/local/bin/python"), "UNLISTED=synthetic"),
        "privileges": lambda argv: argv.__setitem__(argv.index("--user") + 1, "0:0"),
        "network": lambda argv: argv.__setitem__(argv.index("--network") + 1, "bridge"),
        "resources": lambda argv: argv.__setitem__(argv.index("--memory") + 1, "512m"),
        "rlimits": lambda argv: argv.__setitem__(argv.index("fsize=8388608:8388608"), "fsize=4096:4096"),
        "readonly": lambda argv: argv.__setitem__(argv.index(next(value for value in argv if "target=/target,readonly" in value)),
                                                  next(value for value in argv if "target=/target,readonly" in value).removesuffix(",readonly")),
        "scratch": lambda argv: argv.__setitem__(argv.index(next(value for value in argv if value.startswith("/scratch:"))),
                                                 "/scratch:rw,nosuid,nodev,noexec,size=32m,mode=1777"),
    }
    for control, mutation in mutations.items():
        owned = name + "-" + control
        command = create_command(image, owned, target) + ["--probe-only"]
        mutation(command)
        try:
            bounded(command, deadline=deadline)
            code, raw = bounded(["docker", "start", "--attach", owned], deadline=deadline, permitted=(0, 2))
            records = [json.loads(line) for line in raw.splitlines()]
            _, state = bounded(["docker", "inspect", "--format", "{{json .State}}", owned], deadline=deadline)
            state = json.loads(state)
            if (state["ExitCode"] != 2 or state["Running"] or len(records) != 1
                    or records[0].get("ok") is not False or records[0].get("phase") != control):
                raise ValueError("Missing isolation control was not refused before target execution")
            logger.info("negative preflight refused missing_control=%s", control)
        finally:
            cleanup_deadline = time.monotonic() + 20
            bounded(["docker", "rm", "--force", owned], deadline=cleanup_deadline, idle=10, permitted=(0, 1))
            _, survivors = bounded(["docker", "ps", "--all", "--filter", f"name=^/{owned}$", "--format", "{{.Names}}"], deadline=cleanup_deadline, idle=10)
            if survivors.strip():
                raise ValueError("Negative preflight container survived cleanup")


def stage_source(root, destination):
    network = destination / ".network"
    network.mkdir()
    for filename in ("hosts", "hostname", "resolv.conf"):
        (network / filename).write_text("", encoding="utf-8")
    paths = list((root / "skills/revayat-novel/scripts").glob("*.py"))
    paths += [root / "tests/sandbox/cases.py", root / "tests/e2e_evidence.py"]
    if not paths:
        raise ValueError("Target allowlist is empty")
    for source in paths:
        if source.is_symlink() or not source.is_file():
            raise ValueError("Target source is not a regular allowlisted file")
        relative = source.relative_to(root)
        output = destination / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(source.read_bytes())


def execute(image, report, logger):
    root = Path(__file__).resolve().parents[2]
    name = "revayat-novel-" + uuid.uuid4().hex
    deadline = time.monotonic() + WALL
    result = {"ok": False, "cleanup": False, "phase": "negative-preflight", "exit_code": None}
    created = False
    with tempfile.TemporaryDirectory(prefix="sandbox-", dir=report.parent) as temporary:
        target = Path(temporary) / "target"
        target.mkdir()
        stage_source(root, target)
        # The staged tree is non-sensitive read-only source, readable by UID65532.
        target.chmod(0o755)
        Path(temporary).chmod(0o755)
        try:
            _, version = bounded(["docker", "version", "--format", "{{json .Server}}"], deadline=deadline)
            version = json.loads(version)
            result["engine_version"] = version["Version"]
            logger.info("engine ready version=%s", version["Version"])
            negative_preflights(image, name, target, deadline, logger)
            result["phase"] = "admission"
            logger.info("container admission started execution=%s", name)
            # If create times out its name may nevertheless exist: always remove it.
            created = True
            bounded(create_command(image, name, target), deadline=deadline)
            _, raw = bounded(["docker", "inspect", name], deadline=deadline)
            validate_inspect(json.loads(raw), image, name, target)
            result["phase"] = "execution"
            logger.info("host configuration admitted")
            code, raw = bounded(["docker", "start", "--attach", name], deadline=deadline, permitted=(0, 1, 2, 125, 137))
            result["cli_exit_code"] = code
            _, state = bounded(["docker", "inspect", "--format", "{{json .State}}", name], deadline=deadline)
            state = json.loads(state)
            result["exit_code"] = state["ExitCode"]
            records = [json.loads(line) for line in raw.splitlines()]
            if records and records[-1].get("ok") is False:
                phase = records[-1].get("phase")
                result["phase"] = phase if phase in (*CONTROLS, "target") else "unverified"

            if len(records) != 2 or records[0].get("phase") != "controls" or records[0].get("controls") != records[1].get("controls"):
                raise ValueError("Trusted control preflight did not precede target validation")
            if state["Running"] or state["OOMKilled"] or state["Error"]:
                raise ValueError("Container execution state is incomplete")
            validate_result(records[1], exit_code=state["ExitCode"] if code == 0 else code)
            result.update(records[1])
            result["phase"] = "complete"
            logger.info("control proof and all %s fixture cases passed", len(CASES))
        except Exception as error:
            result["ok"] = False
            result["error_type"] = type(error).__name__
            logger.error("sandbox refused error_type=%s", type(error).__name__)
        finally:
            if created:
                cleanup_deadline = time.monotonic() + 20
                try:
                    bounded(["docker", "rm", "--force", name], deadline=cleanup_deadline, idle=10, permitted=(0, 1))
                    _, survivors = bounded(["docker", "ps", "--all", "--filter", f"name=^/{name}$", "--format", "{{.Names}}"], deadline=cleanup_deadline, idle=10)
                    if survivors.strip():
                        raise ValueError("Owned container survived cleanup")
                    result["cleanup"] = True
                    logger.info("owned container removed and absence verified")
                except Exception as error:
                    result.update(ok=False, cleanup=False, error_type=type(error).__name__)
                    logger.error("owned container cleanup unverified")
    report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0 if result["ok"] and result["cleanup"] else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    log = args.report.parent / ("sandbox_" + datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S_UTC") + "_" + uuid.uuid4().hex[:8] + ".log")
    handler = None
    logger = logging.getLogger("sandbox")
    logger.setLevel(logging.INFO)
    try:
        handler = logging.FileHandler(log, encoding="utf-8", mode="x")
        formatter = logging.Formatter("[%(asctime)s UTC] [%(levelname)s] [SANDBOX] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        formatter.converter = time.gmtime
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        return execute(args.image, args.report, logger)
    except Exception as error:
        print(f"Sandbox validation failed: {type(error).__name__}")
        return 2
    finally:
        if handler is not None:
            logger.removeHandler(handler)
            handler.close()


if __name__ == "__main__":
    raise SystemExit(main())
