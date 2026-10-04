"""Public transaction recovery preserves old objects, including extra owner files."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "install"))


def test_failed_second_publication_restores_entire_old_plan(tmp_path):
    from install_transaction import publish
    destinations = []
    for agent in (".claude", ".kiro"):
        path = tmp_path / agent / "skills" / "revayat-novel"
        path.mkdir(parents=True)
        (path / "owner.txt").write_bytes(b"original owner bytes")
        destinations.append(path)

    def fail(event, index):
        if event == "promoted" and index == 1:
            raise OSError("controlled publication failure")

    with pytest.raises(OSError, match="controlled"):
        publish(tmp_path, destinations, {"SKILL.md": (b"new skill", 0o644)},
                observer=fail)
    for path in destinations:
        assert (path / "owner.txt").read_bytes() == b"original owner bytes"
        assert not (path / "SKILL.md").exists()


@pytest.mark.parametrize("conflict", ["target", "backup"])
def test_recovery_validates_all_participants_before_restoring_any(tmp_path, conflict):
    from install_transaction import publish, recover
    from install_files import STATE
    paths = [tmp_path / a / "skills" / "revayat-novel" for a in (".claude", ".kiro")]
    for path in paths:
        path.mkdir(parents=True)
        (path / "owner.txt").write_bytes(b"old")
    def stop(event, index):
        if event == "promoted" and index == 1:
            if conflict == "target":
                (paths[0] / "SKILL.md").write_bytes(b"external edit")
            else:
                backup = next((tmp_path / STATE).glob("backup-*-0"))
                (backup / "owner.txt").write_bytes(b"damaged backup")
            raise OSError("stop")
    with pytest.raises(ValueError, match="conflict"):
        publish(tmp_path, paths, {"SKILL.md": (b"new", 0o644)}, observer=stop)
    with pytest.raises(ValueError, match="conflict"):
        recover(tmp_path)
    assert (paths[1] / "SKILL.md").read_bytes() == b"new"
    assert not (paths[1] / "owner.txt").exists()
    assert (tmp_path / STATE / "journal.json").is_file()


def test_source_change_after_staging_preserves_every_old_target(tmp_path):
    from install_transaction import publish
    path = tmp_path / ".claude" / "skills" / "revayat-novel"
    path.mkdir(parents=True)
    (path / "owner.txt").write_bytes(b"old")
    def check():
        raise ValueError("Source payload changed during staging")
    with pytest.raises(ValueError, match="Source payload"):
        publish(tmp_path, [path], {"SKILL.md": (b"new", 0o644)}, source_check=check)
    assert (path / "owner.txt").read_bytes() == b"old"


def test_committed_receipt_failure_does_not_rollback(tmp_path, monkeypatch):
    import install_transaction as transaction
    original = transaction.atomic_bytes
    def fail_receipt(path, body):
        if path.name.startswith("receipt-"):
            raise OSError("controlled receipt failure")
        return original(path, body)
    monkeypatch.setattr(transaction, "atomic_bytes", fail_receipt)
    path = tmp_path / ".claude" / "skills" / "revayat-novel"
    path.mkdir(parents=True)
    (path / "owner.txt").write_bytes(b"old")
    result = transaction.publish(tmp_path, [path], {"SKILL.md": (b"new", 0o644)})
    assert result["committed"] and result["cleanup_pending"]
    assert (path / "SKILL.md").read_bytes() == b"new"
    monkeypatch.setattr(transaction, "atomic_bytes", original)
    assert transaction.recover(tmp_path)["committed"]


@pytest.mark.parametrize("field,value", [
    ("schema", "foreign"), ("token", "../escape"), ("phase", "unknown"),
    ("participants", []), ("base", "foreign base"),
])
def test_tampered_journal_refuses_without_mutation(tmp_path, field, value):
    import json
    from install_transaction import publish, recover
    path = tmp_path / ".claude/skills/revayat-novel"
    path.mkdir(parents=True)
    (path / "owner.txt").write_bytes(b"old")
    def stop(event, index):
        if event == "prepared":
            raise RuntimeError("leave prepared evidence")
    with pytest.raises(RuntimeError):
        publish(tmp_path, [path], {"SKILL.md": (b"new", 0o644)}, observer=stop)
    journal = tmp_path / ".revayat-novel-installer/journal.json"
    record = json.loads(journal.read_text(encoding="utf-8"))
    record[field] = value
    journal.write_text(json.dumps(record), encoding="utf-8")
    before = journal.read_bytes()
    with pytest.raises(ValueError):
        recover(tmp_path)
    assert journal.read_bytes() == before
    assert (path / "owner.txt").read_bytes() == b"old"


@pytest.mark.parametrize("event,index", [("backed_up", 0), ("promoted", 0), ("backed_up", 1), ("promoted", 1)])
def test_each_publication_failure_preserves_originals(tmp_path, event, index):
    from install_transaction import publish
    paths = [tmp_path / a / "skills/revayat-novel" for a in (".claude", ".kiro")]
    for path in paths:
        path.mkdir(parents=True)
        (path / "owner.txt").write_bytes(b"old extra file")
    def fail(actual, number):
        if (actual, number) == (event, index):
            raise OSError("controlled boundary failure")
    with pytest.raises(OSError, match="controlled boundary"):
        publish(tmp_path, paths, {"SKILL.md": (b"new", 0o644)}, observer=fail)
    for path in paths:
        assert (path / "owner.txt").read_bytes() == b"old extra file"


def test_second_staging_failure_does_not_replace_any_target(tmp_path, monkeypatch):
    import install_transaction as transaction
    original = transaction.stage_payload
    count = 0
    def fail(path, snapshot):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("controlled copy failure")
        original(path, snapshot)
    monkeypatch.setattr(transaction, "stage_payload", fail)
    paths = [tmp_path / a / "skills/revayat-novel" for a in (".claude", ".kiro")]
    for path in paths:
        path.mkdir(parents=True)
        (path / "owner.txt").write_bytes(b"old")
    with pytest.raises(OSError, match="controlled copy"):
        transaction.publish(tmp_path, paths, {"SKILL.md": (b"new", 0o644)})
    for path in paths:
        assert (path / "owner.txt").read_bytes() == b"old"



def test_conflicting_receipt_reports_committed_cleanup_pending(tmp_path):
    from install_transaction import publish, recover
    import json
    path = tmp_path / ".claude/skills/revayat-novel"
    path.mkdir(parents=True)
    (path / "owner.txt").write_bytes(b"old")
    def conflict(event, index):
        if event == "committed":
            state = tmp_path / ".revayat-novel-installer"
            record = json.loads((state / "journal.json").read_text(encoding="utf-8"))
            (state / ("receipt-" + record["token"] + ".json")).write_bytes(b"conflicting receipt")
    result = publish(tmp_path, [path], {"SKILL.md": (b"new", 0o644)}, observer=conflict)
    assert result["committed"] and result["cleanup_pending"]
    assert (path / "SKILL.md").read_bytes() == b"new"
    result = recover(tmp_path)
    assert result["committed"] and result["cleanup_pending"]
    receipt = next((tmp_path / ".revayat-novel-installer").glob("receipt-*"))
    assert receipt.read_bytes() == b"conflicting receipt"
