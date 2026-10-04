"""A modified inventory cannot silently describe an incomplete installable skill."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "install"))


def test_incomplete_shipped_inventory_refuses_before_payload_read(tmp_path):
    from installer import verified_snapshot
    inventory = json.loads((ROOT / "install/payload.json").read_text(encoding="utf-8"))
    inventory.remove("scripts/bookir.py")
    candidate = tmp_path / "payload.json"
    candidate.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="inventory integrity"):
        verified_snapshot(ROOT / "skills/revayat-novel", candidate)
