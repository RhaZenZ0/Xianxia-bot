"""Cross-language contract: equipment stats agree in all three places they live.

Why three
---------
Equipment combat stats are defined independently in

* ``app/rules/advanced_runtime.py`` ``EQUIPMENT_DEFINITIONS`` (Python: display, bind,
  /equipment status, admin grant checks),
* ``go_core/internal/game/group_combat_actions.go`` ``equipmentDefinitionsGo()``
  (Go: boss raids and party combat via ``equipmentPowerRows``), and
* ``go_core/internal/game/combat_actions.go`` ``equipDefs`` (Go: 1v1 battles via
  ``combatEquipment``).

An item added to only one or two of them silently contributes zero attack in
whichever combat system was missed - the bot would still *display* the bonus
from the Python table, so nothing in play would look wrong. The Bugslayer Sword
(v0.19.32) was the first item added after this wart was recognised; this test
is what keeps the next one honest.

The Go maps are parsed straight out of the source text, so the test needs no Go
toolchain. Map keys may be string literals or the ``bugslayerSwordItemID``-style
constants declared in the same package.
"""

import re
import unittest

from tests.support import PROJECT_ROOT

GO_GAME = PROJECT_ROOT / "go_core" / "internal" / "game"
GROUP_COMBAT = GO_GAME / "group_combat_actions.go"
COMBAT = GO_GAME / "combat_actions.go"

STATS = ("attack", "defense", "spirit", "agility")

_CONST_RE = re.compile(r'^\s*(\w+ItemID)\s*=\s*"([a-z0-9_]+)"', re.MULTILINE)
_KEY = r'(?:"(?P<lit>[a-z0-9_]+)"|(?P<const>\w+ItemID))'
# {"weapon", 120, 4, 0, 1, 0, false}
_DEF_ROW_RE = re.compile(
    _KEY + r'\s*:\s*\{\s*"(?P<slot>\w+)"\s*,\s*(?P<maxdur>-?\d+)\s*,\s*'
    r"(?P<attack>-?\d+)\s*,\s*(?P<defense>-?\d+)\s*,\s*(?P<spirit>-?\d+)\s*,\s*"
    r"(?P<agility>-?\d+)\s*,\s*(?P<indestructible>true|false)\s*\}"
)
# "spirit_iron_sword": {4, 0, 1, 0}
_TUPLE_RE = re.compile(
    _KEY + r"\s*:\s*\{\s*(?P<attack>-?\d+)\s*,\s*(?P<defense>-?\d+)\s*,\s*"
    r"(?P<spirit>-?\d+)\s*,\s*(?P<agility>-?\d+)\s*\}"
)


def _go_item_constants() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in GO_GAME.glob("*.go"):
        if path.name.endswith("_test.go"):
            continue
        for name, value in _CONST_RE.findall(path.read_text(encoding="utf-8")):
            out[name] = value
    return out


def _resolve(match: "re.Match[str]", constants: dict[str, str]) -> str:
    if match.group("lit"):
        return match.group("lit")
    name = match.group("const")
    if name not in constants:
        raise AssertionError(f"Go map key {name} is not a declared *ItemID constant")
    return constants[name]


def _function_body(source: str, header: str) -> str:
    start = source.index(header)
    end = source.index("\n}\n", start)
    return source[start:end]


def _group_combat_defs() -> dict[str, dict]:
    constants = _go_item_constants()
    body = _function_body(GROUP_COMBAT.read_text(encoding="utf-8"), "func equipmentDefinitionsGo()")
    out = {}
    for m in _DEF_ROW_RE.finditer(body):
        out[_resolve(m, constants)] = {
            "slot": m.group("slot"),
            "max_durability": int(m.group("maxdur")),
            "indestructible": m.group("indestructible") == "true",
            **{s: int(m.group(s)) for s in STATS},
        }
    return out


def _combat_equip_defs() -> dict[str, dict]:
    constants = _go_item_constants()
    source = COMBAT.read_text(encoding="utf-8")
    start = source.index("var equipDefs = map[string][4]int64{")
    end = source.index("\n", start)
    return {
        _resolve(m, constants): {s: int(m.group(s)) for s in STATS}
        for m in _TUPLE_RE.finditer(source[start:end])
    }


def _python_defs() -> dict[str, dict]:
    from app.rules.advanced_runtime import EQUIPMENT_DEFINITIONS

    return {
        item_id: {
            "slot": d["slot"],
            "max_durability": int(d["max_durability"]),
            "indestructible": bool(d.get("indestructible", False)),
            **{s: int(d.get(s, 0)) for s in STATS},
        }
        for item_id, d in EQUIPMENT_DEFINITIONS.items()
    }


class EquipmentStatParityTest(unittest.TestCase):
    def setUp(self):
        self.py = _python_defs()
        self.go_group = _group_combat_defs()
        self.go_1v1 = _combat_equip_defs()
        # Guard the parsers themselves: an empty map means the regex drifted
        # from the source, and every "parity" assertion below would pass vacuously.
        self.assertGreaterEqual(len(self.py), 6)
        self.assertEqual(len(self.go_group), len(self.py), "group-combat map parse drifted")
        self.assertEqual(len(self.go_1v1), len(self.py), "1v1 equipDefs parse drifted")

    def test_same_item_ids_everywhere(self):
        self.assertEqual(set(self.py), set(self.go_group))
        self.assertEqual(set(self.py), set(self.go_1v1))

    def test_combat_stats_match_in_all_three_tables(self):
        for item_id, py in self.py.items():
            for stat in STATS:
                self.assertEqual(
                    py[stat], self.go_group[item_id][stat],
                    f"{item_id}.{stat}: Python vs equipmentDefinitionsGo",
                )
                self.assertEqual(
                    py[stat], self.go_1v1[item_id][stat],
                    f"{item_id}.{stat}: Python vs combat_actions.go equipDefs",
                )

    def test_slot_and_durability_match_python(self):
        for item_id, py in self.py.items():
            self.assertEqual(py["slot"], self.go_group[item_id]["slot"], item_id)
            self.assertEqual(py["max_durability"], self.go_group[item_id]["max_durability"], item_id)

    def test_indestructible_flag_agrees(self):
        # Python only *displays* "indestructible"; Go is what enforces it. If
        # they disagree, /equipment status lies about what combat will do.
        for item_id, py in self.py.items():
            self.assertEqual(
                py["indestructible"], self.go_group[item_id]["indestructible"],
                f"{item_id}: indestructible flag",
            )

    def test_bugslayer_sword_is_the_only_indestructible_item(self):
        self.assertEqual(
            [i for i, d in self.py.items() if d["indestructible"]], ["bugslayer_sword"]
        )


if __name__ == "__main__":
    unittest.main()
