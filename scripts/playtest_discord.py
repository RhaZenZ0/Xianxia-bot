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
import json
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from playtest_common import Report, bootstrap, launch_engine, step, stop_engine  # noqa: E402 - beside this file

GUILD_ID = 900000000000000001
HEALTH_PORT = 18182  # 1..65535 is enforced by the settings; the engine playtest's own port is 18089
# How long a hub panel stays open **under the harness** (v1.0.13). It is a
# setting, so it belongs in the environment block below with every other thing
# this run pins, and the one step that waits a panel out reads it back through
# `panel_timeout()` rather than restating it.
#
# It is bounded from both sides and neither bound is arbitrary. **Long enough**
# that the leaf sweep never expires a panel out from under itself: a page is
# opened once and every leaf on it pressed, and an expired panel disables its
# own controls, which the sweep correctly reports as a failure. **Short enough**
# that jumping the clock past it is cheap: the jump wakes every periodic worker
# for that much virtual time, and at the shipped 120 minutes the quiet step went
# from instant to minutes of woken workers for no extra proof. One minute is too
# short at the other end - a view timer that near counts as runnable and the
# settle after it never completes (the rc.35 lesson), which is measured, not
# guessed. Fifteen is what the harness in fact ran against for twenty-six
# releases before the setting existed.
PANEL_IDLE_MINUTES = 15
CONTROL_TOKEN = "simcord-playtest-control"  # the GM dashboard's shared secret with the bot
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
    "/reset": (
        "beginning again (v1.0.1): the leaf removes the cultivator every leaf "
        "after it needs, and the sweep confirms a confirm - pressed here it "
        "would empty the run. Driven explicitly in section 9b instead, last of "
        "all, where losing the character costs nothing."
    ),
    "/seclusion start": (
        "the closed-door lockout (v1.0.0-rc.56): a retreat refuses every "
        "other command until the player emerges, so pressing this leaf mid-"
        "sweep would refuse every leaf after it - the same shape as lockdown. "
        "Driven explicitly in section 4c, which shuts the doors, proves a "
        "player is refused, proves the way out is not, and emerges again."
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
        "HUB_PANEL_IDLE_MINUTES": str(PANEL_IDLE_MINUTES),
        # The bot hosts the GM dashboard's control plane on the health port;
        # without a token `HealthServer` answers 404 and section 2b cannot run.
        "BOT_CONTROL_TOKEN": CONTROL_TOKEN,
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
    """The first live select whose placeholder starts with `prefix`.

    **Disabled ones are not selects to answer (v1.0.12).** SimCord refuses a
    click on one - *"a real user could not interact with it"* - and it is right
    to: the control is on screen to say why it is empty, not to be used. Both
    answerers below call `prefix=""`, meaning *any* select on the message, and
    a message may be the action's own **result** rather than a question it is
    asking; see `answer_generically`.
    """
    for node in _walk(components):
        if node.get("disabled"):
            continue
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
                       limit: int = 6, confirm: bool = False) -> Any:
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
        if confirm and message is not None:
            # A danger leaf (Close, Withdraw) asks "Are you sure?" first; with
            # confirm the player says yes, once per button.
            button = next((n for n in _walk(message.components) if n.get("type") == 2 and not n.get("disabled")
                           and str(n.get("custom_id")) not in answered and str(n.get("label") or "").startswith("Yes, ")), None)
            if button is not None:
                answered.add(str(button["custom_id"]))
                result = await actor.click(message, custom_id=str(button["custom_id"]))
                continue
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
    nothing to choose from. Returns the last result and how it was reached.

    **A disabled control is not an input step (v1.0.12).** The scans below skip
    one, because the sweep reads the *result's* message and a result may carry
    controls of its own: a resolved `/battle challenge` posts a `BattleView`
    whose two selects are `disabled=not available`, so a cultivator with no Law
    techniques and nothing to drink gets two dead pickers with a single
    explanatory option each. Taking the first select on the message could not
    tell that from the picker the action is actually asking about, and SimCord
    refused the click with *"That component is disabled - a real user could not
    interact with it"*, which is exactly right: a real user could not.

    It had never come up because the challenge had never *resolved* in a sweep
    - at realm 0 it finds no target - and v1.0.12's realm raise is what reached
    it. That is rc.58's `REFUSAL_ONLY_OPERATIONS` lesson on the Discord side:
    **driven means resolved**, and a leaf only ever driven into a refusal has
    had only its refusal proved."""
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
        button = next((n for n in _walk(components) if n.get("type") == 2 and not n.get("disabled")
                       and str(n.get("custom_id")) not in answered
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
    from app.bot.admin.server_setup import (
        BASE_CATEGORY_NAMES,
        CATEGORY_ORDER,
        SERVER_AUCTION_CATEGORY,
        SERVER_BASE_CATEGORY,
        SERVER_EVENT_CATEGORY,
        SERVER_STALL_CATEGORY,
        SERVER_REALM_CATEGORY,
        SERVER_WORLD_CATEGORY,
    )
    from app.bot.runtime import CULTIVATOR_ROLE_NAME, DB, ENGINE, WORLD
    from app.bot.runtime import _realm_access_role_name
    from app.rules.realm_hubs import REALM_HUBS, realm_presence_role_name
    from app.bot.bot import bot
    from app.bot.runtime import SETTINGS
    from app.bot.services import GUILD
    from app.bot import hubs as hub_registry  # `hubs` is section 4's step below
    from app.bot.hubs import panel_timeout
    from app.bot.surface import _HUB_COMMANDS, TREE_COMMANDS

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
        # `#bugs` is a forum channel with `available_tags`, which simcord 2.0.1 does
        # not implement on channel create - and because that comes back as its own
        # `UnsupportedField` rather than a `discord.HTTPException`, the warning path
        # in `ensure_bugs_forum_channel` (which handles a real server without
        # Community enabled) never catches it and Full Setup below dies on it. So
        # the harness stages the channel: setup then *finds* it by name and never
        # calls `create_forum`. This is the one thing in the layout the fake cannot
        # model, and staging it is what keeps the rest of the path drivable.
        guild.create_forum_channel("bugs")
        admin_role = guild.create_role("Admin", permissions=discord.Permissions(administrator=True))
        gm = guild.add_member(env.create_user("GM"), roles=[admin_role])
        player = guild.add_member(env.create_user("Player One"))
        other = guild.add_member(env.create_user("Player Two"))  # no character: every member picker's answer
        bot_id = int(bot.user.id)

        # ---- 1. boot -----------------------------------------------------------
        async def boot():
            # The set, not a count (v1.0.12). This was `9 + len(_HUB_COMMANDS)`,
            # and the 9 was how many roots `register_command_surface` happened
            # to name - so v1.0.9's `/locked` made the boot step red and nobody
            # saw it for three releases, because a harness is a script and not
            # CI. `TREE_COMMANDS` is a name now, so what the tree registers and
            # what this expects cannot differ.
            wanted = set(TREE_COMMANDS) | {c.name for c in _HUB_COMMANDS}
            synced = [c.name for c in bot.tree.get_commands(guild=GUILD)]
            expect(set(synced) == wanted,
                   f"synced and registered disagree: missing {sorted(wanted - set(synced))}, "
                   f"unexpected {sorted(set(synced) - wanted)}")
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
            # No modal since v1.0.0-rc.59: the command used to ask for one
            # category name, and base channels sit in five now.
            chosen = await choose(gm, picked, "Choose action", label="Validate / bind existing base Xianxia channels")
            await settle_patiently(env)
            text = result_text(chosen) + "\n" + panel.text()
            for name in BASE_CHANNEL_SPECS:
                expect(channels[name].mention in text or f"#{name}" in text, f"{name} not named in:\n{text[:800]}")
            expect("Missing" not in text and "Could not" not in text, text[:800])
            return text
        await step(report, "/admin → Server → Basechannels → bind connects every base channel", bind_channels())

        # ---- 2b. the layout the dashboard owns ---------------------------------
        # Every provisioning helper defaults to `create_missing=False`, and the ONE
        # caller that passes True is the GM dashboard's Full Setup - so no slash
        # command and no hub button can reach `guild.create_category`, and until
        # v1.0.0-rc.52 nothing in either harness did either: the categories, the four
        # realm capitals, the nine auction channels and the four per-world events
        # channels were provisioned by code no test had ever run.
        #
        # This drives it over the bot's **own control plane** - the same HTTP wire the
        # dashboard posts to (`POST /control/discord`, `X-Xianxia-Control`), on the
        # health port `_configure` set - rather than by calling the handler. The
        # harness's rule is that a loop goes through a surface; the control plane is a
        # surface, it is simply not a Discord one.
        async def _control(action: str) -> dict[str, Any]:
            body = json.dumps({"action": action, "payload": {"reason": "playtest"}}).encode("utf-8")
            request = urllib.request.Request(
                f"http://127.0.0.1:{HEALTH_PORT}/control/discord", data=body, method="POST",
                headers={"Content-Type": "application/json", "X-Xianxia-Control": CONTROL_TOKEN},
            )
            def post() -> dict[str, Any]:
                # A refusal carries its reason in the body; urlopen raises on 4xx,
                # so without this a step reports "HTTP Error 403" and nothing about
                # what was actually refused.
                try:
                    return dict(json.loads(urllib.request.urlopen(request, timeout=30).read().decode("utf-8")))
                except urllib.error.HTTPError as exc:
                    return {"ok": False, "status": exc.code, "body": exc.read().decode("utf-8", "replace")[:600]}
            answer = await asyncio.to_thread(post)
            expect(answer.get("ok"), str(answer)[:600])
            await settle_patiently(env)
            return answer

        def _raise_the_bot_role() -> None:
            """The one precondition a live server has to meet before Full Setup.

            Its last act is reconciling the realm roles, and that refuses outright
            - "Move the bot role above the generated realm roles before syncing" -
            when the bot's own role sits below the roles it just created; the
            endpoint reports that as a 403. SimCord gives the bot a managed role at
            position 1, like a fresh invite. Both the backend model and discord.py's
            cached Role have to move: `guild.me.top_role` reads the cache, and the
            backend is what a later fetch would return.
            """
            for role in env.backend.guilds[guild.id].roles.values():
                if role.managed:
                    role.position = 500
            live = bot.get_guild(guild.id)
            for role in (live.roles if live else []):
                if role.managed:
                    role.position = 500

        async def full_setup():
            _raise_the_bot_role()
            await _control("setup")
            live = bot.get_guild(guild.id)
            expect(live is not None, "the bot has no cached guild to read its layout from")
            categories = {c.name for c in live.categories}
            made = {c.name: c for c in live.text_channels}
            # Read off CATEGORY_ORDER, so a category added later is asserted
            # the day it is declared. SERVER_BASE_CATEGORY is deliberately not
            # in it: v1.0.0-rc.59 retired it, and Setup must *not* make one.
            wanted = CATEGORY_ORDER
            for name in wanted:
                expect(name in categories, f"{name!r} was not created; have {sorted(categories)}")
            expect(SERVER_BASE_CATEGORY not in categories,
                   f"the retired {SERVER_BASE_CATEGORY!r} was created; have {sorted(categories)}")
            ordered = {c.name: c.position for c in live.categories if c.name in set(wanted)}
            expect(list(sorted(ordered, key=ordered.get)) == list(wanted),
                   f"categories are out of order: {sorted(ordered.items(), key=lambda kv: kv[1])}")
            for channel_name, spec in BASE_CHANNEL_SPECS.items():
                sits_in = getattr(made.get(channel_name), "category", None)
                expect(getattr(sits_in, "name", None) == BASE_CATEGORY_NAMES[spec.category],
                       f"#{channel_name} sits in {getattr(sits_in, 'name', None)!r}, "
                       f"not {BASE_CATEGORY_NAMES[spec.category]!r}")
            for world, hub in REALM_HUBS.items():
                capital, feed = str(hub["channel_name"]), str(hub["events_channel_name"])
                expect(capital in made, f"no capital channel for {world}")
                expect(feed in made, f"no events channel for {world}")
                expect(getattr(made[capital].category, "name", None) == SERVER_REALM_CATEGORY,
                       f"#{capital} sits in {getattr(made[capital].category, 'name', None)!r}")
                expect(getattr(made[feed].category, "name", None) == SERVER_EVENT_CATEGORY,
                       f"#{feed} sits in {getattr(made[feed].category, 'name', None)!r}")
                # v1.7.0: the world's market, in its own category, read-only -
                # the access role sees it and cannot post in it.
                market = str(hub["stalls_channel_name"])
                expect(market in made, f"no market-stalls channel for {world}")
                expect(getattr(made[market].category, "name", None) == SERVER_STALL_CATEGORY,
                       f"#{market} sits in {getattr(made[market].category, 'name', None)!r}")
                overwrites = made[market].overwrites or {}
                access = discord.utils.get(live.roles, name=_realm_access_role_name(world))
                expect(access is not None and getattr(overwrites.get(access), "view_channel", None) is True,
                       f"#{market} never allowed {_realm_access_role_name(world)}")
                expect(getattr(overwrites.get(access), "send_messages", None) is False
                       and getattr(overwrites.get(live.default_role), "send_messages", None) is False,
                       f"#{market} is not read-only")
            auctions = sorted(c.name for c in live.text_channels
                              if getattr(c.category, "name", None) == SERVER_AUCTION_CATEGORY)
            expect(len(auctions) == 9, f"{len(auctions)} auction channels, expected 9: {auctions}")
            rows = {str(r["world_name"]) for r in await DB.get_world_event_channels(guild.id)}
            expect(rows == set(REALM_HUBS), f"bound worlds {sorted(rows)}")
            markets = {str(r["world_name"]) for r in await DB.get_stall_channels(guild.id)}
            expect(markets == set(REALM_HUBS), f"bound markets {sorted(markets)}")

            # The gate actually closed, not merely "no exception". SimCord gives
            # the bot every permission except administrator, so a channel
            # overwrite applies to it too - which is how the first run of this
            # step found that the bot denied @everyone before allowing itself,
            # was refused 403 on every call after, and left each capital denied
            # to @everyone with no allow for anybody.
            by_name = made
            for world, hub in REALM_HUBS.items():
                for name, role_name in ((str(hub["channel_name"]), realm_presence_role_name(world)),
                                        (str(hub["events_channel_name"]), _realm_access_role_name(world))):
                    overwrites = by_name[name].overwrites or {}
                    expect(getattr(overwrites.get(live.default_role), "view_channel", None) is False,
                           f"#{name} does not deny @everyone")
                    role = discord.utils.get(live.roles, name=role_name)
                    expect(role is not None and getattr(overwrites.get(role), "view_channel", None) is True,
                           f"#{name} never allowed {role_name} - the bot was probably locked out mid-gate")
            # 🗺️ Cultivation World behind having played (v1.0.11), held the
            # same way and for the same reason: a bare "no exception" would
            # pass just as well for a gate that denied @everyone and allowed
            # nobody, which is the state rc.52 found in the capitals.
            cultivator = discord.utils.get(live.roles, name=CULTIVATOR_ROLE_NAME)
            expect(cultivator is not None, "Full Setup never created the cultivator role")
            world_category = next((c for c in live.categories if c.name == SERVER_WORLD_CATEGORY), None)
            expect(world_category is not None, f"no {SERVER_WORLD_CATEGORY} category")
            for target in [world_category] + [by_name[n] for n in ("player-homes", "expeditions")]:
                overwrites = target.overwrites or {}
                expect(getattr(overwrites.get(live.default_role), "view_channel", None) is False,
                       f"{target.name} does not deny @everyone")
                expect(getattr(overwrites.get(cultivator), "view_channel", None) is True,
                       f"{target.name} never allowed {CULTIVATOR_ROLE_NAME} - the bot was probably locked out mid-gate")
                # The read-only anchors stay read-only: `set_permissions(**perms)`
                # replaces an overwrite rather than merging, so a bare
                # view_channel=False would have taken `send_messages=False`
                # off @everyone with it.
                if target is not world_category:
                    expect(getattr(overwrites.get(live.default_role), "send_messages", None) is False,
                           f"#{target.name} is no longer read-only: the gate replaced the anchor's own overwrite")
            return (f"{len(wanted)} categories in order, every base channel in its own, "
                    f"4 capitals, 4 world feeds, 4 markets, {len(auctions)} auction channels, all gated")
        built = await step(report, "the dashboard's Full Setup creates every category and per-world channel", full_setup())
        if built:
            report.add("PASS", "what Full Setup built", built)

        async def setup_is_idempotent():
            def layout() -> set[int]:
                live = bot.get_guild(guild.id)
                return {c.id for c in live.categories} | {c.id for c in live.text_channels}
            before = layout()
            await _control("repair")
            after = layout()
            expect(after == before, f"Repair made {len(after - before)} new channel(s)/category(ies)")
        await step(report, "Repair over the same layout makes nothing new", setup_is_idempotent())

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
            # "Before the Door" is `beginner_household`, the stage `grantBeginnerPathTx`
            # hands over in the transaction that made the character. Until
            # v1.0.0-rc.45 this looked for "First Steps" instead - the retired
            # orphan, which was never held and only ever sat under "Available".
            expect("Quest Journal" in text and "Before the Door" in text, text[:400])
            # And nothing a roster hands over is on that list. This is what the
            # first sweep after rc.45 printed: ten examinations offered to a
            # character seconds old, `The Expert's Toxicity` among them, and an
            # accept select built from the same list - and accepting one is
            # what stops its hall ever offering it, because
            # `grantOrdinaryQuestTx` reads an already-held quest as "no".
            offered = text.split("**Available**", 1)[1] if "**Available**" in text else ""
            for handed in ("Tier 3", "Tier 2", "Tier 1", "Out of the Gate",
                           "Iron for the Hearth", "A Road Toward a Sect"):
                expect(handed not in offered,
                       f"the journal offers {handed!r}, which a roster exists to hand over: {offered[:200]}")
        await step(report, "/quests shows the beginner path's first stage and offers no roster quest", journal())

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

        # ---- 4c. the doors close behind a retreat (v1.0.0-rc.56) ---------------
        # Not left to the generic sweep for lockdown's reason: a retreat
        # refuses every other command until the player emerges. Here the run
        # can prove the three things that matter - that it refuses, that the
        # way out is never refused, and that the doors open again - before
        # anything else is pressed.
        async def closed_doors():
            panel = await open_hub(player, channels["begin-here"], "cultivation", env=env)
            await panel.goto("Cultivate", env=env)
            custom_id = await find_leaf_button(panel, "Seclusion Start")
            expect(custom_id, f"no Seclusion Start leaf on {panel.page_title()!r}: {panel.labels()}")
            started = result_text(await answer_steps(
                player, await player.click(panel.message(), custom_id=custom_id),
                picks={"Choose mode": "Qi Cultivation"},
                fields={"Minutes": "120"},
            ))
            expect("could not" not in started.casefold(), f"the retreat raised: {started[:300]}")
            if "seclusion" not in started.casefold():
                # The site has to allow a retreat; a refusal here is designed
                # and says so, and there is nothing to prove about a lockout
                # that never started.
                return f"no retreat could be started here: {started[:160]}"
            # `/me` is a read and stays open; `/tribute` acts and does not.
            # Neither is a hub, so both reach the command tree's own check.
            refused = result_text(await player.slash(channels["begin-here"], "tribute"))
            expect("closed-door" in refused.casefold(),
                   f"a secluded player was not refused: {refused[:300]}")
            sheet = result_text(await player.slash(channels["begin-here"], "me"))
            expect("closed-door" not in sheet.casefold(),
                   f"the sheet was refused behind a closed door: {sheet[:300]}")
            # The way out is a hub leaf, not a slash command: the panel opens
            # (a read, and where the door is drawn) and End is pressed on it.
            panel = await open_hub(player, channels["begin-here"], "cultivation", env=env)
            await panel.goto("Cultivate", env=env)
            end_id = await find_leaf_button(panel, "Seclusion End")
            expect(end_id, f"no Seclusion End leaf on {panel.page_title()!r}: {panel.labels()}")
            # Assert what the door *says*, not the absence of a string. The
            # first version of this checked `"closed-door" not in out` - but
            # the cultivation card grew a Seclusion field in this same release
            # whose text is "closed-door, every other command is locked", and
            # a leaf press redraws the panel, so the step failed on its own
            # feature. A positive assertion cannot collide that way.
            out = result_text(await player.click(panel.message(), custom_id=end_id))
            expect("emerge from seclusion" in out.casefold(),
                   f"the way out did not open: {out[:600]}")
            back = result_text(await player.slash(channels["begin-here"], "tribute"))
            expect("closed-door" not in back.casefold(),
                   f"still locked out after emerging: {back[:300]}")
            return "shut, the player refused, the sheet and the way out open, emerged"
        await step(report, "/cultivation → Cultivate → Seclusion shuts the doors and opens them again", closed_doors())

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

        # ---- 4a. the daily five are one step (v1.3.2) ---------------------------
        async def daily_row():
            from app.bot.surface import DAILY_ACTIONS
            menu = await player.slash(channels["begin-here"], "menu")
            panel = Panel(player, channels["begin-here"], menu.response)
            for label in ("Cultivate", "Explore", "Hunt", "Forage", "Mine"):
                expect(section_button(panel.message().components, label) is not None,
                       f"the menu draws no {label!r} in its Daily row; it offers {panel.labels()}")
            await panel.press("Cultivate")
            text = panel.text()
            for failure in WIRING_FAILURE_TEXTS:
                expect(failure not in text, f"the Daily Cultivate press raised: {text[:600]}")
            expect("Cultivation" in text, f"the Daily press did not open the cultivation hub in place: {text[:600]}")
            expect("cultivat" in text.lower() and ("essence" in text.lower() or "again in" in text.lower() or "cooldown" in text.lower()),
                   f"the Daily press drew the hub and ran nothing: {text[:600]}")
            for root in DAILY_ACTIONS:
                result = await player.slash(channels["begin-here"], root)
                reply = result_text(result)
                expect(reply.strip(), f"/{root} answered nothing")
                for failure in WIRING_FAILURE_TEXTS:
                    expect(failure not in reply, f"/{root} raised: {reply[:400]}")
            return list(DAILY_ACTIONS)
        daily = await step(report, "the menu's Daily row runs a leaf in one tap and each of the five is a slash command", daily_row())
        if daily:
            report.add("PASS", "daily five", ", ".join("/" + r for r in daily))

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

        # ---- 6b. the curriculum, and then past it ---------------------------------
        # v1.0.9 gave a page a **third** state: a leaf can be drawn, hidden with
        # its own `🔒` lock line (the engine would refuse it here), or held back
        # by the curriculum - which prints **one collapsed line per page** and
        # no line of its own. The sweep below knows two states, so every
        # curriculum-held leaf was "neither drawn nor locked": 97 of 245 leaves,
        # and the run went red for three releases with nothing in CI to say so.
        #
        # Both halves ship together on purpose. The curriculum is asserted here,
        # at realm 0, where it is true; and then the player is raised past its
        # own ceiling so the sweep presses every leaf exactly as it did before.
        # v1.0.9 states that gating is **advertising, never a bound**, so a
        # harness that is proving *wiring* must not be stopped by it - and a
        # harness that simply counted the held-back leaves as covered would be
        # trading one blindness for another.
        def _curriculum_roster() -> dict[str, Any]:
            return dict(WORLD.data.get("feature_unlocks") or {})

        async def curriculum():
            # The page is chosen off the roster, not named here: a page this
            # harness picks by hand is a page that stops holding anything back
            # the day somebody retunes the content, and the step would then
            # pass for a curriculum that had been deleted.
            pages = (_curriculum_roster().get("pages") or {})
            expect(pages, "the curriculum roster holds no page back; the harness cannot prove it works")
            key = max(pages, key=lambda k: (int(pages[k]), k))
            hub_name, _, page_label = str(key).partition(" / ")
            panel = await open_hub(player, channels["begin-here"], hub_name.strip())
            await panel.goto(page_label.strip(), limit=12, env=env)
            text = panel.text()
            expect("/locked" in text,
                   f"{key!r} opens at realm {pages[key]} and says nothing about it at realm 0:\n{text[:700]}")
            summary = next((ln for ln in text.splitlines() if "/locked" in ln), "")
            listed = result_text(await player.slash(channels["begin-here"], "locked"))
            expect("realm" in listed.lower(), f"/locked lists nothing a player can act on:\n{listed[:500]}")
            return f"{key}: {summary.strip()[:130]}"
        held = await step(report, "at realm 0 a page says how many doors wait, and /locked lists them", curriculum())
        if held:
            report.add("PASS", "what the collapsed line reads", held)

        async def open_the_curriculum():
            """Raise the player to the curriculum's own ceiling.

            The number is read off `feature_unlocks`, never written down here:
            a roster that later authors a deeper floor raises this with it, and
            a harness carrying its own copy of a content number is the fault
            three of this session's gates were written for.
            """
            roster = _curriculum_roster()
            floors = [int(v) for v in list((roster.get("pages") or {}).values())
                      + list((roster.get("leaves") or {}).values())]
            expect(floors, "the curriculum roster is empty; the harness cannot know what to open")
            ceiling = max(floors)
            await ENGINE.action("admin.player.set_realm", int(gm.id), {
                "user_id": int(player.id), "realm_index": ceiling, "phase": 1,
                "reason": "playtest: open every curriculum door",
            })
            await settle_patiently(env)
            panel = await open_hub(player, channels["begin-here"], "cultivation")
            expect("/locked" not in panel.text(),
                   f"realm {ceiling} is the roster's ceiling and the page still holds doors back:\n{panel.text()[:500]}")
            return f"realm {ceiling}, the roster's own ceiling, so every curriculum door is drawn"
        opened = await step(report, "the curriculum opens fully at the realm its roster tops out at", open_the_curriculum())
        if opened:
            report.add("PASS", "what the sweep below presses at", opened)

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

        # ---- 7b. a stall in the street (v1.5.0) ----------------------------------
        # Scripted rather than left to the sweep, because the sweep fills the
        # list modal with a canned item and gets the designed refusal: this is
        # the loop that matters - open, lay goods on it, see it on the board,
        # take them back, take it down - driven at the curriculum's ceiling,
        # which section 6 has already raised the player to.
        async def stall():
            # Driven through the panel: /stall is a hub group, never a tree
            # command, so v1.5.0's slash calls could not reach it at all.
            was_at = str((await DB.get_character(int(player.id)) or {}).get("location") or "")
            await ENGINE.action("admin.player.teleport", int(gm.id), {"user_id": int(player.id), "location": "Greenriver Town", "reason": "playtest: a city's street for the stall"})
            # A pill needs the Alchemy certificate to go on a stall (v1.7.1) and
            # nothing earlier in this run sits an examination, so the pill is
            # withheld and a raw material is what is sold.
            await ENGINE.action("admin.player.adjust_item", int(gm.id), {"user_id": int(player.id), "item_id": "recovery_pill", "quantity": 1, "reason": "playtest: a pill the stall must refuse"})
            await ENGINE.action("admin.player.adjust_item", int(gm.id), {"user_id": int(player.id), "item_id": "beast_core", "quantity": 3, "reason": "playtest: goods for the stall"})
            await settle_patiently(env)
            economy = await open_hub(player, channels["begin-here"], "economy", env=env)
            await economy.goto("Market Stalls", env=env)

            async def leaf(label: str, **kwargs: Any) -> str:
                result = await answer_steps(player, await economy.press(label), confirm=True, **kwargs)
                await settle_patiently(env)
                return result_text(result) + "\n" + economy.text()

            opened = await leaf("Open", fields={"Name": "Sim's Table"})
            expect("is set up in" in opened, f"Open did not set the stall up: {opened[:400]}")
            # The List picker offers only what the stall will take (v1.7.1), so
            # the pill is not among its options and Status names it instead.
            listed = await leaf("List", picks={"": "Beast Core"}, fields={"Quantity": "3", "Price": "6"})
            expect("Listing **#" in listed, f"List did not lay the goods out: {listed[:400]}")
            # v1.7.0: the stall's card is in its world's market channel.
            market_row = next((r for r in await DB.get_stall_channels(guild.id) if str(r["world_name"]) == "Mortal World"), None)
            card = next((r for r in await DB.list_stall_cards(guild.id) if int(r["user_id"]) == int(player.id)), None)
            expect(market_row is not None and card is not None and int(card["channel_id"]) == int(market_row["channel_id"]),
                   f"no card for the stall in the Mortal World's market channel: {card} / {market_row}")
            board = await leaf("Board")
            expect("Sim's Table" in board and "Beast Core" in board, f"the board does not show the stall: {board[:400]}")
            status = await leaf("Status")
            expect("On the stall" in status, f"Status does not list the goods: {status[:400]}")
            expect("Not for your stall yet" in status and "Recovery Pill" in status, f"Status does not say the pill needs a certificate: {status[:600]}")
            withdrawn = await leaf("Withdraw", picks={"": "Beast Core"})
            expect("back into your bag" in withdrawn, f"Withdraw did not return the goods: {withdrawn[:400]}")
            closed = await leaf("Close")
            expect("is taken down" in closed, f"Close did not take the stall down: {closed[:400]}")
            gone = next((r for r in await DB.list_stall_cards(guild.id) if int(r["user_id"]) == int(player.id)), None)
            expect(gone is None, f"the card outlived the stall: {gone}")
            if was_at:
                await ENGINE.action("admin.player.teleport", int(gm.id), {"user_id": int(player.id), "location": was_at, "reason": "playtest: back where the run had them"})
                await settle_patiently(env)
            return "opened, a pill refused for want of a certificate, cores listed, on the board, withdrawn, closed"
        await step(report, "/economy → Market Stalls: a stall is opened, stocked, seen on the board, emptied and taken down", stall())

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
            # The window is **read**, never written down here (v1.0.13). This
            # used to jump **901 seconds** - not the deadline but an *encoding*
            # of it, one second past the `timeout=900` five production files
            # spelled out, so no search for the number could have found that
            # seventh copy; raising the default to 120 minutes left it moving a
            # panel an eighth of the way to its deadline and then reporting
            # that the panel would not expire. `PANEL_IDLE_MINUTES` at the top
            # of this file is what the run is configured with and why; the step
            # only has to agree with it, which reading `panel_timeout()` is.
            window = panel_timeout()
            expect(window is not None,
                   "HUB_PANEL_IDLE_MINUTES is 0 for this run, so no panel ever expires and this step "
                   "cannot be driven; PANEL_IDLE_MINUTES at the top of this file sets it")
            panel = await open_hub(player, channels["begin-here"], "family")
            # The jump settles before it moves the clock and again after; a
            # settle that gives up on the way in leaves the clock where it was,
            # and one on the way out leaves the workers the jump woke still
            # talking to a busy engine. So: quiet first, jump, and if the jump
            # gave up, wait the workers out and jump once more (a second jump
            # past the same deadline changes nothing the step holds).
            await settle_patiently(env)
            for _ in range(2):
                try:
                    await env.advance_time(int(window) + 1)
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
        note = await step(report, "a panel left past its idle window goes quiet and can be reopened", quiet())
        if note:
            report.add("PASS", "how it went quiet", note)

        # ---- 9b. beginning again (v1.0.1) ------------------------------------------
        # Last of all, for erasure's reason in the engine half: it is the one
        # leaf that leaves nothing behind. What this proves is the wiring the
        # engine half cannot see - that the leaf is on the page, that its
        # confirm is answered, that the engine is reached and that whichever
        # answer comes back is rendered. Since v1.0.14 what the sweep left
        # behind no longer refuses - a reset releases it - so this is usually
        # the success path; the refusal is still accepted for an anonymise
        # column a reset has not been told how to release. The success path
        # is also driven end to end by scripts/playtest_engine.py, on an
        # account created for it.
        async def begin_again():
            panel = await open_hub(player, channels["begin-here"], "character", env=env)
            await panel.goto("Samsara", env=env)
            custom_id = await find_leaf_button(panel, "Reset")
            expect(custom_id, f"no Reset leaf on {panel.page_title()!r}: {panel.labels()}")
            # "reset" is in `_DANGER_ACTION_WORDS`, so the leaf does not run:
            # it sends a confirm step with its own buttons. `answer_steps`
            # walks selects and modals, and the generic sweep deliberately
            # stops at a confirm rather than pressing Yes - which is why the
            # first version of this step read back the confirm prompt itself.
            # Here the Yes is the point, so it is clicked.
            started = await player.click(panel.message(), custom_id=custom_id)
            step = started.response.message if started.response is not None else None
            expect(step is not None, "the reset leaf sent no confirm step")
            expect("Are you sure" in message_text(step),
                   f"the reset leaf ran without asking:\n{message_text(step)[:300]}")
            out = result_text(await answer_steps(
                player, await player.click(step, label="Yes, Reset"),
            ))
            for text in WIRING_FAILURE_TEXTS:
                expect(text not in out, f"the reset leaf raised: {out[:300]}")
            lowered = out.casefold()
            if "mark the world keeps" in lowered:
                return "refused: a mark the world keeps that a reset cannot release yet"
            if "is gone" in lowered and "/begin" in lowered:
                return "reset: the cultivator was taken back and /begin was named"
            raise Failed("the reset leaf answered neither a result nor its designed refusal:\n" + out[:600])
        note = await step(report, "the reset leaf answers a result or its designed refusal", begin_again())
        if note:
            report.add("PASS", "how beginning again went", note)

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


def _exit(code: int) -> None:
    """Leave, whatever is still holding the interpreter open.

    `SystemExit` runs `threading._shutdown()`, which **joins every non-daemon
    thread** - and this harness boots the real bot, which keeps aiosqlite
    connections open, and aiosqlite runs one non-daemon thread per connection.
    So a run could print its report, return its exit code, and then hang for
    ever on a thread nobody is going to stop, with the result already on
    screen and the process still alive (v1.0.1).

    `os._exit` skips that shutdown, so it is only correct *after* the report is
    written and `main`'s `finally` has stopped the engine and removed the
    scratch directory - which is why it is here rather than inside `main`.
    stdout is flushed first because `os._exit` does not.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    _exit(main())
