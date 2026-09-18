#!/usr/bin/env python3
"""Drive the bot through a simulated Discord (v1.0.0-rc.33): the half of the
playtest a player actually touches.

`scripts/playtest_engine.py` drives the roadmap's loops through the engine's
HTTP API. This drives the bot itself - `app.bot`, unmodified - through SimCord,
an in-memory Discord that runs discord.py's real machinery: `setup_hook` runs,
`tree.sync(guild=GUILD)` registers the commands, `on_ready` fires, and a test
actor fires slash commands, presses the panel's buttons, chooses from its
selects, fills its modals and types a line, then reads what came back. Every
loop below goes through those surfaces and never through a handler or a
database method: the point is the wiring, not the mechanics the engine playtest
already covers.

It prints one line per step - PASS, FAIL or SKIP with the reason - and exits
non-zero if anything failed. Nothing here asserts on dice: a panel answered, a
thread exists, a locked line is printed, a modal was shown, a quest line
followed a reply. Rolls are reported, never bounded.

    python3 scripts/playtest_discord.py --launch     # build, start and bootstrap a scratch engine, run, stop
    GAME_ENGINE_URL=http://127.0.0.1:8081 ENGINE_AUTH_TOKEN=... DATABASE_PATH=... python3 scripts/playtest_discord.py

Never point it at the production database: it binds channels, creates a
character, moves it about and spends its stones. The environment is set
before `app.bot` is imported, because `app/bot/runtime.py` builds its settings,
engine client and database at import - see `_configure` below.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from playtest_common import Report, bootstrap, launch_engine, step, stop_engine  # noqa: E402 - beside this file

GUILD_ID = 900000000000000001
HEALTH_PORT = 18182  # 1..65535 is enforced by the settings; the engine playtest's own port is 18089
PLAYER_NAME = "Shen Rui"

# Leaves the sweep (section 8) does not press, each with its reason.
# `tests/python/contracts/test_playtest_coverage.py` holds every key to a
# live hub path, so a renamed or removed leaf cannot leave a stale entry.
DEFERRED_LEAVES: dict[str, str] = {
    "/admin server lockdown": (
        "maintenance mode: the sweep answers a bool by enabling it, and this "
        "one closes the world to every player - it would refuse every leaf "
        "pressed after it. Driven explicitly in section 4b instead, where the "
        "refusal and the reopening are both asserted."
    ),
}

# What the bot prints when a handler raised: never a designed refusal, which
# always names what is missing. The gate reads these off the source.
WIRING_FAILURE_TEXTS = (
    "❌ That action could not be completed. The game state was rechecked and no additional hub-side rule was applied.",
    "❌ This interface hit an unexpected error. No extra hub-side game rule was applied.",
    "Something went wrong. Check your current state (e.g. inventory, sheet) before retrying — this error does not guarantee nothing changed. An administrator can check the configured bot log channel.",
)
METER_TEXT = "⏳ Actions are limited to about"
_ACTION_META = re.compile(r"actions (\d+)-(\d+) of (\d+)")


def _configure(url: str, token: str, db_path: str) -> None:
    """Everything `Settings.from_env()` needs, set before the bot is imported.
    Only keys `.env.example` already carries; the workers that would reach the
    network or need a key are switched off, and the narrator is procedural so
    no route is ever called."""
    os.environ.update({
        "DISCORD_TOKEN": "simcord-playtest-token", "GUILD_ID": str(GUILD_ID),
        "GAME_ENGINE_URL": url, "ENGINE_AUTH_TOKEN": token, "DATABASE_PATH": db_path,
        "NARRATOR_PROVIDER": "procedural", "HEALTH_PORT": str(HEALTH_PORT),
        "MESSAGE_CONTENT_INTENT": "true", "AUTO_NARRATE": "true",  # typed play listens in a private scene only with both
        "UPDATE_CHECK_ENABLED": "false", "QUEST_FORGE_AUTO": "false", "ROUTE_AUDIT_HOURS": "0",
        # A surprise on the typed explore (28% by default) blocks the road
        # until it is resolved, which would fail the capital step on the dice
        # about one run in four; the engine playtest drives the surprises.
        "UNEXPECTED_EVENT_CHANCE_PERCENT": "0",
        # The per-player action meter (about six a minute) is right for a
        # person and wrong for a harness that presses sixty buttons in one;
        # the meter itself is held by tests/python/contracts/test_narrator_budget.py.
        "TYPED_PLAY_BURST": "1000", "TYPED_PLAY_PER_MINUTE": "6000",
    })


# ---------------------------------------------------------------------------
# Reading what the bot drew
# ---------------------------------------------------------------------------

def _component_dicts(components: Any) -> list[dict[str, Any]]:
    out = []
    for item in list(components or []):
        if isinstance(item, dict):
            out.append(item)
        elif hasattr(item, "to_dict"):
            out.append(item.to_dict())
    return out


def _walk(components: Any):
    from simcord.components import walk_components

    yield from walk_components(_component_dicts(components))


def component_text(components: Any) -> str:
    """Every word a player can read on a message's components: text displays,
    button labels, select placeholders and options."""
    parts = []
    for node in _walk(components):
        for key in ("content", "label", "placeholder"):
            if node.get(key):
                parts.append(str(node[key]))
        for option in node.get("options") or []:
            parts.append(str(option.get("label") or ""))
    return "\n".join(parts)


def message_text(message: Any) -> str:
    parts = [str(getattr(message, "content", "") or "")]
    for embed in list(getattr(message, "embeds", None) or []):
        parts += [str(embed.title or ""), str(embed.description or "")]
        for field in getattr(embed, "fields", []) or []:
            parts += [str(field.name or ""), str(field.value or "")]
    parts.append(component_text(getattr(message, "components", None)))
    return "\n".join(p for p in parts if p)


def result_text(result: Any) -> str:
    """The response, every followup and the modal a slash or click produced."""
    parts = []
    if result is None:
        return ""
    if getattr(result, "response", None) is not None:
        parts.append(message_text(result.response))
    for followup in list(getattr(result, "followups", None) or []):
        parts.append(message_text(followup))
    if getattr(result, "modal", None):
        parts.append(component_text(result.modal.get("components")))
    return "\n".join(p for p in parts if p)


def button_labels(components: Any) -> list[str]:
    return [str(n.get("label") or "") for n in _walk(components) if n.get("type") == 2]


def section_button(components: Any, label: str) -> str | None:
    """The custom_id of the button on the panel row that names `label` (a
    Components V2 section: the text names the action, its accessory runs it).
    On the classic panel a quick-action button carries the label itself."""
    for node in _walk(components):
        if node.get("type") == 9:
            text = "\n".join(str(c.get("content") or "") for c in node.get("components") or [])
            accessory = node.get("accessory") or {}
            if f"**{label}**" in text and accessory.get("type") == 2:
                return str(accessory.get("custom_id"))
    for node in _walk(components):
        if node.get("type") == 2 and str(node.get("label") or "") == label:
            return str(node.get("custom_id"))
    return None


def select_by_placeholder(components: Any, prefix: str) -> dict[str, Any] | None:
    for node in _walk(components):
        if node.get("type") in (3, 5, 6, 7, 8) and str(node.get("placeholder") or "").startswith(prefix):
            return node
    return None


def modal_values(result: Any, **by_label: str) -> dict[str, str]:
    """Map a modal's text inputs by their label to the custom_ids SimCord
    submits by. A label is what the player reads; the id is what Discord keys."""
    ids: dict[str, str] = {}
    for node in _walk((result.modal or {}).get("components")):
        if node.get("type") == 4:
            ids[str(node.get("label") or "")] = str(node.get("custom_id"))
        if node.get("type") == 18 and isinstance(node.get("component"), dict):  # a Label-wrapped input
            inner = node["component"]
            if inner.get("type") == 4:
                ids[str(node.get("label") or "")] = str(inner.get("custom_id"))
    values = {}
    for label, value in by_label.items():
        if label not in ids:
            raise Failed(f"the modal has no field labelled {label!r}; it has {sorted(ids)}")
        values[ids[label]] = value
    return values


class Failed(AssertionError):
    """What a step reports when the surface did not do what it promises."""


def expect(condition: object, message: str = "the surface did not do what it promises") -> None:
    if not condition:
        raise Failed(message)


class Panel:
    """A hub panel as one player sees it: the message the slash command drew,
    re-read after every press because the bot edits it in place."""

    def __init__(self, actor: Any, channel: Any, message: Any) -> None:
        self.actor = actor
        self.channel = channel
        self.message_id = int(message.id)

    def message(self) -> Any:
        for message in self.channel.history(viewer=self.actor):
            if int(message.id) == self.message_id:
                return message
        raise Failed("the panel message is gone")

    def text(self) -> str:
        return message_text(self.message())

    def labels(self) -> list[str]:
        return button_labels(self.message().components)

    def page_title(self) -> str:
        for line in self.text().splitlines():
            if line.startswith("### "):
                return line[4:]
        return ""

    async def press(self, label: str) -> Any:
        custom_id = section_button(self.message().components, label)
        if custom_id is None:
            raise Failed(f"no {label!r} on the panel; it offers {self.labels()} and reads:\n{self.text()[:600]}")
        return await self.actor.click(self.message(), custom_id=custom_id)

    async def goto(self, page_label: str, *, limit: int = 12, env: Any = None) -> None:
        """Step systems with the panel's own button until the page shows. With
        `env`, a slow step is waited out rather than failed."""
        for _ in range(limit):
            if self.page_title().endswith(page_label):
                return
            try:
                await self.actor.click(self.message(), label="Next system")
            except TimeoutError:
                if env is None:
                    raise
                await settle_patiently(env)
        raise Failed(f"no page {page_label!r} within {limit} steps; the last was {self.page_title()!r}")


async def open_hub(actor: Any, channel: Any, name: str, *, env: Any = None) -> Panel:
    """Open a hub. With `env`, a slow open (the bot still refreshing the
    panel's status when SimCord's settle gives up) is waited out and the panel
    the bot drew is taken from the channel rather than opened twice."""
    try:
        result = await actor.slash(channel, name)
    except TimeoutError:
        if env is None:
            raise
        await settle_patiently(env)
        newest = next((m for m in reversed(list(channel.history(viewer=actor))) if getattr(m, "components", None)), None)
        if newest is None:
            raise Failed(f"/{name} was slow and drew no panel")
        return Panel(actor, channel, newest)
    if result.response is None:
        raise Failed(f"/{name} answered nothing (acknowledged={result.acknowledged}, deferred={result.deferred})")
    return Panel(actor, channel, result.response.message)


async def choose(actor: Any, result: Any, placeholder: str, *, value: str | None = None, label: str | None = None) -> Any:
    """Answer an input step (a picker the hub sent) with one of its options."""
    message = result.response.message if result.response is not None else None
    if message is None:
        raise Failed("the step did not draw a picker")
    select = select_by_placeholder(message.components, placeholder)
    if select is None:
        raise Failed(f"no picker starting {placeholder!r}; the step reads:\n{message_text(message)[:400]}")
    options = list(select.get("options") or [])
    if label is not None:
        matching = [o for o in options if str(o.get("label") or "") == label]
        if not matching:
            raise Failed(f"no option {label!r}; the picker offers {[o.get('label') for o in options]}")
        value = str(matching[0]["value"])
    if value is None:
        value = str(options[0]["value"])
    return await actor.select(message, [value], custom_id=str(select["custom_id"]))


async def answer_steps(actor: Any, result: Any, *, picks: dict[str, Any] | None = None, fields: dict[str, str] | None = None,
                       limit: int = 6) -> Any:
    """Walk the hub's input steps as a player would: each picker the hub sends
    is answered from `picks` (placeholder prefix -> option label, or an entity
    handle for a member/channel picker), each modal from `fields` (label ->
    value), until the action has run."""
    picks = dict(picks or {})
    fields = dict(fields or {})
    answered: set[str] = set()
    for _ in range(limit):
        if result.modal:
            # Fill the fields this modal shows; a field it does not ask for
            # (one with a default the hub kept) is simply not sent.
            shown: set[str] = set()
            for node in _walk((result.modal or {}).get("components")):
                inner = node.get("component") if node.get("type") == 18 else node
                if isinstance(inner, dict) and inner.get("type") == 4:
                    shown.add(str(node.get("label") or ""))
            values = modal_values(result, **{label: value for label, value in fields.items() if label in shown})
            result = await actor.submit_modal(result, values)
            continue
        message = result.response.message if result.response is not None else None
        select = select_by_placeholder(message.components, "") if message is not None else None
        if select is None or str(select.get("custom_id")) in answered:
            # A picker answered once and still on screen is the deferred
            # acknowledgement of the action it started, not another question.
            return result
        answered.add(str(select.get("custom_id")))
        placeholder = str(select.get("placeholder") or "")
        answer = next((value for prefix, value in picks.items() if placeholder.startswith(prefix)), None)
        expect(answer is not None, f"nothing to answer the picker {placeholder!r} with; the step reads:\n{message_text(message)[:300]}")
        if isinstance(answer, str) and select.get("type") == 3:
            options = list(select.get("options") or [])
            matching = [o for o in options if str(o.get("label") or "") == answer] or \
                       [o for o in options if answer in str(o.get("label") or "")]
            expect(matching, f"no option {answer!r} on {placeholder!r}; it offers {[o.get('label') for o in options]}")
            result = await actor.select(message, [str(matching[0]["value"])], custom_id=str(select["custom_id"]))
        else:
            result = await actor.select(message, [answer], custom_id=str(select["custom_id"]))
    raise Failed(f"the action still asks after {limit} steps")


def _modal_inputs(result: Any) -> list[tuple[str, str]]:
    """(label, custom_id) for every text input a modal shows, plain or
    Label-wrapped."""
    out = []
    for node in _walk((result.modal or {}).get("components")):
        inner = node.get("component") if node.get("type") == 18 else node
        if isinstance(inner, dict) and inner.get("type") == 4:
            out.append((str(node.get("label") or inner.get("label") or ""), str(inner.get("custom_id"))))
    return out


def canned_value(hubs: Any, spec: Any, *, member_id: int, channel_id: int) -> str:
    """One value for a modal field, chosen the way `hubs._resolve_input` will
    read it: a choice's own name, a range's minimum, a member or channel id,
    `true` for a flag, `1` for a number, a word for anything free."""
    if spec is None:
        return "playtest"
    if not spec.required and spec.default not in (None, ""):
        return str(spec.default)
    annotation = hubs._annotation_text(spec.annotation)
    if spec.choices:
        first = spec.choices[0]
        return str(getattr(first, "name", first))
    if "discord.Member" in annotation or annotation.endswith("Member") or "discord.User" in annotation:
        return str(member_id)
    if "discord.TextChannel" in annotation or "discord.Thread" in annotation or "discord.abc.GuildChannel" in annotation:
        return str(channel_id)
    ranged = hubs._RANGE_ANNOTATION.search(annotation)
    if ranged:
        return ranged.group(2)
    lowered = annotation.replace(" ", "")
    if annotation is int or annotation is float or lowered in {"int", "<class'int'>", "float", "<class'float'>"} or "int|None" in lowered or "float|None" in lowered:
        return "1"
    if hubs._is_bool_input(spec):
        return "true"
    return "playtest"


async def answer_generically(hubs: Any, actor: Any, action: Any, result: Any, *, member: Any, channel: Any, limit: int = 8) -> tuple[Any, str]:
    """Walk an action's input steps as a player with no plan would: a confirm
    is confirmed, a modal is filled with canned values, a picker takes its
    first option, a member picker the second member, a channel picker the
    first channel, until the action has run or the hub has said there is
    nothing to choose from. Returns the last result and how it was reached."""
    specs = {spec.label: spec for spec in hubs._inputs_for(action)}
    answered: set[str] = set()
    how: list[str] = []
    for _ in range(limit):
        if result.modal:
            values = {custom_id: canned_value(hubs, specs.get(label), member_id=int(member.id), channel_id=int(channel.id))
                      for label, custom_id in _modal_inputs(result)}
            how.append("modal")
            result = await actor.submit_modal(result, values)
            continue
        message = result.response.message if result.response is not None else None
        if message is None:
            break
        components = message.components
        button = next((n for n in _walk(components) if n.get("type") == 2 and str(n.get("custom_id")) not in answered
                       and (str(n.get("label") or "").startswith("Yes, ") or str(n.get("label") or "") == "Continue")), None)
        if button is not None:
            answered.add(str(button["custom_id"]))
            how.append("confirm" if str(button.get("label") or "").startswith("Yes, ") else "continue")
            result = await actor.click(message, custom_id=str(button["custom_id"]))
            continue
        select = select_by_placeholder(components, "")
        if select is None or str(select.get("custom_id")) in answered:
            break
        answered.add(str(select["custom_id"]))
        kind = int(select.get("type") or 3)
        if kind == 3:
            options = list(select.get("options") or [])
            expect(options, f"an empty picker {select.get('placeholder')!r}")
            how.append("pick")
            result = await actor.select(message, [str(options[0]["value"])], custom_id=str(select["custom_id"]))
        elif kind == 5:
            how.append("member")
            result = await actor.select(message, [member], custom_id=str(select["custom_id"]))
        elif kind == 8:
            how.append("channel")
            result = await actor.select(message, [channel], custom_id=str(select["custom_id"]))
        else:
            raise Failed(f"a select of type {kind} the sweep cannot answer")
    else:
        raise Failed(f"the action still asks after {limit} steps ({'+'.join(how)})")
    return result, "+".join(how) or "ran"


async def settle_patiently(env: Any, *, attempts: int = 8) -> None:
    """Wait for the bot's outstanding work in short settles rather than one
    long one (see the note at `simcord.run`). A leaf that asks the engine for
    the whole city's rumours can take longer than one settle under a sweep
    that has kept the engine busy; the last attempt raises."""
    for attempt in range(1, attempts + 1):
        try:
            await env.settle()
            return
        except TimeoutError:
            if attempt == attempts:
                raise


async def find_leaf_button(panel: Panel, label: str, *, limit: int = 12) -> str | None:
    """The custom_id of the row that names `label`, paging with the panel's
    own "More actions" until the offset wraps. The visible row limit is
    recomputed on every rebuild and drops while a result is shown, so a leaf
    that fit on first load may need a page after the previous press."""
    seen: set[int] = set()
    for _ in range(limit):
        custom_id = section_button(panel.message().components, label)
        if custom_id is not None:
            return custom_id
        meta = _ACTION_META.search(panel.text())
        offset = int(meta.group(1)) if meta else 0
        if offset in seen or "More actions" not in panel.labels():
            return None
        seen.add(offset)
        await panel.actor.click(panel.message(), label="More actions")
    return None


async def press_leaf(env: Any, hubs: Any, panel: Panel, action: Any, *, member: Any, channel: Any) -> tuple[str, str]:
    """Press one leaf and hold the wiring: the reply is a result or a
    designed refusal, never the hub's failure text, never the meter, never an
    exception. A leaf the panel hides must print its lock line instead.
    Returns ("pressed" | "locked", a one-line note)."""
    custom_id = await find_leaf_button(panel, action.label)
    if custom_id is None:
        text = panel.text()
        line = next((ln for ln in text.splitlines() if ln.startswith(f"🔒 {action.label}")), "")
        expect(line, f"{action.label!r} is neither drawn nor locked on {panel.page_title()!r}; the page offers {panel.labels()} and reads:\n{text[:700]}")
        return "locked", line
    before = set(panel.text().splitlines())
    raised_before = len(list(env.errors))
    try:
        result = await panel.actor.click(panel.message(), custom_id=custom_id)
        result, how = await answer_generically(hubs, panel.actor, action, result, member=member, channel=channel)
    except TimeoutError:
        # The press was delivered and the bot is still on it: wait it out and
        # read the panel; the ephemeral reply, if any, is lost to the record.
        result, how = None, "slow"
    await settle_patiently(env)
    try:
        after = panel.text()
    except Failed:
        after = ""
    # What the press drew: the lines the panel gained (its result block) first,
    # because a reply that edits the panel in place answers with the whole
    # panel, header and all; then the ephemeral reply, if there was one.
    fresh = "\n".join(ln for ln in after.splitlines() if ln not in before)
    reply = fresh + "\n" + result_text(result)
    # Held against what this press drew, not the whole panel: a result block
    # an earlier press left there would otherwise fail every leaf after it
    # whose own reply was ephemeral.
    for failure in WIRING_FAILURE_TEXTS:
        expect(failure not in reply, f"the hub's failure text after {how}:\n{reply[:600]}")
    expect(METER_TEXT not in reply, f"the action meter refused the sweep after {how}: raise TYPED_PLAY_PER_MINUTE")
    raised = list(env.errors)[raised_before:]
    expect(not raised, "; ".join(f"{type(e).__name__}: {e}" for e in raised)[:600])
    note = next((ln.strip() for ln in reply.splitlines() if ln.strip() and not ln.startswith(("## ", "### ", "-# "))), "(no reply)")
    return "pressed", f"{how} → {note[:120]}"


async def bot_replies_after(env: Any, channel: Any, own_message: Any, bot_id: int) -> list[Any]:
    await env.settle()
    return [m for m in channel.history() if int(m.id) > int(own_message.id) and int(m.author.id) == int(bot_id)]


def _thread_named_for(channel: Any, viewer: Any) -> Any | None:
    """A private thread under `channel` that `viewer` can read: the one the
    bot opened for them. A thread the bot never added them to is invisible."""
    for thread in channel.threads:
        try:
            thread.history(viewer=viewer)
        except Exception:  # noqa: BLE001, S112 - not a member, then; the next thread may be theirs
            continue
        return thread
    return None


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

async def run(url: str, token: str, db_path: str) -> Report:
    _configure(url, token, db_path)
    import logging

    import discord
    import simcord
    from simcord import SetupError

    from app.bot import main as _main  # noqa: F401 - registers the surface on the bot at import
    from app.bot.admin.channel_messages import BASE_CHANNEL_SPECS
    from app.bot.bot import bot
    from app.bot.runtime import SETTINGS
    from app.bot.services import GUILD
    from app.bot import hubs as hub_registry  # `hubs` is section 4's step below
    from app.bot.surface import _HUB_COMMANDS

    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per engine round trip is not a report
    report = Report()

    async with simcord.run(bot, strict_sync=True, check_errors=False) as env:
        # SimCord's settle timeout stays at its default (five seconds) on
        # purpose: a bot-owned worker whose next wake falls inside the
        # deadline counts as runnable, so a longer deadline swallows the
        # periodic workers' sleeps and never settles at all. A slow leaf is
        # waited out by `settle_patiently` - several short settles - instead.
        guild = env.create_guild("Xianxia Playtest", id=GUILD_ID)
        channels = {name: guild.create_text_channel(name) for name in BASE_CHANNEL_SPECS}
        admin_role = guild.create_role("Admin", permissions=discord.Permissions(administrator=True))
        gm = guild.add_member(env.create_user("GM"), roles=[admin_role])
        player = guild.add_member(env.create_user("Player One"))
        other = guild.add_member(env.create_user("Player Two"))  # no character: every member picker's answer
        bot_id = int(bot.user.id)

        # ---- 1. boot -----------------------------------------------------------
        async def boot():
            expected = 9 + len(_HUB_COMMANDS)
            synced = [c.name for c in bot.tree.get_commands(guild=GUILD)]
            expect(len(synced) == expected, f"{len(synced)} commands synced, expected {expected}: {sorted(synced)}")
            for phase in ("DATABASE_READY", "CATALOG_READY", "SIMULATION_READY", "DISCORD_READY"):
                expect(bot.health_state.phases[phase].ready, f"{phase} not ready")
            return synced
        synced = await step(report, "the bot booted through every startup phase and synced its commands", boot())
        if synced:
            report.add("PASS", f"{len(synced)} commands are registered with the guild", ", ".join(sorted(synced)))

        # ---- 2. the server -----------------------------------------------------
        async def refused_admin():
            result = await player.slash(channels["bot-logs"], "admin")
            text = result_text(result)
            expect("**Administrator** permission" in text, text[:200])
        await step(report, "/admin refuses a player who is not an administrator", refused_admin())

        async def bind_channels():
            panel = await open_hub(gm, channels["bot-logs"], "admin")
            expect(panel.page_title().endswith("Server"), panel.page_title())
            picked = await panel.press("Basechannels")
            chosen = await choose(gm, picked, "Choose action", label="Validate / bind existing base Xianxia channels")
            expect(chosen.modal, "the category name should be asked in a modal after the choice")
            done = await gm.submit_modal(chosen, modal_values(chosen, **{"Category Name": "📜 Xianxia RP"}))
            await settle_patiently(env)
            text = result_text(done) + "\n" + panel.text()
            for name in BASE_CHANNEL_SPECS:
                expect(channels[name].mention in text or f"#{name}" in text, f"{name} not named in:\n{text[:800]}")
            expect("Missing" not in text and "Could not" not in text, text[:800])
            return text
        await step(report, "/admin → Server → Basechannels → bind connects the eight base channels", bind_channels())

        # ---- 3. a cultivator ---------------------------------------------------
        async def begin():
            result = await player.slash(channels["begin-here"], "begin")
            message = result.response.message
            chosen = await player.click(message, label="Choose Family")
            message = chosen.response.message if chosen.response is not None else message
            style = select_by_placeholder(message.components, "Choose your cultivation path")
            expect(style is not None, message_text(message)[:300])
            await player.select(message, [str(style["options"][0]["value"])], custom_id=str(style["custom_id"]))
            sex = select_by_placeholder(Panel(player, channels["begin-here"], message).message().components, "Choose birth sex")
            expect(sex is not None)
            await player.select(message, ["male"], custom_id=str(sex["custom_id"]))
            form = await player.click(message, label="Open Character Form")
            expect(form.modal, "Open Character Form should open the character modal")
            created = await player.submit_modal(form, modal_values(form, **{"Character name": PLAYER_NAME}))
            await settle_patiently(env)
            text = result_text(created)
            expect("First Step on the Dao" in text, text[:400])
            thread = _thread_named_for(channels["player-homes"], player)
            expect(thread is not None, f"no household thread the player can read under #player-homes; threads: {[t.name for t in channels['player-homes'].threads]}")
            return thread
        household = await step(report, "/begin → family → path → sex → form makes a cultivator and opens the household thread", begin())
        if household is not None:
            report.add("PASS", "the household thread is private and the player is inside it", household.name)

        async def journal():
            result = await player.slash(channels["begin-here"], "quests")
            text = result_text(result)
            expect("Quest Journal" in text and "First Steps" in text, text[:400])
        await step(report, "/quests shows the beginner path's first stage", journal())

        # ---- 4b. the world closed for maintenance (v1.0.0-rc.41) ---------------
        # Not left to the generic sweep: pressing this leaf shuts every other
        # door, so it is driven here, where the run can prove the refusal and
        # then open the world again before anything else is pressed. The admin
        # groups are hub leaves rather than slash subcommands, so it is pressed
        # the way a GM would press it - on the panel.
        async def lockdown():
            async def flip(enabled: bool, reason: str) -> str:
                panel = await open_hub(gm, channels["bot-logs"], "admin", env=env)
                await panel.goto("Operations", env=env)
                custom_id = await find_leaf_button(panel, "Lockdown")
                expect(custom_id, f"no Lockdown leaf on {panel.page_title()!r}: {panel.labels()}")
                result = await gm.click(panel.message(), custom_id=custom_id)
                result = await answer_steps(
                    gm, result,
                    picks={"Choose enabled": "Yes" if enabled else "No"},
                    fields={"Reason": reason},
                )
                return result_text(result)

            closed = await flip(True, "playtest lockdown")
            expect("closed" in closed.casefold(), closed[:300])
            refused = await player.slash(channels["begin-here"], "quests")
            text = result_text(refused)
            expect("maintenance" in text.casefold(), f"a player was not refused while closed: {text[:300]}")
            expect("playtest lockdown" in text, f"the operator's reason did not reach the player: {text[:300]}")
            opened = await flip(False, "")
            expect("open" in opened.casefold(), opened[:300])
            back = await player.slash(channels["begin-here"], "quests")
            expect("maintenance" not in result_text(back).casefold(), result_text(back)[:300])
            return "closed, the player refused with the reason, reopened"
        await step(report, "/admin → Operations → Lockdown closes the world and opens it again", lockdown())

        # ---- 4. every hub answers with a panel ----------------------------------
        async def hubs():
            opened = []
            for command in _HUB_COMMANDS:
                result = await player.slash(channels["begin-here"], command.name)
                expect(result.response is not None and result.response.components, f"/{command.name} drew no panel: {result_text(result)[:200]}")
                opened.append(command.name)
            menu = await player.slash(channels["begin-here"], "menu")
            expect(menu.response is not None and menu.response.components, "no menu")
            return opened
        opened = await step(report, "every hub command and /menu answer with a panel", hubs())
        if opened:
            report.add("PASS", f"{len(opened)} hubs opened", ", ".join(opened))

        # ---- 5. home, and the doors it hides ------------------------------------
        async def inside():
            panel = await open_hub(player, channels["begin-here"], "family")
            text = panel.text()
            expect("🔒 Enter — you are already inside" in text, text[:600])
            expect(section_button(panel.message().components, "Enter") is None, "Enter is drawn while inside")
            expect(section_button(panel.message().components, "Leave") is not None, "Leave is not drawn while inside")
            return panel
        panel = await step(report, "/family inside the household hides Enter and prints why", inside())

        async def errand():
            hearth = await open_hub(player, channels["begin-here"], "family")
            await hearth.goto("Hearth")
            handed = await hearth.press("Errand")
            await settle_patiently(env)
            text = result_text(handed) + "\n" + hearth.text()
            expect("❌" not in text, text[:500])
            expect("asks something of you: **" in text and "/quests" in text, "the errand was not handed over as a quest: " + text[:500])
            return text.split("asks something of you: **", 1)[1].split("**", 1)[0]
        errand_title = await step(report, "/family → Hearth → Errand hands over a household errand, one at a time", errand())
        if errand_title:
            report.add("PASS", "the errand", errand_title)

        town = None

        async def leave():
            nonlocal town
            confirm = await panel.press("Leave")
            yes = await player.click(confirm.response.message, label="Yes, Leave")
            await settle_patiently(env)
            text = result_text(yes) + "\n" + panel.text()
            expect("🚪 Left" in text, text[:600])
            town = text.split("returned to **", 1)[1].split("**", 1)[0]
            journal_thread = _thread_named_for(channels["expeditions"], player)
            expect(journal_thread is not None, "no expedition journal opened after leaving")
            return journal_thread
        journal_thread = await step(report, "/family → Leave (confirmed) returns to the street and opens the expedition journal", leave())
        if town:
            report.add("PASS", "the household's town is named", town)

        # ---- 6. typed play ------------------------------------------------------
        async def typed():
            expect(journal_thread is not None)
            sent = await player.send(journal_thread, f"{SETTINGS.typed_play_prefix} I explore")
            replies = await bot_replies_after(env, journal_thread, sent, bot_id)
            expect(replies, "the bot did not answer a typed `$ I explore` in the expedition journal")
            text = "\n".join(message_text(m) for m in replies)
            expect("can't be done" not in text and "Create your cultivator" not in text, text[:400])
            return text[:160].replace("\n", " ")
        note = await step(report, "`$ I explore` typed in the expedition journal routes to /explore", typed())
        if note:
            report.add("PASS", "the reply, for the record", note)

        async def shorthand():
            word = SETTINGS.typed_play_shorthand
            if not word:
                report.add("SKIP", "the shorthand is disabled in this environment")
                return
            sent = await player.send(channels["begin-here"], f"{word} cooldowns")
            replies = await bot_replies_after(env, channels["begin-here"], sent, bot_id)
            expect(replies, f"the bot did not answer `{word} cooldowns` in an ordinary channel")
            expect("Cooldowns" in "\n".join(message_text(m) for m in replies))
        await step(report, "the shorthand names a command from any channel", shorthand())

        async def cooldowns():
            result = await player.slash(channels["begin-here"], "cooldowns")
            text = result_text(result)
            expect("**Waiting**" in text and "**Ready now**" in text, text[:600])
            expect("nothing." not in text.split("**Ready now**")[0], "no live wait after an explore")
        await step(report, "/cooldowns lists a live wait and a ready action", cooldowns())

        # ---- 7. away, and back ---------------------------------------------------
        async def away():
            travel = await open_hub(player, channels["begin-here"], "travel")
            await travel.goto("Realm Capitals")
            gone = await answer_steps(player, await travel.press("Go"), picks={"Choose world": "Mortal World — Azure Crown Imperial City"})
            await settle_patiently(env)
            text = result_text(gone) + "\n" + travel.text()
            expect("arrives at" in text, "the capital road did not arrive: " + text[:400])
            family = await open_hub(player, channels["begin-here"], "family")
            page = family.text()
            expect("🔒 Enter — the household stands in" in page, page[:600])
            expect(section_button(family.message().components, "Enter") is None, "Enter is drawn away from home")
            return page.split("🔒 Enter — ", 1)[1].splitlines()[0]
        reason = await step(report, "away from the town, /family hides Enter and says where the house stands", away())
        if reason:
            report.add("PASS", "the locked line", reason)

        async def talisman():
            items = await open_hub(player, channels["begin-here"], "items")
            await items.goto("Use Item")
            used = await answer_steps(player, await items.press("Use"), picks={"Choose item": "Hearth-Return Talisman"})
            await settle_patiently(env)
            text = result_text(used) + "\n" + items.text()
            expect("standing inside" in text, text[:600])
            expect("📜 Quest progress: **" in text, "no quest line followed the reply: " + text[:600])
            return text.split("found you: **", 1)[1].split("**", 1)[0] if "found you: **" in text else ""
        found_at = await step(report, "/items → Use Item → Use → Hearth-Return Talisman carries the player home and reports return_home after the reply", talisman())
        if found_at:
            report.add("PASS", "the talisman remembers where it found you", found_at)

        async def inside_again():
            family = await open_hub(player, channels["begin-here"], "family")
            expect("🔒 Enter — you are already inside" in family.text(), family.text()[:600])
            return family
        family = await step(report, "/family after the talisman shows the player inside", inside_again())

        async def hearth():
            expect(family is not None)
            await family.goto("Hearth")
            supported = await family.press("Support")
            await settle_patiently(env)
            text = result_text(supported) + "\n" + family.text()
            expect("❌" not in text, text[:400])
            contributed = await family.press("Contribute")
            expect(contributed.modal, "Contribute should ask the amount in a modal")
            done = await player.submit_modal(contributed, modal_values(contributed, Amount="1"))
            await settle_patiently(env)
            text = result_text(done) + "\n" + family.text()
            expect("go into" in text and "coffers" in text, text[:600])
            return text.split("Your standing:", 1)[1].splitlines()[0].strip() if "Your standing:" in text else ""
        standing = await step(report, "/family → Hearth → Support pays, and Contribute (a modal) fills the coffers", hearth())
        if standing:
            report.add("PASS", "standing after the contribution", standing)

        async def lesson():
            expect(family is not None)
            await family.goto("Hearth")
            first = await family.press("Lesson")
            await settle_patiently(env)
            text = result_text(first) + "\n" + family.text()
            expect("Check: **" in text and " vs TN " in text, "no check was printed: " + text[:600])
            outcome = "pass" if "qualified at level 0" in text else "fail"
            again = await family.press("Lesson")
            await settle_patiently(env)
            refusal = result_text(again) + "\n" + family.text()
            expect("❌" in refusal, "a second ask was not refused: " + refusal[:400])
            expect(("all this lesson holds" if outcome == "pass" else "ask again in") in refusal, refusal[:400])
            return outcome
        outcome = await step(report, "/family → Hearth → Lesson prints the head's check, and a second ask is refused either way", lesson())
        if outcome:
            report.add("PASS", "how the demonstration went (the dice, not the wiring)", outcome)

        async def back():
            expect(family is not None)
            await family.goto("Family")
            confirm = await family.press("Leave")
            await player.click(confirm.response.message, label="Yes, Leave")
            await settle_patiently(env)
            street = await open_hub(player, channels["begin-here"], "family")
            expect(section_button(street.message().components, "Enter") is not None, street.text()[:600])
            entered = await street.press("Enter")
            await settle_patiently(env)
            text = result_text(entered) + "\n" + street.text()
            expect("🏠 Entered" in text, text[:600])
            expect(household is not None and household.mention in text, "the reply does not name the household thread: " + text[:400])
            return street
        street = await step(report, "from the town, /family offers Enter; pressing it comes home and names the household thread", back())

        async def road():
            expect(street is not None)
            confirm = await street.press("Leave")
            await player.click(confirm.response.message, label="Yes, Leave")
            await settle_patiently(env)
            travel = await open_hub(player, channels["begin-here"], "travel")
            gone = await travel.press("Go")
            picker = gone.response.message
            select = select_by_placeholder(picker.components, "Choose destination")
            expect(select is not None, message_text(picker)[:300])
            elsewhere = [o for o in select["options"] if str(o.get("label")) != town]
            expect(elsewhere, f"only {town} is known; nowhere to walk")
            walked = await player.select(picker, [str(elsewhere[0]["value"])], custom_id=str(select["custom_id"]))
            await settle_patiently(env)
            text = result_text(walked) + "\n" + travel.text()
            expect("❌" not in text, text[:400])
            return " ".join(text.split())[:200]
        note = await step(report, "/travel → Go offers the known roads as live options and sets out on one", road())
        if note:
            report.add("PASS", "the road, for the record", note)

        # ---- 8. every leaf of every hub ------------------------------------------
        # Everything above is scripted: a loop whose outcome matters, asserted
        # on. This is generic: every leaf the hubs register, pressed once by a
        # player with no plan, each input step answered with its first option,
        # and one thing held for each - the reply is a result or a designed
        # refusal, never the hub's own failure text and never an exception. A
        # new leaf is covered the day it is registered. Admin goes last, under
        # the GM, and every member picker is answered with a second member who
        # has no character, so nothing here mutes, bans or erases the player
        # the rest of the run walks. The panel is reopened per page so no
        # view times out under a long sweep.
        live = {action.path for definition in hub_registry.REGISTERED_HUBS for page in definition.pages for action in hub_registry._leaf_actions(page)}
        pressed: dict[str, str] = {}
        locked: dict[str, str] = {}
        for definition in sorted(hub_registry.REGISTERED_HUBS, key=lambda d: d.name == "admin"):
            actor = gm if definition.name == "admin" else player
            for page in definition.pages:
                leaves = [action for action in hub_registry._leaf_actions(page) if action.path not in DEFERRED_LEAVES]
                for action in hub_registry._leaf_actions(page):
                    if action.path in DEFERRED_LEAVES:
                        report.add("SKIP", action.path, DEFERRED_LEAVES[action.path])
                if not leaves:
                    continue
                panel = None
                for attempt in (1, 2):
                    try:
                        opened = await open_hub(actor, channels["begin-here"], definition.name, env=env)
                        await opened.goto(page.label, limit=len(definition.pages) + 1, env=env)
                        panel = opened
                        break
                    except Exception as exc:  # noqa: BLE001 - a playtest reports, it does not crash
                        if attempt == 2:
                            report.add("FAIL", f"/{definition.name} → {page.label}", f"{type(exc).__name__}: {exc}")
                        await settle_patiently(env)
                if panel is None:
                    continue
                for action in leaves:
                    name = f"{action.path}  ({definition.name} → {page.label})"
                    try:
                        status, note = await press_leaf(env, hub_registry, panel, action, member=other, channel=channels["begin-here"])
                    except Exception as exc:  # noqa: BLE001 - a playtest reports, it does not crash
                        report.add("FAIL", name, f"{type(exc).__name__}: {exc}")
                        continue
                    report.add("PASS", name, note)
                    (pressed if status == "pressed" else locked)[action.path] = note
                    if not panel.page_title().endswith(page.label):
                        try:
                            panel = await open_hub(actor, channels["begin-here"], definition.name, env=env)
                            await panel.goto(page.label, limit=len(definition.pages) + 1, env=env)
                        except Exception as exc:  # noqa: BLE001 - reported on the next leaf's press
                            report.add("FAIL", f"reopen /{definition.name} → {page.label}", f"{type(exc).__name__}: {exc}")

        async def covered():
            missing = sorted(live - set(pressed) - set(locked) - set(DEFERRED_LEAVES))
            expect(not missing, f"never pressed nor locked: {missing}")
            return f"{len(pressed)} pressed, {len(locked)} locked, {len(DEFERRED_LEAVES)} deferred, of {len(live)} leaves"
        note = await step(report, "every reachable leaf was pressed and every hidden one printed its lock line", covered())
        if note:
            report.add("PASS", "the sweep's count", note)

        # ---- 9. a panel goes quiet ------------------------------------------------
        async def quiet():
            panel = await open_hub(player, channels["begin-here"], "family")
            # The jump settles before it moves the clock and again after; a
            # settle that gives up on the way in leaves the clock where it was,
            # and one on the way out leaves the workers the jump woke still
            # talking to a busy engine. So: quiet first, jump, and if the jump
            # gave up, wait the workers out and jump once more (a second
            # fifteen minutes changes nothing the step holds).
            await settle_patiently(env)
            for _ in range(2):
                try:
                    await env.advance_time(901)
                    break
                except TimeoutError:
                    await settle_patiently(env)
            await settle_patiently(env)
            text = panel.text()
            if "Reopen" in panel.labels():
                reopened = await player.click(panel.message(), label="Reopen")
                await settle_patiently(env)
                expect("Family" in panel.text() or (reopened.response is not None), "Reopen drew nothing")
                return "expired to a Reopen button, and it reopened"
            # Whichever leaf the page draws: after the sweep the player may
            # stand outside the household, so Leave is not always there.
            button = next((n for n in _walk(panel.message().components) if n.get("type") == 2 and n.get("custom_id")), None)
            expect(button is not None, "the panel offers no button to press:\n" + text[:400])
            try:
                await player.click(panel.message(), custom_id=str(button["custom_id"]))
            except SetupError as exc:
                return f"buttons disabled: {exc}"
            raise Failed("the panel still takes presses after its timeout:\n" + text[:400])
        note = await step(report, "a panel left for fifteen minutes goes quiet and can be reopened", quiet())
        if note:
            report.add("PASS", "how it went quiet", note)

        # ---- 10. nothing raised ---------------------------------------------------
        async def clean():
            errors = list(env.errors)
            expect(not errors, "; ".join(f"{type(e).__name__}: {e}" for e in errors)[:800])
        await step(report, "the bot raised nothing along the way", clean())

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--launch", action="store_true", help="build, start and bootstrap a scratch engine for the run")
    parser.add_argument("--keep", action="store_true", help="with --launch, keep the scratch directory")
    args = parser.parse_args()
    proc = None
    tmp = Path(tempfile.mkdtemp(prefix="xianxia-playtest-discord-"))
    try:
        if args.launch:
            proc, url, token = launch_engine(tmp)
            db_path = str(tmp / "playtest.sqlite3")
            bootstrap(url, token, db_path)
        else:
            url = os.environ.get("GAME_ENGINE_URL", "").rstrip("/")
            token = os.environ.get("ENGINE_AUTH_TOKEN", "")
            db_path = os.environ.get("DATABASE_PATH", "")
            if not url or not token or not db_path:
                raise SystemExit("set GAME_ENGINE_URL, ENGINE_AUTH_TOKEN and DATABASE_PATH, or pass --launch")
        report = asyncio.run(run(url, token, db_path))
        print(f"\n{len(report.rows)} steps, {report.failed} failed")
        return 1 if report.failed else 0
    finally:
        stop_engine(proc)
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
