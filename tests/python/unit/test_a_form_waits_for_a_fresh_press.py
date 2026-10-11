"""A form waits for a fresh press (v1.33.0).

The menu's Next button and the journal's door buttons open the hub in place -
that is the press's one answer, an `edit_message` - and then press the leaf on
the same interaction. A leaf whose first input is free text answers with a
form, and Discord takes a form only as an interaction's first answer:
`send_modal` raised `InteractionResponded`. Six tutorial steps name
**/combat -> Boss Raids -> Claim** and one names **/innerworld -> Personal
World -> Create**, both form-first, so the button the menu drew for the next
step of the road crashed when it was pressed, and the player was told nothing
had happened.

`_form_can_answer` is the one place that says whether a form may still be the
answer, and `_present_input_step` replies with the existing Continue button when
it may not: Continue is a press of its own, a fresh interaction, and opens the
form. A modal's own submit is the second case Discord refuses a form for.

The response here is discord.py's own `InteractionResponse`, so `is_done()` and
`send_modal`'s refusal are the library's; only what would reach Discord is
stubbed, and each stub marks the response used the way a successful call does.
Without that a fake `send_modal` would take a second answer the real one refuses,
which is how a hand-made `AsyncMock` response let this through.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from app.rules.quests import (
    OBJECTIVE_PATHS,
    QUEST_DEFINITIONS,
    ascension_quest_seed_rows,
    beginner_path_seed_rows,
    household_errand_seed_rows,
    labelled_objective,
    profession_exam_seed_rows,
    realm_road_seed_rows,
    static_quest_seed_rows,
)
from tests.support import PROJECT_ROOT

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
WORLD_FILE = PROJECT_ROOT / "content" / "world.json"


def _modules():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.surface"), importlib.import_module("app.bot.hubs")


class _Response(discord.InteractionResponse):
    """discord.py's response with the HTTP calls stubbed and nothing else."""

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.log: list[str] = []
        self.modals: list[discord.ui.Modal] = []

    async def _use(self, kind: discord.InteractionResponseType, what: str) -> None:
        if self.is_done():
            raise discord.InteractionResponded(self._parent)
        self._response_type = kind
        self.log.append(what)

    async def edit_message(self, **kw) -> None:
        await self._use(discord.InteractionResponseType.message_update, f"edit_message({type(kw.get('view')).__name__})")

    async def send_message(self, *args, **kw) -> None:
        await self._use(discord.InteractionResponseType.channel_message, f"send_message({type(kw.get('view')).__name__})")

    async def defer(self, **kw) -> None:
        await self._use(discord.InteractionResponseType.deferred_message_update, "defer")

    async def send_modal(self, modal) -> None:
        self.modals.append(modal)
        if self.is_done():
            return await super().send_modal(modal)   # the library's own refusal
        await self._use(discord.InteractionResponseType.modal, f"send_modal({type(modal).__name__})")


class _Followup:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.sent: list[tuple[str, object]] = []

    async def send(self, content=None, *, view=None, **kw) -> None:
        self.sent.append((str(content or ""), view))
        self.response.log.append(f"followup.send({type(view).__name__})")


def _interaction(*, kind=discord.InteractionType.component, ephemeral_message: bool = False) -> SimpleNamespace:
    inter = SimpleNamespace(
        id=99, type=kind, guild=None, channel=None, client=SimpleNamespace(), _state=None,
        user=SimpleNamespace(id=7, display_name="T", guild_permissions=SimpleNamespace(administrator=False)),
        message=SimpleNamespace(id=2 if ephemeral_message else 1,
                                flags=SimpleNamespace(components_v2=not ephemeral_message, ephemeral=ephemeral_message)),
    )
    inter.response = _Response(inter)
    inter.followup = _Followup(inter.response)
    return inter


@contextlib.contextmanager
def _no_engine(surface, hubs):
    """A bot with no engine around it: the panel's lookups answer nothing, the
    live options are empty and the hub carries no status."""
    with contextlib.ExitStack() as stack:
        for name in ("_READ_SCOPE", "_HIDDEN_ACTIONS", "_NOT_YET_UNLOCKED"):
            stack.enter_context(patch.object(hubs, name, None))
        stack.enter_context(patch.object(hubs, "_live_options", AsyncMock(return_value=[])))
        stack.enter_context(patch.object(surface, "_status_provider_for", lambda definition: None))
        stack.enter_context(patch.dict(surface._LAST_HUB))
        yield


def _leaf(surface, hub: str, path: str):
    action = surface._daily_leaf(hub, path)
    if action is None:
        raise AssertionError(f"{hub} has no leaf {path}")
    return action


async def _hub(hubs, surface, hub: str):
    return hubs.LayoutHubView(7, surface._HUB_BY_NAME[hub], owner_name="T")


async def _present_or_fail(testcase: unittest.TestCase, hubs, inter, view, action, inputs) -> None:
    try:
        await hubs._present_input_step(inter, view, action, inputs, {})
    except discord.InteractionResponded:
        testcase.fail(f"{action.path} sent a form as the answer to an interaction that was already answered "
                      f"(a modal is only ever an interaction's first answer)")


def _continue_buttons(view) -> list:
    return [item for item in getattr(view, "children", []) if item.__class__.__name__ == "HubContinueInputButton"]


async def _form_first(hubs, action) -> bool:
    """Whether a fresh press of this leaf is answered with a form. Read off the
    production step on an unspent interaction, so the answer does not depend on
    the guard being tested. A danger leaf is confirmed before any input, and a
    leaf with no input would run its real handler: neither is driven."""
    inputs = hubs._inputs_for(action)
    if not inputs or hubs._is_danger_action(action):
        return False

    view = hubs.LayoutHubView(7, hubs.REGISTERED_HUBS[0], owner_name="T")
    inter = _interaction()
    await hubs._present_input_step(inter, view, action, inputs, {})
    return len(inter.response.modals) == 1


class ASpentResponseIsHandedContinue(unittest.TestCase):
    def test_a_spent_response_is_handed_continue_not_a_form(self):
        surface, hubs = _modules()
        action = _leaf(surface, "innerworld", "/innerworld create")
        inputs = hubs._inputs_for(action)
        self.assertEqual([i.name for i in inputs], ["name"], "the leaf this test drives is no longer form-first")

        async def run():
            view = await _hub(hubs, surface, "innerworld")
            inter = _interaction()
            await inter.response.edit_message(view=view)      # what the menu's press does first
            await _present_or_fail(self, hubs, inter, view, action, inputs)
            return inter

        with _no_engine(surface, hubs):
            inter = asyncio.run(run())
        self.assertEqual(inter.response.modals, [], "a form was attempted on a spent response")
        self.assertEqual(len(inter.followup.sent), 1, inter.response.log)
        content, reply = inter.followup.sent[0]
        self.assertIsInstance(reply, hubs.HubContinueInputView)
        self.assertIn("Continue", content)
        carried = _continue_buttons(reply)
        self.assertEqual(len(carried), 1)
        self.assertEqual([i.name for i in carried[0].remaining], [i.name for i in inputs],
                         "Continue does not carry every input still to ask")

    def test_the_continue_press_opens_the_form(self):
        surface, hubs = _modules()
        action = _leaf(surface, "innerworld", "/innerworld create")
        inputs = hubs._inputs_for(action)

        async def run():
            view = await _hub(hubs, surface, "innerworld")
            spent = _interaction()
            await spent.response.edit_message(view=view)
            await _present_or_fail(self, hubs, spent, view, action, inputs)
            button = _continue_buttons(spent.followup.sent[0][1])[0]
            fresh = _interaction(ephemeral_message=True)     # the press of Continue is its own interaction
            await button.callback(fresh)
            return fresh

        with _no_engine(surface, hubs):
            fresh = asyncio.run(run())
        self.assertEqual(len(fresh.response.modals), 1, f"Continue did not open the form: {fresh.response.log}")
        modal = fresh.response.modals[0]
        self.assertIsInstance(modal, hubs.HubActionModal)
        self.assertEqual([i.name for i in modal.inputs], ["name"])
        self.assertEqual(fresh.followup.sent, [], "Continue answered with another Continue")

    def test_a_fresh_press_still_opens_the_form_at_once(self):
        surface, hubs = _modules()
        action = _leaf(surface, "innerworld", "/innerworld create")

        async def run():
            view = await _hub(hubs, surface, "innerworld")
            inter = _interaction()
            await _present_or_fail(self, hubs, inter, view, action, hubs._inputs_for(action))
            return inter

        with _no_engine(surface, hubs):
            inter = asyncio.run(run())
        self.assertEqual(len(inter.response.modals), 1, inter.response.log)
        self.assertEqual(inter.followup.sent, [], "a press that could open the form was asked to press again")

    def test_a_forms_own_submit_is_never_answered_with_another_form(self):
        surface, hubs = _modules()
        action = _leaf(surface, "innerworld", "/innerworld create")

        async def run():
            view = await _hub(hubs, surface, "innerworld")
            inter = _interaction(kind=discord.InteractionType.modal_submit)   # nothing answered yet
            self.assertFalse(inter.response.is_done())
            await _present_or_fail(self, hubs, inter, view, action, hubs._inputs_for(action))
            return inter

        with _no_engine(surface, hubs):
            inter = asyncio.run(run())
        self.assertEqual(inter.response.modals, [], "a form was sent as the answer to a form's own submit")
        self.assertEqual([type(v).__name__ for _, v in inter.followup.sent] + inter.response.log,
                         ["send_message(HubContinueInputView)"])


def _seeded_rows() -> dict[str, list[dict]]:
    world = json.loads(WORLD_FILE.read_text(encoding="utf-8"))
    shim = type("W", (), {"data": world})()
    return {
        "beginner_path": beginner_path_seed_rows(shim),
        "realm_road": realm_road_seed_rows(shim),
        "commission": list(world.get("commissions") or []),
        "static": static_quest_seed_rows(QUEST_DEFINITIONS),
        "household_errand": household_errand_seed_rows(shim),
        "ascension": ascension_quest_seed_rows(shim),
        "profession_exam": profession_exam_seed_rows(shim),
    }


async def _press(button) -> tuple[SimpleNamespace, str]:
    """Press a Next / door button the way the menu hands it over: an unspent
    component interaction, the hub opened in place, the leaf pressed. Returns
    what happened and, if it went wrong, how."""
    inter = _interaction()
    try:
        await button.callback(inter)
    except discord.InteractionResponded:
        return inter, "raised InteractionResponded"
    asked = [type(v).__name__ for _, v in inter.followup.sent]
    if inter.response.modals or asked != ["HubContinueInputView"]:
        return inter, f"answered {inter.response.log}"
    return inter, ""


class EveryDoorThatOpensAFormAnswersFromTheMenuAndTheJournal(unittest.TestCase):
    def _doors(self, surface, hubs) -> dict[tuple[str, str, str], tuple[str, object]]:
        """Every Next button and journal door the game can draw, one per
        (kind, hub, leaf), with the quest or objective type it came from.

        The menu's tutorial line is a road stage's objective label; the journal
        prints the label with the place it is done (`labelled_objective`), and
        an objective type's own door is `OBJECTIVE_PATHS`. Each is built
        through the production factory, not named here."""
        doors: dict[tuple[str, str, str], tuple[str, object]] = {}
        rows = _seeded_rows()
        for source in ("beginner_path", "realm_road"):
            for row in rows[source]:
                for objective in row.get("objectives") or []:
                    label = objective.get("label") or objective.get("id")
                    step = surface._next_step(f"🧭 Next: **{row.get('title')}** — {label}")
                    if step is not None:
                        doors.setdefault(("menu", step[0], step[1].path),
                                         (str(row.get("quest_key")), surface.MenuNextButton(*step)))
        texts = [(str(row.get("quest_key")), labelled_objective(objective))
                 for seeded in rows.values() for row in seeded for objective in row.get("objectives") or []]
        texts += [(kind, text) for kind, text in OBJECTIVE_PATHS.items()]
        for origin, text in texts:
            for button in hubs.path_buttons(text):
                if isinstance(button, surface.MenuNextButton):
                    doors.setdefault(("journal", button.hub, button.path), (origin, button))
        return doors

    def test_every_door_that_opens_a_form_answers_from_the_menu_and_the_journal(self):
        surface, hubs = _modules()
        failures: list[str] = []
        pressed: list[tuple[str, str]] = []

        async def run(doors):
            for (how, hub, path), (origin, button) in sorted(doors.items()):
                action = surface._daily_leaf(hub, path)
                if action is None or not await _form_first(hubs, action):
                    continue
                _, problem = await _press(button)
                pressed.append((how, path))
                if problem:
                    failures.append(f"{how} {hub} {path} ({origin}): {problem}")

        with _no_engine(surface, hubs):
            doors = self._doors(surface, hubs)
            self.assertEqual({how for how, _, _ in doors}, {"menu", "journal"}, "a kind of door was not built at all")
            asyncio.run(run(doors))
        self.assertTrue(pressed, "no door that opens a form was found: the reader found nothing, so nothing was held")
        self.assertEqual(failures, [], "These doors cannot open the form they lead to; offer Continue, "
                                       "a form is only an interaction's first answer.")


class EveryFormFirstLeafAnswersTheMenusPress(unittest.TestCase):
    def test_every_leaf_whose_first_step_is_a_form_answers_the_menus_press(self):
        surface, hubs = _modules()
        failures: list[str] = []
        pressed: list[str] = []

        async def run():
            for definition in surface._HUB_DEFINITIONS:
                for page in definition.pages:
                    for action in hubs._leaf_actions(page):
                        if not await _form_first(hubs, action):
                            continue
                        _, problem = await _press(surface.MenuNextButton(definition.name, action))
                        pressed.append(f"{definition.name} {action.path}")
                        if problem:
                            failures.append(f"{definition.name} {action.path}: {problem}")

        with _no_engine(surface, hubs):
            asyncio.run(run())
        self.assertTrue(pressed, "no form-first leaf was found: the classifier found nothing, so nothing was held")
        self.assertEqual(failures, [], "These leaves are form-first and crash when the menu's Next presses them.")


if __name__ == "__main__":
    unittest.main()
