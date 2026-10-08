"""A timeout terminates real descendants, not only the bounded launcher."""

import concurrent.futures
import json
import socket
import subprocess
import sys
import time

import bookir as ir

# Each process owns an independent control connection. A surviving descendant
# keeps its connection open, so direct-child-only cleanup cannot pass the test.
WORKER = '''import json, os, socket, subprocess, sys, time
role, port = int(sys.argv[1]), int(sys.argv[2])
child = None
if role < 2:
    child = subprocess.Popen([sys.executable, __file__, str(role + 1), str(port)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
with socket.create_connection(("127.0.0.1", port), timeout=3) as control:
    control.sendall((json.dumps({"role": role, "pid": os.getpid(),
        "started_ns": time.monotonic_ns()}) + "\\n").encode("utf-8"))
    control.settimeout(20)
    try:
        control.recv(1)
    except (OSError, TimeoutError):
        pass
if child is not None:
    try:
        child.wait(timeout=3)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=3)
'''


def test_run_bounded_kills_parent_child_and_grandchild_without_patching(tmp_path):
    worker = tmp_path / "owned_tree.py"
    worker.write_text(WORKER, encoding="utf-8")
    connections, identities = [], []
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(3)
        listener.settimeout(4)
        started = time.monotonic()
        future = pool.submit(ir.run_bounded,
            [sys.executable, str(worker), "0", str(listener.getsockname()[1])], 5)
        try:
            for _ in range(3):
                control, _ = listener.accept()
                control.settimeout(3)
                connections.append(control)
                received = b""
                while not received.endswith(b"\n"):
                    piece = control.recv(1024)
                    assert piece and len(received) < 1024, "invalid readiness envelope"
                    received += piece
                identities.append(json.loads(received.decode("utf-8")))
            assert {i["role"] for i in identities} == {0, 1, 2}
            assert len({i["pid"] for i in identities}) == 3
            assert all(type(i["started_ns"]) is int and i["started_ns"] > 0 for i in identities)
            try:
                future.result(timeout=15)
            except subprocess.TimeoutExpired:
                pass
            else:
                raise AssertionError("the owned tree did not exceed its timeout")
            for control in connections:
                control.settimeout(2)
                try:
                    ended = control.recv(1) == b""
                except ConnectionResetError:
                    ended = True
                assert ended, "a real descendant survived tree cleanup"
            assert time.monotonic() - started < 15
        finally:
            # STOP reaches only the processes that connected to our private
            # readiness socket. No PID reuse or global process-name kill.
            for control in connections:
                try:
                    control.sendall(b"S")
                    control.shutdown(socket.SHUT_WR)
                    control.settimeout(4)
                    control.recv(1)
                except OSError:
                    pass
                finally:
                    control.close()
            pool.shutdown(wait=True, cancel_futures=True)
