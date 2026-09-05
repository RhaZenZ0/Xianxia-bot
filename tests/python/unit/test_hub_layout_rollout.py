"""The Components V2 layout is now every hub's panel, including /admin.

These are source-level checks because app/bot/hubs.py cannot be imported without
discord.py, which is not installable in the build sandbox. They guard the three
things that would silently break the rollout: a hub quietly dropping back to the
classic panel, the admin permission re-check going missing, and the component
budget being exceeded by raising the per-page action limit.
"""
from tests.support import PROJECT_ROOT, bot_package_source, bot_source_files
import ast
import textwrap
import re
import unittest

HUBS = PROJECT_ROOT / "app" / "bot" / "hubs.py"

HUBS_SOURCE = HUBS.read_text(encoding="utf-8")
# Phase 1 of the main.py split (v0.19.33): the hub declarations live in
# main.py today and move to surface.py in the plan's final phase; read the
# package so neither the move nor a hub declared elsewhere escapes this file.
MAIN_SOURCE = bot_package_source()


def _layout_hub_names() -> set[str]:
    """LAYOUT_HUB_NAMES as literally declared, read out of the AST."""
    for node in ast.walk(ast.parse(HUBS_SOURCE)):
        targets = []
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = node.targets
        if any(getattr(t, "id", None) == "LAYOUT_HUB_NAMES" for t in targets):
            return {e.value for e in node.value.elts}
    raise AssertionError("LAYOUT_HUB_NAMES not found in hubs.py")


def _declared_hub_names() -> set[str]:
    """Every hub name declared anywhere in app/bot: 16 player hubs plus /admin."""
    names = set()
    for path in bot_source_files():
      if path.name == "hubs.py":
        continue  # defines HubDefinition itself
      for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "HubDefinition"
        ):
            for keyword in node.keywords:
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                    names.add(keyword.value.value)
    return names


class HubLayoutRolloutTests(unittest.TestCase):
    def test_every_hub_uses_the_layout(self):
        declared, enabled = _declared_hub_names(), _layout_hub_names()
        self.assertEqual(len(declared), 17, "expected 16 player hubs plus /admin")
        missing = sorted(declared - enabled)
        self.assertEqual(missing, [], f"these hubs still fall back to the classic panel: {missing}")
        unknown = sorted(enabled - declared)
        self.assertEqual(unknown, [], f"LAYOUT_HUB_NAMES lists hubs that do not exist: {unknown}")

    def test_the_classic_panel_is_still_present_as_the_fallback(self):
        """Converting every hub must not delete the path we fall back to."""
        self.assertIn("class CommandHubView(discord.ui.View):", HUBS_SOURCE)
        self.assertIn("_send_classic_hub", HUBS_SOURCE)
        self.assertIn("LAYOUT_COMPONENTS_AVAILABLE", HUBS_SOURCE)


class AdminPanelGuardTests(unittest.TestCase):
    """The GM console is owner-locked AND permission-locked, on every interaction.

    /admin is gated at invoke time by default_permissions and require_admin, but a
    panel stays live for 15 minutes. Without a re-check, an administrator whose
    role is removed mid-session keeps a working GM console until it times out.
    The classic view has always re-checked; the layout view has to as well, or
    enabling "admin" in LAYOUT_HUB_NAMES silently drops that guard.
    """

    def _interaction_check(self, class_name: str) -> str:
        tree = ast.parse(HUBS_SOURCE)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for item in node.body:
                    if isinstance(item, ast.AsyncFunctionDef) and item.name == "interaction_check":
                        return ast.get_source_segment(HUBS_SOURCE, item) or ""
        raise AssertionError(f"{class_name}.interaction_check not found")

    def _has_admin_permission_branch(self, class_name: str) -> bool:
        """Find an `if <admin-name test>:` whose body performs the permission check.

        Checked structurally rather than by substring, because
        `self.definition.name == "admin"` also appears in the ownership branch's
        message selection - a text search matches that and cannot tell whether the
        guard itself is still there. An earlier version of this test passed with
        the guard disabled, which is the failure mode worth avoiding in a test
        that exists to protect the GM console.
        """
        body = self._interaction_check(class_name)
        tree = ast.parse(textwrap.dedent(body))
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            condition = ast.dump(node.test)
            if "'admin'" not in condition and '"admin"' not in condition:
                continue
            inner = "".join(ast.dump(child) for child in node.body)
            if "guild_permissions" in inner and "administrator" in inner:
                return True
        return False

    def test_layout_view_re_checks_the_administrator_permission(self):
        self.assertTrue(
            self._has_admin_permission_branch("LayoutHubView"),
            "LayoutHubView.interaction_check has no `if name == 'admin'` branch that "
            "checks guild_permissions.administrator - enabling 'admin' in "
            "LAYOUT_HUB_NAMES would then drop the re-check the classic panel performs.",
        )
        body = self._interaction_check("LayoutHubView")
        self.assertIn("isinstance(member, discord.Member)", body)
        self.assertIn("Administrator", body)

    def test_layout_view_still_enforces_ownership(self):
        body = self._interaction_check("LayoutHubView")
        self.assertIn("interaction.user.id != self.owner_id", body)

    def test_both_views_guard_the_admin_panel_the_same_way(self):
        layout = self._interaction_check("LayoutHubView")
        classic = self._interaction_check("CommandHubView")
        for fragment in (
            'self.definition.name == "admin"',
            "guild_permissions.administrator",
            "isinstance(member, discord.Member)",
            "This admin panel belongs to another administrator.",
        ):
            self.assertIn(fragment, classic, f"classic panel lost: {fragment}")
            self.assertIn(fragment, layout, f"layout panel lost: {fragment}")
        self.assertTrue(self._has_admin_permission_branch("CommandHubView"))
        self.assertTrue(self._has_admin_permission_branch("LayoutHubView"))


class ComponentBudgetTests(unittest.TestCase):
    def test_the_action_limit_keeps_the_worst_page_inside_discords_limit(self):
        """Discord counts all 40 components, nested ones included.

        Chrome costs 12 at worst (container, header, three separators, page text,
        and a five-button control row); each action row costs 3 (Section +
        TextDisplay + accessory Button). This arithmetic is what stops someone
        raising the limit to "show more actions" and pushing /sect over the edge.

        The number is 40, not 30. An external review asserted 30 and recommended
        dropping the limit to 6; three primary sources say otherwise and were
        checked directly:
          - Discord's Component Reference: "Messages allow up to 40 total
            components"
          - discord.py discord/ui/view.py: `if self._total_children > 40:
            raise ValueError('maximum number of children exceeded (40)')`
          - discord.js guide: "Messages can have up to 40 total components
            (nested components count!)"
        Do not lower this to 30 without re-checking those three.
        """
        match = re.search(r"^_LAYOUT_ACTION_LIMIT = (\d+)$", HUBS_SOURCE, re.M)
        self.assertIsNotNone(match, "_LAYOUT_ACTION_LIMIT not found")
        limit = int(match.group(1))
        worst_case = 12 + 3 * limit
        self.assertLessEqual(
            worst_case,
            40,
            f"_LAYOUT_ACTION_LIMIT={limit} puts the busiest page at {worst_case}/40 components",
        )
        self.assertGreaterEqual(limit, 5, "too few visible actions to be worth the layout")

    def test_only_one_top_level_component_is_used(self):
        """rebuild() adds a single Container; everything else nests inside it.

        Whatever Discord's top-level cap is, one is comfortably under it - and
        keeping it at one is what makes the 40-component total the only budget
        that has to be reasoned about.
        """
        tree = ast.parse(HUBS_SOURCE)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "LayoutHubView":
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "rebuild":
                        body = ast.get_source_segment(HUBS_SOURCE, item) or ""
                        self.assertEqual(
                            body.count("self.add_item("), 3,
                            "rebuild() should add exactly one top-level Container per return path",
                        )
                        self.assertIn("self.add_item(container)", body)
                        return
        self.fail("LayoutHubView.rebuild not found")

    def test_the_layout_has_no_select_menu(self):
        """The system dropdown was removed; Prev/Next is the only navigation."""
        self.assertNotIn("HubLayoutSystemSelect", HUBS_SOURCE)


class ActionOrderingTests(unittest.TestCase):
    """Actions sort by usefulness, not alphabetically.

    Alphabetical put /family's "Leave" tenth of fourteen - on the second chunk -
    while the onboarding copy told new players to use exactly that action to get
    out of their birth household.
    """

    @staticmethod
    def _rank():
        """Lift _action_rank and its word sets out of hubs.py (no discord import)."""
        wanted = {
            "_action_rank", "_ORIENTING_ACTION_WORDS", "_PRIMARY_ACTION_WORDS",
            "_IRREVERSIBLE_ACTION_WORDS",
        }
        picked = []
        for node in ast.parse(HUBS_SOURCE).body:
            if isinstance(node, ast.FunctionDef) and node.name in wanted:
                picked.append(node)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(getattr(t, "id", None) in wanted for t in targets):
                    picked.append(node)
        namespace = {"re": re}
        exec(compile(ast.Module(body=picked, type_ignores=[]), "hubs.py", "exec"), namespace)
        return namespace["_action_rank"]

    def test_bands_are_ordered_orienting_primary_rest_rare(self):
        rank = self._rank()
        self.assertEqual(rank("status"), 0)
        self.assertEqual(rank("view"), 0)
        self.assertEqual(rank("enter"), 1)
        self.assertEqual(rank("leave"), 1)
        self.assertEqual(rank("descendants"), 2)
        self.assertEqual(rank("sever"), 3)
        self.assertEqual(rank("reincarnate"), 3)

    def test_leave_outranks_ordinary_actions(self):
        """The regression this ordering exists for."""
        rank = self._rank()
        for ordinary in ("ancestry", "child", "clan", "conflict", "descendants", "legacy", "support"):
            self.assertLess(
                rank("leave"), rank(ordinary),
                f"'leave' must sort ahead of '{ordinary}' or /family buries it on chunk 2",
            )

    def test_irreversible_wins_over_a_common_word_in_the_same_name(self):
        """Checked first, so a rare action is never promoted by a common word."""
        rank = self._rank()
        self.assertEqual(rank("reset_status"), 3)
        self.assertEqual(rank("sever_bond"), 3)

    def test_ordering_is_a_different_axis_from_button_colour(self):
        """_DANGER_ACTION_WORDS decides colour; sorting by it would bury Leave."""
        danger = re.search(r"_DANGER_ACTION_WORDS = frozenset\(\{(.*?)\}\)", HUBS_SOURCE, re.S)
        self.assertIsNotNone(danger)
        self.assertIn('"leave"', danger.group(1), "leave is still styled as a danger action")
        self.assertEqual(self._rank()("leave"), 1, "but it must not be ordered as a rare one")


class EphemeralRoutingTests(unittest.TestCase):
    """Private hub feedback must never be rewritten into the public hub card."""

    def test_proxy_tracks_deferred_visibility(self):
        self.assertIn("self.deferred_ephemeral = False", HUBS_SOURCE)
        self.assertIn('if "ephemeral" in kwargs:', HUBS_SOURCE)
        self.assertIn(
            'self.owner.deferred_ephemeral = bool(kwargs["ephemeral"])',
            HUBS_SOURCE,
        )

    def test_ephemeral_responses_bypass_public_hub_edits(self):
        self.assertIn("async def _send_ephemeral_followup(", HUBS_SOURCE)
        self.assertIn(
            "if _response_is_ephemeral(\n            kwargs, default=self.owner.deferred_ephemeral",
            HUBS_SOURCE,
        )
        self.assertIn('followup_kwargs["ephemeral"] = True', HUBS_SOURCE)

    def test_hub_ui_only_feedback_is_private(self):
        for fragment in (
            "This system panel belongs to another player.",
            "This panel requires the **Administrator** permission.",
            "nothing to choose from right now.",
        ):
            self.assertIn(fragment, HUBS_SOURCE)

        tree = ast.parse(HUBS_SOURCE)
        guarded_messages = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            segment = ast.get_source_segment(HUBS_SOURCE, node) or ""
            if any(
                marker in segment
                for marker in (
                    "This system panel belongs to another player.",
                    "This panel requires the **Administrator** permission.",
                    "nothing to choose from right now.",
                )
            ):
                guarded_messages.append(segment)
        self.assertTrue(guarded_messages)
        for segment in guarded_messages:
            self.assertIn("ephemeral=True", segment)


class LongReplyVisibilityTests(unittest.TestCase):
    def test_reply_long_preserves_caller_visibility(self):
        runtime_source = (PROJECT_ROOT / "app" / "bot" / "runtime.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(runtime_source)
        reply_long = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "reply_long"
        )
        body = ast.get_source_segment(runtime_source, reply_long) or ""
        self.assertEqual(body.count("ephemeral=ephemeral"), 3)
        self.assertNotIn("ephemeral=False", body)


def _function_body(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name
    )
    return ast.get_source_segment(source, node) or ""


class HubFailurePublicSurfaceTests(unittest.TestCase):
    """A v0.19.31 ephemeral-routing patch tried to fold these two paths into
    the private-followup helper. Both are deliberately excluded from that
    patch: they answer a hub action/command with the hub panel itself still
    the thing everyone in the channel is looking at, so the response has to
    land back on that public surface (or on the panel-preserving Components
    V2 followup), not vanish into a message only the acting player can see.
    """

    def test_invoke_action_failure_still_refreshes_the_public_hub_panel(self):
        body = _function_body(HUBS_SOURCE, "_invoke_action")
        self.assertIn("_layout_targets_panel(interaction, hub_view)", body)
        self.assertIn("_layout_result_send(interaction, text, {})", body)
        self.assertIn(
            "interaction.response.edit_message(content=text, embed=None, view=hub_view)",
            body,
        )
        self.assertIn(
            "interaction.edit_original_response(content=text, embed=None, view=hub_view)",
            body,
        )
        # The private-followup helper stays out of this path entirely - a
        # failed action re-renders the panel, it does not whisper to the
        # acting player instead.
        self.assertNotIn("_send_ephemeral_followup", body)

    def test_response_proxy_edit_message_still_defers_to_the_layout_panel(self):
        tree = ast.parse(HUBS_SOURCE)
        proxy = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "_HubResponseProxy"
        )
        edit_message = next(
            n
            for n in ast.walk(proxy)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "edit_message"
        )
        body = ast.get_source_segment(HUBS_SOURCE, edit_message) or ""
        self.assertIn(
            "_layout_targets_panel(self.owner.source, self.owner.hub_view)", body
        )
        self.assertIn("_layout_result_send(", body)
        self.assertIn("source_message == hub_message", body)


if __name__ == "__main__":
    unittest.main()
