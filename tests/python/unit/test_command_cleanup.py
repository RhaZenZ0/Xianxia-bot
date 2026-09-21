from tests.support import (
    declared_hub_names,
    PROJECT_ROOT,
    bot_function_source,
    bot_module_defining,
    bot_package_source,
    bot_source_files,
)
import ast
import unittest


ROOT = PROJECT_ROOT
HUBS = ROOT / "app" / "bot" / "hubs.py"
# Phase 1 of the main.py split (v0.19.33): every check here used to read
# app/bot/main.py by path. Those that assert an invariant now read the whole
# package (bot_package_source / bot_source_files), and those that slice out one
# function use bot_function_source, so a handler moving to another module can
# neither break these tests nor slip out from under them.


class CommandCleanupTests(unittest.TestCase):
    def test_player_surface_keeps_compact_roots_plus_admin(self):
        hub_names = declared_hub_names()
        self.assertEqual(
            set(hub_names),
            {
                "character", "ascend", "cultivation", "items", "npc", "world",
                "travel", "combat", "economy", "craft", "beast", "sect",
                "family", "abode", "innerworld", "realm", "admin",
            },
        )
        self.assertEqual(len(hub_names), 17)
        # /begin, /action and /check remain direct convenience roots.
        source = bot_package_source()
        for name in ("begin", "action", "check"):
            self.assertRegex(source, rf'@registered_root_command\([^\n]*name="{name}"|@registered_root_command\(name="{name}"')

    def test_internal_action_groups_are_not_registered_as_slash_roots(self):
        source = bot_package_source()
        self.assertNotIn("tree.get_command(", source)
        self.assertNotIn("tree.remove_command(", source)
        self.assertIn("def register_command_surface", source)
        self.assertIn("client.tree.add_command(ACTIONS.root(name), guild=GUILD)", source)
        self.assertNotIn("bot.tree.add_command(admin_group, guild=GUILD)", source)
        self.assertIn('name="admin",', source)
        self.assertIn("async def admin_panel", source)
        self.assertIn("_MIGRATED_ROOTS", source)
        migrated_source = bot_module_defining("_MIGRATED_ROOTS").read_text(encoding="utf-8")
        migrated_node = next(node for node in ast.parse(migrated_source).body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "_MIGRATED_ROOTS" for target in node.targets))
        # 82 since v1.0.0-rc.43: `/learn` joined, which is the one direction
        # this count has never moved in. Every other change here has been a
        # removal - `/gender` at rc.15 (sex is chosen at creation, so the
        # standalone setter was a second door onto a furnished room) and
        # `bodyperfect` folding into `perfect` at rc.13 - and a set that only
        # ever shrinks is a set nobody checks for absences. `/learn` was
        # registered, implemented and priced, and was in neither this set nor
        # the tree tuple, so no player could reach it; see
        # `tests/python/unit/test_commands_reach_a_player.py`, which is the gate
        # that makes an absence loud.
        self.assertEqual(len(ast.literal_eval(migrated_node.value)), 83)
        self.assertNotIn("tree.remove_command", source)
        self.assertIn('"alchemy": alchemy_group', source)
        self.assertIn('_hub_page("alchemy", "Alchemy"', source)

    def test_private_expedition_copy_uses_registered_world_hub(self):
        source = bot_package_source()
        wiring = bot_module_defining("_MIGRATED_ROOTS").read_text(encoding="utf-8")
        self.assertIn('"explore"', wiring[wiring.index("_MIGRATED_ROOTS"):wiring.index("_ROOT_ACTIONS")])
        # rc.13 renames the page: Explore and Hunt are both things you do with
        # the place you are standing in, so they share one page keyed "explore".
        self.assertIn('_hub_page("explore", "Act"', source)
        self.assertIn("**/world → Act → Explore**", source)
        self.assertIn("**/action**", source)
        self.assertNotIn("**/explore**", source)
        self.assertNotIn("**/act**", source)

    def test_deleted_configured_channels_are_treated_as_stale(self):
        source = bot_package_source()
        self.assertIn('except discord.NotFound:', source)
        self.assertIn('treating binding as stale', source)


    def test_hub_public_replies_use_interaction_webhook_and_chunk_long_text(self):
        source = HUBS.read_text(encoding="utf-8")
        self.assertIn("def _hub_content_chunks", source)
        self.assertIn("source.edit_original_response", source)
        self.assertIn("interaction.edit_original_response", source)
        self.assertIn("hub_error_reporter", source)
        self.assertIn("await self.owner.source.followup.send(chunk, ephemeral=False)", source)

    def test_world_output_uses_shared_long_reply_helper(self):
        world_block = bot_function_source("world")
        self.assertIn('await reply_long(interaction, "".join(lines), ephemeral=False)', world_block)

    def test_only_begin_flow_can_send_ephemeral_responses(self):
        allowed_private_classes = {
            "CharacterModal",
            "BirthFamilyPreviousButton",
            "BirthFamilyChooseButton",
            "BirthFamilyNextButton",
            "CultivationStyleSelect",
            "BirthSexSelect",
            "BirthFamilyBackButton",
            "BirthFamilyConfirmButton",
            "BirthFamilyView",
            # v0.19.29: #xianxia-info is one persistent, shared message with a
            # single timeout=None View - every player who opens its guide
            # dropdown sees the identical option list, so the "Server
            # Administration" topic can't be hidden from the menu itself.
            # Instead every reply from this dropdown (not just the admin one)
            # is sent privately to whoever clicked, both to stop the guide
            # from spamming the channel on every click and so a non-admin who
            # clicks the admin topic sees only a private "not for you" notice.
            "XianxiaInfoSelect",
            # v0.19.31: hub action feedback that is inherently single-user gets
            # routed ephemeral instead of overwriting/spamming the shared hub
            # card - none of this is "game output", it is UI plumbing around a
            # panel only one player is interacting with at that moment.
            # _fallback_followup forwards the *caller's own* ephemeral choice
            # (it does not force True; it is flagged only because that choice
            # is a variable, not a literal, so the static check cannot see
            # through it) when the primary edit/response failed and a fresh
            # message has to be sent instead.
            "_fallback_followup",
            # reply_long (runtime.py) is the same shape: v0.19.31 made it forward
            # the caller's own `ephemeral` argument instead of hardcoding False.
            # It never chooses privacy itself. Seen here only since phase 1 of
            # the split widened this scan from main.py+hubs.py to the package.
            "reply_long",
            # _HubFollowupProxy.send / _HubResponseProxy.send_message only take
            # the ephemeral branch when the registered command handler (or a
            # prior deferral) explicitly asked for a private reply - e.g. an
            # error only the acting player needs to see - never as a default.
            "_HubFollowupProxy",
            "_HubResponseProxy",
            # HubActionModal.on_submit reports bad modal input (and "continue
            # to finish the action" prompts) back to the one player who is
            # filling out that modal - nobody else can see the modal to begin
            # with, so a public reply would leak nothing useful and would just
            # spam the channel. Same shape as CharacterModal above.
            "HubActionModal",
            # _present_input_step shows the next choice/bool/member/channel
            # picker for a guided multi-step hub action - directed at the one
            # player continuing their own in-progress action, the same as the
            # BirthFamily* picker chain above.
            "_present_input_step",
            # LayoutHubView/CommandHubView.interaction_check reject a click
            # from someone who does not own the panel, or who lost the
            # Administrator permission a panel was opened with. These used to
            # reply ephemeral=False, which broadcast "this panel belongs to
            # another player" / "requires Administrator" to the whole channel
            # on every mis-click - a real privacy leak this release fixes.
            "LayoutHubView",
            "CommandHubView",
            # v0.40.0: _step_reply is the one door every input step goes
            # through (a picker, a confirm) - a step the acting player is in
            # the middle of, as _present_input_step above. The Menu button's
            # "open the menu with /menu" fallback and the expired panel's
            # owner check are the same plumbing as the interaction_checks.
            "_step_reply",
            "HubLayoutMenuButton",
            "ExpiredPanelView",
        }
        violations = []

        class VisibilityVisitor(ast.NodeVisitor):
            def __init__(self):
                self.scopes = []

            def visit_ClassDef(self, node):
                self.scopes.append(node.name)
                self.generic_visit(node)
                self.scopes.pop()

            def visit_FunctionDef(self, node):
                self.scopes.append(node.name)
                self.generic_visit(node)
                self.scopes.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node):
                for keyword in node.keywords:
                    if keyword.arg != "ephemeral":
                        continue
                    is_public = isinstance(keyword.value, ast.Constant) and keyword.value.value is False
                    is_begin_flow = "begin" in self.scopes or any(
                        scope in allowed_private_classes for scope in self.scopes
                    )
                    forwards_begin_choice = "_report_game_ui_error" in self.scopes
                    if not (is_public or is_begin_flow or forwards_begin_choice):
                        violations.append((node.lineno, ast.unparse(keyword.value)))
                self.generic_visit(node)

        # Every bot module, not just main.py and hubs.py: a handler that moves
        # out of main.py must stay under this invariant. Violations carry the
        # file so a new one is easy to place.
        for path in bot_source_files():
            before = len(violations)
            VisibilityVisitor().visit(ast.parse(path.read_text(encoding="utf-8")))
            violations[before:] = [(path.name, *v) for v in violations[before:]]

        self.assertEqual(violations, [])

        self.assertIn("ephemeral=True", bot_function_source("begin"))

    def test_stage7_dashboard_owned_channels_only_change_what_the_dashboard_asked_for(self):
        """Repair re-applies the read-only lock, and only ever behind
        `can_create` (rewritten in v1.0.0-rc.59).

        This test used to assert `set_permissions(` was **never** called, and
        that is what it was written for in stage 7: Setup must not stamp on a
        GM's own permissions. But the effect was that `READ_ONLY_BASE_CHANNELS`
        was consumed at exactly one place - the `overwrites=` argument of
        `create_text_channel` - so `#xianxia-info`, `#expeditions` and
        `#player-homes` were read-only only where the bot had created them, and
        writable on every server where a GM had bound an existing channel.

        The rule it was defending is still here and is what is checked now: the
        lock is applied only for a channel this codebase declares read-only,
        only behind `can_create`, and nothing else about a channel's
        permissions is touched. `test_the_layout_reaches_an_existing_server.py`
        holds the other half - that the lock is applied outside the
        `channel is None` branch at all.
        """
        setup_block = bot_function_source("ensure_base_xianxia_channels")
        self.assertIn("if name in READ_ONLY_BASE_CHANNELS else {}", setup_block)
        for line in setup_block.splitlines():
            if "set_permissions(" not in line:
                continue
            self.assertIn("guild.default_role", setup_block,
                          "the lock must apply to @everyone and nobody else")
        self.assertEqual(setup_block.count("set_permissions("), 1,
                         "one lock, in one place")
        # Still never a blanket permission rewrite: no overwrite object is
        # built for a channel that already exists.
        self.assertEqual(setup_block.count("PermissionOverwrite("), 1)

    def test_reusable_hub_framework_enforces_owner_and_uses_registered_handlers(self):
        source = HUBS.read_text(encoding="utf-8")
        for required in (
            "class CommandHubView", "interaction_check", "on_timeout",
            "class HubQuickActionButton", "class HubPageButton",
            "class HubActionModal", "class HubPageSelect", "class HubActionSelect",
            "class HubMemberSelect", "class HubContinueInputView", "HubInteractionProxy", "action.handler",
            "response.edit_message", "message.edit",
        ):
            self.assertIn(required, source)
        self.assertIn("command_override=action.command", source)
        self.assertIn("if self.definition.name == \"admin\"", source)

    def test_hubs_resolve_explicit_handlers_without_callback_introspection(self):
        hub_source = HUBS.read_text(encoding="utf-8")
        registry_source = (ROOT / "app" / "bot" / "registry.py").read_text(encoding="utf-8")
        self.assertIn("handler=ACTIONS.handler_for(item)", hub_source)
        self.assertIn("await action.handler(proxy", hub_source)
        self.assertNotIn("action.command.callback", hub_source)
        self.assertIn("class ActionRegistry", registry_source)



if __name__ == "__main__":
    unittest.main()


def test_admin_invocations_are_mirrored_to_private_discord_log():
    source = bot_package_source()
    assert "async def log_admin_command_invocation" in source
    assert 'post_server_log(interaction.guild, "Admin command", detail)' in source
    assert "await log_admin_command_invocation(interaction)" in source
    assert "_admin_command_option_summary" in source
