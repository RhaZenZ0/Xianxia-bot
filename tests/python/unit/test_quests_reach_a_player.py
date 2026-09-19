"""Every quest that is seeded can be given to somebody (v1.0.0-rc.45).

rc.26 found `first_steps`: a quest seeded into `quest_definitions` on every
boot since v0.23.1, listed in `/quests`, with exactly the right objectives, and
handed to nobody - because the only two statements that wrote a
`character_quests` row both wanted a `giver_npc` the static quests deliberately
do not have. It built `beginner_path` to fix that.

It fixed it for the beginner path. `road_to_a_sect` was seeded from the same
dictionary, by the same call, and went on reaching nobody for nineteen more
releases - the string appeared in exactly one place in the whole tree, its own
definition - and `first_steps` itself was left seeded beside the stage that
replaced it. So the fault was found, named, and half fixed, and nothing said so.

That is the shape `tests/python/unit/test_commands_reach_a_player.py` gates on
the command side: a thing that exists, is complete, and has no door. This is the
same gate for quests. Every key that reaches `quest_definitions` must be
reachable by one of the paths that can put it in a player's hands:

  - a commission, which an NPC offers in person (it carries a `giver_npc`);
  - a `beginner_path` stage, granted at creation or as the previous stage's
    `follow_on`;
  - a household errand, handed over one at a time by `family.errand`;
  - an ascension quest, handed over by a cleared world-crossing tribulation;
  - a profession examination, offered when a trade's rank is reached;
  - anything named as some other quest's `follow_on`.

`UNGRANTABLE_QUESTS` is empty and was empty the day it was written, because
retiring `first_steps` and chaining `road_to_a_sect` left no orphan. An entry
here is a new decision, never a backlog inherited from this one.
"""
from __future__ import annotations

import json
import unittest

from app.ops.core_services import HANDED_OVER_BY_A_ROSTER, QuestService
from app.rules.quests import (
    QUEST_DEFINITIONS,
    ascension_quest_seed_rows,
    beginner_path_seed_rows,
    household_errand_seed_rows,
    profession_exam_seed_rows,
    static_quest_seed_rows,
)
from tests.support import PROJECT_ROOT

WORLD_FILE = PROJECT_ROOT / "content" / "world.json"
BOT = PROJECT_ROOT / "app" / "bot" / "bot.py"

# A seeded quest that deliberately reaches nobody, with the reason it may. An
# entry naming a quest that is not seeded, or one that is in fact grantable,
# fails below - so this can only shrink honestly.
UNGRANTABLE_QUESTS: dict[str, str] = {}


def _world() -> dict:
    return json.loads(WORLD_FILE.read_text(encoding="utf-8"))


def _seeded_keys() -> dict[str, str]:
    """Every quest key the bot seeds, and which seeder put it there.

    Read through the seeders themselves rather than off the content file, so a
    roster the bot stops seeding stops being checked and a roster it starts
    seeding is checked the day it is added.
    """
    world = _world()
    shim = type("W", (), {"data": world})()
    seeded: dict[str, str] = {}
    for source, rows in (
        ("commission", list(world.get("commissions") or [])),
        ("static", static_quest_seed_rows(QUEST_DEFINITIONS)),
        ("beginner_path", beginner_path_seed_rows(shim)),
        ("household_errand", household_errand_seed_rows(shim)),
        ("ascension", ascension_quest_seed_rows(shim)),
        ("profession_exam", profession_exam_seed_rows(shim)),
    ):
        for row in rows:
            key = str(row.get("quest_key") or "").strip()
            if key:
                seeded[key] = source
    return seeded


def _grantable_keys() -> set[str]:
    """Every quest key some path can actually hand to a player."""
    world = _world()
    shim = type("W", (), {"data": world})()
    grantable: set[str] = set()
    # A commission is offered in person; the giver is what makes it offerable.
    for row in list(world.get("commissions") or []):
        if str(row.get("giver_npc") or "").strip():
            grantable.add(str(row.get("quest_key") or ""))
    # The rosters whose whole purpose is to be handed over.
    for rows in (beginner_path_seed_rows(shim), household_errand_seed_rows(shim),
                 ascension_quest_seed_rows(shim), profession_exam_seed_rows(shim)):
        for row in rows:
            grantable.add(str(row.get("quest_key") or ""))
    # And anything a chain points at, wherever the chain lives. `follow_on` is
    # read off `seed_json` at runtime, so a stage pointing at a static quest
    # gives that quest a door.
    for rows in (beginner_path_seed_rows(shim), household_errand_seed_rows(shim),
                 ascension_quest_seed_rows(shim), profession_exam_seed_rows(shim)):
        for row in rows:
            follow_on = str(dict(row.get("seed") or {}).get("follow_on") or "").strip()
            if follow_on:
                grantable.add(follow_on)
    grantable.discard("")
    return grantable


def _roster_rows() -> list[dict]:
    """Every row the four rosters seed, plus the static quests a chain points at.

    These are the quests something hands over at a moment it chooses. They are
    the population `QuestService.available` must not offer, which is the other
    half of this file's rule and the one v1.0.0-rc.46 added.
    """
    world = _world()
    shim = type("W", (), {"data": world})()
    rows = [*beginner_path_seed_rows(shim), *household_errand_seed_rows(shim),
            *ascension_quest_seed_rows(shim), *profession_exam_seed_rows(shim)]
    chained = {str(dict(r.get("seed") or {}).get("follow_on") or "").strip() for r in rows}
    rows.extend(r for r in static_quest_seed_rows(QUEST_DEFINITIONS)
                if str(r.get("quest_key") or "") in chained)
    return rows


class TheJournalOffersWhatNothingHandsOver(unittest.TestCase):
    """The same fault as this file's, seen from the other side (v1.0.0-rc.46).

    rc.45 gave five rosters the power to hand a quest over and left
    `QuestService.available` - the journal's "Available" block and the accept
    select built from the same list - offering every one of their quests from
    minute one. The Discord playtest's first run after the merge is what saw
    it: a character created seconds earlier was shown ten examinations, `The
    Expert's Toxicity` among them, at Novice, holding no trade.

    Accepting one is not cosmetic. `grantOrdinaryQuestTx` treats an
    already-held quest as "no" and returns silently, so a quest taken here is
    the thing that stops its roster ever offering it: the hall never says the
    examination is open, `family.errand` skips an errand it thinks is already
    out, and the beginner chain hands over a stage the player has been sitting
    on since creation.
    """

    def test_the_roster_set_is_exactly_what_the_seeders_write(self):
        families = {str(row.get("source_key") or "").split(":", 1)[0] for row in _roster_rows()}
        self.assertEqual(sorted(families), sorted(HANDED_OVER_BY_A_ROSTER), (
            "HANDED_OVER_BY_A_ROSTER names the rosters whose quests the journal must not offer, "
            "and it has drifted from the seeders in app/rules/quests.py. A new roster belongs in "
            "the frozenset; a retired one belongs out of it"))

    def test_no_roster_quest_is_offered_from_the_journal(self):
        offered = sorted(str(row["quest_key"]) for row in _roster_rows()
                         if not QuestService.handed_over(row))
        self.assertEqual(offered, [], (
            "these quests exist to be handed over and the journal would also offer them, so a "
            f"player can take one before its roster ever gets to: {offered}"))

    def test_the_examination_the_sweep_found_is_named_outright(self):
        # Named rather than left to the sweep above, because it is the one the
        # playtest actually printed and because a regression here is silent:
        # the journal simply starts listing twelve examinations again.
        exams = profession_exam_seed_rows(type("W", (), {"data": _world()})())
        self.assertEqual(len(exams), 12)
        for row in exams:
            with self.subTest(quest=row["quest_key"]):
                self.assertTrue(QuestService.handed_over(row))
        # And a forged draft, which no roster hands over, still is offered -
        # the journal exists for exactly those.
        self.assertFalse(QuestService.handed_over({"source_type": "forge", "source_key": "history:1"}))
        self.assertFalse(QuestService.handed_over({}))

    def test_every_seeded_quest_arrives_exactly_one_way(self):
        seeded = _seeded_keys()
        world = _world()
        givers = {str(r.get("quest_key") or ""): str(r.get("giver_npc") or "").strip()
                  for r in list(world.get("commissions") or [])}
        handed = {str(r["quest_key"]) for r in _roster_rows() if QuestService.handed_over(r)}
        for key in sorted(seeded):
            with self.subTest(quest=key):
                doors = [bool(givers.get(key)), key in handed]
                self.assertLessEqual(sum(doors), 1, (
                    f"{key} is both offered in person and handed over by a roster; a commission "
                    "occupies the one-at-a-time slot and grantOrdinaryQuestTx refuses a giver, so "
                    "one of the two doors does nothing"))


class EverySeededQuestHasADoor(unittest.TestCase):
    def test_the_seeders_are_still_where_this_test_thinks_they_are(self):
        # The load-bearing half: if the seeders silently returned nothing,
        # every quest would look grantable and the gate would be noise rather
        # than a gate.
        seeded = _seeded_keys()
        self.assertGreater(len(seeded), 20, "the seeders returned almost nothing")
        self.assertIn("beginner_household", seeded)
        self.assertIn("road_to_a_sect", seeded)
        bot = BOT.read_text(encoding="utf-8")
        for call in ("static_quest_seed_rows(QUEST_DEFINITIONS)", "beginner_path_seed_rows(WORLD)",
                     "household_errand_seed_rows(WORLD)", "ascension_quest_seed_rows(WORLD)",
                     "profession_exam_seed_rows(WORLD)"):
            with self.subTest(call=call):
                self.assertIn(call, bot, "a seeder nothing calls seeds nothing")

    def test_no_seeded_quest_reaches_nobody(self):
        seeded = _seeded_keys()
        grantable = _grantable_keys()
        orphans = sorted(key for key in seeded if key not in grantable and key not in UNGRANTABLE_QUESTS)
        self.assertEqual(orphans, [], (
            "these quests are seeded into quest_definitions and no path can hand them to a player - "
            "they carry no giver, they are on no roster that grants, and nothing chains to them. "
            "Give each one a door (a giver, a roster, or another quest's follow_on), or name it in "
            f"UNGRANTABLE_QUESTS with the reason it is allowed to reach nobody: {orphans}"))

    def test_the_sect_road_is_the_one_this_gate_exists_for(self):
        # Named outright rather than left to the sweep above, because this is
        # the quest the gate was written for and a regression is silent: the
        # definition goes on being seeded and nothing errors.
        world = _world()
        last = [s for s in list(world.get("beginner_path") or [])][-1]
        self.assertEqual(str(last.get("follow_on")), "road_to_a_sect",
                         "the last beginner stage is what hands over the sect road")
        self.assertIn("road_to_a_sect", QUEST_DEFINITIONS)

    def test_the_superseded_onboarding_quest_is_gone(self):
        # `first_steps` and `beginner_household` were two definitions for one
        # moment, and only one of them could be held.
        self.assertNotIn("first_steps", QUEST_DEFINITIONS)
        self.assertNotIn("first_steps", _seeded_keys())
        core = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
        self.assertIn("point_the_last_beginner_stage_at_the_sect_road", core,
                      "a live world's rows need the migration; the content change is insert-only")

    def test_the_allowlist_names_seeded_quests_and_says_why(self):
        seeded = _seeded_keys()
        grantable = _grantable_keys()
        for key, reason in UNGRANTABLE_QUESTS.items():
            with self.subTest(quest=key):
                self.assertIn(key, seeded, f"UNGRANTABLE_QUESTS names {key!r}, which nothing seeds")
                self.assertNotIn(key, grantable, f"UNGRANTABLE_QUESTS names {key!r}, which is grantable")
                self.assertGreaterEqual(len(reason.strip()), 20,
                                        f"UNGRANTABLE_QUESTS[{key!r}] needs a reason worth reading")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
