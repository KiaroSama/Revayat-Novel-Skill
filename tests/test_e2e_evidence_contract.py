"""Failure artifacts contain only individually admitted synthetic files."""

import hashlib
import importlib
import json
import os

import pytest


def test_synthetic_failure_artifact_excludes_real_unlisted_private_sentinel(tmp_path):
    evidence = importlib.import_module("e2e_evidence")
    work = tmp_path / "synthetic run"
    work.mkdir()
    (work / "book.json").write_bytes(b'{"synthetic":true}')
    private = work / "private unlisted.txt"
    private.write_bytes(b"PRIVATE-SENTINEL-NO-ARTIFACT")
    (work / ".env").write_bytes(b"PRIVATE-SENTINEL-NO-ARTIFACT")
    staging = tmp_path / "artifact"
    manifest = evidence.prepare(work, staging, stage="merge", exit_code=1,
                                job_sha="a" * 40, files=["book.json"])
    assert {p.name for p in staging.iterdir()} == {"manifest.json", "book.json"}
    assert manifest == json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["stage"] == "merge" and manifest["exit_code"] == 1
    assert manifest["job"] == "pipeline" and manifest["job_sha"] == "a" * 40
    assert manifest["files"] == [{"path": "book.json", "bytes": 18,
                                 "sha256": hashlib.sha256(b'{"synthetic":true}').hexdigest()}]
    assert private.read_bytes() == b"PRIVATE-SENTINEL-NO-ARTIFACT"
    assert all(b"PRIVATE-SENTINEL" not in p.read_bytes() for p in staging.iterdir())


@pytest.mark.parametrize("names", [["private.txt"], [".env"], ["../book.json"], ["book.json", "book.json"], [None]])
def test_unlisted_unsafe_or_duplicate_evidence_refuses_before_staging(tmp_path, names):
    import e2e_evidence

    work = tmp_path / "work"
    work.mkdir()
    (work / "book.json").write_bytes(b"{}")
    staging = tmp_path / "artifact"
    with pytest.raises(ValueError):
        e2e_evidence.prepare(work, staging, stage="qa", exit_code=1, job_sha="a" * 40, files=names)
    assert not staging.exists()


def test_byte_and_file_limits_include_the_manifest(tmp_path, monkeypatch):
    import e2e_evidence

    work = tmp_path / "work"
    work.mkdir()
    (work / "book.json").write_bytes(b"x" * 100)
    # A smaller tested bound avoids allocating10MiB merely to test arithmetic.
    monkeypatch.setattr(e2e_evidence, "MAX_BYTES", 100)
    with pytest.raises(ValueError, match="manifest"):
        e2e_evidence.prepare(work, tmp_path / "artifact", stage="qa", exit_code=1,
                             job_sha="a" * 40, files=["book.json"])
    assert not (tmp_path / "artifact").exists()
    monkeypatch.setattr(e2e_evidence, "MAX_FILES", 1)
    with pytest.raises(ValueError, match="enumeration"):
        e2e_evidence.prepare(work, tmp_path / "artifact", stage="qa", exit_code=1,
                             job_sha="a" * 40, files=["book.json"])


def test_linked_evidence_is_never_copied(tmp_path):
    import e2e_evidence

    work = tmp_path / "work"
    work.mkdir()
    private = tmp_path / "private.txt"
    private.write_bytes(b"excluded private bytes")
    linked = work / "book.json"
    try:
        os.symlink(private, linked)
    except OSError:
        # Windows can lack symlink privilege. A native junction tests the same
        # admission boundary there without claiming symlink coverage.
        if os.name != "nt":
            raise
        import subprocess
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "fig.png").write_bytes(private.read_bytes())
        created = subprocess.run(["cmd", "/c", "mklink", "/J", str(work / "assets"), str(outside)],
                                 capture_output=True, timeout=5,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert created.returncode == 0, created.stderr.decode("utf-8", "replace")
        linked = work / "assets"
        names = ["assets/fig.png"]
    else:
        names = ["book.json"]
    try:
        with pytest.raises(ValueError, match="Linked"):
            e2e_evidence.prepare(work, tmp_path / "artifact", stage="qa", exit_code=1,
                                 job_sha="a" * 40, files=names)
        assert not (tmp_path / "artifact").exists()
        assert private.read_bytes() == b"excluded private bytes"
    finally:
        if linked.is_symlink():
            linked.unlink()
        else:
            os.rmdir(linked)


def test_incomplete_manifest_never_becomes_an_upload_marker(tmp_path, monkeypatch):
    import e2e_evidence

    work = tmp_path / "work"
    work.mkdir()
    (work / "book.json").write_bytes(b"{}")
    staging = tmp_path / "artifact"

    def fail_sync(fd):
        raise OSError("controlled manifest sync failure")

    monkeypatch.setattr(e2e_evidence.os, "fsync", fail_sync)
    with pytest.raises(OSError):
        e2e_evidence.prepare(work, staging, stage="qa", exit_code=1,
                             job_sha="a" * 40, files=["book.json"])
    assert not (staging / "manifest.json").exists()
    assert not (staging / ".manifest-pending").exists()
    assert (staging / "book.json").read_bytes() == b"{}"


@pytest.mark.parametrize("failure", [ValueError, RuntimeError])
def test_failed_e2e_keeps_original_exit_when_evidence_packaging_fails(tmp_path, monkeypatch, failure):
    import e2e_pipeline

    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(e2e_pipeline, "_workspace", lambda: work)
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)

    def fail_source(path):
        raise SystemExit(7)

    def failed_package(*args, **kwargs):
        raise failure("controlled packaging failure")

    monkeypatch.setattr(e2e_pipeline, "build_source_book", fail_source)
    monkeypatch.setattr(e2e_pipeline.e2e_evidence, "prepare", failed_package)
    with pytest.raises(SystemExit) as outcome:
        e2e_pipeline.main()
    assert outcome.value.code == 7
    assert json.loads((work / "status.json").read_text(encoding="utf-8"))["exit_code"] == 7
    assert work.exists()
