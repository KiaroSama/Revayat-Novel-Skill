"""Contributor facts are checked against their owning tracked files."""

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_contributor_guide_names_current_benchmark_lint_and_floor_owners():
    guide = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    count = len(json.loads((ROOT / "evaluation/cases.json").read_text(encoding="utf-8"))["cases"])
    version = re.search(r"^ruff==([^\s]+)$", (ROOT / "requirements-lint.txt").read_text(encoding="utf-8"), re.M).group(1)
    assert f"{count} cases" in guide
    assert version in guide
    assert '`dependency-floors.json`' in guide
    assert '["E4", "E7", "E9", "F"]' in guide
    assert "pins live in a heredoc" not in guide
