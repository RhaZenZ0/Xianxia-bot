"""Features arrive as cultivation grows, and the road stays visible (v1.0.9).

Reported from live play: *"it's become complex and overwhelming."* A character
three minutes old met **249 leaves across 67 pages in 16 hubs** - every system
the game has, at once, none of them refused, with nothing saying which were
for them yet.

**This is a different rule from rc.32's and the difference is what these tests
hold.** `PROGRESSION_GATES` hides a door the engine **would refuse outright**,
and CLAUDE.md sets its limit: *"never a status read or the door into the
system, because a road nobody can see is a road nobody learns exists."* A
pacing curriculum hides doors that would have worked, so it has to earn that
limit back three ways - nothing vanishes (a collapsed line, and `/locked`), the
slash command still works, and the roster is content a GM can retune.

The two absolute floors are the ones a careless retune would break, and they
each have a test: anything the beginner path or a household errand needs stays
at realm 0, and `reset` stays at realm 0 - the player most likely to want it is
the one who just found the game overwhelming.
"""
from __future__ import annotations

import ast
import json
import unittest

from tests.support import PROJECT_ROOT

APP = PROJECT_ROOT / "app"
CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
ROSTER = CONTENT.get("feature_unlocks") or {}
LEAVES: dict[str, int] = {str(k): int(v) for k, v in (ROSTER.get("leaves") or {}).items()}

SURFACE = (APP / "bot" / "surface.py").read_text(encoding="utf-8")
HUBS = (APP / "bot" / "hubs.py").read_text(encoding="utf-8")
RULES = (APP / "rules" / "feature_unlocks.py").read_text(encoding="utf-8")


def _function(source: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} is gone; this gate is guarding a function that moved")


def _code(source: str, name: str) -> str:
    """One function's statements, without its docstring.

    Found by this file's own drill (rc.52's rule, arriving immediately rather
    than a release later): deleting `/locked` from the sentence `unlock_summary`
    returns left the string in the *docstring that explains it*, so the check
    passed against a broken tree. A gate that cannot tell prose from code is
    decoration.
    """
    node = _function(source, name)
    statements = node.body
    if statements and isinstance(statements[0], ast.Expr) and isinstance(statements[0].value, ast.Constant):
        statements = statements[1:]
    return "\n".join(ast.get_source_segment(source, statement) or "" for statement in statements)


class TheRosterIsReal(unittest.TestCase):
    def test_the_roster_was_read(self):
        """Asserted before it is trusted (rc.57): an empty roster would make
        every assertion below vacuously true rather than red."""
        self.assertGreater(
            len(LEAVES), 50,
            "content/world.json carries no feature_unlocks worth the name; the gate is broken, "
            "not the tree",
        )

    def test_every_gated_leaf_stands_on_the_realm_ladder(self):
        ladder = CONTENT.get("realms") or []
        self.assertTrue(ladder, "no realm ladder in the content file")
        for path, realm in sorted(LEAVES.items()):
            self.assertTrue(
                0 < realm < len(ladder),
                f"{path!r} opens at realm {realm}, which is not a rung of the {len(ladder)}-realm "
                "ladder. A floor of 0 is not a gate and a floor past the ladder is a door nobody "
                "ever reaches.",
            )


class TheFirstHourIsNeverGated(unittest.TestCase):
    """The floors a retune must not cross."""

    def test_no_leaf_the_beginner_path_needs_is_held_back(self):
        """The curriculum exists to make the opening legible. Gating a leaf the
        opening *requires* would make it illegible instead - and silently, since
        the quest would still be held and simply have no visible way to advance.
        """
        stages = CONTENT.get("beginner_path") or []
        self.assertTrue(stages, "no beginner_path in the content file")
        needed = {
            # objective type -> the leaves that report it
            "cultivate": ("cultivate",),
            "scene_action": ("scene status",),
            "explore": ("explore",),
            "talk": ("talk",),
            "trade": ("trade offer", "trade accept", "shop buy"),
            "travel": ("travel go",),
            "gather": ("alchemy forage",),
            "craft": ("craft",),
            "combat_win": ("battle challenge", "battle act"),
            "return_home": ("family enter",),
            "family_lesson": ("family lesson",),
        }
        types = {str(o.get("type")) for s in stages for o in (s.get("objectives") or [])}
        self.assertTrue(types, "the beginner path declares no objectives")
        offenders = []
        for objective in sorted(types):
            for leaf in needed.get(objective, ()):
                if leaf in LEAVES:
                    offenders.append(f"{leaf!r} (reports {objective!r}, opens at realm {LEAVES[leaf]})")
        self.assertEqual(
            offenders, [],
            "the beginner path asks for objectives whose only doors the curriculum hides:\n  "
            + "\n  ".join(offenders),
        )

    def test_the_quest_the_beginner_path_hands_over_is_not_held_back(self):
        """The floor above, one quest further (v1.1.0).

        `beginner_lesson` hands over `road_to_a_sect` as its `follow_on`, so
        every player who finishes the first hour holds it - and its trial sat
        behind a realm-1 floor on a leaf nothing but the hub can reach, since
        the command takes an argument and the typed shorthand reads two words.
        A quest the curriculum makes unreachable is the illegible opening the
        floor exists to prevent, one link down the chain.
        """
        from app.rules.quests import QUEST_DEFINITIONS

        stages = {str(s.get("quest_key")): s for s in CONTENT.get("beginner_path") or []}
        handed = [str(s.get("follow_on") or "") for s in stages.values()]
        beyond = [key for key in handed if key and key not in stages]
        self.assertIn("road_to_a_sect", beyond,
                      "the beginner path no longer hands over the road into a sect; the gate is broken, not the tree")
        doors = {
            # objective type -> every leaf that reports it; one ungated is enough
            "sect_discovery": ("city envoys", "sect recruitment recommendation", "sect recruitment status"),
            "sect_trial": ("sect recruitment trial",),
        }
        offenders = []
        for key in beyond:
            for objective in QUEST_DEFINITIONS.get(key, {}).get("objectives") or []:
                leaves = doors.get(str(objective.get("type")), ())
                if leaves and all(leaf in LEAVES for leaf in leaves):
                    offenders.append(f"{key}: {objective.get('type')!r} is reported only by "
                                     + ", ".join(f"{leaf!r} (realm {LEAVES[leaf]})" for leaf in leaves))
        self.assertEqual(offenders, [], "a quest the beginner path hands over has no door open at realm 0:\n  "
                         + "\n  ".join(offenders))

    def test_the_way_out_is_never_gated(self):
        """`/reset` is the door for somebody who has just decided this game is
        too much - which is precisely the player this release is for."""
        self.assertNotIn(
            "reset", LEAVES,
            "the curriculum holds back `reset`. The player most likely to want it is the one who "
            "just found the game overwhelming; it opens at realm 0 or the feature works against "
            "the reason it exists.",
        )

    def test_a_household_errand_can_always_be_walked(self):
        """Errands are handed over by a roster from the first hour and their
        last objective is always the door home (rc.32)."""
        for leaf in ("family enter", "family errand", "family support"):
            self.assertNotIn(
                leaf, LEAVES,
                f"{leaf!r} is gated, but household errands are handed over from realm 0 and end at "
                "the household's own door",
            )


class AStatusReadIsNeverHeldBack(unittest.TestCase):
    """rc.32's limit, held rather than quoted (v1.0.13).

    This file's own docstring above has quoted *"never a status read or the
    door into the system"* since v1.0.9 and never tested it; CLAUDE.md said
    **"Every status read stays open at realm 0"** in bold; and
    `author_feature_unlocks.py` wrote *"Every page's own status stays"* as a
    comment - **directly above eleven entries that set one to 1 or 2**. Eight
    more were never listed and inherited a page floor. Nineteen status reads
    were held back from a realm-0 player.

    It went unseen because the five CLAUDE.md names as examples - `sect
    status`, `beast status`, `abode status`, `innerworld status`,
    `secretrealm status` - are exactly the five that were right. The rule was
    checked against its own examples and never against "and the rest".
    """

    def test_no_status_read_is_gated(self):
        offenders = sorted(f"{path} (opens at realm {realm})"
                           for path, realm in LEAVES.items() if path.split()[-1:] == ["status"])
        # assertFalse, not assertEqual: a list diff prints first and the
        # finding last, and a message scrolled past is one nobody reads.
        self.assertFalse(offenders, (
            "a page's status read waits for a realm, so a player meets an empty page rather than "
            "a locked one and never learns the system is there. rc.32's limit is that a road "
            "nobody can see is a road nobody learns exists:\n  " + "\n  ".join(offenders)))

    def test_the_generator_forces_it_rather_than_listing_it(self):
        """A list is what drifted, so the rule lives in `build()`.

        Written behaviourally: the authoring script is asked to gate a status
        read at realm 2 and must refuse, which a hand-written table of zeroes
        could never promise about the twentieth one somebody adds.
        """
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "author_feature_unlocks", PROJECT_ROOT / "scripts" / "author_feature_unlocks.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        self.assertTrue(module.is_status_read("dantian status"), (
            "the generator no longer recognises a status read; the gate is broken, not the tree"))
        module.LEAVES["dantian status"] = 2
        try:
            built = module.build({"cultivation / Qi Body": ["dantian status", "dantian refine"]})
        finally:
            module.LEAVES.pop("dantian status", None)
        self.assertNotIn("dantian status", built["leaves"], (
            "the authoring script let a status read be gated by writing a number beside it. That "
            "is exactly how nineteen of them ended up at realm 1 and 2 under a comment saying "
            "they stay open - the rule has to be in build(), not in the table"))


class TheRoadStaysVisible(unittest.TestCase):
    """rc.32's limit, earned back."""

    def test_a_gated_page_prints_one_collapsed_line_not_a_column_of_padlocks(self):
        body = _code(HUBS, "unlock_line")
        self.assertIn(
            "_unlock_summary", body,
            "the collapsed line is gone, so a gated page either shows nothing about what it is "
            "holding back, or went back to a padlock each - which is the wall of rows this "
            "release exists to remove",
        )
        summary = _code(RULES, "unlock_summary")
        self.assertIn(
            "/locked", summary,
            "the collapsed line no longer names /locked, so the full list has no door and the "
            "hidden roads really are invisible",
        )

    def test_locked_reads_the_same_roster_the_panels_read(self):
        """One statement of what is shut. Two would be free to disagree, and a
        card that disagrees with the panel is worse than either alone."""
        source = (APP / "bot" / "commands" / "locked.py").read_text(encoding="utf-8")
        called = {
            node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            for node in ast.walk(_function(source, "locked")) if isinstance(node, ast.Call)
        }
        self.assertIn(
            "locked_leaves", called,
            "/locked no longer asks feature_unlocks.locked_leaves, so it is a second statement of "
            "what is shut and free to drift from the panels",
        )

    def test_the_two_kinds_of_hiding_are_kept_apart(self):
        """"The engine would refuse this here" and "you have not reached this
        yet" are different sentences and must not render alike."""
        self.assertIn(
            "_NOT_YET_UNLOCKED", HUBS,
            "the unlock registry is gone; the curriculum would have to ride the hidden-actions "
            "provider, and every held-back door would print as a padlock naming a refusal that "
            "is not what happened",
        )
        self.assertIn("register_not_yet_unlocked", SURFACE)


class GatingIsAdvertisingNeverABound(unittest.TestCase):
    """rc.48's rule, for the fifth time: a bound that lives in the client is
    not a bound. The curriculum decides what is *shown*; the engine decides
    what is allowed, and nothing here may be asked on a press."""

    def test_the_press_gate_does_not_consult_the_curriculum(self):
        body = _code(SURFACE, "_panel_gate")
        for forbidden in ("feature_unlocks", "locked_leaves", "_curriculum_unlocks"):
            self.assertNotIn(
                forbidden, body,
                f"_panel_gate consults {forbidden}: a realm floor written for pacing would become "
                "a refusal the engine never agreed to, which is the exact fault rc.48 names",
            )

    def test_no_engine_client_reads_the_roster(self):
        for relative in ("ops/game_engine.py", "ops/core_services.py"):
            source = (APP / relative).read_text(encoding="utf-8")
            self.assertNotIn(
                "feature_unlocks", source,
                f"{relative} reads the curriculum roster; it is presentation and must never reach "
                "a request",
            )

    def test_an_unreadable_roster_hides_nothing(self):
        """Fails towards showing, like `maintenance.py`'s flag fails towards
        play: a filter that hid the game when its lookup broke would be
        indistinguishable from a correct empty page."""
        for roster in (None, {}, {"leaves": None}, {"leaves": {"x": "not a number"}}):
            with self.subTest(roster=roster):
                from app.rules import feature_unlocks

                self.assertEqual(feature_unlocks.locked_leaves(roster, 0), {})


if __name__ == "__main__":
    unittest.main()
