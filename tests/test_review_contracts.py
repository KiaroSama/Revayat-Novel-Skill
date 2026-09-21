"""Whole review manifests, refusal states, event identity and locked application."""

from __future__ import annotations

import json

import pytest

import bookir as ir
import bookwrite
import fluency
import meaning
import qa
import reviewsheet
import signoff
from test_repair_budget import _settled, _pass
from test_repair_budget import translated as translated
from tests_support import review_reply


def setup_stage(stage, translated, tmp_path, per_sheet=20):
    module = meaning if stage == "meaning" else fluency
    mean = tmp_path / "meaning"
    assert _settled(translated, mean)["ok"] is True
    out = tmp_path / "review" if stage == "meaning" else tmp_path / "fluency"
    args = (translated, out) if stage == "meaning" else (translated, out, mean)
    sheets = module.write_sheets(*args, per_sheet=per_sheet)["sheets"]
    for sheet_id in sheets:
        reply(out, sheet_id, "")
    return module, out, mean, sheets


def reply(out, sheet_id, body):
    ir.write_text(out / f"out_{sheet_id}.md", review_reply(
        out / f"{sheet_id}.md", body + f"!! reviewed {sheet_id}\n"))


@pytest.mark.parametrize("stage", ["meaning", "fluency"])
@pytest.mark.parametrize("count", [0, 1])
def test_ownership_cannot_hide_unenumerated_sheets(translated, tmp_path, stage, count):
    module, out, _, _ = setup_stage(stage, translated, tmp_path, per_sheet=2)
    path = out / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert len(manifest["sheets"]) == 2
    manifest["sheets"] = manifest["sheets"][:count]
    ir.write_text(path, json.dumps(manifest))
    assert module.record(out, translated)["ok"] is False
    assert not module.sidecar_path(out).exists()


@pytest.mark.parametrize("stage", ["meaning", "fluency"])
@pytest.mark.parametrize("ending", ["sense extra", "sense!", "SENSE", ""])
def test_malformed_own_stage_controls_never_mean_clean(translated, tmp_path, stage, ending):
    module, out, _, sheets = setup_stage(stage, translated, tmp_path)
    sigil = "??" if stage == "meaning" else "++"
    header = f"{sigil} b00001 {ending}".rstrip()
    reply(out, sheets[0], header + "\nDiagnostic text.\n")
    assert module.record(out, translated)["ok"] is False
    assert not module.sidecar_path(out).exists()


@pytest.mark.parametrize("stage", ["meaning", "fluency"])
def test_duplicate_record_is_byte_identical_and_spends_no_attempt(translated, tmp_path, stage):
    module, out, _, sheets = setup_stage(stage, translated, tmp_path)
    body = "?? b00001 sense\nFirst argument.\n" if stage == "meaning" else "++ b00001 calque\nاو دعوت را پس نزد.\n"
    reply(out, sheets[0], body)
    first = module.record(out, translated)
    before = module.sidecar_path(out).read_bytes()
    second = module.record(out, translated)
    assert second == first
    assert module.sidecar_path(out).read_bytes() == before


def test_two_arguments_in_one_review_allow_the_first_repair(translated, tmp_path):
    module, out, _, sheets = setup_stage("meaning", translated, tmp_path)
    reply(out, sheets[0], "?? b00001 sense\nThe negation changed.\n"
          "?? b00001 sense\nThe participant also changed.\n")
    recorded = module.record(out, translated)
    assert len(recorded["findings"]) == 2
    assert meaning.repair_requests(out)["ok"] is True
    episode = recorded["episodes"]["b00001/sense"]
    assert episode["attempts"] == 1
    assert "negation" in str(episode["arguments"]) and "participant" in str(episode["arguments"])


@pytest.mark.parametrize("stage", ["meaning", "fluency"])
def test_three_regenerations_preserve_every_response(translated, tmp_path, stage):
    module, out, mean, sheets = setup_stage(stage, translated, tmp_path)
    expected = []
    for generation in range(3):
        reply(out, sheets[0], f"Diagnostic generation {generation}.\n")
        expected.append((out / f"out_{sheets[0]}.md").read_bytes())
        args = (translated, out) if stage == "meaning" else (translated, out, mean)
        module.write_sheets(*args)
    archived = [p.read_bytes() for p in (out / "superseded").rglob("*.md")]
    for body in expected:
        assert archived.count(body) == 1
        stage_name, _, token = reviewsheet.request_of(body.decode("utf-8"))
        assert stage_name == stage and token


@pytest.mark.parametrize("mode", ["rounds-exhausted", "no-new-evidence", "oscillating"])
def test_refused_fluency_reaches_neither_verdict_nor_final_qa(translated, tmp_path, mode, capsys):
    _, out, mean, _ = setup_stage("fluency", translated, tmp_path)
    initial = ir.load_book(translated)["blocks"][0]["target"]
    targets = ["او دعوت را پس نزد.", "دعوت را رد نکرد.", "او دعوت را قبول کرد."]
    if mode == "oscillating":
        targets[1] = initial
    if mode == "no-new-evidence":
        targets = targets[:2]
    for index, target in enumerate(targets):
        recorded = _pass(translated, out, mean, target)
        if index < len(targets) - 1 and mode != "no-new-evidence":
            assert recorded["ok"] is True
            assert fluency.apply_edits(translated, out)["ok"] is True
            assert _settled(translated, mean)["ok"] is True
    assert recorded["ok"] is False and recorded["refused"] == mode
    assert fluency.apply_edits(translated, out)["ok"] is False
    assert fluency.verdict(out, translated, mean)["ok"] is False
    assert any(unit == "fluency" for _, unit, _ in signoff.problems(
        translated, review_dir=mean, fluency_dir=out))
    assert qa.main(["check", "--book", str(translated), "--review", str(mean),
                    "--fluency", str(out), "--strict"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert any(item["code"] == "semantic-rejected" and item["unit"] == "fluency"
               for item in report["findings"])


@pytest.mark.parametrize("change,accepted", [("target", False), ("source", False),
                                            ("metadata", True), ("no-change", True)])
def test_apply_revalidates_after_a_competing_transaction(translated, tmp_path, monkeypatch, change, accepted):
    _, out, mean, _ = setup_stage("fluency", translated, tmp_path)
    assert _pass(translated, out, mean, "او دعوت را پس نزد.")["ok"] is True
    before = fluency.sidecar_path(out).read_bytes()
    transaction = bookwrite.transaction

    def interpose(path, **kwargs):
        if kwargs.get("actor") == "fluency.apply":
            with transaction(path, actor="competitor") as tx:
                if change == "target":
                    tx.book["blocks"][0]["target"] = "متن نویسندهٔ دیگر."
                elif change == "source":
                    tx.book["blocks"][0]["text"] = "She refused the invitation."
                elif change == "metadata":
                    tx.book["meta"]["operator_note"] = "retain me"
        return transaction(path, **kwargs)

    monkeypatch.setattr(bookwrite, "transaction", interpose)
    result = fluency.apply_edits(translated, out)
    assert result["ok"] is accepted
    if not accepted:
        assert fluency.sidecar_path(out).read_bytes() == before
        if change == "target":
            assert ir.load_book(translated)["blocks"][0]["target"] == "متن نویسندهٔ دیگر."
    if change == "metadata":
        assert ir.load_book(translated)["meta"]["operator_note"] == "retain me"


@pytest.mark.parametrize("stage", ["meaning", "fluency"])
@pytest.mark.parametrize("value", [[], None, 7, "value", {"schema": "future"}])
def test_corrupt_sidecars_refuse_without_mutation(translated, tmp_path, stage, value):
    module, out, mean, _ = setup_stage(stage, translated, tmp_path)
    path = module.sidecar_path(out)
    ir.write_text(path, json.dumps(value))
    before = path.read_bytes()
    args = (out, meaning.revision(meaning.pairs(ir.load_book(translated)))) if stage == "meaning" else (out, translated, mean)
    assert module.verdict(*args)["ok"] is False
    assert module.record(out, translated)["ok"] is False
    if stage == "fluency":
        assert module.apply_edits(translated, out)["ok"] is False
    assert path.read_bytes() == before


@pytest.mark.parametrize("missing", ["ok", "status", "edits"])
def test_a_no_edit_approval_requires_explicit_complete_state(translated, tmp_path, missing):
    module, out, mean, _ = setup_stage("fluency", translated, tmp_path)
    assert module.record(out, translated)["ok"] is True
    assert module.verdict(out, translated, mean)["ok"] is True
    path = module.sidecar_path(out)
    found = json.loads(path.read_text(encoding="utf-8"))
    found.pop(missing, None)
    ir.write_text(path, json.dumps(found))
    assert module.verdict(out, translated, mean)["ok"] is False
