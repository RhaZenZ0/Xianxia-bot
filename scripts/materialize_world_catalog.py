#!/usr/bin/env python3
"""Write the expanded manual/technique catalog into content/world.json.

`app/rules/advanced_catalog.augment_advanced_catalog` grows the compact seed
(6 manuals, 12 techniques) to the documented 148 manuals / 528 techniques -
but until v0.21.3 it did so only in Python's memory, when `World` loaded the
file. The Go engine reads `content/world.json` raw, so it had never heard of
the other 142: it could not stock them, `manual.study` refused them as
"unknown cultivation manual", and no righteous manual was obtainable at all.

Content is content. This script runs the same deterministic, idempotent
expansion once and writes the result back, so both sides read one catalog.
`World` still calls the expansion at load; on a materialised file it is a
no-op, and `tests/python/unit/test_world_catalog_materialised.py` fails if
the file on disk ever drifts from what the expansion produces.

    python3 scripts/materialize_world_catalog.py           # rewrite the file
    python3 scripts/materialize_world_catalog.py --check   # exit 1 if it would change
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.rules.advanced_catalog import augment_advanced_catalog  # noqa: E402

WORLD = ROOT / "content" / "world.json"


def materialised_text(raw: str) -> str:
    data = json.loads(raw)
    augment_advanced_catalog(data)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    raw = WORLD.read_text(encoding="utf-8")
    text = materialised_text(raw)
    if "--check" in argv:
        if text == raw:
            print("content/world.json is materialised.")
            return 0
        print("content/world.json would change: run scripts/materialize_world_catalog.py")
        return 1
    if text == raw:
        print("content/world.json already materialised; nothing to do.")
        return 0
    WORLD.write_text(text, encoding="utf-8")
    data = json.loads(text)
    system = data["technique_system"]
    print(
        f"Wrote content/world.json: {len(system['manuals'])} manuals, "
        f"{len(system['techniques'])} techniques, {len(data['items'])} items."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
