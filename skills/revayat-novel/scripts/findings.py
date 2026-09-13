"""A finding, and the report that collects them.

Lives on its own because three modules produce findings — the checks over the IR,
the checks over the finished package, and the page-level gates — and a shared
vocabulary cannot sit inside any one of them without the others importing
upwards. `qa` re-exports these names, because `qa.Report` is how every caller and
every test already reaches them.
"""

from __future__ import annotations

from typing import Any

ERROR = "error"
WARNING = "warning"


class Report:
    def __init__(self) -> None:
        self.findings: list[dict[str, Any]] = []
        self.counts: dict[str, int] = {}

    def add(self, severity: str, code: str, unit: str, detail: str) -> None:
        self.findings.append(
            {"severity": severity, "code": code, "unit": unit, "detail": detail[:200]}
        )

    def count(self, name: str, value: int) -> None:
        """Record a total the findings cannot express.

        ``findings`` is capped at ``limit``, so a list of untranslated blocks
        says nothing about whether one paragraph was missed or a thousand.
        """
        self.counts[name] = value

    def summary(self, limit: int | None = 60) -> dict[str, Any]:
        """``limit=None`` for every finding — what a *gate* has to ask for.

        The default exists so a report a person opens is readable. Anything that
        branches on the list, or counts it, or concatenates it into a list
        something else will branch on, passes ``None``: the cap is a display
        decision and must never become a verdict.
        """
        errors = [f for f in self.findings if f["severity"] == ERROR]
        warnings = [f for f in self.findings if f["severity"] == WARNING]
        by_code: dict[str, int] = {}
        for finding in self.findings:
            by_code[finding["code"]] = by_code.get(finding["code"], 0) + 1
        return {
            "ok": not errors,
            "errors": len(errors),
            "warnings": len(warnings),
            "counts": dict(sorted(self.counts.items())),
            "by_code": dict(sorted(by_code.items(), key=lambda kv: -kv[1])),
            "findings": (errors + warnings)[:limit],
            "truncated": 0 if limit is None else max(0, len(self.findings) - limit),
        }
