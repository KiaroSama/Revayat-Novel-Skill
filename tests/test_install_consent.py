"""Explicit replacement consent; empty or missing interactive input never approves."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "install"))


@pytest.mark.parametrize("answer,accepted", [("", False), ("n", False), ("yes", True), ("Y", True)])
def test_interactive_consent_defaults_no(tmp_path, monkeypatch, answer, accepted):
    import installer
    destination = tmp_path / "old"
    destination.mkdir()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: answer)
    assert installer.consent(destination, False) is accepted


def test_interactive_eof_is_not_consent(tmp_path, monkeypatch):
    import installer
    destination = tmp_path / "old"
    destination.mkdir()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    def eof(_):
        raise EOFError
    monkeypatch.setattr("builtins.input", eof)
    assert not installer.consent(destination, False)
