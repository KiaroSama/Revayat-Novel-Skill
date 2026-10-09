"""The CI controller admits only a completely configured isolated container."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def module():
    path = Path(__file__).parent / "sandbox" / "runner.py"
    spec = importlib.util.spec_from_file_location("sandbox_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sandbox_launch_has_all_required_controls(tmp_path, module):
    command = module.create_command("sha256:" + "a" * 64, "revayat-novel-test", tmp_path)
    assert "--read-only" in command
    for flag, expected in [("--network", "none"), ("--user", "65532:65532"),
                           ("--cpus", "1"), ("--memory", "768m"),
                           ("--memory-swap", "768m"), ("--pids-limit", "32")]:
        assert command[command.index(flag) + 1] == expected
    assert "ALL" == command[command.index("--cap-drop") + 1]
    assert "no-new-privileges=true" in command
    assert "cpu=60:60" in command and "fsize=8388608:8388608" in command
    assert any(s.startswith("/scratch:") and "size=64m" in s for s in command)
    assert all("readonly" in command[i + 1] for i, s in enumerate(command) if s == "--mount")
    assert "env" in command and "-i" in command
    assert not any("docker.sock" in s or "GITHUB_TOKEN" in s for s in command)


@pytest.mark.parametrize("image", ["python:latest", "", "sha256:bad", "a;echo bad"])
def test_unbound_image_identity_refuses_before_launch(tmp_path, image, module):
    with pytest.raises(ValueError):
        module.create_command(image, "revayat-novel-test", tmp_path)


def test_missing_control_proof_never_authorizes_target_results(module):
    for result in ({}, {"controls": {"network": True}}, {"ok": True, "cases": 4}):
        with pytest.raises(ValueError):
            module.validate_result(result)


def test_nonzero_or_incomplete_case_result_refuses(module):
    result = {"controls": {key: True for key in module.CONTROLS},
              "cases": {key: True for key in module.CASES}, "ok": True}
    module.validate_result(result)
    with pytest.raises(ValueError):
        module.validate_result(result, exit_code=137)
    result["cases"].pop(module.CASES[0])
    with pytest.raises(ValueError):
        module.validate_result(result)


@pytest.fixture()
def inspection(tmp_path, module):
    image = "sha256:" + "a" * 64
    command = module.create_command(image, "revayat-novel-test", tmp_path)
    bindings = []
    for index, flag in enumerate(command):
        if flag != "--mount":
            continue
        fields = dict(item.split("=", 1) for item in command[index + 1].split(",") if "=" in item)
        bindings.append({"Type": "bind", "Destination": fields["target"],
                         "Source": fields["source"], "RW": False})
    return [{"Image": image, "Name": "/revayat-novel-test", "State": {"Running": False},
             "Config": {"User": "65532:65532", "Entrypoint": ["env"],
                        "Cmd": command[command.index(image) + 1:], "Volumes": None},
             "HostConfig": {"ReadonlyRootfs": True, "NetworkMode": "none",
                            "NanoCpus": 1000000000, "Memory": 805306368,
                            "MemorySwap": 805306368, "PidsLimit": 32,
                            "Privileged": False, "PidMode": "", "IpcMode": "private",
                            "CgroupnsMode": "private", "Tmpfs": dict(module.TMPFS),
                            "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges=true"],
                            "Ulimits": [{"Name": "cpu", "Soft": 60, "Hard": 60},
                                        {"Name": "fsize", "Soft": 8388608, "Hard": 8388608}]},
             "Mounts": bindings}]


def test_exact_inspection_admits_only_fixed_configuration(inspection, tmp_path, module):
    module.validate_inspect(inspection, "sha256:" + "a" * 64, "revayat-novel-test", tmp_path)


@pytest.mark.parametrize("field,value", [("NetworkMode", "host"), ("Privileged", True),
    ("CgroupnsMode", "host"), ("CapAdd", ["SYS_ADMIN"]), ("Memory", 0),
    ("PidsLimit", 0), ("Devices", [{"PathOnHost": "/dev/sda"}]),
    ("Binds", ["/private:/private"]), ("Tmpfs", {"/extra": "rw"})])
def test_changed_isolation_configuration_refuses(inspection, tmp_path, module, field, value):
    inspection[0]["HostConfig"][field] = value
    with pytest.raises(ValueError):
        module.validate_inspect(inspection, "sha256:" + "a" * 64, "revayat-novel-test", tmp_path)


@pytest.mark.parametrize("mutation", ["command", "mount", "volume", "tty"])
def test_extra_entrypoint_or_storage_refuses(inspection, tmp_path, module, mutation):
    if mutation == "command":
        inspection[0]["Config"]["Cmd"] = ["arbitrary-target"]
    elif mutation == "mount":
        inspection[0]["Mounts"][0]["RW"] = True
    elif mutation == "volume":
        inspection[0]["Config"]["Volumes"] = {"/unexpected": {}}
    else:
        inspection[0]["Config"]["Tty"] = True
    with pytest.raises(ValueError):
        module.validate_inspect(inspection, "sha256:" + "a" * 64, "revayat-novel-test", tmp_path)


def test_closed_output_does_not_remove_idle_ceiling(monkeypatch, module):
    from types import SimpleNamespace
    process = SimpleNamespace(pid=123, stdout=SimpleNamespace(fileno=lambda: 7, close=lambda: None),
                              poll=lambda: None, wait=lambda timeout: 0)
    class Selector:
        active = True
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def register(self, *args):
            pass
        def unregister(self, *args):
            self.active = False
        def get_map(self):
            return {7: True} if self.active else {}
        def select(self, timeout):
            return [(SimpleNamespace(fileobj=process.stdout), None)] if self.active else []
    clock = iter([0, 0, 0, 31])
    killed = []
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(module.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(module.os, "read", lambda *args: b"")
    monkeypatch.setattr(module.os, "killpg", lambda pid, signal: killed.append(pid), raising=False)
    with pytest.raises(TimeoutError):
        module.bounded(["docker", "start"], deadline=180)
    assert killed == [123]


def test_cancellation_still_reaps_owned_command(monkeypatch, module):
    from types import SimpleNamespace
    killed = []
    process = SimpleNamespace(pid=123, stdout=SimpleNamespace(close=lambda: None), wait=lambda timeout: 0)
    def interrupted_selector():
        raise module.Cancelled("cancelled")
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(module.selectors, "DefaultSelector", interrupted_selector)
    monkeypatch.setattr(module.os, "killpg", lambda pid, signal: killed.append(pid), raising=False)
    with pytest.raises(module.Cancelled):
        module.bounded(["docker", "start"], deadline=module.time.monotonic() + 30)
    assert killed == [123]


def test_failed_start_is_cleaned_without_target_success(monkeypatch, module, tmp_path):
    import json
    import logging
    commands = []
    monkeypatch.setattr(module, "stage_source", lambda *args: None)
    monkeypatch.setattr(module, "negative_preflights", lambda *args: None)
    monkeypatch.setattr(module, "validate_inspect", lambda *args: None)
    def transport(command, **kwargs):
        commands.append(command[1])
        if command[1] == "version":
            return 0, '{"Version":"28.0.4"}'
        if command[1] == "start":
            raise module.Cancelled("cancelled")
        return 0, "[]" if command[1] == "inspect" else ""
    monkeypatch.setattr(module, "bounded", transport)
    report = tmp_path / "result.json"
    assert module.execute("sha256:" + "a" * 64, report, logging.getLogger("test")) == 2
    result = json.loads(report.read_text(encoding="utf-8"))
    assert result["ok"] is False and result["cleanup"] is True
    assert commands[-2:] == ["rm", "ps"]
    assert not list(tmp_path.glob("sandbox-*"))


def test_surviving_container_is_never_reported_clean(monkeypatch, module, tmp_path):
    import json
    import logging
    monkeypatch.setattr(module, "stage_source", lambda *args: None)
    monkeypatch.setattr(module, "negative_preflights", lambda *args: None)
    monkeypatch.setattr(module, "validate_inspect", lambda *args: None)
    def transport(command, **kwargs):
        if command[1] == "version":
            return 0, '{"Version":"28.0.4"}'
        if command[1] == "start":
            raise ValueError("synthetic failed start")
        return 0, "revayat-novel-survivor" if command[1] == "ps" else "[]"
    monkeypatch.setattr(module, "bounded", transport)
    report = tmp_path / "result.json"
    assert module.execute("sha256:" + "a" * 64, report, logging.getLogger("test")) == 2
    assert json.loads(report.read_text(encoding="utf-8"))["cleanup"] is False
