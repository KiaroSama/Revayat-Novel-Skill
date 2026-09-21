"""Run the reconstructed contract audit in normal collection, with the baseline."""

import argparse
from contextvars import Context
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    options = parser.parse_args()
    repo = options.repo.resolve()
    if not (repo / "skills/revayat-novel/scripts/bookwrite.py").is_file():
        parser.error("--repo must name the Revayat Novel checkout")
    sys.path.insert(0, str(repo / "skills/revayat-novel/scripts"))
    import bookir as ir
    import pytest
    import runlog

    ir.use_utf8_stdio()
    evidence = repo / ".pytest-tmp" / "audit"
    evidence.mkdir(parents=True, exist_ok=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                         text=True, encoding="utf-8", check=True, timeout=10).stdout.strip()

    def check():
        return Context().run(pytest.main, [str(repo / "tests"), "-q", "--tb=short", "--durations=10",
                                          f"--junitxml={evidence / 'results.xml'}"])

    code = runlog.execute(check, name="run_audit", directory=evidence / "logs")
    ir.write_text(evidence / "result.json", json.dumps({
        "head": sha, "exit": code, "python": sys.version, "junit": str(evidence / "results.xml"),
        "versions": {name: version(name) for name in ("pymupdf", "python-docx", "beautifulsoup4", "lxml", "pillow", "pytest", "pytest-timeout")},
        "scope": "normal repository collection including reconstructed shared-contract regressions",
        "review_fixtures": "simulated reviewer replies, not independent linguistic evaluation"}, indent=1) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
