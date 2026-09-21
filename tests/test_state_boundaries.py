"""Expected corrupt input is a structured refusal and never consumes evidence."""

import importlib.util
import json
from pathlib import Path

import pytest

import bookir as ir
import bookwrite
import fluency
import meaning
import reviewsheet
from test_review_contracts import setup_stage, reply
from test_repair_budget import _pass, _review
from test_repair_budget import translated as translated


@pytest.mark.parametrize("stage", ["meaning", "fluency"])
@pytest.mark.parametrize("value", [[], None, "bad", 5, {"schema": "future"}])
def test_regeneration_preserves_corrupt_manifest_and_responses(translated, tmp_path, stage, value):
    module, out, mean, _ = setup_stage(stage, translated, tmp_path)
    manifest = out / "manifest.json"
    ir.write_text(manifest, json.dumps(value))
    before = {path.name: path.read_bytes() for path in out.iterdir() if path.is_file()}
    args = (translated, out) if stage == "meaning" else (translated, out, mean)
    assert module.write_sheets(*args)["ok"] is False
    assert module.record(out, translated)["ok"] is False
    assert {path.name: path.read_bytes() for path in out.iterdir() if path.is_file()} == before
    assert not (out / "superseded").exists()


@pytest.mark.parametrize("stage", ["meaning", "fluency"])
def test_changed_sheet_payload_cannot_keep_its_old_token(translated, tmp_path, stage):
    module, out, _, sheets = setup_stage(stage, translated, tmp_path)
    path = out / f"{sheets[0]}.md"
    ir.write_text(path, path.read_text(encoding="utf-8").replace("او دعوت را رد نکرد.", "او دعوت را رد کرد."))
    assert module.record(out, translated)["ok"] is False


@pytest.mark.parametrize("stage", ["meaning", "fluency"])
@pytest.mark.parametrize("control", ["!! reviewed sheet_0001 extra", "<!-- revayat-novel: mystery -->",
                                      "??b00001 sense", "++b00001 calque"])
def test_unknown_controls_never_disappear(translated, tmp_path, stage, control):
    module, out, _, sheets = setup_stage(stage, translated, tmp_path)
    reply(out, sheets[0], control + "\n")
    assert module.record(out, translated)["ok"] is False


@pytest.mark.parametrize("stage", ["meaning", "fluency"])
def test_literal_controls_and_diagnostic_prose_round_trip(stage):
    body = "Diagnostic prose.\n?? b00001 sense extra\n++ b00002 flow\n!! reviewed literal\n<!-- revayat-novel: mystery -->"
    parser = meaning.read_findings if stage == "meaning" else fluency.read_edits
    header = "?? b00001 sense" if stage == "meaning" else "++ b00001 calque"
    records, claims, problems = parser(header + "\n" + reviewsheet.escape_payload(body) + "\n!! reviewed sheet_0001\n")
    assert problems == [] and claims == ["sheet_0001"]
    assert records[0]["detail" if stage == "meaning" else "target"] == body


@pytest.mark.parametrize("change", ["proposal", "meaning"])
def test_apply_rechecks_the_evidence_files_after_lock_acquisition(translated, tmp_path, monkeypatch, change):
    _, out, mean, _ = setup_stage("fluency", translated, tmp_path)
    assert _pass(translated, out, mean, "او دعوت را پس نزد.")["ok"]
    original_book = translated.read_bytes()
    transaction = bookwrite.transaction

    def interpose(path, **kwargs):
        if kwargs["actor"] == "fluency.apply":
            if change == "meaning":
                _review(translated, mean, "?? b00001 sense\nA newly noticed meaning problem.\n")
            else:
                side = fluency.sidecar_path(out)
                found = json.loads(side.read_text(encoding="utf-8"))
                found["edits"][0]["target"] = "پیشنهاد دیگری ثبت شد."
                ir.write_text(side, json.dumps(found))
        return transaction(path, **kwargs)

    monkeypatch.setattr(bookwrite, "transaction", interpose)
    result = fluency.apply_edits(translated, out)
    assert result["ok"] is False
    assert result["refused"] == ("review-changed" if change == "proposal" else "meaning-unconfirmed")
    assert translated.read_bytes() == original_book


@pytest.mark.parametrize("value", [[], None, 4, "value", {"schema": ir.SCHEMA, "blocks": [None]}])
def test_corrupt_book_has_a_structured_cli_refusal(tmp_path, value, capsys):
    path = tmp_path / "book.json"
    ir.write_text(path, json.dumps(value))
    script = Path(__file__).resolve().parents[1] / "skills/revayat-novel/scripts/revayat-novel.py"
    spec = importlib.util.spec_from_file_location("revayat_novel_cli", script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    assert cli.main(["meaning", "status", "--book", str(path), "--out", str(tmp_path / "meaning")]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False and report["refused"]
    assert json.loads(path.read_text(encoding="utf-8")) == value


@pytest.mark.parametrize("stage", ["meaning", "fluency"])
def test_legacy_review_requires_fresh_proof_and_preserves_history(translated, tmp_path, stage):
    module, out, mean, _ = setup_stage(stage, translated, tmp_path)
    first = module.record(out, translated)
    old = dict(first, schema=f"revayat-novel/{stage}@1")
    old.pop("status")
    old.pop("event")
    ir.write_text(module.sidecar_path(out), json.dumps(old))
    args = (out, meaning.revision(meaning.pairs(ir.load_book(translated)))) if stage == "meaning" else (out, translated, mean)
    assert module.verdict(*args)["ok"] is False
    build_args = (translated, out) if stage == "meaning" else (translated, out, mean)
    for sheet_id in module.write_sheets(*build_args)["sheets"]:
        reply(out, sheet_id, "")
    fresh = module.record(out, translated)
    assert fresh["ok"] is True and fresh["history"][:-1] == first["history"]


def test_proposal_replaced_after_validation_is_preserved(translated, tmp_path, monkeypatch):
    _, out, mean, _ = setup_stage("fluency", translated, tmp_path)
    assert _pass(translated, out, mean, "او دعوت را پس نزد.")["ok"]
    path = fluency.sidecar_path(out)
    before = translated.read_bytes()
    address = fluency.merging.addressing
    replacement = None

    def interpose(book):
        nonlocal replacement
        found = json.loads(path.read_text(encoding="utf-8"))
        found["edits"][0]["target"] = "پیشنهادی که هم‌زمان جایگزین شد."
        replacement = json.dumps(found, ensure_ascii=False)
        ir.write_text(path, replacement)
        return address(book)

    monkeypatch.setattr(fluency.merging, "addressing", interpose)
    result = fluency.apply_edits(translated, out)
    assert result["ok"] is False and result["refused"] == "sidecar-changed"
    assert translated.read_bytes() == before
    assert path.read_text(encoding="utf-8") == replacement


def test_failure_after_book_replacement_requires_recovery_before_signoff(translated, tmp_path, monkeypatch):
    _, out, mean, _ = setup_stage("fluency", translated, tmp_path)
    assert _pass(translated, out, mean, "او دعوت را پس نزد.")["ok"]
    before = translated.read_bytes()
    write = ir.write_text

    def fail_after_replace(path, body):
        write(path, body)
        if Path(path) == translated:
            raise OSError("injected failure after book replacement")

    with monkeypatch.context() as patch:
        patch.setattr(ir, "write_text", fail_after_replace)
        result = fluency.apply_edits(translated, out)
    assert result["ok"] is False and result["recovery_required"] is True
    assert translated.read_bytes() != before
    assert fluency.verdict(out, translated, mean)["refused"] == "recovery-pending"
    assert bookwrite.recover(translated)["direction"] == "forward"
    assert _review(translated, mean, "")["ok"]
    assert fluency.verdict(out, translated, mean)["ok"] is True


def test_cli_logs_are_unique_utf8_and_do_not_copy_error_payloads(tmp_path):
    import runlog

    assert runlog.execute(lambda: 0, directory=tmp_path) == 0

    def fail():
        raise RuntimeError("SENSITIVE_TEST_PAYLOAD")

    with pytest.raises(RuntimeError, match="SENSITIVE_TEST_PAYLOAD"):
        runlog.execute(fail, directory=tmp_path)
    paths = list(tmp_path.glob("*.log"))
    assert len(paths) == 2
    logs = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert " UTC] [INFO] [pipeline]" in logs and "[ERROR]" in logs
    assert "finished exit=0" in logs and "finished exit=1" in logs
    assert "RuntimeError" in logs and "function=fail" in logs
    assert "SENSITIVE_TEST_PAYLOAD" not in logs
    for path in paths:
        path.rename(path.with_suffix(".closed"))


@pytest.mark.parametrize("value", [[], None, 7, "bad", {"schema": "future"}])
def test_corrupt_visual_review_is_preserved_and_refused(tmp_path, value):
    import review
    import runstate

    runstate.RunState(tmp_path).set_page(1, "qa_passed", hashes={"render": "render-a"})
    path = review.review_path(tmp_path, 1)
    ir.write_text(path, json.dumps(value))
    assert review.verdict(tmp_path, 1)["ok"] is False
    assert review.record(tmp_path, 1, {key: True for key in review.QUESTIONS})["ok"] is False
    assert json.loads(path.read_text(encoding="utf-8")) == value


@pytest.mark.parametrize("value", [[], None, 7, "bad", {"schema": "future"}])
def test_corrupt_run_state_cannot_be_silently_replaced(tmp_path, value):
    import reviewstate
    import runstate

    path = tmp_path / runstate.STATE_NAME
    ir.write_text(path, json.dumps(value))
    with pytest.raises(reviewstate.Refused):
        runstate.RunState(tmp_path).record("extract", {"source": "hash"})
    assert json.loads(path.read_text(encoding="utf-8")) == value


def test_cli_never_falls_back_to_an_agent_profile_for_book_logs(tmp_path, monkeypatch):
    import runlog

    monkeypatch.delenv("REVAYAT_NOVEL_LOG_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "profile"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "profile"))
    assert runlog.execute(lambda: 0) == 0
    assert not list(tmp_path.iterdir())
    output = tmp_path / "translated"
    monkeypatch.setenv("REVAYAT_NOVEL_LOG_DIR", str(output))
    assert runlog.execute(lambda: 0) == 0
    assert len(list(output.glob("*.log"))) == 1


@pytest.mark.parametrize("value", [[], None, 7, {"schema": "future"},
    {"schema": "revayat-novel/renderqa@1", "scope": "document", "visual_render_sha256": []}])
def test_document_review_refuses_corrupt_render_identity_without_mutation(tmp_path, value, capsys):
    import docqa
    import review

    report = docqa.report_path(tmp_path)
    ir.write_text(report, json.dumps(value))
    before = report.read_bytes()
    args = ["review", "--work", str(tmp_path)]
    for question in review.QUESTIONS:
        args.extend(["--answer", question + "=yes"])
    assert docqa.main(args) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False and result["refused"] == "invalid-state"
    assert report.read_bytes() == before
    assert not review.review_path(tmp_path, review.DOCUMENT).exists()


def test_log_write_failure_is_visible_and_does_not_hide_command_result(tmp_path, monkeypatch, capsys):
    import runlog

    class FullDisk:
        closed = False

        def write(self, text):
            raise OSError("SENSITIVE_TEST_PAYLOAD")

        def flush(self):
            pass

        def close(self):
            self.closed = True

    sink = FullDisk()
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: sink)
    assert runlog.execute(lambda: 0, directory=tmp_path) == 0
    error = capsys.readouterr().err
    assert "log writing failed" in error and "SENSITIVE_TEST_PAYLOAD" not in error
    assert sink.closed
