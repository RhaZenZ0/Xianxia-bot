"""The explicit Discord command surface: which roots and hubs the bot
registers with Discord, the hub page tables, the /admin panel root, the
application-command error handler, and the event-handler bindings.

Split phase 10 (v0.20.0, docs/MAIN_SPLIT_PLAN.md). Cut verbatim from
main.py, which is now the composition root and nothing else. This module
imports every command module whose groups or status handlers it wires;
the modules that define only root commands (commands/sense.py) and the
admin operations (admin/world_ops.py, admin/inspect_sim.py) are imported
for the registration that happens when they load - the `_MIGRATED_ROOTS`
check below fails at import if any of their roots is missing.
"""
from __future__ import annotations

import discord
from discord import app_commands

from ..database import SCHEMA_VERSION
from .admin.core import (
    admin_family_group,
    admin_npc_group,
    admin_player_group,
    admin_sect_group,
    admin_server_group,
    admin_sim_group,
    admin_world_group,
    require_admin,
)
from .admin import inspect_sim as _admin_inspect_sim  # noqa: F401  (registers commands)
from .admin import playtest_board as _admin_playtest_board  # noqa: F401  (registers commands)
from .admin import world_ops as _admin_world_ops  # noqa: F401  (registers commands)
from .bot import XianxiaBot
from .channels import post_server_log
from .commands.abode import abode_group, array_group, innerworld_group
from .commands.aptitude import aptitude_group
from .commands.artifact import artifact_group
from .commands.battle import battle_group, battle_status
from .commands.beast import beast_group
from .commands.boss import boss_group, boss_status, hunter_group, hunter_status
from .commands.character import bond_group, fate_group
from .commands.cultivation import body_group, bodyperfect_group, perfect_group, seclusion_group, tribulation_group
from .commands.duel import duel_group
from .commands.economy import (
    auction_browse,
    auction_group,
    blackmarket_group,
    blackmarket_status,
    civilization_group,
    civilization_status_command,
    market_group,
    merchant_group,
    shop_group,
    storage_group,
)
from .commands.equipment import equipment_group
from .commands.exploration import alchemy_group, realmhub_group, travel_group
from .commands.family import family_group
from .commands.formation import formation_group, formation_status
from .commands.law import condition_group, crime_group, law_group, manual_group, profession_group
from .commands.scene import scene_group, scene_status, talk
from .commands.secretrealm import secret_group, secret_status
from .commands.sect import sect_group
from .commands import sense as _commands_sense  # noqa: F401  (registers its root commands on import)
from .commands.territory import caravan_group, party_group, party_status, territory_group, war_group, war_status
from .hubs import HubDefinition, HubPage, HubStatusField, _hub_icon, register_hubs, send_hub
from .registry import ACTIONS, EVENT_HANDLERS, registered_root_command
from .runtime import DB, WORLD, character_location_display, log
from .services import GUILD, SIM


# ---------------------------------------------------------------------------
# Explicit Discord command surface
# ---------------------------------------------------------------------------
# Internal action objects provide slash metadata for the GUI without becoming
# public Discord roots. Gameplay validation and transactions stay in one handler.

_GROUP_ACTION_ROOTS = {
    "seclusion": seclusion_group,
    "aptitude": aptitude_group,
    "perfect": perfect_group,
    "body": body_group,
    "bodyperfect": bodyperfect_group,
    "secretrealm": secret_group,
    "scene": scene_group,
    "storage": storage_group,
    "auction": auction_group,
    "battle": battle_group,
    "law": law_group,
    "manual": manual_group,
    "condition": condition_group,
    "tribulation": tribulation_group,
    "profession": profession_group,
    "crime": crime_group,
    "beast": beast_group,
    "artifact": artifact_group,
    "territory": territory_group,
    "war": war_group,
    "caravan": caravan_group,
    "party": party_group,
    "duel": duel_group,
    "equipment": equipment_group,
    "formation": formation_group,
    "boss": boss_group,
    "hunter": hunter_group,
    "abode": abode_group,
    "alchemy": alchemy_group,
    "array": array_group,
    "innerworld": innerworld_group,
    "sect": sect_group,
    "family": family_group,
    "civilization": civilization_group,
    "market": market_group,
    "merchant": merchant_group,
    "shop": shop_group,
    "blackmarket": blackmarket_group,
    "realmhub": realmhub_group,
    "fate": fate_group,
    "bond": bond_group,
    "travel": travel_group,
}

_MIGRATED_ROOTS = {
    "abode", "afterlife", "alchemy", "aptitude", "array", "artifact", "auction", "battle", "beast",
    "body", "bodyperfect", "bond", "boss", "bounty", "breakthrough", "caravan",
    "civilization", "conceal", "condition", "craft", "crime", "cultivate",
    "daoheart", "duel", "effects", "equipment", "era", "explore", "family",
    "formation", "gender", "grudges", "hunt", "hunter", "inheritances", "fate",
    "innerworld", "inventory", "karma", "law", "lifespan", "manual", "market", "merchant", "shop", "blackmarket",
    "npcinfo", "party", "perfect", "profession", "provenance", "reincarnate",
    "reputation", "rulers", "scene", "seclusion", "secretrealm", "sect", "sense",
    "sheet", "soul", "spatialkey", "specialeffects", "storage", "talk", "territory",
    "time", "travel", "realmhub", "tribulation", "use", "wallet", "war", "world",
    "worldevents", "worldrules",
}

_ROOT_ACTIONS: dict[str, object] = dict(_GROUP_ACTION_ROOTS)
_ROOT_ACTIONS.update({
    name: command for name, command in ACTIONS.roots().items()
    if name in _MIGRATED_ROOTS
})

_missing_action_roots = sorted(_MIGRATED_ROOTS - set(_ROOT_ACTIONS))
if _missing_action_roots:
    raise RuntimeError(f"Command registry lost action roots: {_missing_action_roots}")

def _hub_page(root: str, label: str, description: str) -> HubPage:
    return HubPage(key=root, label=label, description=description, command=_ROOT_ACTIONS[root])


_HUB_DEFINITIONS = (
    HubDefinition(
        name="character",
        title="🧑 Cultivator — Character Hub",
        description="Identity, public character state, consequences, relationships and Samsara.",
        pages=(
            _hub_page("sheet", "Overview", "Your main character sheet and public cultivation overview."),
            _hub_page("gender", "Sex", "Male/Female identity used for gendered titles and forms of address."),
            _hub_page("lifespan", "Lifespan", "Age, lifespan and mortality state."),
            _hub_page("karma", "Karma", "Metaphysical karma and its known consequences."),
            _hub_page("fate", "Fate", "Spendable providence that can avert true death and grows through major fortunate deeds."),
            _hub_page("bond", "Dao Partnership", "Consensual partnership, paired cultivation resonance and Samsara partner echoes."),
            _hub_page("daoheart", "Dao Heart", "Dao-heart stability and sworn commitments."),
            _hub_page("reputation", "Reputation", "Persistent faction and social reputation."),
            _hub_page("grudges", "Grudges", "Personal, family and faction grudges."),
            _hub_page("crime", "Crimes", "Jurisdictional crimes, evidence and atonement."),
            _hub_page("bounty", "Bounties", "Active capture/death bounties."),
            _hub_page("inheritances", "Inheritances", "Ancient inheritances you have obtained."),
            _hub_page("effects", "Conditions & Effects", "Generic buffs, debuffs, curses and conditions."),
            _hub_page("specialeffects", "Special Effects", "Law, domain, curse and control effects."),
            _hub_page("condition", "Treatment", "Inspect and treat persistent injuries and deviations."),
            _hub_page("soul", "Soul", "Soul state and legacy across incarnations."),
            _hub_page("afterlife", "Samsara", "Afterlife state and reincarnation timing."),
            _hub_page("reincarnate", "Reincarnate", "Begin the next incarnation when Samsara permits it."),
        ),
    ),
    HubDefinition(
        name="quest",
        title="☯ Quest Journal",
        description="Progression objectives, Perfection paths and ascension gates.",
        pages=(
            _hub_page("breakthrough", "Main Progression", "Normal realm breakthrough and Stage 9 progression."),
            _hub_page("perfect", "Realm Perfection", "Optional Stage 9 Realm Perfection path."),
            _hub_page("bodyperfect", "Body Perfection", "Optional Stage 9 Body Realm Perfection path."),
            _hub_page("tribulation", "Tribulation / Ascension", "Prepare for and attempt heavenly tribulations."),
            _hub_page("bounty", "Bounties", "Active bounty objectives and consequences."),
        ),
    ),
    HubDefinition(
        name="cultivation",
        title="🧘 Cultivation Hub",
        description="Meditation, seclusion, body cultivation, aptitudes, Laws, manuals and concealment.",
        pages=(
            _hub_page("cultivate", "Meditation", "Gather cultivation essence."),
            _hub_page("seclusion", "Seclusion", "Start, inspect or end closed-door cultivation."),
            _hub_page("body", "Body Cultivation", "Parallel body-cultivation progression."),
            _hub_page("aptitude", "Aptitudes", "Roots, bloodlines, physiques and aptitude progression."),
            _hub_page("law", "Laws", "Comprehend and wield Laws."),
            _hub_page("manual", "Manuals & Techniques", "Study manuals and use learned techniques."),
            _hub_page("conceal", "Concealment", "Toggle cultivation-aura concealment."),
            _hub_page("profession", "Profession", "Cultivation-profession mastery status."),
        ),
    ),
    HubDefinition(
        name="items",
        title="🎒 Items Hub",
        description="Inventory, storage, equipment, artifacts, consumables and provenance.",
        pages=(
            _hub_page("inventory", "Inventory", "View carried items and materials."),
            _hub_page("storage", "Storage", "Inspect, deposit into and withdraw from spatial storage."),
            _hub_page("use", "Use Item", "Consume or activate a carried item."),
            _hub_page("equipment", "Equipment", "Bind, equip, unequip and repair durable equipment."),
            _hub_page("artifact", "Artifacts", "Bond and awaken personal artifacts."),
            _hub_page("provenance", "Provenance", "Inspect ownership marks, legality and tracking."),
        ),
    ),
    HubDefinition(
        name="npc",
        title="👥 NPC Hub",
        description="Public, location-aware NPC inspection and interaction.",
        pages=(
            _hub_page("npcinfo", "Inspect", "View public information for a known NPC."),
            _hub_page("talk", "Talk", "Speak with a persistent NPC."),
            _hub_page("sense", "Sense", "Use Spiritual Sense on NPCs, players or the area."),
        ),
    ),
    HubDefinition(
        name="world",
        title="🌍 World Hub",
        description="Your location, local actions, current events, civilization and world laws.",
        pages=(
            _hub_page("world", "Current Location", "Show the current world and known locations."),
            _hub_page("explore", "Explore", "Explore the current location for events and discoveries."),
            _hub_page("hunt", "Hunt", "Hunt a spirit beast at the current location."),
            _hub_page("worldevents", "Events", "Active phenomena, consequences and realm openings."),
            _hub_page("civilization", "Civilization", "Population, security and named regional NPC activity."),
            _hub_page("scene", "Scene", "Current roleplay scene and in-world time."),
            _hub_page("era", "Era", "The active era and cycle transitions."),
            _hub_page("time", "Time", "Canonical cultivation calendar."),
            _hub_page("rulers", "Rulers", "Publicly recognized rulers."),
            _hub_page("worldrules", "World Laws", "Rules governing NPCs, sects, families and forbidden arts."),
        ),
    ),
    HubDefinition(
        name="travel",
        title="🗺 Travel Hub",
        description="Choose destinations, teleportation arrays and special movement options.",
        pages=(
            _hub_page("travel", "Destinations", "Travel to another known normal destination, or check your in-transit status."),
            _hub_page("realmhub", "Realm Capitals", "Travel to and inspect the public meeting city for every realm world."),
            _hub_page("array", "Teleportation Arrays", "List and use public teleportation formations."),
        ),
    ),
    HubDefinition(
        name="combat",
        title="⚔️ Combat Hub",
        description="Battles, duels, parties, formations, bosses and bounty-hunter pursuits.",
        pages=(
            _hub_page("battle", "Active Battle", "Current battle, challenges and battle actions."),
            _hub_page("duel", "Duels", "Consent-gated nonlethal PvP duels."),
            _hub_page("party", "Party", "Create, join, inspect or leave cultivation parties."),
            _hub_page("formation", "Formations", "Assign party positions and formation stances."),
            _hub_page("boss", "Boss Raids", "Persistent multi-phase party boss encounters."),
            _hub_page("hunter", "Bounty Hunter", "Respond to autonomous bounty-hunter pursuits."),
        ),
    ),
    HubDefinition(
        name="economy",
        title="💰 Economy Hub",
        description="Wallet, city shops, local markets, black markets, protected auctions, travelling merchants and trade caravans.",
        pages=(
            _hub_page("wallet", "Wallet", "View cultivation currencies."),
            _hub_page("shop", "City Shops", "The smithy, apothecary and talisman hall of each city: find them by exploring, enter them by travelling, buy and sell inside."),
            _hub_page("market", "Local Market", "Buy and sell in the dynamic local economy."),
            _hub_page("blackmarket", "Black Market", "Locate rotating underworld posts and trade forbidden goods."),
            _hub_page("auction", "Auction House", "Browse, list and bid in protected auctions."),
            _hub_page("merchant", "Merchants", "Find the travelling merchants and buy what the auction floors could not sell."),
            _hub_page("caravan", "Caravans", "Dispatch and inspect persistent trade caravans."),
        ),
    ),
    HubDefinition(
        name="craft",
        title="🛠 Craft Hub",
        description="Craft alchemy, forging and inscription recipes; deploy shared location arrays through Items → Use Item.",
        pages=(
            _hub_page("alchemy", "Alchemy", "Refine pills, forage simulated herb resources, track toxicity and purge medicinal residue."),
            _hub_page("craft", "General Crafting", "Practice alchemy, forging or Formation inscription from known recipes."),
            _hub_page("profession", "Profession", "View crafting and support-profession mastery."),
        ),
    ),
    HubDefinition(
        name="beast",
        title="🐉 Beast Hub",
        description="Manage contracted spirit beasts and the active companion.",
        pages=(_hub_page("beast", "Companions", "Inspect, train, evolve and activate contracted beasts."),),
    ),
    HubDefinition(
        name="sect",
        title="🏯 Sect Hub",
        description="Sect membership, player discipleship, shared manor, resources, politics, territory and war.",
        pages=(
            _hub_page("sect", "Sect", "Membership, player discipleship, shared manor, treasury and martial family."),
            _hub_page("territory", "Territory", "Persistent territory control and claims."),
            _hub_page("war", "War", "Sieges, defenses and territorial conflict actions."),
        ),
    ),
    HubDefinition(
        name="family",
        title="🏠 Family Hub",
        description="Birth family, clan structure, descendants, support and family history.",
        pages=(_hub_page("family", "Family", "View and manage your persistent birth-family branch."),),
    ),
    HubDefinition(
        name="abode",
        title="🏡 Abode Hub",
        description="Establish, enter, upgrade and manage access to your private player-owned location.",
        pages=(_hub_page("abode", "Player Property", "Private property type, facilities, visitors and location-scene access."),),
    ),
    HubDefinition(
        name="innerworld",
        title="🌌 Inner World Hub",
        description="Create, enter and define rules for a stabilized personal world.",
        pages=(_hub_page("innerworld", "Personal World", "Personal-world creation, rules and travel."),),
    ),
    HubDefinition(
        name="realm",
        title="🌀 Secret Realm Hub",
        description="Secret realms, active expeditions and spatial keys.",
        pages=(
            _hub_page("secretrealm", "Secret Realms", "Available realms and active realm exploration."),
            _hub_page("spatialkey", "Spatial Keys", "Use a key/token to open its linked dimension."),
        ),
    ),
)

_HUB_BY_NAME = {definition.name: definition for definition in _HUB_DEFINITIONS}


def _hub_resource_value(current: object, maximum: object) -> str:
    current_value = max(0, int(current or 0))
    maximum_value = max(1, int(maximum or 1))
    percentage = max(0, min(100, round(current_value * 100 / maximum_value)))
    filled = max(0, min(10, round(percentage / 10)))
    bar = "▰" * filled + "▱" * (10 - filled)
    return f"`{bar}` **{percentage}%**\n{current_value:,} / {maximum_value:,}"


async def _player_hub_status(interaction: discord.Interaction) -> list[HubStatusField]:
    character = await DB.get_character(interaction.user.id)
    if character is None:
        return [
            HubStatusField("🌱 Character", "Not created — use **/begin**", inline=False),
        ]
    inventory = await DB.get_inventory(interaction.user.id)
    item_stacks = sum(max(0, int(quantity)) for quantity in inventory.values())
    realm = WORLD.realm_name(int(character.get("realm_index", 0)), character.get("gender"))
    return [
        HubStatusField(
            "☯️ Realm",
            f"**{realm}** • Stage **{int(character.get('phase', 1))}**",
        ),
        HubStatusField(
            "❤️ Vitality",
            _hub_resource_value(character.get("vitality"), character.get("vitality_max")),
        ),
        HubStatusField(
            "💠 Qi",
            _hub_resource_value(character.get("qi"), character.get("qi_max")),
        ),
        HubStatusField(
            "🎒 Items",
            f"**{item_stacks:,}** total • {len(inventory)} types",
        ),
        HubStatusField(
            "📍 Location",
            f"**{(await character_location_display(character))[:180]}**",
            inline=False,
        ),
    ]


async def _economy_hub_status(interaction: discord.Interaction) -> list[HubStatusField]:
    character = await DB.get_character(interaction.user.id)
    if character is None:
        return [
            HubStatusField("🌱 Character", "Not created — use **/begin**", inline=False),
        ]
    location = str(character.get("location") or "Unknown")
    rows = await SIM.market_rows(location, 100)
    stocked = [row for row in rows if int(row.get("supply") or 0) > 0]
    average_index = (
        sum(float(row.get("price_index") or 1.0) for row in stocked) / len(stocked)
        if stocked else 1.0
    )
    market_signal = "No local market" if not rows else (
        f"**{len(stocked):,}** stocked items • index **x{average_index:.2f}**"
    )
    return [
        HubStatusField("🪙 Spirit Stones", f"**{int(character.get('spirit_stones', 0) or 0):,}**"),
        HubStatusField("📍 Location", f"**{(await character_location_display(character))[:180]}**", inline=False),
        HubStatusField("💹 Local Market", market_signal, inline=False),
    ]


async def _admin_hub_status(interaction: discord.Interaction) -> list[HubStatusField]:
    guild = interaction.guild
    return [
        HubStatusField("🌐 Server", f"**{getattr(guild, 'name', 'Unknown server')}**"),
        HubStatusField("👥 Members", f"**{int(getattr(guild, 'member_count', 0) or 0):,}**"),
        HubStatusField("🗂️ Channels", f"**{len(getattr(guild, 'channels', ()) or ()):,}**"),
        HubStatusField("🗃️ Database", f"Schema **{SCHEMA_VERSION}**"),
    ]


def _build_hub_command(definition: HubDefinition) -> app_commands.Command:
    async def hub_command(interaction: discord.Interaction) -> None:
        if definition.name == "economy":
            await send_hub(interaction, definition, status_provider=_economy_hub_status)
            return
        await send_hub(interaction, definition, status_provider=_player_hub_status)

    hub_command.__name__ = f"{definition.name}_hub_command"
    return app_commands.command(
        name=definition.name,
        description=definition.description[:100],
    )(hub_command)


_HUB_COMMANDS = tuple(_build_hub_command(definition) for definition in _HUB_DEFINITIONS)

# ---------------------------------------------------------------------------
# Administrator control panel
# ---------------------------------------------------------------------------
# Admin action groups are internal hub metadata. Only the single /admin surface
# is registered with Discord; its actions resolve through ACTIONS explicitly.
_ADMIN_HUB_DEFINITION = HubDefinition(
    name="admin",
    title="🛡️ Xianxia — Administrator Control Panel",
    description=(
        "Server configuration, GM controls and world simulation in one interactive panel. "
        "Choose a section, then choose an action. Actions use the explicitly registered canonical handlers and audit logging."
    ),
    pages=(
        HubPage(key="server", label="Server", description="Channels, health, AI/narrator status, chat monitoring, maintenance, backups and audit logs.", command=admin_server_group),
        HubPage(key="world", label="World", description="Events, secret realms and canonical world time.", command=admin_world_group),
        HubPage(key="player", label="Players", description="Inspect, restore, reward or move cultivators.", command=admin_player_group),
        HubPage(key="sect", label="Sects", description="Membership, ranks and master/disciple administration.", command=admin_sect_group),
        HubPage(key="family", label="Families", description="Inspect hidden birth-family state.", command=admin_family_group),
        HubPage(key="npc", label="NPCs", description="Inspect hidden canonical NPC state.", command=admin_npc_group),
        HubPage(key="simulation", label="Simulation", description="Automation, regions, markets, factions and world simulation.", command=admin_sim_group),
    ),
)


class MenuView(discord.ui.View):
    """The one door (v0.33.1): a select listing every hub, Admin included for
    an administrator. Picking one opens that hub exactly as its own slash
    command does; the sixteen hub commands remain beside it."""

    def __init__(self, *, owner_id: int, is_admin: bool) -> None:
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.add_item(MenuSelect(is_admin=is_admin))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.owner_id:
            await interaction.response.send_message("Open your own menu with **/menu**.", ephemeral=False, delete_after=15)
            return False
        return True


class MenuSelect(discord.ui.Select):
    def __init__(self, *, is_admin: bool) -> None:
        options = [
            discord.SelectOption(
                label=definition.title.split("—")[-1].strip()[:100] if "—" in definition.title else definition.name.title(),
                value=definition.name,
                description=definition.description[:100],
                emoji=_hub_icon(definition.name),
            )
            for definition in _HUB_DEFINITIONS
        ]
        if is_admin:
            options.append(discord.SelectOption(
                label="Administrator Control Panel", value="admin",
                description="Server setup, GM controls and world simulation.", emoji=_hub_icon("admin"),
            ))
        super().__init__(placeholder="Choose a hub…", min_values=1, max_values=1, options=options[:25])

    async def callback(self, interaction: discord.Interaction) -> None:
        choice = str(self.values[0])
        if choice == "admin":
            await admin_panel.callback(interaction)
            return
        definition = _HUB_BY_NAME[choice]
        provider = _economy_hub_status if definition.name == "economy" else _player_hub_status
        await send_hub(interaction, definition, status_provider=provider)


@registered_root_command(
    name="menu",
    description="Open the main menu: every hub in one place",
    guild=GUILD,
)
async def menu(interaction: discord.Interaction) -> None:
    member = interaction.user
    is_admin = isinstance(member, discord.Member) and member.guild_permissions.administrator
    lines = ["🧭 **Xianxia RP — Main Menu**", "Pick a hub below. Each opens the same panel as its own slash command."]
    await interaction.response.send_message(
        "\n".join(lines), view=MenuView(owner_id=member.id, is_admin=bool(is_admin)), ephemeral=False,
    )


register_hubs(*_HUB_DEFINITIONS, _ADMIN_HUB_DEFINITION)


@registered_root_command(
    name="admin",
    description="Open the Xianxia administrator control panel",
    guild=GUILD,
)
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def admin_panel(interaction: discord.Interaction) -> None:
    if not await require_admin(interaction):
        return
    await send_hub(interaction, _ADMIN_HUB_DEFINITION, status_provider=_admin_hub_status)

async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    log.exception("Application command error", exc_info=error)
    command_name = interaction.command.qualified_name if interaction.command else "unknown"
    original = getattr(error, "original", error)
    await post_server_log(
        interaction.guild,
        "Application command error",
        f"Command: `/{command_name}`\n"
        f"User: **{interaction.user}** (`{interaction.user.id}`)\n"
        f"Channel: <#{interaction.channel_id}>\n"
        f"Error: `{type(original).__name__}: {str(original)[:900]}`",
    )
    # This handler cannot know whether the failure happened before or after an
    # authoritative Go mutation committed - many handlers do further work
    # (narration, history, presentation) after a successful authoritative_action
    # call, and an exception in that later work still reaches here with the
    # mutation already applied. Claiming "no game-state change" would be a
    # guess dressed up as a fact, so tell the player to check rather than assume.
    message = "Something went wrong. Check your current state (e.g. inventory, sheet) before retrying — this error does not guarantee nothing changed. An administrator can check the configured bot log channel."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=False)
    else:
        await interaction.response.send_message(message, ephemeral=False)


def register_command_surface(client: XianxiaBot) -> None:
    """Register only public Discord commands; gameplay actions stay internal."""
    # "act" is deliberately absent here: it's only ever a subcommand name under
    # several groups (/battle act, /boss act, /hunter act, /war act, /duel act
    # - see registered_group_command call sites above), never its own root
    # command, so it was never registered into ACTIONS._roots and
    # ACTIONS.root("act") raised KeyError on every bot startup.
    for name in ("begin", "me", "quests", "action", "check", "admin", "menu"):
        client.tree.add_command(ACTIONS.root(name), guild=GUILD)
    for command in _HUB_COMMANDS:
        client.tree.add_command(command, guild=GUILD)
    client.tree.error(on_app_command_error)


def register_event_handlers() -> None:
    bindings = {
        "talk": talk,
        "battle": battle_status,
        "secret": secret_status,
        "war": war_status,
        "auction": auction_browse,
        "party": party_status,
        "boss": boss_status,
        "formation": formation_status,
        "hunter": hunter_status,
        "blackmarket": blackmarket_status,
        "civilization": civilization_status_command,
        "scene": scene_status,
    }
    for name, command in bindings.items():
        EVENT_HANDLERS.register(name, ACTIONS.handler_for(command))
