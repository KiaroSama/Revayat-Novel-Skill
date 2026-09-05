#!/usr/bin/env bash
# Install the Revayat skill into one or more coding agents.
#
# Copies skills/revayat/ into each agent's real skill directory. Copies, never
# links: several agents do not follow symlinks when discovering skills, so a
# real directory is the only reliable form.
#
#   ./install.sh                                  # every installed agent, user scope
#   ./install.sh --agent claude                   # one agent
#   ./install.sh --scope project --path ~/my-book # into a project
set -euo pipefail

AGENT="all"
SCOPE="user"
PROJECT_PATH="$PWD"
FORCE=0

usage() {
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --agent) AGENT="${2:?--agent needs a value}"; shift 2 ;;
        --scope) SCOPE="${2:?--scope needs a value}"; shift 2 ;;
        --path)  PROJECT_PATH="${2:?--path needs a value}"; shift 2 ;;
        --force) FORCE=1; shift ;;
        -h|--help) usage 0 ;;
        *) printf 'unknown option: %s\n\n' "$1" >&2; usage 2 ;;
    esac
done

case "$AGENT" in
    claude|kiro|codex|cursor|cline|all) ;;
    *) printf 'unknown agent: %s\n' "$AGENT" >&2; exit 2 ;;
esac
case "$SCOPE" in
    user|project) ;;
    *) printf 'unknown scope: %s\n' "$SCOPE" >&2; exit 2 ;;
esac

# Resolve relative to this script, so the installer works from any directory.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname -- "$SCRIPT_DIR")"
SKILL_NAME="revayat"
SOURCE="$REPO_ROOT/skills/$SKILL_NAME"

if [ ! -f "$SOURCE/SKILL.md" ]; then
    printf 'Cannot find the skill at %s\n' "$SOURCE" >&2
    printf 'Run this script from inside a clone of the repository.\n' >&2
    exit 1
fi

if [ "$SCOPE" = "project" ]; then
    [ -d "$PROJECT_PATH" ] || { printf 'Project path not found: %s\n' "$PROJECT_PATH" >&2; exit 1; }
    PROJECT_PATH="$(cd -- "$PROJECT_PATH" && pwd)"
fi

agent_folder() {
    case "$1" in
        claude) printf '.claude' ;;
        kiro)   printf '.kiro' ;;
        codex)  printf '.codex' ;;
        cursor) printf '.cursor' ;;
        cline)  printf '.cline' ;;
    esac
}

if [ "$AGENT" = "all" ]; then
    TARGETS="claude kiro codex cursor cline"
else
    TARGETS="$AGENT"
fi

printf '\nRevayat installer\n'
printf '  source : %s\n' "$SOURCE"
printf '  scope  : %s' "$SCOPE"
[ "$SCOPE" = "project" ] && printf ' (%s)' "$PROJECT_PATH"
printf '\n\n'

installed=""
skipped=""

for name in $TARGETS; do
    folder="$(agent_folder "$name")"
    base="$HOME"
    [ "$SCOPE" = "project" ] && base="$PROJECT_PATH"
    root="$base/$folder/skills"
    destination="$root/$SKILL_NAME"

    # For 'all' at user scope, only install where the agent is actually present,
    # so we do not create config directories for tools that are not installed.
    if [ "$AGENT" = "all" ] && [ ! -d "$base/$folder" ]; then
        skipped="$skipped $name(absent)"
        continue
    fi

    if [ -e "$destination" ] && [ "$FORCE" -eq 0 ]; then
        printf '  %s already has %s. Replace it? [Y/n] ' "$name" "$SKILL_NAME"
        read -r answer </dev/tty || answer="y"
        case "${answer:-y}" in
            y|Y|yes|YES) ;;
            *) skipped="$skipped $name(kept)"; continue ;;
        esac
    fi

    rm -rf -- "$destination"
    mkdir -p -- "$root"
    cp -R -- "$SOURCE" "$destination"

    # Never ship caches or a local virtualenv into an agent's skill directory.
    find "$destination" \( -name __pycache__ -o -name .pytest_cache \
        -o -name .venv -o -name venv \) -type d -prune -exec rm -rf -- {} + 2>/dev/null || true

    printf '  installed -> %s\n' "$destination"
    installed="$installed $name"
done

printf '\n'
if [ -n "$installed" ]; then
    printf 'Installed for:%s\n' "$installed"
else
    printf 'Nothing was installed.\n'
fi
[ -n "$skipped" ] && printf 'Skipped:%s\n' "$skipped"

printf '\nPython dependencies:\n  pip install -r "%s/requirements.txt"\n' "$SOURCE"
printf '\nThen check the install with:\n  python3 "%s/scripts/revayat.py" doctor\n' "$SOURCE"
printf '\nActivate the skill by its name, which is "revayat" — the value of\n'
printf 'name: in SKILL.md, not the folder name.\n\n'

[ -n "$installed" ] || exit 1
exit 0
