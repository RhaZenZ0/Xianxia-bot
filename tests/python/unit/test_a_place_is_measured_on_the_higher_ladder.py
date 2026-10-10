"""A place is measured on the ladder that carried the cultivator there.

`accessRealmIndex` is the higher of the qi and body ladders: a body cultivator
clears the Mortal Body Ascension and breaks into the Spiritual World's body
realm exactly as a qi cultivator clears theirs, so every check of a *place*
reads it. The engine's three qi-only doors (`sect.ascend`, `array.use`,
`caravan.dispatch`) are fixed and held in Go by `place_floor_test.go`; this is
the Python half - the panels and pickers that anticipate those refusals, and
the one role sync that grants a world's access role - each of which compared
`realm_index` alone and so showed a body cultivator a door as shut, or left
them outside the world's channels, when the road had let them in.

Driven with fakes, because the source reads correctly in both the right and the
wrong version: the comparison is one operand, and what it compares is decided
by the value it is handed. Each test fails only on its own site's revert.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _module(name: str):
    with patch.dict(os.environ, ENV):
        return importlib.import_module(name)


class _Db:
    """Every read answers "nothing there" except what the test hands it."""

    def __init__(self, character=None, **reads):
        self._character = character
        self._reads = reads

    async def get_character(self, _uid):
        return self._character

    async def list_world_crossings(self, _location):
        return []

    def __getattr__(self, name):
        if name in self._reads:
            value = self._reads[name]

            async def answer(*_a, **_k):
                return value
            return answer

        async def nothing(*_a, **_k):
            return None
        return nothing


def _interaction():
    return SimpleNamespace(user=SimpleNamespace(id=7), guild=None)


class TheFixtureIsARealBodyCultivator(unittest.TestCase):
    def test_the_shipped_content_has_a_world_a_body_ladder_alone_can_reach(self):
        hubs = _module("app.rules.realm_hubs")
        floor = int(hubs.REALM_HUBS["Spiritual World"]["min_realm_index"])
        self.assertGreaterEqual(floor, 5, "the Spiritual World no longer opens high; the fixture is wrong, not the rule")
        self.assertEqual(hubs.access_realm_index({"realm_index": 3, "body_realm_index": floor}), floor)
        self.assertEqual(hubs.access_realm_index({"realm_index": 3}), 3, "an absent body ladder reads as 0, never as a sentinel")
        self.assertEqual(hubs.access_realm_index({"realm_index": floor, "body_realm_index": 0}), floor)

    def test_there_is_one_definition_of_the_rule(self):
        hubs = _module("app.rules.realm_hubs")
        locations = _module("app.bot.locations")
        self.assertIs(locations.access_realm_index, hubs.access_realm_index)
        exploration = _module("app.bot.commands.exploration")
        self.assertIs(exploration.access_realm_index, hubs.access_realm_index)


class TheArrayPickerOffersWhatTheEngineWillCarry(unittest.TestCase):
    def test_a_body_cultivator_is_offered_the_crossing_their_body_stage_opens(self):
        abode = _module("app.bot.commands.abode")
        hubs = _module("app.rules.realm_hubs")
        array = abode.WORLD.teleport_arrays["imperial_spirit"]
        floor = int(array["min_realm_index"])
        self.assertEqual(floor, int(hubs.REALM_HUBS["Spiritual World"]["min_realm_index"]), "the crossing and the world no longer share a floor")
        here = {"location": array["from"], "realm_index": 3, "body_realm_index": floor}
        with patch.object(abode, "DB", _Db(here)):
            choices = asyncio.run(abode.array_destination_autocomplete(_interaction(), ""))
        self.assertIn("imperial_spirit", [choice.value for choice in choices],
                      "a body cultivator at the crossing was not offered the array the engine will carry them through")

    def _listing(self, body_realm):
        abode = _module("app.bot.commands.abode")
        array = abode.WORLD.teleport_arrays["imperial_spirit"]
        here = {"location": array["from"], "realm_index": 3, "body_realm_index": body_realm}
        interaction = SimpleNamespace(user=SimpleNamespace(id=7), response=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(abode, "DB", _Db(here)), patch.object(abode, "require_character", AsyncMock(return_value=here)):
            asyncio.run(abode.array_list.callback(interaction))
        line = next(part for part in interaction.response.send_message.call_args.args[0].splitlines() if array["name"] in part)
        return line

    def test_the_list_does_not_call_a_crossing_sealed_to_the_body_stage_that_opens_it(self):
        abode = _module("app.bot.commands.abode")
        floor = int(abode.WORLD.teleport_arrays["imperial_spirit"]["min_realm_index"])
        line = self._listing(floor)
        self.assertNotIn("sealed", line, "the list marks sealed an array the engine will carry a body cultivator through")

    def test_the_list_still_seals_it_below_the_floor_on_both_ladders(self):
        abode = _module("app.bot.commands.abode")
        floor = int(abode.WORLD.teleport_arrays["imperial_spirit"]["min_realm_index"])
        line = self._listing(floor - 1)
        self.assertIn("sealed", line)

    def test_the_array_is_still_withheld_below_the_floor_on_both_ladders(self):
        abode = _module("app.bot.commands.abode")
        array = abode.WORLD.teleport_arrays["imperial_spirit"]
        floor = int(array["min_realm_index"])
        here = {"location": array["from"], "realm_index": 3, "body_realm_index": floor - 1}
        with patch.object(abode, "DB", _Db(here)):
            choices = asyncio.run(abode.array_destination_autocomplete(_interaction(), ""))
        self.assertNotIn("imperial_spirit", [choice.value for choice in choices])


class TheRealmhubPickerOffersTheWorldsTheBodyStageReaches(unittest.TestCase):
    def test_a_body_cultivator_is_offered_the_spiritual_world(self):
        exploration = _module("app.bot.commands.exploration")
        hubs = _module("app.rules.realm_hubs")
        floor = int(hubs.REALM_HUBS["Spiritual World"]["min_realm_index"])
        here = {"location": "Greenriver Town", "realm_index": 3, "body_realm_index": floor}
        with patch.object(exploration, "DB", _Db(here)):
            choices = asyncio.run(exploration.realmhub_world_autocomplete(_interaction(), ""))
        self.assertIn("Spiritual World", [choice.value for choice in choices],
                      "travel's hub mode admits the body stage; the picker did not offer the world")
        self.assertNotIn("Immortal World", [choice.value for choice in choices])

    def test_the_status_card_lists_the_capitals_the_body_stage_can_perceive(self):
        exploration = _module("app.bot.commands.exploration")
        hubs = _module("app.rules.realm_hubs")
        floor = int(hubs.REALM_HUBS["Spiritual World"]["min_realm_index"])
        here = {"location": "Greenriver Town", "realm_index": 3, "body_realm_index": floor}
        reply = AsyncMock()
        with patch.object(exploration, "DB", _Db(here)), \
                patch.object(exploration, "require_character", AsyncMock(return_value=here)), \
                patch.object(exploration, "reply_long", reply):
            asyncio.run(exploration.realmhub_status.callback(_interaction()))
        text = reply.call_args.args[1]
        self.assertIn("Spiritual World", text, "the card hides a capital travel's hub mode will take a body cultivator to")
        self.assertNotIn("Immortal World", text)


class TheAccessRoleFollowsTheHigherLadder(unittest.TestCase):
    def test_a_body_cultivator_is_granted_the_role_of_the_world_they_can_enter(self):
        runtime = _module("app.bot.runtime")
        hubs = _module("app.rules.realm_hubs")
        floor = int(hubs.REALM_HUBS["Spiritual World"]["min_realm_index"])
        roles = [SimpleNamespace(id=100 + i, name=runtime._realm_access_role_name(world)) for i, world in enumerate(hubs.REALM_HUBS)]
        guild = SimpleNamespace(me=SimpleNamespace(guild_permissions=SimpleNamespace(manage_roles=True)), roles=roles)
        member = MagicMock(spec=discord.Member)
        member.id = 7
        member.roles = [roles[0]]
        member.add_roles = AsyncMock()
        member.remove_roles = AsyncMock()
        asyncio.run(runtime._sync_realm_access_roles(guild, member, {"realm_index": 3, "body_realm_index": floor}))
        granted = [role.name for call in member.add_roles.call_args_list for role in call.args]
        self.assertEqual(granted, [runtime._realm_access_role_name("Spiritual World")],
                         f"the Spiritual World's access role was not granted to a body cultivator at body {floor}: {granted}")
        member.remove_roles.assert_not_called()


class TheTribulationPadlockTakesEitherLadder(unittest.TestCase):
    def _hidden(self, *, realm, body_realm):
        surface = _module("app.bot.surface")
        character = {"location": "Greenriver Town", "realm_index": realm, "phase": 9, "body_realm_index": body_realm, "body_phase": 9}
        interaction = SimpleNamespace(user=SimpleNamespace(id=7))
        with patch.object(surface, "DB", _Db()):
            return surface, asyncio.run(surface._progression_hidden_actions(interaction, character))

    def test_a_body_cultivator_at_a_gate_realm_is_not_told_the_tribulation_is_shut(self):
        gate_realm = min(_module("app.rules.progression_systems").ASCENSION_GATES)
        _surface, hidden = self._hidden(realm=3, body_realm=gate_realm)
        self.assertEqual(hidden.get("/tribulation prepare", ""), "",
                         "`eligibleTribulation` takes the body ladder's gate stage; the panel padlocked the leaf")
        self.assertEqual(hidden.get("/tribulation attempt", ""), "")

    def test_somebody_at_no_gate_realm_on_either_ladder_still_is(self):
        _surface, hidden = self._hidden(realm=3, body_realm=4)
        self.assertIn("world-crossing gate", hidden.get("/tribulation prepare", ""))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
