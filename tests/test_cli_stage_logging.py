"""Public commands write safe stage diagnostics to exactly one opt-in log."""

import os
from pathlib import Path
import sys

import bookir as ir

CLI = Path(__file__).resolve().parents[1] / "skills/revayat-novel/scripts/revayat-novel.py"


def test_cli_records_safe_stage_and_refusal_without_sensitive_arguments(tmp_path):
    logs = tmp_path / "logs with spaces"
    sensitive = "PRIVATE-MANUSCRIPT-AND-TOKEN-SENTINEL"
    environment = dict(os.environ, REVAYAT_NOVEL_LOG_DIR=str(logs), PRIVATE_TOKEN=sensitive)
    process = ir.run_bounded([sys.executable, str(CLI), "web-import", "--manifest",
                              str(tmp_path / sensitive), "--out", str(tmp_path / "work")],
                             10, env=environment)
    assert process.returncode == 2
    paths = list(logs.glob("*.log"))
    assert len(paths) == 1
    text = paths[0].read_text(encoding="utf-8")
    assert "stage=web-import" in text
    assert "cause=invalid-input" in text
    assert "exit=2" in text
    assert sensitive not in text and str(tmp_path) not in text
    paths[0].unlink()


def test_owned_module_events_record_only_known_templates_and_counts(tmp_path):
    import logging
    import runlog

    logger = logging.getLogger("read_epub")
    before = (logger.level, list(logger.handlers))

    def command():
        runlog.stage("extract")
        logger.info("EPUB extracted: %d blocks, %d notes, %d distinct assets", 3, 1, 2)
        logger.error("PRIVATE-MANUSCRIPT-%s", "PRIVATE-TOKEN")
        logger.info("EPUB extracted: %d blocks, %d notes, %d distinct assets", "PRIVATE-TOKEN", 1, 2)
        return runlog.execute(lambda: 0, directory=tmp_path)

    assert runlog.execute(command, directory=tmp_path) == 0
    assert (logger.level, list(logger.handlers)) == before
    logs = list(tmp_path.glob("*.log"))
    assert len(logs) == 1
    text = logs[0].read_text(encoding="utf-8")
    assert "event=epub-extracted blocks=3 notes=1 assets=2" in text
    assert "PRIVATE-" not in text
    logs[0].rename(logs[0].with_suffix(".closed"))


def test_doctor_and_unknown_stage_have_safe_distinct_log_events(tmp_path):
    environment = dict(os.environ, REVAYAT_NOVEL_LOG_DIR=str(tmp_path))
    doctor = ir.run_bounded([sys.executable, str(CLI), "doctor"], 10, env=environment)
    assert doctor.returncode == 0
    unknown = ir.run_bounded([sys.executable, str(CLI), "PRIVATE-STAGE-SENTINEL"], 10, env=environment)
    assert unknown.returncode == 2
    logs = list(tmp_path.glob("*.log"))
    assert len(logs) == 2
    text = "\n".join(p.read_text(encoding="utf-8") for p in logs)
    assert "stage=doctor" in text and "stage=unknown" in text
    assert "cause=unknown-stage" in text
    assert "PRIVATE-STAGE" not in text
