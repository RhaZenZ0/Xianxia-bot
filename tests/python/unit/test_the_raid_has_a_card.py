"""The raid is drawn as one card (v1.8.3), and it can be pressed (v1.8.5).

Start, Status and Act answered in loose text lines - the boss's HP, a phase
name, and each raider as a bare line - and the engine names a struck raider by
user id, so a player read "Last Roar hits 1456074443989188610 for 9". The card
draws the boss's health as a bar, the phase and round, and every raider's
vitality and whether they have acted this round, off the row the engine wrote.

v1.8.5 gives it buttons - Attack, Defend, Support, Technique, and Claim once
the boss is down - each acting for whoever presses it, and draws what it still
left out: the party's leader or that it fights alone, its formation, what the
phase hits and guards with, and each raider's reward and whether it is taken.
"""

from __future__ import annotations

import asyncio
import ast
import importlib
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

BOSS = Path(__file__).resolve().parents[3] / "app" / "bot" / "commands" / "boss.py"
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
RAIDER, OTHER = 1456074443989188610, 1456074443989188611


def _encounter(**overrides):
    row = {
        "encounter_id": 7, "template_key": "iron_tusk_boar_king", "boss_name": "Iron-Tusk Boar King",
        "location": "Greenriver Town", "boss_hp": 90, "boss_hp_max": 180, "phase_index": 1,
        "round_index": 3, "status": "active", "version": 4,
        "participants": [
            {"user_id": RAIDER, "vitality": 30, "vitality_max": 40, "acted_round": 3, "guard": 0,
             "total_damage": 55, "status": "active"},
            {"user_id": OTHER, "vitality": 0, "vitality_max": 40, "acted_round": 2, "guard": 0,
             "total_damage": 12, "status": "knocked_out"},
        ],
    }
    row.update(overrides)
    return row


def _boss():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.commands.boss")


def raid_card(*args, **kwargs):
    return _boss().raid_card(*args, **kwargs)


def _fields(card):
    return dict(card.fields)


class TheRaidHasACard(unittest.TestCase):
    def test_the_card_draws_the_boss_the_phase_and_every_raider(self):
        embed = raid_card(_encounter())
        fields = _fields(embed)
        self.assertIn("Raid #7", embed.title)
        self.assertIn("**90/180**", fields["Boss"])
        self.assertIn("Blood Frenzy", fields["Phase"])
        self.assertIn("2/3", fields["Phase"])
        self.assertIn("33%", fields["Phase"], "the phase does not say when it shifts, off the template's threshold")
        self.assertEqual(fields["Round"], "**3**")
        party = fields["Raid Party (2)"]
        self.assertIn(f"<@{RAIDER}> • ✅ acted", party)
        self.assertIn(f"<@{OTHER}> • 💀 down", party)
        self.assertIn("**30/40**", party)

    def test_a_raider_who_has_not_acted_is_waited_on(self):
        row = _encounter()
        row["participants"][0]["acted_round"] = 2
        self.assertIn("⏳ to act", _fields(raid_card(row))["Raid Party (2)"])

    def test_the_engines_user_ids_become_mentions(self):
        embed = raid_card(_encounter(), events=[f"Blood Frenzy hits {RAIDER} for 9 raid vitality."])
        self.assertIn(f"hits <@{RAIDER}> for 9", embed.description)
        self.assertNotIn(f" {RAIDER} ", embed.description)

    def test_a_won_raid_says_how_to_claim(self):
        embed = raid_card(_encounter(status="victory", boss_hp=0))
        self.assertIn("🏆", embed.title)
        self.assertIn("/boss claim encounter_id:7", embed.footer)
        self.assertNotIn("shifts", _fields(embed)["Phase"], "a finished raid does not promise a phase shift")

    def test_a_large_party_stays_inside_one_field(self):
        row = _encounter()
        row["participants"] = [
            {"user_id": RAIDER + i, "vitality": 10, "vitality_max": 40, "acted_round": 0, "guard": 0,
             "total_damage": 0, "status": "active"} for i in range(30)
        ]
        party = _fields(raid_card(row))["Raid Party (30)"]
        self.assertLessEqual(len(party), 1024)
        self.assertIn("more raiders", party)

    def test_start_status_and_act_all_answer_with_the_card(self):
        tree = ast.parse(BOSS.read_text(encoding="utf-8"))
        callers = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "_send_raid_card"
        }
        self.assertTrue({"boss_start", "boss_status", "boss_act"} <= callers, callers)

    def test_the_card_names_the_leader_the_formation_and_the_phases_numbers(self):
        row = _encounter(party={"party_id": 3, "leader_user_id": RAIDER, "raid_only": 0},
                         formation={"name": "Iron Wall", "stance": "defensive", "cohesion": 76})
        fields = _fields(raid_card(row))
        self.assertIn(f"Led by <@{RAIDER}>", fields["Party"])
        self.assertIn("Formation **Iron Wall** • Defensive stance • Cohesion **76/100**", fields["Party"])
        self.assertIn("Attack **11** • Defence **2**", fields["Phase"], "the Blood Frenzy's numbers are the template's")

    def test_a_party_with_no_formation_is_told_where_to_set_one(self):
        fields = _fields(raid_card(_encounter(party={"leader_user_id": RAIDER, "raid_only": 0})))
        self.assertIn("No formation active", fields["Party"])

    def test_a_raid_fought_alone_says_so_on_every_card(self):
        row = _encounter(party={"leader_user_id": RAIDER, "raid_only": 1})
        row["participants"] = row["participants"][:1]
        party = _fields(raid_card(row))["Party"]
        self.assertIn("alone", party)
        self.assertNotIn("Led by", party)
        self.assertNotIn("No formation", party, "a raider alone has nobody to stand in formation with")

    def test_the_reward_is_the_templates_and_a_won_raid_counts_the_claims(self):
        self.assertIn("**120** Low Spirit Stones + **Low Beast Core ×2**", _fields(raid_card(_encounter()))["Reward, each raider"])
        won = _encounter(status="victory", boss_hp=0,
                         claims=[{"user_id": RAIDER, "claimed": 1}, {"user_id": OTHER, "claimed": 0}])
        fields = _fields(raid_card(won))
        self.assertIn(f"<@{RAIDER}> • 🎁 claimed", fields["Raid Party (2)"])
        self.assertIn(f"<@{OTHER}> • 🎁 to claim", fields["Raid Party (2)"])
        self.assertIn("1/2 claimed", fields["Reward, each raider"])


def _buttons(status):
    async def build():
        view = _boss().RaidView(7, raid_card(_encounter(status=status)))
        return [(item.label, item.raid_style) for item in view.buttons()]
    return asyncio.run(build())


class _Response:
    def __init__(self):
        self.done = False
        self.sent = []

    def is_done(self):
        return self.done

    async def defer(self, **kwargs):
        self.done = True

    async def send_message(self, content=None, **kwargs):
        self.done = True
        self.sent.append((content, kwargs))


class _Followup:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


class _Interaction:
    def __init__(self, user_id):
        self.id = 99
        self.user = type("U", (), {"id": user_id})()
        self.response = _Response()
        self.followup = _Followup()
        self.message = None
        self.edits = []

    async def edit_original_response(self, **kwargs):
        self.edits.append(kwargs)
        return None


class _DB:
    def __init__(self, encounter, after=None):
        self.encounter, self.after = encounter, after

    async def get_boss_encounter(self, **kwargs):
        return self.after if (self.after and kwargs.get("encounter_id") and self.encounter.get("_acted")) else self.encounter


class _Engine:
    def __init__(self, db):
        self.db, self.calls = db, []

    async def authoritative_action(self, operation, user_id, payload, *, action_id):
        self.calls.append((operation, user_id, payload))
        self.db.encounter["_acted"] = True
        return {"result": {"encounter_id": 7, "status": "active", "events": ["Attack deals 18 damage."]}}


def _press(user_id, style, encounter):
    boss = _boss()
    db = _DB(encounter, after={**encounter, "boss_hp": 72})
    engine = _Engine(db)
    interaction = _Interaction(user_id)

    async def clock():
        return None

    async def run():
        with patch.object(boss, "DB", db), patch.object(boss, "ENGINE", engine), \
             patch.object(boss, "budget_refusal_line", lambda uid, door: None), \
             patch.object(boss, "_user_action_lock", lambda uid: asyncio.Lock()), \
             patch.object(boss, "current_world_time", clock):
            await boss.RaidView(7, boss.raid_card(encounter)).dispatch(interaction, style)
    asyncio.run(run())
    return interaction, engine


class TheCardCanBePressed(unittest.TestCase):
    def test_an_active_raid_offers_every_action_and_defend_sends_guard(self):
        buttons = dict(_buttons("active"))
        self.assertEqual(list(buttons), ["Attack", "Defend", "Support", "Technique", "Refresh"])
        # v1.7.6: the engine's switch knows "guard"; "defend" was refused.
        self.assertEqual(buttons["Defend"], "guard")

    def test_a_won_raid_offers_the_claim_and_a_lost_one_nothing(self):
        self.assertEqual([label for label, _ in _buttons("victory")], ["Claim reward", "Refresh"])
        self.assertEqual(_buttons("defeat"), [])

    def test_the_card_is_drawn_in_the_newer_layout_the_hub_panels_use(self):
        # A Components V2 message: one coloured box, the buttons inside it,
        # and no embed or content anywhere (Discord refuses both on it).
        import discord

        async def build():
            return _boss().RaidView(7, raid_card(_encounter()))
        view = asyncio.run(build())
        self.assertIsInstance(view, discord.ui.LayoutView)
        (box,) = view.children
        self.assertIsInstance(box, discord.ui.Container)
        self.assertEqual(int(getattr(box.accent_colour, "value", box.accent_colour)), 0x8E44AD)
        text = "\n".join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))
        for heading in ("Raid #7", "### Boss", "**Phase**", "**Round**", "### Raid Party (2)", "### Reward, each raider"):
            self.assertIn(heading, text)
        self.assertLessEqual(len(text), 4000, "Discord caps a layout message's text at 4000 characters")
        rows = [item for item in view.walk_children() if isinstance(item, discord.ui.ActionRow)]
        self.assertEqual(len(rows), 1)
        self.assertIn(rows[0], list(box.children), "the buttons sit inside the box")

    def test_quiet_buttons_leave_the_card_and_say_how_to_redraw_it(self):
        import discord

        async def build():
            view = _boss().RaidView(7, raid_card(_encounter()), quiet=True)
            return view
        view = asyncio.run(build())
        self.assertEqual(view.buttons(), [])
        text = "\n".join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))
        self.assertIn("/boss status", text)

    def test_a_press_acts_for_the_raider_who_pressed_it_and_redraws_the_card(self):
        interaction, engine = _press(OTHER, "attack", _encounter())
        self.assertEqual([(op, uid, payload["style"]) for op, uid, payload in engine.calls], [("boss.act", OTHER, "attack")])
        self.assertEqual(len(interaction.edits), 1, "the card was not redrawn in place")
        embed = interaction.edits[0]["view"].card
        self.assertIn(f"<@{OTHER}> — ⚔️ Attack", embed.description)
        self.assertIn("**72/180**", _fields(embed)["Boss"], "the card was not drawn from the row the action wrote")

    def test_somebody_outside_the_raid_is_refused_before_the_engine(self):
        interaction, engine = _press(12345, "attack", _encounter())
        self.assertEqual(engine.calls, [])
        self.assertEqual(interaction.edits, [])
        self.assertIn("Only the raid party", interaction.followup.sent[0][0])


if __name__ == "__main__":
    unittest.main()
