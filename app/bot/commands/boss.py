"""The /boss and /hunter hubs: boss encounters and bounty-hunter pursuers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import discord
from discord import app_commands

from ...rules.advanced_runtime import BOSS_TEMPLATES, boss_encounter_phase, boss_lair
from ...rules.battle import vitality_bar
from ...ops.game_engine import GameEngineError
from ..channels import _report_game_ui_error
from ..hubs import panel_timeout, register_hub_option_hint
from ..registry import registered_group_command
from ..runtime import (
    _explain_engine_error,
    DB,
    ENGINE,
    WORLD,
    _user_action_lock,
    budget_refusal_line,
    current_world_time,
    require_character,
    reply_long,
    serialized_user_action,
)


boss_group = app_commands.Group(
    name="boss",
    description="Run persistent multi-phase party boss encounters",
)

hunter_group = app_commands.Group(
    name="hunter",
    description="Respond to autonomous bounty-hunter pursuits",
)


_RAID_COLOURS = {"active": 0x8E44AD, "victory": 0x2ECC71, "defeat": 0xE74C3C}
_RAID_TITLES = {"active": "👹", "victory": "🏆", "defeat": "💀"}
_RAID_STYLE_LABELS = {"attack": "⚔️ Attack", "guard": "🛡️ Defend", "support": "💚 Support", "technique": "🌌 Technique"}


def _mention_participants(text: str, participants: list[dict[str, Any]]) -> str:
    """The engine names a struck raider by user id; the card names them."""
    for participant in participants:
        uid = str(participant.get("user_id") or "")
        if uid:
            text = re.sub(rf"(?<!<@)\b{uid}\b", f"<@{uid}>", text)
    return text


def _fit(entries: list[str], *, limit: int = 980, noun: str = "raiders") -> str:
    """Entries up to one embed field, with a count of the rest."""
    shown: list[str] = []
    for entry in entries:
        if len("\n".join([*shown, entry])) > limit:
            break
        shown.append(entry)
    if len(shown) < len(entries):
        shown.append(f"-# and {len(entries) - len(shown)} more {noun}")
    return "\n".join(shown)


def _fights_alone(encounter: dict[str, Any]) -> bool:
    party = dict(encounter.get("party") or {})
    return bool(int(party.get("raid_only") or 0)) and len(list(encounter.get("participants") or [])) <= 1


@dataclass
class RaidCard:
    """What the raid card says, apart from how Discord draws it."""

    title: str
    colour: int
    description: str
    fields: list[tuple[str, str]] = field(default_factory=list)
    footer: str = ""
    status: str = "active"

    def value_of(self, name: str) -> str:
        return next((value for key, value in self.fields if key == name), "")


def raid_card(encounter: dict[str, Any], *, events: list[str] | None = None, note: str = "") -> RaidCard:
    """The raid drawn as one card (v1.8.3): the boss's health, its phase,
    the round, and every raider's vitality and whether they have acted.

    v1.8.5 adds what the card still left out: who leads the party or that
    it fights alone, the formation it fights in, what the phase hits and
    guards with, and the reward each raider is owed and whether they have
    taken it.

    Every number is read off the encounter row the engine wrote and the
    template it was started from; nothing here decides anything.
    """
    status = str(encounter.get("status") or "active")
    participants = list(encounter.get("participants") or [])
    template = BOSS_TEMPLATES.get(str(encounter.get("template_key")), {})
    phases = list(template.get("phases") or [])
    phase_index = min(max(0, int(encounter.get("phase_index") or 0)), max(0, len(phases) - 1))
    round_index = int(encounter.get("round_index") or 1)
    party = dict(encounter.get("party") or {})
    formation = dict(encounter.get("formation") or {})
    claimed = {int(row.get("user_id") or 0): bool(int(row.get("claimed") or 0)) for row in list(encounter.get("claims") or [])}

    lines = [_mention_participants(str(event), participants) for event in list(events or [])[:12]]
    if note:
        lines.append(note)
    if not lines:
        lines.append(
            {
                "victory": "The boss has fallen. Every raider has a reward waiting.",
                "defeat": "The party has fallen. The boss keeps its lair.",
            }.get(status, "Each raider acts once a round; the boss answers when the whole party has acted.")
        )
    card = RaidCard(
        title=f"{_RAID_TITLES.get(status, '👹')} Raid #{encounter.get('encounter_id')} — {encounter.get('boss_name', 'Boss')}",
        description="\n".join(f"• {line}" if events else line for line in lines)[:1200],
        colour=_RAID_COLOURS.get(status, _RAID_COLOURS["active"]),
        status=status,
    )
    card.fields.append(("Boss", vitality_bar(int(encounter.get("boss_hp") or 0), int(encounter.get("boss_hp_max") or 0))))
    if phases:
        phase = boss_encounter_phase(encounter)
        threshold = float(phase.get("threshold") or 0)
        shift = f"\n-# shifts below {round(threshold * 100)}% health" if status == "active" and threshold > 0 else ""
        card.fields.append(("Phase", (
                f"**{phase.get('name', 'Unknown')}** • {phase_index + 1}/{len(phases)}\n"
                f"Attack **{int(phase.get('attack') or 0)}** • Defence **{int(phase.get('defense') or 0)}**{shift}"
            )))
    card.fields.append(("Round", f"**{round_index}**"))
    card.fields.append(("Lair", str(encounter.get("location") or "Unknown")))

    # Who leads, and in what formation. A party made for this raid alone
    # (schema 68) is a cultivator fighting by themselves against a weaker
    # boss; the start reply said so and every card after it did not.
    party_lines: list[str] = []
    alone = _fights_alone(encounter)
    if alone:
        party_lines.append("Fought **alone**: a party of one, closed when the raid ends")
    elif party.get("leader_user_id"):
        party_lines.append(f"Led by <@{int(party['leader_user_id'])}>")
    if formation:
        party_lines.append(
            f"Formation **{formation.get('name', 'Unnamed')}** • {str(formation.get('stance') or 'balanced').title()} stance"
            f" • Cohesion **{int(formation.get('cohesion') or 0)}/100**"
        )
    elif party and not alone:
        party_lines.append("-# No formation active: **/combat → Formations** sets one")
    if party_lines:
        card.fields.append(("Party", "\n".join(party_lines)))

    rows: list[str] = []
    for participant in participants:
        uid = int(participant.get("user_id") or 0)
        if status == "victory" and uid in claimed:
            state = "🎁 claimed" if claimed[uid] else "🎁 to claim"
        elif str(participant.get("status")) == "knocked_out":
            state = "💀 down"
        elif status != "active":
            state = "standing"
        elif int(participant.get("acted_round") or 0) >= round_index:
            state = "🛡️ guarding" if int(participant.get("guard") or 0) else "✅ acted"
        else:
            state = "⏳ to act"
        rows.append(
            f"<@{uid}> • {state} • dealt **{int(participant.get('total_damage') or 0)}**\n"
            f"{vitality_bar(int(participant.get('vitality') or 0), int(participant.get('vitality_max') or 0), width=8)}"
        )
    if rows:
        card.fields.append((f"Raid Party ({len(participants)})", _fit(rows)))

    # What each raider is owed on a win: the template the engine pays from
    # (bossTemplatesGo), held equal to this one by the boss-table parity gate.
    if template.get("reward_currency") or template.get("reward_item"):
        reward = [f"**{int(template.get('reward_currency') or 0)}** Low Spirit Stones"]
        if template.get("reward_item") and int(template.get("reward_quantity") or 0) > 0:
            reward.append(f"**{WORLD.item_name(str(template['reward_item']))} ×{int(template['reward_quantity'])}**")
        taken = sum(1 for done in claimed.values() if done)
        tail = f"\n-# {taken}/{len(claimed)} claimed" if status == "victory" and claimed else ""
        card.fields.append(("Reward, each raider", " + ".join(reward) + tail))

    if status == "victory":
        card.footer = f"Press Claim reward, or /boss claim encounter_id:{encounter.get('encounter_id')}"
    elif status == "active":
        card.footer = "The buttons act for whoever presses them • each raider acts once a round"
    return card


def _unlocked_law_techniques(character: dict[str, Any], law_rows: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """The Law techniques this cultivator may use in a raid.

    The engine's own test (bossActActionGo): the technique's Law at or past
    its `requires_stage`, and the character at or past its realm floor. A
    picker offering the rest would offer what the engine refuses (rc.46).
    """
    comprehension = {str(row.get("law_id")): int(row.get("comprehension") or 0) for row in law_rows}
    realm = int(character.get("realm_index") or 0)
    out: list[tuple[str, str, str]] = []
    for tid, definition in sorted(WORLD.law_system.get("techniques", {}).items()):
        law = str(definition.get("law") or "")
        if law not in comprehension:
            continue
        stage = int(WORLD.law_stage(comprehension[law]).get("index", 0))
        if stage >= int(definition.get("requires_stage", 99)) and realm >= int(definition.get("min_realm_index", 999)):
            out.append((str(tid), str(definition.get("name", tid)), str(definition.get("description") or "Law technique")))
    return out


async def _raid_act(
    user_id: int, action_id: str, encounter: dict[str, Any], style: str, technique: str = ""
) -> tuple[dict[str, Any] | None, list[str], str]:
    """One raid action through the engine: (card, events, refusal)."""
    _ = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "boss.act",
            int(user_id),
            {
                "encounter_id": int(encounter["encounter_id"]),
                "style": style,
                "technique": technique.strip(),
                "version": int(encounter["version"]),
            },
            action_id=action_id,
        )
        result = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        return None, [], f"❌ {_explain_engine_error(exc)}"
    # The card is drawn from the row the action just wrote, read by id
    # because a finished raid is no longer the party's active one.
    after = await DB.get_boss_encounter(encounter_id=int(result.get("encounter_id") or encounter["encounter_id"]))
    card = {**encounter, **result, **(after or {})}
    card["status"] = str(result.get("status") or card.get("status") or "active")
    return card, [str(e) for e in list(result.get("events") or [])], ""


def _claim_line(result: dict[str, Any]) -> str:
    return (
        f"🏆 Claimed **{result.get('currency_amount', 0)} Low Spirit Stones** and "
        f"**{WORLD.item_name(str(result.get('item_id', '')))} x{result.get('item_quantity', 0)}**."
    )


async def _raid_claim(user_id: int, action_id: str, encounter_id: int) -> tuple[dict[str, Any] | None, str]:
    try:
        envelope = await ENGINE.authoritative_action(
            "boss.claim", int(user_id), {"id": int(encounter_id)}, action_id=action_id,
        )
    except GameEngineError as exc:
        return None, f"❌ {_explain_engine_error(exc)}"
    return dict(envelope.get("result") or {}), ""


class RaidTechniqueSelect(discord.ui.Select):
    """The presser's own unlocked Law techniques, in a message only they see."""

    def __init__(self, parent: "RaidView", card_message: discord.Message | None, techniques: list[tuple[str, str, str]]) -> None:
        self.parent_view = parent
        self.card_message = card_message
        super().__init__(
            placeholder="Choose a Law technique",
            min_values=1, max_values=1,
            options=[discord.SelectOption(label=name[:100], value=tid[:100], description=text[:100]) for tid, name, text in techniques[:25]],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.dispatch(interaction, "technique", self.values[0], card_message=self.card_message)


class _RaidButton(discord.ui.Button):
    def __init__(self, raid_style: str, label: str, emoji: str, colour: discord.ButtonStyle) -> None:
        super().__init__(label=label, emoji=emoji, style=colour)
        self.raid_style = raid_style

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, RaidView):
            await view.dispatch(interaction, self.raid_style)


class RaidView(discord.ui.LayoutView):
    """The raid card, in the layout the hub panels use (v1.8.5).

    One coloured box: the raid's title, what just happened, the boss's
    health, its phase and round, the party, every raider and the reward,
    with the buttons inside it. The card is shared by the whole party, so a
    press acts for whoever pressed it - never for the cultivator who drew
    the card - and anybody outside the raid is told so privately. Every
    press goes through the engine's `boss.act` / `boss.claim`, which is the
    only thing that decides whether it was allowed; the view redraws the
    card from the row it wrote.
    """

    def __init__(self, encounter_id: int, card: RaidCard, *, quiet: bool = False) -> None:
        super().__init__(timeout=panel_timeout())
        self.encounter_id = int(encounter_id)
        self.card = card
        self.message: discord.Message | None = None
        self.render(quiet=quiet)

    def render(self, *, quiet: bool = False) -> None:
        card = self.card
        self.clear_items()
        box = discord.ui.Container(accent_colour=card.colour)
        box.add_item(discord.ui.TextDisplay(f"## {card.title}\n-# Xianxia RP  ·  Boss Raid"))
        box.add_item(discord.ui.TextDisplay(card.description))
        box.add_item(discord.ui.Separator())
        box.add_item(discord.ui.TextDisplay(f"### Boss\n{card.value_of('Boss')}"))
        where = [f"**Phase** {card.value_of('Phase')}" if card.value_of("Phase") else "",
                 f"**Round** {card.value_of('Round')}  ·  **Lair** {card.value_of('Lair')}"]
        box.add_item(discord.ui.TextDisplay("\n".join(line for line in where if line)))
        box.add_item(discord.ui.Separator())
        for name, value in card.fields:
            if name in ("Boss", "Phase", "Round", "Lair"):
                continue
            box.add_item(discord.ui.TextDisplay(f"### {name}\n{value}"))
        buttons: list[discord.ui.Item[Any]] = []
        if not quiet:
            if card.status == "active":
                for raid_style, emoji, label, colour in (
                    ("attack", "⚔️", "Attack", discord.ButtonStyle.danger),
                    ("guard", "🛡️", "Defend", discord.ButtonStyle.primary),
                    ("support", "💚", "Support", discord.ButtonStyle.success),
                    ("technique", "🌌", "Technique", discord.ButtonStyle.secondary),
                ):
                    buttons.append(_RaidButton(raid_style, label, emoji, colour))
            elif card.status == "victory":
                buttons.append(_RaidButton("claim", "Claim reward", "🎁", discord.ButtonStyle.success))
            if card.status in ("active", "victory"):
                buttons.append(_RaidButton("refresh", "Refresh", "🔄", discord.ButtonStyle.secondary))
        if buttons:
            box.add_item(discord.ui.Separator())
            row = discord.ui.ActionRow()
            for button in buttons:
                row.add_item(button)
            box.add_item(row)
        footer = "These buttons went quiet. **/boss status** draws a fresh card." if quiet else card.footer
        if footer:
            box.add_item(discord.ui.TextDisplay(f"-# {footer}"))
        self.add_item(box)

    def buttons(self) -> list["_RaidButton"]:
        return [item for item in self.walk_children() if isinstance(item, _RaidButton)]

    async def on_timeout(self) -> None:
        if self.message is None or not self.buttons():
            return
        self.render(quiet=True)
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        await _report_game_ui_error(interaction, error, where=f"raid:{self.encounter_id}:{type(item).__name__}")

    async def _redraw(self, interaction: discord.Interaction, encounter: dict[str, Any], *, events: list[str] | None = None,
                      card_message: discord.Message | None = None) -> None:
        view = RaidView(self.encounter_id, raid_card(encounter, events=events))
        if card_message is not None:
            message = await card_message.edit(view=view)
        else:
            message = await interaction.edit_original_response(view=view)
        view.message = message
        self.stop()

    async def dispatch(self, interaction: discord.Interaction, raid_style: str, technique: str = "",
                       *, card_message: discord.Message | None = None) -> None:
        uid = int(interaction.user.id)
        # A technique is chosen in a message only its raider sees, so its
        # answer stays there and the shared card is edited by reference.
        private = card_message is not None
        refusal = budget_refusal_line(uid, "raid_card") if raid_style != "refresh" else None
        if refusal:
            await interaction.response.send_message(refusal, ephemeral=True)
            return
        if not interaction.response.is_done():
            if private:
                await interaction.response.defer(ephemeral=True)
            else:
                await interaction.response.defer()
        async with _user_action_lock(uid):
            encounter = await DB.get_boss_encounter(encounter_id=self.encounter_id)
            if not encounter:
                await interaction.followup.send("This raid no longer exists.", ephemeral=True)
                return
            raiders = {int(p.get("user_id") or 0) for p in list(encounter.get("participants") or [])}
            if raid_style != "refresh" and uid not in raiders:
                await interaction.followup.send("Only the raid party can act on this card.", ephemeral=True)
                return
            if raid_style == "refresh" or (raid_style != "claim" and str(encounter.get("status")) != "active"):
                await self._redraw(interaction, encounter, card_message=card_message)
                return
            if raid_style == "claim":
                result, error = await _raid_claim(uid, f"discord:{interaction.id}:boss.claim", self.encounter_id)
                await interaction.followup.send(error or _claim_line(result or {}), ephemeral=bool(error))
                fresh = await DB.get_boss_encounter(encounter_id=self.encounter_id) or encounter
                await self._redraw(interaction, fresh, card_message=card_message)
                return
            if raid_style == "technique" and not technique:
                c = await DB.get_character(uid)
                techniques = _unlocked_law_techniques(c, await DB.get_law_progress(uid)) if c else []
                if not techniques:
                    await interaction.followup.send(
                        "You have no Law technique unlocked for a raid yet: a Law's stage and your realm open them "
                        "(**/cultivation → Laws**).", ephemeral=True)
                    return
                picker = discord.ui.View(timeout=panel_timeout())
                picker.add_item(RaidTechniqueSelect(self, interaction.message, techniques))
                await interaction.followup.send("Which technique?", view=picker, ephemeral=True)
                return
            card, events, error = await _raid_act(uid, f"discord:{interaction.id}:boss.act", encounter, raid_style, technique)
            if error or card is None:
                await interaction.followup.send(error or "❌ The raid action did not resolve.", ephemeral=True)
                return
            label = _RAID_STYLE_LABELS.get(raid_style, raid_style.title())
            if raid_style == "technique":
                label = f"🌌 {WORLD.law_system.get('techniques', {}).get(technique, {}).get('name', technique)}"
            await self._redraw(interaction, card, events=[f"<@{uid}> — {label}", *events], card_message=card_message)
            if private:
                await interaction.followup.send(f"{label}: the raid card is updated.", ephemeral=True)


async def _send_raid_card(interaction: discord.Interaction, encounter: dict[str, Any], *,
                          events: list[str] | None = None, note: str = "") -> None:
    """The card, with its buttons while there is something to press.

    A Components V2 message carries no content or embeds, only the view.
    """
    view = RaidView(int(encounter.get("encounter_id") or 0), raid_card(encounter, events=events, note=note))
    # Always a followup: it returns the card's own message, where a first
    # response pressed from a hub panel would hand back the panel's.
    if not interaction.response.is_done():
        await interaction.response.defer()
    view.message = await interaction.followup.send(view=view, ephemeral=False, wait=True)


async def law_technique_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """The techniques this raider has unlocked, not every one in the world."""
    c = await DB.get_character(interaction.user.id)
    if not c:
        return []
    needle = current.casefold().strip()
    out: list[app_commands.Choice[str]] = []
    for tid, name, _ in _unlocked_law_techniques(c, await DB.get_law_progress(interaction.user.id)):
        if not needle or needle in name.casefold() or needle in tid.casefold():
            out.append(app_commands.Choice(name=name[:100], value=tid[:100]))
    return out[:25]


@registered_group_command(boss_group, name="list", description="List persistent multi-phase bosses and their required locations")
async def boss_list(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    lines = ["👹 **Boss Encounters**"]
    for key, boss in BOSS_TEMPLATES.items():
        lair, realm_id = boss_lair(boss, WORLD.secret_realms)
        where = lair
        if realm_id:
            # A secret floor (v1.3.0): fought at the realm's entrance by a
            # party whose leader has walked the realm to its last room.
            where = f"{lair} — the floor beneath the {boss['location']}, open once you have walked that realm to its end"
        lines.append(
            f"\n`{key}` — **{boss['name']}** • {where} • every member at **{WORLD.realm_name(int(boss['realm_index']))}** or above"
            f" • {len(boss['phases'])} phases • base HP {boss['max_hp']}"
        )
    lines.append(
        "\n-# The party leader starts a raid with the whole party standing at the lair. "
        "Alone, just press Start: a party of one is formed for the raid and closed when it ends, "
        "and a boss fought alone has less health than one a party faces."
    )
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(boss_group, name="start", description="Start a boss raid - as your party's leader, or alone")
@serialized_user_action
async def boss_start(interaction: discord.Interaction, boss: str) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    _ = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "boss.start",
            interaction.user.id,
            {"template_key": boss},
            action_id=f"discord:{interaction.id}:boss.start",
        )
        result = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    # A lone cultivator is given a party of one for the raid (v1.7.8); the
    # engine says so, and closes it when the raid ends.
    alone = (
        "You face it alone: a party of one was formed for this raid and closes when the raid ends."
        if result.get("solo_party") else ""
    )
    encounter = await DB.get_boss_encounter(encounter_id=int(result.get("encounter_id") or 0)) if result.get("encounter_id") else None
    if not encounter:
        encounter = {**result, "boss_name": result.get("boss_name", "Boss"), "status": "active"}
    await _send_raid_card(interaction, encounter, note=alone)


@boss_start.autocomplete("boss")
async def boss_template_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Every boss, the ones at the player's location first (v0.33.0). The
    engine refuses a boss fought from the wrong place; the label says where
    each one is so the refusal is never the first time a player learns it."""
    c = await DB.get_character(interaction.user.id)
    here = str(c.get("location") or "") if c else ""
    needle = current.casefold().strip()
    choices: list[tuple[int, str, app_commands.Choice[str]]] = []
    for key, boss in BOSS_TEMPLATES.items():
        name = str(boss.get("name", key))
        location = boss_lair(boss, WORLD.secret_realms)[0]
        if needle and needle not in name.casefold() and needle not in key.casefold() and needle not in location.casefold():
            continue
        marker = "here" if location == here else location
        realm = WORLD.realm_name(int(boss.get("realm_index") or 0))
        choices.append((0 if location == here else 1, name, app_commands.Choice(name=f"{name} — {marker}, {realm}+"[:100], value=key[:100])))
    return [choice for _, _, choice in sorted(choices, key=lambda row: row[:2])][:25]


register_hub_option_hint(
    boss_start,
    "boss",
    "No boss encounter is defined in the world content. **/combat → Boss Raids → List** shows the catalogue when there is one.",
)


@registered_group_command(boss_group, name="status", description="View the active party boss phase, HP, raid vitality and round")
async def boss_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    encounter = await DB.get_boss_encounter(user_id=interaction.user.id)
    if not encounter:
        await interaction.response.send_message("Your party has no active boss encounter.", ephemeral=False)
        return
    await _send_raid_card(interaction, encounter)


@registered_group_command(boss_group, name="act", description="Take your once-per-round raid action; boss retaliates after the full party acts")
@app_commands.choices(style=[app_commands.Choice(name="Attack", value="attack"), app_commands.Choice(name="Technique", value="technique"), app_commands.Choice(name="Defend", value="guard"), app_commands.Choice(name="Support", value="support")])
@app_commands.autocomplete(technique=law_technique_autocomplete)
@serialized_user_action
async def boss_act(interaction: discord.Interaction, style: app_commands.Choice[str], technique: str = "") -> None:
    if not await require_character(interaction):
        return
    encounter = await DB.get_boss_encounter(user_id=interaction.user.id)
    if not encounter:
        await interaction.response.send_message("Your party has no active boss encounter.", ephemeral=False)
        return
    if style.value == "technique" and not technique.strip():
        await interaction.response.send_message("Choose which unlocked Law technique to use.", ephemeral=False)
        return
    # Acknowledged before the engine is asked, so an expired token can never
    # leave a committed action unreported (test_ack_before_mutation).
    await interaction.response.defer(ephemeral=False)
    card, events, error = await _raid_act(
        interaction.user.id, f"discord:{interaction.id}:boss.act", encounter, style.value, technique
    )
    if error or card is None:
        await interaction.followup.send(error or "❌ The raid action did not resolve.", ephemeral=False)
        return
    await _send_raid_card(interaction, card, events=events)


@registered_group_command(boss_group, name="claim", description="Claim your reward from a completed boss encounter")
@serialized_user_action
async def boss_claim(interaction: discord.Interaction, encounter_id: int) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    result, error = await _raid_claim(interaction.user.id, f"discord:{interaction.id}:boss.claim", encounter_id)
    await interaction.followup.send(error or _claim_line(result or {}), ephemeral=False)


@registered_group_command(hunter_group, name="status", description="View the autonomous bounty hunter currently tracking this incarnation")
async def hunter_status(interaction: discord.Interaction) -> None:
    if not await require_character(interaction):
        return
    pursuit = await DB.get_bounty_hunter_pursuit(user_id=interaction.user.id)
    if not pursuit:
        await interaction.response.send_message("🎯 No autonomous bounty hunter is actively pursuing you.", ephemeral=False)
        return
    await interaction.response.send_message(
        f"🎯 **Pursuit #{pursuit['pursuit_id']} — {pursuit['hunter_name']}**\n"
        f"Jurisdiction **{pursuit['jurisdiction']}** • bounty **{pursuit['amount']}** • hunter power **{pursuit['hunter_power']}**\n"
        f"Status **{pursuit['status']}** • pressure **{pursuit['pressure']}%** • escape **{pursuit['escape_progress']}%** • capture **{pursuit['capture_progress']}%**",
        ephemeral=False,
    )


@registered_group_command(hunter_group, name="act", description="Evade, fight or surrender to an active bounty hunter")
@app_commands.choices(action=[app_commands.Choice(name="Evade", value="evade"), app_commands.Choice(name="Fight", value="fight"), app_commands.Choice(name="Surrender", value="surrender")])
@serialized_user_action
async def hunter_act(interaction: discord.Interaction, pursuit_id: int, action: app_commands.Choice[str]) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    _ = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "bounty_hunter.act",
            interaction.user.id,
            {"pursuit_id": int(pursuit_id), "action": action.value},
            action_id=f"discord:{interaction.id}:bounty_hunter.act",
        )
        result = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    # The trail is the engine's word for what the hunter is following - what
    # the cultivator is still carrying that carries somebody's mark. It is read
    # off the action result rather than computed here: the rule is Go's.
    trail = f"\nTrail **{int(result.get('trail') or 0)}%** — {result.get('trail_word', '')}" if result.get("trail_word") else ""
    await interaction.followup.send(
        f"🎯 **{result.get('hunter_name', 'Hunter')}** • status **{result.get('status', 'active')}** • pressure {result.get('pressure', 0)}% • escape {result.get('escape_progress', 0)}% • capture {result.get('capture_progress', 0)}%"
        + trail,
        ephemeral=False,
    )
