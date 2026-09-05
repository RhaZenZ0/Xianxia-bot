from tests.support import (
    PROJECT_ROOT,
    bot_function_source,
    bot_module_defining,
    bot_package_source,
    bot_source_files,
)
import ast
import re
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
        hub_names = []
        for path in bot_source_files():
            if path.name == "hubs.py":
                continue  # defines HubDefinition; does not declare hubs
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call):
                    continue
                if not isinstance(node.func, ast.Name) or node.func.id != "HubDefinition":
                    continue
                for kw in node.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                        hub_names.append(str(kw.value.value))
        self.assertEqual(
            set(hub_names),
            {
                "character", "quest", "cultivation", "items", "npc", "world",
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
        self.assertEqual(len(ast.literal_eval(migrated_node.value)), 74)
        self.assertNotIn("tree.remove_command", source)
        self.assertIn('"alchemy": alchemy_group', source)
        self.assertIn('_hub_page("alchemy", "Alchemy"', source)

    def test_private_expedition_copy_uses_registered_world_hub(self):
        source = bot_package_source()
        wiring = bot_module_defining("_MIGRATED_ROOTS").read_text(encoding="utf-8")
        self.assertIn('"explore"', wiring[wiring.index("_MIGRATED_ROOTS"):wiring.index("_ROOT_ACTIONS")])
        self.assertIn('_hub_page("explore", "Explore"', source)
        self.assertIn("**/world → Explore**", source)
        self.assertIn("**/action**", source)
        self.assertNotIn("**/explore**", source)
        self.assertNotIn("**/act**", source)

    def test_admin_is_a_single_dropdown_panel_with_all_registered_actions(self):
        source = bot_package_source()
        groups = [
            "admin_server_group", "admin_world_group", "admin_player_group",
            "admin_sect_group", "admin_family_group", "admin_npc_group", "admin_sim_group",
        ]
        total = 0
        for group in groups:
            commands = re.findall(rf"@registered_group_command\({group},\s*name=\"([^\"]+)\"", source)
            self.assertLessEqual(len(commands), 25, group)
            total += len(commands)
        # 40 through v0.19.14; +2 in v0.19.15 for the administrator chat monitor
        # (server ai_status, server chat_digest).  This literal is deliberate:
        # it is what catches an admin action being added or lost by accident.
        self.assertEqual(total, 42)
        self.assertIn("_ADMIN_HUB_DEFINITION = HubDefinition(", source)
        self.assertIn('title="🛡️ Xianxia — Administrator Control Panel"', source)
        self.assertIn("command=admin_server_group", source)
        self.assertIn("command=admin_sim_group", source)
        self.assertIn("@app_commands.default_permissions(administrator=True)", source)
        self.assertNotIn("bot.tree.add_command(admin_group, guild=GUILD)", source)

    def test_stage7_channel_provisioning_is_gated_behind_dashboard_create_missing(self):
        """Stage 7 moved channel/category setup to the admin dashboard. The bot can
        now create the base and realm-hub channels/categories, but only when the
        caller explicitly opts in with create_missing=True (default False) *and*
        the bot actually holds Manage Channels — and only the web GM dashboard's
        Full Setup/Repair action passes that True. The /admin Discord slash command
        keeps the old validate-and-bind-only behavior.
        """
        source = bot_package_source()
        self.assertIn("guild.create_text_channel", source)
        self.assertIn("guild.create_category", source)
        self.assertIn("create_missing: bool = False", source)
        self.assertIn(
            "can_create = create_missing and bool(me) and me.guild_permissions.manage_channels",
            source,
        )
        self.assertIn("Discord channel creation is dashboard-owned", source)
        self.assertIn("the bot will not provision channels", source)

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

    def test_stage7_dashboard_owned_channels_do_not_rewrite_existing_permissions(self):
        """Setup/repair never touches permissions on a channel that already exists -
        set_permissions() is never called at all. The one PermissionOverwrite use is
        the read-only overwrite applied at creation time for READ_ONLY_BASE_CHANNELS
        (world-events, bot-logs, xianxia-info), and only fires for a channel the bot
        is itself creating (create_missing + Manage Channels), never for one that was
        merely bound to an existing channel.
        """
        source = bot_package_source()
        setup_block = bot_function_source("ensure_base_xianxia_channels")
        self.assertNotIn("set_permissions(", setup_block)
        self.assertEqual(setup_block.count("PermissionOverwrite("), 1)
        self.assertIn("if name in READ_ONLY_BASE_CHANNELS else {}", setup_block)
        self.assertIn("Discord channel creation is dashboard-owned", source)
        self.assertIn("the bot will not provision channels", source)

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
        self.assertIn("interaction.user.id != self.owner_id", source)
        self.assertIn("command_override=action.command", source)
        self.assertIn("if self.definition.name == \"admin\"", source)

    def test_refactored_packages_have_no_legacy_module_shims(self):
        self.assertTrue((ROOT / "app" / "bot" / "main.py").is_file())
        self.assertTrue((ROOT / "app" / "database" / "core.py").is_file())
        self.assertTrue((ROOT / "app" / "simulation" / "world.py").is_file())
        self.assertFalse((ROOT / "app" / "bot.py").exists())
        self.assertFalse((ROOT / "app" / "database.py").exists())
        self.assertFalse((ROOT / "app" / "worldsim.py").exists())

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
