"""Trusted stdlib control checks run before any repository module is imported."""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import resource
import signal
import stat
import socket
import subprocess
import sys

ENVIRONMENT = {
    "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/scratch/home",
    "TMPDIR": "/scratch/tmp", "XDG_CACHE_HOME": "/scratch/cache",
    "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONHASHSEED": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
}


def denied_write(path):
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError as error:
        if error.errno not in {errno.EROFS, errno.EACCES, errno.EPERM, errno.ENOENT}:
            raise
        return
    else:
        os.close(descriptor)
        os.unlink(path)
        raise ValueError("write outside assigned scratch succeeded")


PHASE = "environment"


def observe():
    global PHASE
    controls = {}
    if dict(os.environ) != ENVIRONMENT:
        raise ValueError("target environment differs from the safe allowlist")
    controls["environment"] = True
    PHASE = "privileges"
    if os.getuid() != 65532 or os.getgid() != 65532:
        raise ValueError("sandbox identity is not the unprivileged assigned user")
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines() if ":" in line)
    if int(status["CapEff"].strip(), 16) or status["NoNewPrivs"].strip() != "1" or status["Seccomp"].strip() != "2":
        raise ValueError("capabilities, no-new-privileges or seccomp are unavailable")
    controls["privileges"] = True
    PHASE = "network"
    interfaces = {line.split(":", 1)[0].strip() for line in Path("/proc/net/dev").read_text(encoding="utf-8").splitlines()[2:]}
    if interfaces != {"lo"}:
        raise ValueError("network namespace contains an external interface")
    routes = Path("/proc/net/route").read_text(encoding="utf-8").splitlines()[1:]
    if routes:
        raise ValueError("sandbox has a network route")
    with socket.socket() as connection:
        connection.settimeout(0.2)
        # TEST-NET address: absence of a route must reject before any packet.
        try:
            connection.connect(("192.0.2.1", 9))
        except OSError as error:
            if error.errno not in {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EPERM}:
                raise ValueError("network refusal did not prove absent routing") from error
        else:
            raise ValueError("external network was reachable")
    controls["network"] = True
    PHASE = "resources"
    cgroup = Path("/sys/fs/cgroup")
    expected = {"memory.max": "805306368", "memory.swap.max": "0", "pids.max": "32"}
    if any((cgroup / name).read_text(encoding="utf-8").strip() != value for name, value in expected.items()):
        raise ValueError("memory, swap or process cgroup bounds differ")
    quota, period = (cgroup / "cpu.max").read_text(encoding="utf-8").split()
    if quota == "max" or int(quota) > int(period) or int(quota) < 1:
        raise ValueError("CPU cgroup quota is absent or too large")
    controls["resources"] = True
    PHASE = "rlimits"
    if resource.getrlimit(resource.RLIMIT_FSIZE) != (8388608, 8388608) or resource.getrlimit(resource.RLIMIT_CPU) != (60, 60):
        raise ValueError("file-size or CPU-time limit differs")
    controls["rlimits"] = True
    PHASE = "readonly"
    mounts = [line.split() for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()]
    for line in mounts:
        # proc exposes namespace-local process observations, not a disk store.
        # Its kernel settings are separately masked/read-only by Docker.
        if "rw" in line[5].split(",") and line[4] != "/scratch":
            if line[line.index("-") + 1] == "proc" and line[4] == "/proc":
                continue
            # Docker masks these kernel interfaces with the same non-storage null device.
            if line[4] in {"/proc/kcore", "/proc/keys", "/proc/interrupts", "/proc/timer_list", "/proc/sched_debug"}:
                info = Path(line[4]).stat()
                if stat.S_ISCHR(info.st_mode) and (os.major(info.st_rdev), os.minor(info.st_rdev)) == (1, 3):
                    continue
            raise ValueError("unassigned writable filesystem")
    for target in ("/", "/target", "/control", "/sys", "/sys/fs/cgroup",
                   "/dev", "/dev/pts", "/dev/shm", "/dev/mqueue"):
        found = next((line for line in mounts if line[4] == target), None)
        if found is None or "ro" not in found[5].split(","):
            raise ValueError("required read-only mount is not proved")
    for target in ("/.sandbox-probe", "/target/.sandbox-probe", "/control/.sandbox-probe",
                   "/usr/local/.sandbox-probe", "/tmp/.sandbox-probe",
                   "/dev/.sandbox-probe", "/dev/shm/.sandbox-probe", "/dev/mqueue/.sandbox-probe"):
        denied_write(target)
    devices = {"null": (1, 3), "zero": (1, 5), "full": (1, 7), "random": (1, 8),
               "urandom": (1, 9), "tty": (5, 0)}
    for entry in Path("/dev").iterdir():
        info = entry.lstat()
        if stat.S_ISCHR(info.st_mode):
            if devices.get(entry.name) != (os.major(info.st_rdev), os.minor(info.st_rdev)):
                raise ValueError("unexpected character device")
        elif stat.S_ISBLK(info.st_mode) or stat.S_ISREG(info.st_mode):
            raise ValueError("unexpected persistent device store")
    for target in ("/proc/sys", "/proc/sysrq-trigger", "/proc/irq", "/proc/bus"):
        found = next((line for line in mounts if line[4] == target), None)
        if found is None or "ro" not in found[5].split(","):
            raise ValueError("proc kernel controls are not read-only")
    controls["readonly"] = True
    PHASE = "scratch"
    scratch = next((line for line in mounts if line[4] == "/scratch"), None)
    if scratch is None or "rw" not in scratch[5].split(",") or scratch[scratch.index("-") + 1] != "tmpfs":
        raise ValueError("assigned scratch is not a writable tmpfs")
    size = os.statvfs("/scratch")
    if size.f_blocks * size.f_frsize != 67108864:
        raise ValueError("scratch disk ceiling differs")
    for path in ENVIRONMENT["HOME"], ENVIRONMENT["TMPDIR"], ENVIRONMENT["XDG_CACHE_HOME"]:
        Path(path).mkdir(mode=0o700)
    probe = Path("/scratch/write-probe")
    probe.write_bytes(b"scratch-only")
    if probe.read_bytes() != b"scratch-only":
        raise ValueError("scratch write did not round-trip")
    probe.unlink()
    # A sparse file crosses the individual-file ceiling without allocating it.
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    with probe.open("wb") as handle:
        try:
            handle.truncate(8388609)
        except OSError as error:
            if error.errno != errno.EFBIG:
                raise
        else:
            raise ValueError("file-size ceiling did not refuse")
    probe.unlink()
    controls["scratch"] = True
    return controls


def main():
    global PHASE
    try:
        controls = observe()
        print(json.dumps({"phase": "controls", "controls": controls}), flush=True)
        if sys.argv[1:] == ["--probe-only"]:
            return 0
        if sys.argv[1:]:
            raise ValueError("unexpected trusted preflight arguments")
        PHASE = "target"
        # The same-UID fixture child must not reopen the parent's proof pipe or memory.
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(4, 0, 0, 0, 0) != 0 or libc.prctl(3, 0, 0, 0, 0) != 0:
            raise ValueError("trusted parent process protection unavailable")
        os.chdir("/scratch")
        with Path("/scratch/target-result.json").open("w+b") as capture:
            subprocess.run([sys.executable, "-B", "/target/tests/sandbox/cases.py"],
                           env=ENVIRONMENT, stdin=subprocess.DEVNULL,
                           stdout=capture, stderr=subprocess.DEVNULL,
                           timeout=150, check=True)
            capture.seek(0)
            raw = capture.read(4097)
        if len(raw) > 4096:
            raise ValueError("target result exceeded the finite report ceiling")
        result = json.loads(raw.decode("utf-8"))
        print(json.dumps({"ok": True, "controls": controls, "cases": result}), flush=True)
        return 0
    except Exception as error:
        print(json.dumps({"ok": False, "phase": PHASE, "error_type": type(error).__name__}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
