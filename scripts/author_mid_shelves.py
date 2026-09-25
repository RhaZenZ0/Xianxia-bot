#!/usr/bin/env python3
"""Author the capitals' Mid-grade shelf lines into content/world.json (v1.6.0).

A crafted item carries a grade, Low to Transcendent, stored as `<id>@<grade>`
with the bare id as Low. On the owner's call, NPC shops deal in the two lowest
grades only: every town sells Low, and a capital (a `realm_hub` city, whose
shops are already a tier better and dearer since v0.36.0) also sells Mid.

So for each crafted item a capital shop already sells, this adds a Mid line
beside it at the grade's price (the ladder's `price_mult` for `mid`), and for
each crafted item it already buys, a Mid buy line the same way. A crafted item
is a recipe's output; anything else has no grade.

Idempotent: a line that already exists is left exactly as it is, so a GM who
re-priced one is obeyed and running this twice changes nothing.

    python3 scripts/author_mid_shelves.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "content" / "world.json"
SEPARATOR = "@"


def crafted_items(world: dict) -> set[str]:
    return {item for recipe in world.get("recipes", {}).values() for item in (recipe.get("output") or {})}


def mid_multiplier(world: dict) -> int:
    grades = world["item_grade_system"]["grades"]
    return int(next(g for g in grades if g["key"] == "mid")["price_mult"])


def author(world: dict) -> int:
    crafted, mult, added = crafted_items(world), mid_multiplier(world), 0
    for shop in world["shops"].values():
        if not world["locations"].get(shop["city"], {}).get("realm_hub"):
            continue
        have = {line["item_id"] for line in shop["sells"]}
        for line in list(shop["sells"]):
            item = line["item_id"]
            mid = f"{item}{SEPARATOR}mid"
            if item in crafted and mid not in have:
                entry = {"item_id": mid, "quantity": max(1, int(line["quantity"]) // 2), "price": int(line["price"]) * mult}
                if "made_here" in line:
                    entry["made_here"] = line["made_here"]
                shop["sells"].insert(shop["sells"].index(line) + 1, entry)
                have.add(mid)
                added += 1
        for item, price in list(shop["buys"].items()):
            mid = f"{item}{SEPARATOR}mid"
            if item in crafted and mid not in shop["buys"]:
                shop["buys"][mid] = int(price) * mult
                added += 1
    return added


def main() -> None:
    raw = CONTENT.read_text(encoding="utf-8")
    world = json.loads(raw)
    added = author(world)
    CONTENT.write_text(json.dumps(world, indent=2, ensure_ascii=False) + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")
    print(f"added {added} Mid-grade lines to capital shops")


if __name__ == "__main__":
    main()
