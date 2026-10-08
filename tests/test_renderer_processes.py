"""Converter selection, bounded process ownership and batch failure contracts."""

from __future__ import annotations

import subprocess
import time
import sys
import pytest
import bookir as ir
import renderqa
import wordrender

def test_a_machine_with_neither_renderer_says_what_to_install(monkeypatch,
                                                              tmp_path):
    """The message has to name the fix for *this* platform, not a generic one."""
    docx = tmp_path / "book.docx"
    docx.write_bytes(b"a document that exists, so the missing renderer is the "
                     b"only thing left to report")
    monkeypatch.setattr(wordrender, "word_available", lambda: False)
    monkeypatch.setattr(wordrender, "find_libreoffice", lambda: None)
    with pytest.raises(renderqa.RenderError) as raised:
        renderqa.render_docx(docx, tmp_path / "renders")
    assert "LibreOffice" in str(raised.value)


def test_windows_reaches_for_word_and_everywhere_else_for_libreoffice(monkeypatch):
    """The deliverable is a .docx, so Word's pagination is the real one.

    Off Windows a structural check against LibreOffice's layout is still worth
    far more than no check: nothing asked here depends on where a line broke.
    """
    monkeypatch.setattr(wordrender.sys, "platform", "win32")
    monkeypatch.setattr(wordrender, "word_available", lambda: True)
    monkeypatch.setattr(wordrender, "find_libreoffice", lambda: "/usr/bin/soffice")
    assert wordrender.backend() == "word"

    monkeypatch.setattr(wordrender, "word_available", lambda: False)
    assert wordrender.backend() == "libreoffice"


def test_word_runs_in_a_child_process_so_the_timeout_is_real(monkeypatch, tmp_path):
    """COM cannot be cancelled, so a timeout on an in-process call is a lie.

    The guard is that the parent runs Word as a subprocess and kills it. This
    proves the wall clock is enforced without needing Word installed.
    """
    import subprocess

    docx = tmp_path / "book.docx"
    docx.write_bytes(b"not really a document")
    monkeypatch.setattr(wordrender, "word_available", lambda: True)

    reached = []

    def wedged(*args, **kwargs):
        reached.append(args)
        raise subprocess.TimeoutExpired(cmd="word", timeout=1)

    monkeypatch.setattr(wordrender, "_run_bounded", wedged)
    with pytest.raises(wordrender.RenderError) as raised:
        wordrender.render(docx, tmp_path / "renders", timeout=1)
    assert "terminated" in str(raised.value)
    # The message alone does not pin the route down: on a machine that has Word,
    # a render path still calling `subprocess.run` starts the real worker, misses
    # the same one-second clock and raises the same named error. Asserting the
    # bounded runner was the thing that ran is what makes this test about the
    # process-tree kill rather than about how slowly Word starts.
    assert reached, "render() never went through _run_bounded, so nothing kills the tree"


def test_doctor_reports_the_backend_the_pipeline_will_actually_use():
    """`doctor` and `wordrender` must never disagree about the same machine.

    They did: `doctor` looked for `WINWORD` on PATH, and Word is never on PATH —
    it is driven through COM — so every Windows machine with Word installed was
    told Word was missing, and LibreOffice was not mentioned at all. The pipeline
    meanwhile found Word fine. A health check that contradicts the code it is
    checking on is worse than none; this pins `doctor` to the pipeline's answer.
    """
    import importlib.util
    from pathlib import Path

    entry = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" \
        / "scripts" / "revayat-novel.py"
    spec = importlib.util.spec_from_file_location("revayat_novel_cli", entry)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    reported = cli.doctor()["optional_tools"]["render"]
    backend = wordrender.backend()
    if backend == "word":
        assert reported.startswith("Microsoft Word"), reported
    elif backend == "libreoffice":
        assert reported.startswith("LibreOffice at "), reported
        assert wordrender.find_libreoffice() in reported
    else:
        assert reported.startswith("not found"), reported
        assert wordrender.unavailable_reason() in reported
    assert "WINWORD" not in reported


def test_a_timed_out_render_kills_the_whole_tree_not_just_the_launcher(
        monkeypatch, tmp_path):
    """Both backends are launchers, so killing the child leaves the renderer.

    The module's docstring promised a process-tree kill; `subprocess.run`
    signals the direct child only, and the real renderer - WINWORD.EXE started
    through COM, or the soffice.bin the launcher forked - is a generation
    further down. It survived, with its COM teardown never reached.
    """
    killed = []

    def record_and_kill(process):
        # It has to kill as well as record. A stub that only records leaves the
        # sleeper below running for its full 30s, and the guarded runner fails
        # the whole run for a leaked process - which is the exact leak this
        # test exists to prove is now closed.
        killed.append(process)
        process.kill()

    # Patched on `bookir`, which is where the call happens. `run_bounded` moved
    # there so the OCR stage could share it, and it calls the bare name
    # `kill_tree` — resolved in *its* globals. `wordrender._kill_tree` is an
    # alias bound at import, so rebinding it reaches nothing. Every assertion
    # below is unchanged; only the seam moved. This test failing loudly on that
    # move, rather than passing vacuously, is what caught it.
    monkeypatch.setattr(ir, "kill_tree", record_and_kill)

    # A command that outlives its timeout without doing anything else.
    slow = [sys.executable, "-c", "import time; time.sleep(30)"]
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        wordrender._run_bounded(slow, timeout=1)
    elapsed = time.monotonic() - started

    assert killed, "the tree was never killed; only the direct child was signalled"
    # When the thing under test is a *bound*, assert the bound. `killed`
    # only says the code noticed; the clock says it acted. A version that
    # raised correctly, killed correctly and still blocked until the child
    # finished would satisfy every other assertion here - and would be
    # exactly the failure the timeout exists to prevent.
    assert elapsed < 10.0, (
        f"the call was bounded at 1s and took {elapsed:.1f}s: it waited for "
        f"the child instead of returning when the bound expired")


def test_libreoffice_is_given_a_profile_of_its_own(monkeypatch, tmp_path):
    """Without one it is single-instance, and a second render returns 0 with no PDF.

    That failure is silent by construction: exit code 0, empty stderr, and a
    "produced no PDF" whose detail names nothing.
    """
    seen = {}

    def capture(command, timeout):
        seen["command"] = command
        (tmp_path / "renders").mkdir(parents=True, exist_ok=True)
        (tmp_path / "renders" / "book.pdf").write_bytes(b"%PDF-1.4\n")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(wordrender, "find_libreoffice", lambda: "/usr/bin/soffice")
    monkeypatch.setattr(wordrender, "_run_bounded", capture)

    docx = tmp_path / "book.docx"
    docx.write_bytes(b"not really a document")
    wordrender._with_libreoffice(docx, tmp_path / "renders", timeout=5)

    profile = [a for a in seen["command"] if a.startswith("-env:UserInstallation=")]
    assert profile, f"no private profile in {seen['command']}"
    assert profile[0].split("=", 1)[1].startswith("file:"), (
        "LibreOffice requires a file:// URL here; a bare path is ignored")
    assert "--norestore" in seen["command"]


def test_no_module_offers_a_predicate_that_is_true_when_it_is_false():
    """`renderqa.word_available()` returned a *reason*, non-empty when Word was
    absent, so `if word_available():` read as "Word is here" and meant the
    opposite. Nothing called it; seven tests patch `wordrender.word_available`,
    which is the real boolean. Two functions, one name, opposite senses.

    `wordrender.word_available` stays: it returns a bool and `backend()` uses it.
    """
    assert not hasattr(renderqa, "word_available"), (
        "renderqa.word_available is back. It returns a reason string, not a "
        "boolean; call wordrender.unavailable_reason() or "
        "wordrender.backend() instead.")
    # The one that is correctly named really is a predicate.
    assert isinstance(wordrender.word_available(), bool)


def test_a_batch_timeout_still_kills_the_whole_tree(tmp_path):
    """Same property as the single path, which a batch must not quietly drop."""
    killed = []

    def record_and_kill(process):
        killed.append(process)
        process.kill()

    docs = [tmp_path / "a.docx"]
    docs[0].write_bytes(b"not really a document")

    import bookir
    original = bookir.run_bounded

    def wedge(command, timeout, **kwargs):
        # A command that outlives its bound, with the real runner underneath so
        # the kill path is the production one.
        return original([sys.executable, "-c", "import time; time.sleep(30)"],
                        timeout, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(bookir, "kill_tree", record_and_kill)
        patch.setattr(wordrender, "backend", lambda: "word")
        patch.setattr(wordrender, "_run_bounded", wedge)
        started = time.monotonic()
        produced, failed, _ = wordrender.render_many(docs, tmp_path / "batch",
                                                    timeout=1)
        elapsed = time.monotonic() - started

    assert killed, "the batch's tree was never killed"
    assert elapsed < 10.0, f"bounded at 1s and took {elapsed:.1f}s"
    # Nothing was reported, and nothing is claimed: a document the worker never
    # got to is in neither map, which is the third state the design needs.
    assert not produced and not failed


def test_a_document_never_reached_is_in_neither_map(tmp_path):
    """Reporting it as failed would blame a page for a wedge in front of it."""
    docs = [tmp_path / f"{name}.docx" for name in ("a", "b", "c")]
    for path in docs:
        path.write_bytes(b"not really a document")

    def only_the_first(command, timeout, **kwargs):
        import subprocess as sp
        return sp.CompletedProcess(command, 0, b"OK\ta.docx\ta.pdf\n", b"")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(wordrender, "backend", lambda: "word")
        patch.setattr(wordrender, "_run_bounded", only_the_first)
        produced, failed, _ = wordrender.render_many(docs, tmp_path / "batch")

    assert list(produced) == [docs[0]]
    assert not failed, "b and c were never reached; they are not failures"
