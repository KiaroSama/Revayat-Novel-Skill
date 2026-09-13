"""Every dependency manifest is in a lane that actually reaches it.

Four files describe what this project installs, and each answers a different
question: `requirements.txt` the range a reader may have, `constraints-ci.txt`
the exact set the build was checked against, `requirements-optional.txt` the two
wheels two stages need, `requirements-lint.txt` the linter the build gates on. A
manifest outside every automated lane is worse than no manifest: `dependabot.yml`
and `dependency-audit.yml` both read as though they cover it, and nothing says
otherwise.

The manifests are **discovered**, not listed. A hard-coded list is the shape that
stops covering what it exists to cover: `requirements-lint.txt` was added and
every test here would have kept passing while saying nothing about it.

The non-obvious part is which filenames Dependabot's pip updater actually
collects, because it is not the filename the dependency graph recognises.
Verified against dependabot-core rather than assumed
(`python/lib/dependabot/python/shared_file_fetcher.rb`): it takes every `.txt`
and `.in` file whose name matches /requirements/ **or** whose every line parses
as a requirement or a comment, from the configured directory and from each
immediate subdirectory of it. So `constraints-ci.txt` is in the lane — and a
future `constraints-ci.toml`, or a pin written as `pymupdf 1.28.2`, silently
would not be. That is what the first test holds in place.

The same reading is why the exact floors live in `dependency-floors.json` rather
than in a tracked `.txt`: the updater collects every `.txt` whose lines parse as
requirements, so a tracked constraints file would join the `ci-constraints` group
and Dependabot would propose raising the floors to latest — the single change
those lanes exist to prevent. JSON it does not read, and both lanes generate
their constraints file from it at run time, so there is one list rather than two
copies drifting apart.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "skills" / "revayat-novel" / "requirements.txt"
OPTIONAL = ROOT / "skills" / "revayat-novel" / "requirements-optional.txt"
PINS = ROOT / "constraints-ci.txt"
LINT = ROOT / "requirements-lint.txt"
SUPPORTED_RANGE = ROOT / ".github" / "workflows" / "supported-range.yml"
AUDIT = ROOT / ".github" / "workflows" / "dependency-audit.yml"
FLOORS = ROOT / "dependency-floors.json"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"


def tracked_manifests() -> list[Path]:
    """Every pip manifest in the repository, found rather than listed.

    This was a hard-coded list of three, which is the shape that stops covering
    the thing it exists to cover: a fourth manifest — `requirements-lint.txt`
    was exactly that — joins the repository and every test here keeps passing
    while saying nothing about it. Discovery is the whole point, since the
    failure being guarded against is *a manifest outside every lane*.

    Scoped the way Dependabot's fetcher is scoped: the configured directory and
    one level below it. Anything deeper would not be collected anyway, so
    reporting it would be a finding nobody can act on.
    """
    found = {PINS, MANIFEST, OPTIONAL, LINT}
    for pattern in ("*.txt", "*/*.txt", "*/*/*.txt"):
        for path in ROOT.glob(pattern):
            name = path.name.lower()
            if "requirement" in name or "constraint" in name:
                found.add(path)
    return sorted(path for path in found if path.is_file())


#: `name>=1.24`, `name==1.24.0`, `name ; marker`, with a trailing comment.
_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*"
    r"(?:(?P<op>[=<>!~]=|[<>])\s*(?P<version>[0-9][^\s;#]*))?"
    r"\s*(?:;[^#]*)?(?:#.*)?$")


def requirements(path: Path) -> dict[str, tuple[str | None, str | None]]:
    """``{distribution: (operator, version)}`` for one manifest."""
    found: dict[str, tuple[str | None, str | None]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-")):
            continue
        match = _REQUIREMENT.match(line)
        assert match, f"{path.name}: cannot parse {line!r}"
        found[match["name"].lower()] = (match["op"], match["version"])
    return found


def dependabot_pip_directories() -> list[str]:
    text = DEPENDABOT.read_text(encoding="utf-8")
    blocks = text.split("- package-ecosystem:")
    directories = []
    for block in blocks[1:]:
        ecosystem = block.split("\n", 1)[0].strip().strip("\"'")
        if ecosystem != "pip":
            continue
        directory = re.search(r"^\s*directory:\s*(\S+)", block, re.M)
        assert directory, f"a pip entry with no directory: {block[:80]!r}"
        directories.append(directory.group(1).strip("\"'").rstrip("/") or "/")
    return directories


def test_every_pip_manifest_sits_in_a_dependabot_directory():
    """One level, because that is how far the fetcher walks."""
    directories = dependabot_pip_directories()
    assert directories, "dependabot.yml configures no pip ecosystem at all"
    for manifest in tracked_manifests():
        relative = manifest.relative_to(ROOT).as_posix()
        parent = "/" + relative.rsplit("/", 1)[0] if "/" in relative else "/"
        covered = [
            directory for directory in directories
            if parent == (directory or "/")
            # The fetcher also scans each immediate subdirectory of the
            # configured one, which is how `/` would reach `ci/pins.txt`.
            or (directory in ("", "/") and parent.count("/") == 1)
        ]
        assert covered, (
            f"{relative} is in no Dependabot pip directory ({directories}), so "
            f"no security update will ever be proposed for it")


@pytest.mark.parametrize("manifest", tracked_manifests(), ids=lambda p: p.name)
def test_every_pip_manifest_has_a_name_the_updater_collects(manifest: Path):
    """`.txt`/`.in`, and every line has to parse — or the file drops out silently.

    Not a style rule. Dependabot decides whether a file is a requirements file
    by reading it: a single line it cannot parse and the whole file is skipped,
    with no error anywhere, and the lane it was supposed to be in simply stops
    mentioning it.
    """
    assert manifest.suffix in (".txt", ".in"), (
        f"{manifest.name} would not be collected: the pip fetcher only takes "
        f"`.txt` and `.in`")
    for number, raw in enumerate(
            manifest.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(("#", "-r ", "-c ", "-e ", "--")):
            continue
        assert _REQUIREMENT.match(line), (
            f"{manifest.name}:{number} does not parse as a requirement "
            f"({line!r}), so Dependabot would skip the entire file")


def test_the_pinned_set_covers_exactly_what_the_manifest_declares():
    """A pin for something undeclared, or a floor with no pin, is a gap."""
    declared = set(requirements(MANIFEST))
    pinned = set(requirements(PINS))
    assert declared == pinned, (
        f"constraints-ci.txt and requirements.txt disagree: "
        f"only declared {sorted(declared - pinned)}, only pinned "
        f"{sorted(pinned - declared)}")


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text))


@pytest.mark.parametrize("name, op, version",
                         [(name, *spec) for name, spec in
                          sorted(requirements(MANIFEST).items())])
def test_every_declared_floor_is_a_floor(name, op, version):
    """`>=`, not `==` and not unbounded. The form is the promise."""
    assert op == ">=", (
        f"{name} is declared as {op or 'no specifier'} in requirements.txt; the "
        f"manifest states the oldest supported version, so it has to be `>=`")
    assert _version(version), f"{name}: {version!r} has no numbers in it"


def floors_pinned_in_the_workflow() -> dict[str, str]:
    """The exact pins, from the one tracked list both lanes read."""
    record = json.loads(FLOORS.read_text(encoding="utf-8"))
    return {name.lower(): version
            for name, version in (record.get("pins") or {}).items()}


def test_the_floors_lane_pins_every_declared_floor():
    declared = set(requirements(MANIFEST))
    pinned = set(floors_pinned_in_the_workflow())
    assert declared == pinned, (
        f"dependency-floors.json does not pin the declared set: missing "
        f"{sorted(declared - pinned)}, extra {sorted(pinned - declared)}. A "
        f"dependency absent from it resolves to latest in both lanes, so they "
        f"would quietly stop testing the oldest set for it")


@pytest.mark.parametrize("workflow", [SUPPORTED_RANGE, AUDIT])
def test_both_lanes_read_the_floors_from_the_one_list(workflow: Path):
    """Not a copy each. Two pin lists is the drift this project keeps paying for."""
    text = workflow.read_text(encoding="utf-8")
    assert "dependency-floors.json" in text, (
        f"{workflow.name} does not read dependency-floors.json, so its idea of "
        f"the oldest set can differ from the other lane's")
    assert not re.search(r"^\s+pymupdf==", text, re.M), (
        f"{workflow.name} pins a floor inline as well as reading the list; one "
        f"of the two will be forgotten")


def test_the_floors_name_the_interpreter_they_install_on():
    """Which interpreter can install the oldest set is a fact about the set.

    On CPython 3.13 `pymupdf==1.24.3` has no wheel at all, so a lane taking the
    interpreter from its own matrix would be testing a set it had silently
    resolved upwards.
    """
    record = json.loads(FLOORS.read_text(encoding="utf-8"))
    assert re.fullmatch(r"3\.\d+", str(record.get("interpreter", ""))), record
    for workflow in (SUPPORTED_RANGE, AUDIT):
        assert "steps.floors.outputs.interpreter" in workflow.read_text(
            encoding="utf-8"), (
            f"{workflow.name} hard-codes its interpreter instead of reading the "
            f"one the floors name")


def test_the_audit_does_not_call_a_latest_resolution_floor_coverage():
    """The label was the defect: `-r` on a range audits the newest match.

    Two steps resolved the same ranges to the same latest versions while one of
    them was titled as floor coverage, so the oldest supported set — what a reader
    with an old constraint or a warm cache installs — was audited by nothing.
    """
    text = AUDIT.read_text(encoding="utf-8")
    assert "Audit the floors a reader installs against" not in text, (
        "the misleading step title is back; `pip-audit -r` on a >= manifest "
        "resolves to latest and is not floor coverage")
    assert "Audit the floors, pinned" in text, (
        "the audit no longer has a job that installs the exact oldest set")


@pytest.mark.parametrize("name, pin",
                         sorted(floors_pinned_in_the_workflow().items()))
def test_each_floors_pin_is_the_floors_own_release_series(name, pin):
    """The pin cannot be derived by appending `.0`, so it is asserted instead.

    There is no release called `pymupdf` 1.24 and no `pytest-timeout` 2.3.0 —
    the lowest release in each series is 1.24.0 and 2.3.1. What must hold is
    that the pin satisfies the floor *and* stays inside its series: pinning
    1.28.2 would satisfy `>=1.24` and test nothing the matrix does not already.
    """
    floor = requirements(MANIFEST)[name][1]
    assert pin.startswith(f"{floor}.") or pin == floor, (
        f"{name} is pinned at {pin} in dependency-floors.json but declared "
        f">={floor}: that is a different release series, so the lane is not "
        f"testing the declared floor")
    assert _version(pin) >= _version(floor), (
        f"{name}: {pin} is below its own declared floor {floor}")


def test_the_optional_manifest_declares_no_floor():
    """An untested floor reads as a tested one.

    Nothing in CI has ever installed an old `ocrmypdf`, so a number here would
    be a claim with no lane behind it — the exact defect `supported-range.yml`
    exists to stop the core manifest making.
    """
    for name, (op, version) in requirements(OPTIONAL).items():
        assert op is None and version is None, (
            f"{name} carries {op}{version} in requirements-optional.txt. If the "
            f"floor is real, pin it in supported-range.yml too so something "
            f"proves it; if it is a guess, drop it")


def test_the_windows_only_wheel_carries_its_marker():
    """Without the marker, a Linux reader's `pip install -r` fails outright."""
    text = OPTIONAL.read_text(encoding="utf-8")
    line = next(raw for raw in text.splitlines()
                if raw.strip().lower().startswith("pywin32"))
    assert "sys_platform" in line and "win32" in line, (
        f"pywin32 is declared without a platform marker: {line!r}")
