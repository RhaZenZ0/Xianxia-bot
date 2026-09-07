"""Realm capitals are visible only while you stand in them (v0.21.6).

v0.21.2 fixed "realm capitals are not hidden" by gating each hub behind the
realm-access role earned by cultivation. v0.21.6 changes what the gate is:
a *presence* role ("Xianxia • <capital>") the bot puts on when a character's
location is that capital and takes off when it is not. These tests hold:

1. `realm_hub_visibility` calls a hub hidden only when @everyone is denied
   View Channel AND the gating role is allowed it.
2. `presence_world_for` maps a location to exactly one capital or none.
3. Setup/Repair applies the gate on every run with the presence role and
   the full member permission set, and strips the old access-role allow.
4. The presence sync runs from require_character, on_message and hub
   travel, and never calls Discord when nothing changed.
5. The dashboard reports it per hub and a visible hub is not "ready".
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests.support import PROJECT_ROOT, bot_class_source, bot_function_source

from app.rules.realm_hubs import (  # pure: lives with the hub table, no discord
    REALM_HUBS, REALM_HUB_MEMBER_PERMISSIONS, presence_world_for, realm_hub_visibility, realm_presence_role_name,
)


def _ow(view):
    return SimpleNamespace(view_channel=view)


class VisibilityRuleTests(unittest.TestCase):
    EVERYONE = object()
    ROLE = object()

    def _channel(self, overwrites):
        return SimpleNamespace(overwrites=overwrites)

    def test_no_overwrites_is_visible_to_all(self):
        state = realm_hub_visibility(self._channel({}), self.ROLE, self.EVERYONE)
        self.assertFalse(state["hidden"])
        self.assertIsNone(state["everyone_view"])

    def test_deny_everyone_without_role_allow_is_not_hidden(self):
        # It would be a lockout, not a gate.
        state = realm_hub_visibility(self._channel({self.EVERYONE: _ow(False)}), self.ROLE, self.EVERYONE)
        self.assertFalse(state["hidden"])

    def test_role_allow_without_everyone_deny_is_not_hidden(self):
        state = realm_hub_visibility(self._channel({self.ROLE: _ow(True)}), self.ROLE, self.EVERYONE)
        self.assertFalse(state["hidden"])

    def test_deny_everyone_and_allow_role_is_hidden(self):
        state = realm_hub_visibility(
            self._channel({self.EVERYONE: _ow(False), self.ROLE: _ow(True)}), self.ROLE, self.EVERYONE
        )
        self.assertTrue(state["hidden"])

    def test_missing_role_is_never_hidden(self):
        state = realm_hub_visibility(self._channel({self.EVERYONE: _ow(False)}), None, self.EVERYONE)
        self.assertFalse(state["hidden"])
        self.assertFalse(state["role_present"])


class PresenceRuleTests(unittest.TestCase):
    def test_every_capital_maps_to_its_world_and_nothing_else_does(self):
        for world, hub in REALM_HUBS.items():
            self.assertEqual(presence_world_for(hub["location"]), world)
        self.assertIsNone(presence_world_for("Greenriver Town"))
        self.assertIsNone(presence_world_for("Azure Cloud Mountain Gate"), "a shared prefix is not the capital")
        self.assertIsNone(presence_world_for("Azure Crown Imperial City Outskirts"), "a longer name is not the capital")
        self.assertIsNone(presence_world_for("abode:7"))
        self.assertIsNone(presence_world_for(""))
        self.assertIsNone(presence_world_for(None))

    def test_presence_role_names_are_distinct_from_access_roles(self):
        names = {realm_presence_role_name(w) for w in REALM_HUBS}
        self.assertEqual(len(names), len(REALM_HUBS))
        for world in REALM_HUBS:
            self.assertNotEqual(realm_presence_role_name(world), f"Xianxia • {world}")
            self.assertTrue(realm_presence_role_name(world).startswith("Xianxia • "))

    def test_member_permission_set_is_what_a_city_needs(self):
        for perm in ("view_channel", "send_messages", "read_message_history", "use_application_commands"):
            self.assertTrue(REALM_HUB_MEMBER_PERMISSIONS[perm])
        self.assertNotIn("manage_channels", REALM_HUB_MEMBER_PERMISSIONS)
        self.assertNotIn("manage_messages", REALM_HUB_MEMBER_PERMISSIONS)


class GateIsAppliedTests(unittest.TestCase):
    def test_setup_path_gates_every_hub_it_resolves_with_the_presence_role(self):
        source = bot_function_source("ensure_realm_hub_channels")
        self.assertIn("roles = await _ensure_realm_presence_roles(guild) if can_create else {}", source)
        self.assertIn("access_roles = await _ensure_realm_access_roles(guild) if can_create else {}", source)
        # Applied for existing channels too, not only inside the create branch:
        # the call sits after `if channel is None: continue`, before the binding is saved.
        gate = source.index("await ensure_realm_hub_overwrites(guild, channel, roles.get(world), stale_roles=stale)")
        self.assertLess(source.index("if channel is None:\n            continue"), gate)
        self.assertLess(gate, source.index("await DB.set_realm_hub_channel("))

    def test_gate_denies_everyone_allows_the_presence_role_fully_and_strips_the_old_allow(self):
        source = bot_function_source("ensure_realm_hub_overwrites")
        self.assertIn("set_permissions(guild.default_role, view_channel=False", source)
        self.assertIn("set_permissions(role, reason=", source)
        self.assertIn("**REALM_HUB_MEMBER_PERMISSIONS", source)
        self.assertIn("set_permissions(guild.me, view_channel=True", source)
        self.assertIn("set_permissions(old, overwrite=None", source, "the v0.21.2 access-role allow is removed")
        # Never a deny without the allow available.
        self.assertIn('if role is None:\n        return "no-role"', source)

    def test_presence_sync_runs_everywhere_a_location_can_change_or_be_seen(self):
        self.assertIn("await _sync_realm_presence_roles(interaction.guild, interaction.user, character)", bot_function_source("require_character"))
        self.assertIn("_sync_realm_presence_roles(message.guild, message.author, character)", bot_class_source("XianxiaBot"))
        self.assertIn('_sync_realm_presence_roles(interaction.guild, interaction.user, {"location": str(hub["location"])})', bot_function_source("realmhub_go"))
        self.assertIn("await _sync_realm_presence_roles(guild, member, character)", bot_function_source("_sync_all_realm_access_roles"))

    def test_presence_sync_is_exactly_one_role_and_no_call_when_unchanged(self):
        source = bot_function_source("_sync_realm_presence_roles")
        self.assertIn("here = presence_world_for(character.get(\"location\"))", source)
        self.assertIn("if world == here and role.id not in current_ids", source)
        self.assertIn("if world != here and role.id in current_ids", source)
        self.assertIn("if add_roles:", source)
        self.assertIn("if remove_roles:", source)

    def test_presence_roles_carry_no_guild_permissions(self):
        source = bot_function_source("_ensure_realm_presence_roles")
        self.assertIn("permissions=discord.Permissions.none()", source)
        self.assertIn("mentionable=False", source)

    def test_gate_is_idempotent(self):
        source = bot_function_source("ensure_realm_hub_overwrites")
        self.assertIn('return "hidden"', source, "an already-gated hub makes no API call")
        self.assertLess(source.index('return "hidden"'), source.index("set_permissions("))

    def test_slash_validate_only_path_does_not_write_permissions(self):
        source = bot_function_source("ensure_realm_hub_channels")
        self.assertIn("if can_create:\n            stale = [r for w, r in access_roles.items() if w == world]\n            await ensure_realm_hub_overwrites", source)

    def test_dashboard_reports_visibility_and_a_visible_hub_is_not_ready(self):
        source = bot_function_source("_dashboard_discord_snapshot")
        self.assertIn('"hidden": bool(visibility.get("hidden"))', source)
        self.assertIn('and bool(visibility.get("hidden"))', source)
        js = (PROJECT_ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
        self.assertIn("VISIBLE TO ALL", js)
        self.assertIn("['Visibility'", js)
        self.assertIn("in-city only", js)
        self.assertIn("['Presence role'", js)
        snapshot = bot_function_source("_dashboard_discord_snapshot")
        self.assertIn("realm_presence_role_name(world)", snapshot, "the dashboard judges visibility by the presence role")


if __name__ == "__main__":
    unittest.main()
