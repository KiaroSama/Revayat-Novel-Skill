#!/usr/bin/env bash
# Install Revayat Novel through its shared Python 3.10+ preservation engine.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
        exec "$candidate" "$SCRIPT_DIR/installer.py" "$@"
    fi
done
printf 'Python 3.10+ is required before installation; no files were replaced.\n' >&2
exit 1
