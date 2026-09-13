"""Every dependency manifest is in a lane that actually reaches it.

Three files describe what this project installs, and each answers a different
question: `requirements.txt` the range a reader may have, `constraints-ci.txt`
the exact set the build was checked against, `requirements-optional.txt` the two
wheels two stages need. A manifest outside every automated lane is worse than no
manifest: `dependabot.yml` and `dependency-audit.yml` both read as though they
cover it, and nothing says otherwise.

The non-obvious part is which filenames Dependabot's pip updater actually
collects, because it is not the filename the dependency graph recognises.
Verified against dependabot-core rather than assumed
(`python/lib/dependabot/python/shared_file_fetcher.rb`): it takes every `.txt`
and `.in` file whose name matches /requirements/ **or** whose every line parses
as a requirement or a comment, from the configured directory and from each
immediate subdirectory of it. So `constraints-ci.txt` is in the lane — and a
future `constraints-ci.toml`, or a pin written as `pymupdf 1.28.2`, silently
would not be. That is what the first test holds in place.

The same reading is why the floors are pinned inside `supported-range.yml`
instead of in a tracked file: a tracked one would join the `ci-constraints` group
and Dependabot would propose raising the floors to latest, which is the single
change that lane exists to prevent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "skills" / "revayat-novel" / "requirements.txt"
OPTIONAL = ROOT / "skills" / "revayat-novel" / "requirements-optional.txt"
PINS = ROOT / "constraints-ci.txt"
SUPPORTED_RANGE = ROOT / ".github" / "workflows" / "supported-range.yml"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"

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
    for manifest in (MANIFEST, OPTIONAL, PINS):
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


@pytest.mark.parametrize("manifest", [MANIFEST, OPTIONAL, PINS],
                         ids=lambda p: p.name)
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
    """The `name==version` lines inside supported-range.yml's heredoc."""
    text = SUPPORTED_RANGE.read_text(encoding="utf-8")
    heredoc = re.search(r"<<'EOF'\n(.*?)\n\s*EOF", text, re.S)
    assert heredoc, "supported-range.yml no longer writes a constraints heredoc"
    return {name.lower(): version for name, version in
            re.findall(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)\s*$",
                       heredoc.group(1), re.M)}


def test_the_floors_lane_pins_every_declared_floor():
    declared = set(requirements(MANIFEST))
    pinned = set(floors_pinned_in_the_workflow())
    assert declared == pinned, (
        f"supported-range.yml does not pin the declared set: missing "
        f"{sorted(declared - pinned)}, extra {sorted(pinned - declared)}. A "
        f"dependency absent from the heredoc resolves to latest there, so the "
        f"lane would quietly stop testing the oldest set for it")


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
        f"{name} is pinned at {pin} in supported-range.yml but declared "
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
