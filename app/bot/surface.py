"""The explicit Discord command surface: which roots and hubs the bot
registers with Discord, the hub page tables, the /admin panel root, the
application-command error handler, and the event-handler bindings.

Split phase 10 (v0.20.0, docs/history/MAIN_SPLIT_PLAN.md). Cut verbatim from
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
from ..rules.progression_systems import ASCENSION_GATES
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
from .commands.character import begin as begin_command, bond_group, fate_group
from .commands.cultivation import (
    body_group,
    dantian_group,
    ghost_group,
    meridian_group,
    perfect_group,
    seclusion_group,
    tribulation_group,
)
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
    trade_group,
)
from .commands.equipment import equipment_group
from .commands.exploration import alchemy_group, city_group, realmhub_group, travel_group
from .commands.family import family_group
from .commands.formation import formation_group, formation_status
from .commands.law import condition_group, crime_group, law_group, manual_group, profession_group
from .commands.scene import scene_group, scene_status, talk
from .commands.secretrealm import secret_group, secret_status
from .commands.sect import sect_group
from .commands import cooldowns as _commands_cooldowns  # noqa: F401  (registers /cooldowns on import)
from .commands import sense as _commands_sense  # noqa: F401  (registers its root commands on import)
from .commands import support as _commands_support  # noqa: F401  (registers /tribute on import)
from .commands.territory import caravan_group, party_group, party_status, territory_group, war_group, war_status
from . import maintenance, seclusion
from .hubs import (
    LAYOUT_COMPONENTS_AVAILABLE,
    HubDefinition,
    HubPage,
    HubStatusField,
    _hub_icon,
    open_hub_in_place,
    register_hubs,
    register_menu_builder,
    register_menu_facts,
    register_hidden_actions,
    register_panel_gate,
    send_hub,
)
from .locations import here_summary
from .registry import ACTIONS, EVENT_HANDLERS, registered_root_command
from .runtime import DB, WORLD, character_location_display, log
from .services import GUILD, SIM
from .status_cards import cultivation_status_fields, menu_facts_line


# ---------------------------------------------------------------------------
# Explicit Discord command surface
# ---------------------------------------------------------------------------
# Internal action objects provide slash metadata for the GUI without becoming
# public Discord roots. Gameplay validation and transactions stay in one handler.

_GROUP_ACTION_ROOTS = {
    "seclusion": seclusion_group,
    "dantian": dantian_group,
    "ghost": ghost_group,
    "meridian": meridian_group,
    "aptitude": aptitude_group,
    "perfect": perfect_group,
    "body": body_group,
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
    "trade": trade_group,
    "blackmarket": blackmarket_group,
    "realmhub": realmhub_group,
    "city": city_group,
    "fate": fate_group,
    "bond": bond_group,
    "travel": travel_group,
}

_MIGRATED_ROOTS = {
    "abode", "afterlife", "alchemy", "aptitude", "array", "artifact", "auction", "battle", "beast",
    "body", "bond", "boss", "bounty", "breakthrough", "caravan",
    "city", "civilization", "conceal", "condition", "craft", "crime", "cultivate",
    "dantian", "daoheart", "duel", "effects", "equipment", "era", "explore", "family",
    "formation", "ghost", "grudges", "hunt", "hunter", "inheritances", "fate",
    "innerworld", "inventory", "karma", "law", "learn", "lifespan", "manual", "market", "merchant", "shop", "trade", "blackmarket",
    "meridian", "npcinfo", "party", "perfect", "profession", "provenance", "reincarnate",
    "reputation", "reset", "rulers", "scene", "seclusion", "secretrealm", "sect", "sense",
    "sheet", "soul", "spatialkey", "specialeffects", "stance", "insight", "storage", "talk", "territory",
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

def _hub_page(root: str, label: str, description: str, *extra_roots: str,
              only: tuple[str, ...] = (), key: str | None = None) -> HubPage:
    """One page of a hub. Extra roots (v1.0.0-rc.4) are gathered onto the same
    page, so a page can be a thing you are doing rather than one command's
    name; the page keeps the first root's key, which hint paths, the playtest
    checklist and the emoji map resolve against.

    `only` (v1.0.0-rc.13) names the leaves this page takes from that root, so
    one large group can be several pages instead of one page four deep in Next
    buttons. A page that takes a subset needs its own `key`, because a key is
    what the page select and every hint path address a page by."""
    return HubPage(
        key=key or root, label=label, description=description, command=_ROOT_ACTIONS[root],
        extras=tuple(_ROOT_ACTIONS[name] for name in extra_roots),
        only=only,
    )


_HUB_DEFINITIONS = (
    HubDefinition(
        name="character",
        title="🧑 Cultivator — Character Hub",
        description="Identity, public character state, consequences, relationships and Samsara.",
        pages=(
            # Six pages, not eighteen (v1.0.0-rc.13). Fourteen of the old
            # eighteen held a single read - nobody opens "Karma" to see one
            # number, they open the sheet - so the reads that describe who you
            # are now arrive together, and the pages that remain are the ones
            # a player actually navigates to.
            _hub_page("sheet", "Overview", "Who you are now: the sheet, your age and longevity, karma, Dao heart, standing, inheritances and soul legacy.",
                      "lifespan", "karma", "daoheart", "reputation", "inheritances", "soul"),
            # One page for "something is wrong with me". It was three - generic
            # effects, special/Law/curse/domain effects, and persistent
            # conditions - and curses were named by two of the three, so a
            # player who felt wrong had to check all of them to find out why.
            _hub_page("effects", "Afflictions", "Everything currently acting on you: buffs, debuffs, curses, Law, domain and control effects, persistent injuries and deviations - and their treatment.",
                      "specialeffects", "condition"),
            _hub_page("bond", "Dao Partnership", "Consensual partnership, paired cultivation resonance and Samsara partner echoes."),
            _hub_page("crime", "Consequences", "What the world holds against you: open crimes and atonement, active capture and death bounties, and standing grudges.",
                      "bounty", "grudges"),
            _hub_page("fate", "Fate", "Spendable providence that can avert true death and grows through major fortunate deeds."),
            # `reset` sits here rather than on Overview (v1.0.1). Overview is
            # seven reads, and a destructive action among them is a misclick;
            # this is the page about a life ending and another beginning, which
            # is what both leaves on it do. Discoverability does not rest on the
            # page - `/begin`'s refusal names the leaf, and that refusal is the
            # message a player who wants to start over actually reaches.
            _hub_page("afterlife", "Samsara", "Afterlife state, reincarnation timing, the next incarnation when the wheel permits it, and abandoning a life you have only just begun.",
                      "reincarnate", "reset"),
        ),
    ),
    # `/ascend`, not `/quest` (v1.0.0-rc.13). This hub holds no quests: it is
    # breakthrough, the two Perfection paths and the tribulation gates. The
    # quests are `/quests`, one character away, which is how long it took a
    # player to pick the wrong one. Bounties moved to /character -> Consequences
    # with the rest of what the world holds against you.
    HubDefinition(
        name="ascend",
        title="☰ Ascension Path",
        description="Breakthrough, the optional Perfection paths, and the heavenly tribulations that gate the next realm.",
        pages=(
            _hub_page("breakthrough", "Main Progression", "Normal realm breakthrough and Stage 9 progression."),
            # One page, one group (v1.0.0-rc.13): `perfect` and `bodyperfect`
            # were the same six verbs twice, and are now a `path` on each.
            _hub_page("perfect", "Perfection", "The optional Stage 9 Perfection path, for the cultivation realm or the body: start it, work its quests, read its clues, attempt its final trial."),
            _hub_page("tribulation", "Tribulation / Ascension", "Prepare for and attempt heavenly tribulations."),
        ),
    ),
    HubDefinition(
        name="cultivation",
        title="🧘 Cultivation Hub",
        description="The cultivation sheet, and four pages for what you are doing: meditate, temper the body, walk the path, practise the arts.",
        pages=(
            # Four pages, grouped by what you are doing (v1.0.0-rc.4). Ten
            # pages named after commands became four named after the work:
            # every action is still here, one tap further in at most.
            _hub_page("cultivate", "Cultivate", "Meditate under your stance, choose the stance, bank a realm-gate insight, break through, or close the doors for a seclusion.",
                      "stance", "insight", "breakthrough", "seclusion"),
            _hub_page("body", "Body", "The parallel body path: temper it, inspect it and break through its stages."),
            _hub_page("dantian", "Qi Body", "The three dantian and the channels that feed them: what you hold, how clean it is, how far you feel.", "meridian"),
            # v1.0.0-rc.8: the ghost road. The page is shown to everyone - the
            # commands say plainly who may walk it - because a road nobody can
            # see is a road nobody learns exists.
            _hub_page("ghost", "Ghost", "The ghost road: death qi from the ground the living have left, the residue it leaves, and the rites that lift it."),
            # Path and Laws are two pages (v1.0.0-rc.13): together they were
            # eleven actions on a page that shows eight, so three of them sat
            # behind a Next button with nothing to say they were there.
            _hub_page("aptitude", "Path", "What you were born with: spiritual roots, bloodlines and special physiques - inspect them, awaken, temper, harmonize and evolve."),
            _hub_page("law", "Laws", "What you comprehend: Law and Dao comprehension, meditation on a Law, and the techniques it unlocks."),
            # `profession` is the Craft hub's - it was listed in both, one
            # handler appearing twice.
            _hub_page("manual", "Arts", "Manuals and techniques, and the concealment of your aura.", "conceal"),
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
            # One page (v1.0.0-rc.13): three pages of one action each, in a hub
            # whose every page was one action. Eight fit on a page.
            _hub_page("npcinfo", "People", "Inspect a known NPC, speak with one, or turn Spiritual Sense on a person, a cultivator or the area.",
                      "talk", "sense"),
        ),
    ),
    HubDefinition(
        name="world",
        title="🌍 World Hub",
        description="Your location, local actions, current events, civilization and world laws.",
        pages=(
            # Four pages, not eleven (v1.0.0-rc.13). Six of the old eleven were
            # a single read answering one question - what is true in the world
            # right now - so they answer it together.
            _hub_page("world", "Almanac", "What is true in the world right now: where you are and what you have discovered, the era and the calendar, its rulers, its laws, and the phenomena currently running.",
                      "era", "time", "rulers", "worldrules", "worldevents"),
            _hub_page("city", "City", "The city you are in: its gates and districts, the commission board, the sect envoys' hall, the rumours and the inn."),
            _hub_page("explore", "Act", "What you can do with this place: explore it for events and discoveries, or hunt the spirit beasts that range here.",
                      "hunt"),
            _hub_page("scene", "Here", "This spot: the running scene and its in-world time, and the region's population, security and named NPC activity.",
                      "civilization"),
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
            _hub_page("trade", "Trade", "Trade directly with another cultivator at the inn: offer what you give and what you want, and they accept or decline."),
            _hub_page("caravan", "Caravans", "Dispatch and inspect persistent trade caravans."),
        ),
    ),
    HubDefinition(
        name="craft",
        title="🛠 Craft Hub",
        description="Craft alchemy, forging, formation and talisman-inscription recipes; deploy shared location arrays through Items → Use Item.",
        pages=(
            _hub_page("alchemy", "Alchemy", "Refine pills, forage simulated herb resources, track toxicity and purge medicinal residue."),
            _hub_page("craft", "General Crafting", "Practice alchemy, forging, formation or talisman inscription from known recipes."),
            _hub_page("profession", "Profession", "Your crafting and support-profession mastery: what rank you hold, the hall examination that certifies it, and reading a method slip into a method you keep.", "learn"),
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
            # Twenty-five actions were one page (v1.0.0-rc.13). A page shows
            # eight, so seventeen of them sat behind a Next button with nothing
            # to say they were there. Four pages, each a thing you came to do.
            _hub_page("sect", "Sect", "Your membership and where you stand in it: rank, roster, politics, the martial family and how it addresses you.",
                      only=("sect status", "sect roster", "sect politics", "sect address",
                            "sect form", "sect family", "sect shadow")),
            _hub_page("sect", "Recruitment", "Getting in: which sects recruit, who will sponsor you, and the entrance examination.",
                      key="sect_recruitment", only=("sect recruitment",)),
            _hub_page("sect", "Discipleship", "Master and disciple: ask, accept, reject, and the bond you already hold.",
                      key="sect_discipleship", only=("sect discipleship",)),
            _hub_page("sect", "Holdings", "What the sect keeps and what you may draw from it: the shared manor, your residence, the treasury, contribution and redemption.",
                      key="sect_holdings", only=("sect manor", "sect abode", "sect treasury",
                                                 "sect contribute", "sect redeem")),
            _hub_page("territory", "Territory", "Persistent territory control and claims."),
            _hub_page("war", "War", "Sieges, defenses and territorial conflict actions."),
        ),
    ),
    HubDefinition(
        name="family",
        title="🏠 Family Hub",
        description="Birth family, clan structure, descendants, support and family history.",
        pages=(
            _hub_page("family", "Family", "The household you belong to now: enter and leave it, and see the clan, its branches and your descendants.",
                      only=("family view", "family enter", "family leave",
                            "family clan", "family descendants", "family child")),
            _hub_page("family", "Hearth", "What the house gives to somebody standing in it (v1.0.0-rc.32): its support, its coffers, its teaching and its errands.",
                      key="family_hearth",
                      only=("family support", "family contribute", "family tutor", "family errand", "family lesson")),
            _hub_page("family", "House", "The cultivation house you found with other players, as distinct from the household you were born into: its seat order, its invitations and its children.",
                      key="family_house",
                      only=("family house status", "family house found", "family house invite",
                            "family house respond", "family house leave", "family house child")),
            _hub_page("family", "Legacy", "What the family was and what it leaves you: its history and ancestry, ancestral sites, investigations, inheritance claims and their conflicts.",
                      key="family_legacy",
                      only=("family history", "family ancestry", "family legacy",
                            "family investigate", "family quest", "family claim", "family conflict")),
        ),
    ),
    HubDefinition(
        name="abode",
        title="🏡 Abode Hub",
        description="Establish, enter, upgrade and manage access to your private player-owned location.",
        pages=(
            _hub_page("abode", "Property", "The home itself: found it, enter and leave it, build and raise its facilities, and use one.",
                      only=("abode status", "abode establish", "abode enter", "abode leave",
                            "abode upgrade", "abode focus")),
            _hub_page("abode", "Access", "Who else may come in: invite, revoke, see your guests, visit another cultivator's home, and the private thread it uses.",
                      key="abode_access",
                      only=("abode visit", "abode invite", "abode guests", "abode revoke", "abode thread")),
        ),
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
        *_here_field(character),
    ]


def _here_field(character: dict) -> list[HubStatusField]:
    """The Here line (v0.40.0): what the place is and offers, and who is
    about - the context a player checked with City → Look before every action."""
    summary = here_summary(str(character.get("location") or ""))
    return [HubStatusField("🧭 Here", summary, inline=False)] if summary else []


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
        *_here_field(character),
        HubStatusField("💹 Local Market", market_signal, inline=False),
    ]


async def _cultivation_hub_status(interaction: discord.Interaction) -> list[HubStatusField]:
    """The cultivation sheet (v1.0.0-rc.3), the hub's first page - computed
    by status_cards from one engine query, the player card when the engine
    is away."""
    return await cultivation_status_fields(interaction, fallback=_player_hub_status)


def _status_provider_for(definition: HubDefinition):
    """The status card a hub carries: the economy hub the wallet and market,
    the cultivation hub its sheet (v1.0.0-rc.3), every other hub the
    player card."""
    if definition.name == "economy":
        return _economy_hub_status
    if definition.name == "cultivation":
        return _cultivation_hub_status
    return _player_hub_status


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
        if definition.name == "cultivation":
            await send_hub(interaction, definition, status_provider=_cultivation_hub_status)
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
        # Three pages of twelve, fifteen and twelve became eight (rc.13): the
        # admin panel shows eight rows like every other, and a GM looking for
        # `unmute` was paging blind through a list called "Players".
        HubPage(key="server", label="Server", description="Install and wire the server: setup, channel bindings, realm capitals and what the current configuration looks like.", command=admin_server_group,
                only=("admin server setup", "admin server status", "admin server basechannels",
                      "admin server bind_channels", "admin server realmhubs")),
        HubPage(key="operations", label="Operations", description="Running it: backups, maintenance, telemetry, the audit log, narrator health and the playtest board.", command=admin_server_group,
                only=("admin server backup", "admin server lockdown", "admin server maintenance", "admin server observability",
                      "admin server audit", "admin server ai_status", "admin server chat_digest",
                      "admin server playtest")),
        HubPage(key="world", label="World", description="Events, secret realms and canonical world time.", command=admin_world_group),
        # One head for one cultivator (v1.0.0-rc.37). rc.13 had split
        # `/admin player` three ways - Players, Grants, Moderation - so that
        # no page needed a Next button; the panel pages a long list with
        # "More actions" now, and a GM asked for every lever on a player to be
        # under one heading, the way the dashboard's Player Editor is. It is
        # the one page allowed past the eight-row layout, and
        # test_hub_pages.py names it as that exception.
        HubPage(key="player", label="Player Edit", description="Everything that edits one cultivator: inspect, set the realm and the body ladder, teleport, revive, clear a stuck battle or scene; give items, currency, storage and karma; ban, freeze and mute with their undos; and erase everything held about one, when they ask for it.", command=admin_player_group),
        HubPage(key="sect", label="Sects", description="Membership, ranks and master/disciple administration.", command=admin_sect_group),
        # Two pages of one action each, both "show me the hidden state of a
        # thing" (rc.13).
        HubPage(key="hidden", label="NPCs & Hidden State", description="Inspect what players cannot see - a cultivator's NPC birth family, a canonical NPC's GM-only state - and stage what the world would: lose an NPC where they stand, or bring one back.",
                command=admin_family_group, extras=(admin_npc_group,)),
        HubPage(key="simulation", label="Simulation", description="Drive it: run a system, set an interval, and see what the automation switches are doing.", command=admin_sim_group,
                only=("admin simulation status", "admin simulation run", "admin simulation interval",
                      "admin simulation automation", "admin simulation toggle",
                      "admin simulation actions")),
        HubPage(key="siminspect", label="Simulation Inspect", description="Read it: the world summary, a region, a market, a named NPC, a sect faction, a clan.", command=admin_sim_group,
                only=("admin simulation world", "admin simulation region", "admin simulation market",
                      "admin simulation npc", "admin simulation sect", "admin simulation clan")),
    ),
)


_MenuBase = discord.ui.LayoutView if LAYOUT_COMPONENTS_AVAILABLE else discord.ui.View

# The menu's shape (v1.0.0-rc.3): four rows of four, grouped by what a player
# is doing, instead of one sixteen-entry select. The hub names stay the hub
# names; two get the label they should always have had.
_HUB_LABELS = {"npc": "NPCs", "innerworld": "Inner World", "ascend": "Ascension"}
_MENU_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("You", "who you are and what you carry", ("character", "cultivation", "items", "ascend")),
    ("World", "where you are and what is happening", ("world", "travel", "realm", "npc")),
    ("Doing", "fighting, making, trading, taming", ("combat", "craft", "economy", "beast")),
    ("Home", "the places that are yours", ("sect", "family", "abode", "innerworld")),
)
# The hub each player opened last, in process: the menu offers it back
# first. Lost at restart, which costs one tap.
_LAST_HUB: dict[int, str] = {}


def _hub_label(name: str) -> str:
    if name in _HUB_LABELS:
        return _HUB_LABELS[name]
    definition = _HUB_BY_NAME.get(name)
    if definition is not None and "—" in definition.title:
        return definition.title.split("—")[-1].strip().replace(" Hub", "")[:80]
    if definition is not None:
        return definition.title.split(" ", 1)[-1].replace(" Hub", "").strip()[:80]
    return name.title()


async def _open_hub_from_menu(interaction: discord.Interaction, name: str) -> None:
    """Open a hub from the menu, in place where the layout allows it; the
    admin panel behind its gate. Remembers the choice for Back."""
    in_place = LAYOUT_COMPONENTS_AVAILABLE and getattr(interaction, "message", None) is not None
    if name == "admin":
        if in_place:
            if not await require_admin(interaction):
                return
            _LAST_HUB[int(interaction.user.id)] = name
            await open_hub_in_place(interaction, _ADMIN_HUB_DEFINITION, _admin_hub_status)
            return
        await admin_panel.callback(interaction)
        return
    definition = _HUB_BY_NAME[name]
    provider = _status_provider_for(definition)
    _LAST_HUB[int(interaction.user.id)] = name
    if in_place:
        await open_hub_in_place(interaction, definition, provider)
        return
    await send_hub(interaction, definition, status_provider=provider)


async def _menu_facts(interaction: discord.Interaction) -> str:
    return await menu_facts_line(interaction)


class MenuHubButton(discord.ui.Button):
    """One hub, one button (v1.0.0-rc.3)."""

    def __init__(self, name: str, *, style: discord.ButtonStyle = discord.ButtonStyle.secondary) -> None:
        self.hub_name = str(name)
        super().__init__(label=_hub_label(self.hub_name)[:20], style=style, emoji=_hub_icon(self.hub_name))

    async def callback(self, interaction: discord.Interaction) -> None:
        await _open_hub_from_menu(interaction, self.hub_name)


class MenuBeginButton(discord.ui.Button):
    """Begin, when there is no character to open a hub for."""

    def __init__(self) -> None:
        super().__init__(label="Begin", style=discord.ButtonStyle.success, emoji="🌱")

    async def callback(self, interaction: discord.Interaction) -> None:
        await begin_command.callback(interaction)


class MenuView(_MenuBase):
    """The one door (v0.33.1), a panel of the hubs' own kind (v0.40.0), and
    since v1.0.0-rc.3 four rows of four - You, World, Doing, Home - under a
    header that says where you are, what stage you hold and what is waiting,
    with Back to the hub you left and Begin when there is no character yet.
    Admin gets a fifth row. Where the layout is not available it is the
    classic select under a line of text."""

    is_layout_hub = False

    def __init__(self, *, owner_id: int, is_admin: bool, owner_name: str = "Cultivator", facts: str = "") -> None:
        super().__init__(timeout=900)
        self.owner_id = int(owner_id)
        self.owner_name = str(owner_name)[:80]
        self.facts = str(facts or "")[:700]
        self.message: discord.Message | None = None
        if not LAYOUT_COMPONENTS_AVAILABLE:
            self.add_item(MenuSelect(is_admin=is_admin))
            return
        container = discord.ui.Container(accent_colour=0x5865F2)
        header = f"## 🧭 Xianxia RP — Main Menu\n-# {self.owner_name}"
        if self.facts:
            header += f"\n{self.facts}"
        container.add_item(discord.ui.TextDisplay(header[:1900]))
        no_character = self.facts.startswith("🌱")
        last = _LAST_HUB.get(self.owner_id)
        for title, blurb, names in _MENU_GROUPS:
            container.add_item(discord.ui.TextDisplay(f"**{title}**\n-# {blurb}"))
            row = discord.ui.ActionRow()
            for name in names:
                style = discord.ButtonStyle.primary if name == last else discord.ButtonStyle.secondary
                row.add_item(MenuHubButton(name, style=style))
            container.add_item(row)
        footer = discord.ui.ActionRow()
        if no_character:
            footer.add_item(MenuBeginButton())
        elif last and last != "admin":
            back = MenuHubButton(last, style=discord.ButtonStyle.primary)
            back.label = f"Back to {_hub_label(last)}"[:40]
            back.emoji = "↩️"
            footer.add_item(back)
        if is_admin:
            footer.add_item(MenuHubButton("admin", style=discord.ButtonStyle.danger))
        if footer.children:
            container.add_item(footer)
        self.add_item(container)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.owner_id:
            await interaction.response.send_message("Open your own menu with **/menu**.", ephemeral=False, delete_after=15)
            return False
        return True


class MenuSelect(discord.ui.Select):
    """The classic menu: one select, for a discord.py without the layout."""

    def __init__(self, *, is_admin: bool) -> None:
        options = [
            discord.SelectOption(
                label=_hub_label(definition.name)[:100],
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
        in_place = LAYOUT_COMPONENTS_AVAILABLE and getattr(self.view, "is_layout_hub", None) is False and getattr(interaction, "message", None) is not None
        if choice == "admin":
            if in_place:
                if not await require_admin(interaction):
                    return
                await open_hub_in_place(interaction, _ADMIN_HUB_DEFINITION, _admin_hub_status)
                return
            await admin_panel.callback(interaction)
            return
        definition = _HUB_BY_NAME[choice]
        provider = _status_provider_for(definition)
        _LAST_HUB[int(interaction.user.id)] = choice
        if in_place:
            await open_hub_in_place(interaction, definition, provider)
            return
        await send_hub(interaction, definition, status_provider=provider)


@registered_root_command(
    name="menu",
    description="Open the main menu: every hub in one place",
    guild=GUILD,
)
async def menu(interaction: discord.Interaction) -> None:
    member = interaction.user
    is_admin = isinstance(member, discord.Member) and member.guild_permissions.administrator
    facts = ""
    if LAYOUT_COMPONENTS_AVAILABLE:
        try:
            facts = await _menu_facts(interaction)
        except Exception:
            log.exception("Menu facts unavailable")
    view = MenuView(owner_id=member.id, is_admin=bool(is_admin), owner_name=getattr(member, "display_name", str(member)), facts=facts)
    if LAYOUT_COMPONENTS_AVAILABLE:
        await interaction.response.send_message(view=view, ephemeral=False)
    else:
        lines = ["🧭 **Xianxia RP — Main Menu**", "Pick a hub below. Each opens the same panel as its own slash command."]
        await interaction.response.send_message("\n".join(lines), view=view, ephemeral=False)
    try:
        view.message = await interaction.original_response()
    except discord.HTTPException:
        view.message = None


register_menu_builder(lambda owner_id, is_admin, owner_name, facts="": MenuView(owner_id=owner_id, is_admin=is_admin, owner_name=owner_name, facts=facts))
register_menu_facts(_menu_facts)


# The household's doors (v1.0.0-rc.32). Enter opens only from the family's
# town, and support, the purse, the teaching and the errands are asked for
# inside - so the panel shows each only where it would work, rather than a
# button that refuses.
# Named as the leaves are, without the slash a hint path carries: these are
# matched against `HubAction.path`, never printed to a player.
HOUSEHOLD_DOOR = "family enter"
HOUSEHOLD_INDOOR_ACTIONS = ("family leave", "family support", "family contribute", "family tutor", "family errand", "family lesson")


def _action_paths(reason: str, *names: str) -> dict[str, str]:
    """path -> why it is shut, which the panel prints as a locked line."""
    return {"/" + name: reason for name in names}


async def _household_hidden_actions(interaction: discord.Interaction) -> dict[str, str]:
    c = await DB.get_character(interaction.user.id)
    if not c:
        return {}
    fam = await DB.get_birth_family(interaction.user.id)
    if not fam:
        return _action_paths("no birth household is recorded", HOUSEHOLD_DOOR, *HOUSEHOLD_INDOOR_ACTIONS)
    here = str(c.get("location") or "")
    inside = here == f"birth_family:{int(fam.get('family_id') or 0)}"
    if inside:
        return _action_paths("you are already inside", HOUSEHOLD_DOOR)
    town = str(fam.get("location") or "")
    hidden = _action_paths("asked for inside the household", *HOUSEHOLD_INDOOR_ACTIONS)
    if here != town:
        hidden.update(_action_paths(f"the household stands in {town}; travel there, or burn a Hearth-Return Talisman", HOUSEHOLD_DOOR))
    return hidden


# And the doors that open later in the game (v1.0.0-rc.32). Each entry hides
# only what the engine would refuse outright for this character - a law
# before the realm that can hold one, a sect's rooms to somebody in no sect,
# a home's keys to somebody with no home - and never a status read or the
# door into the system itself. The rules are read off the same state the
# engine reads: realm and stage, the membership row, the abode row, the
# personal world, the beasts, the house, the soul record.
LAW_MIN_REALM_INDEX = int((WORLD.data.get("law_system") or {}).get("normal_min_realm_index") or 6)
PROGRESSION_GATES: dict[str, tuple[str, ...]] = {
    # gate -> the leaves hidden while the gate is shut
    "law": ("law comprehend", "law technique"),
    "tribulation": ("tribulation prepare", "tribulation attempt"),
    # The seam is anchored after the storm, not during it (v1.0.0-rc.44), so
    # this is a different gate from the one above: `tribulation` is shut
    # everywhere except at a world-crossing stage, and `ascension_gate` is
    # shut until one has actually been survived.
    "ascension_gate": ("tribulation gate",),
    "perfection": ("perfect start",),
    "sect_member": ("sect roster", "sect politics", "sect address", "sect family", "sect shadow",
                    "sect manor establish", "sect manor upgrade", "sect abode", "sect treasury", "sect contribute", "sect redeem",
                    "sect discipleship request", "sect discipleship accept", "sect discipleship reject", "sect discipleship leave"),
    "sect_outsider": ("sect recruitment recommendation", "sect recruitment trial"),
    "abode": ("abode enter", "abode leave", "abode upgrade", "abode focus", "abode invite", "abode revoke", "abode guests", "abode thread"),
    "abode_owner": ("abode establish",),
    "innerworld": ("innerworld enter", "innerworld leave", "innerworld setrule"),
    "innerworld_owner": ("innerworld create",),
    "beast": ("beast feed", "beast train", "beast evolve", "beast active"),
    "house_member": ("family house invite", "family house leave", "family house child"),
    "house_outsider": ("family house found",),
    "samsara": ("family ancestry", "family legacy", "family investigate", "family quest", "family claim", "family conflict"),
}


async def _progression_hidden_actions(interaction: discord.Interaction) -> dict[str, str]:
    c = await DB.get_character(interaction.user.id)
    if not c:
        return {}
    uid = interaction.user.id
    realm = int(c.get("realm_index") or 0)
    phase = int(c.get("phase") or 1)
    shut: dict[str, str] = {}
    if realm < LAW_MIN_REALM_INDEX:
        shut["law"] = f"a Law needs {WORLD.realm_name(LAW_MIN_REALM_INDEX)}; you stand at {WORLD.realm_name(realm)}"
    if realm not in ASCENSION_GATES:
        shut["tribulation"] = "only at a world-crossing gate (realms " + ", ".join(str(r) for r in sorted(ASCENSION_GATES)) + ")"
    here = str(c.get("location") or "")
    standing_in = str((WORLD.locations.get(here) or {}).get("world") or "")
    departing = [index for index, gate in ASCENSION_GATES.items() if gate["from_world"] == standing_in]
    cleared = False
    for index in departing:
        if int((await DB.get_tribulation_state(uid, index) or {}).get("cleared") or 0):
            cleared = True
            break
    if not cleared:
        shut["ascension_gate"] = (
            f"no tribulation out of {standing_in or 'this world'} has been survived here yet"
            if departing else "no world-crossing tribulation leads out of this world")
    if phase != 9:
        shut["perfection"] = "Perfection begins at stage 9"
    if not await DB.get_sect_membership(uid):
        shut["sect_member"] = "you are in no sect — see Recruitment"
    else:
        shut["sect_outsider"] = "you already belong to a sect"
    if not await DB.get_abode(uid):
        shut["abode"] = "you have no property yet — Establish one"
    else:
        shut["abode_owner"] = "you already hold a property"
    if not await DB.get_personal_world(uid):
        shut["innerworld"] = "you have no personal world yet — Create one"
    else:
        shut["innerworld_owner"] = "your personal world already exists"
    if not await DB.get_spirit_beasts(uid):
        shut["beast"] = "no beast is contracted yet — Tame one"
    if not await DB.get_player_family_membership(uid):
        shut["house_member"] = "you belong to no house — Found one or answer an invitation"
    else:
        shut["house_outsider"] = "you already sit in a house"
    legacy = await DB.get_soul_legacy(uid)
    if int((legacy or {}).get("incarnation_count") or 1) <= 1 and not (legacy or {}).get("past_lives"):
        shut["samsara"] = "a first life has no past to trace"
    hidden: dict[str, str] = {}
    for gate, reason in shut.items():
        hidden.update(_action_paths(reason, *PROGRESSION_GATES[gate]))
    return hidden


async def _hidden_actions(interaction: discord.Interaction) -> dict[str, str]:
    """Every door the panel leaves off for this player, and why. Each provider
    is asked on its own, so one failing lookup hides nothing from the others."""
    hidden: dict[str, str] = {}
    for provider in (_household_hidden_actions, _progression_hidden_actions):
        try:
            hidden.update(await provider(interaction))
        except Exception:
            log.exception("Hidden-action provider %s failed", getattr(provider, "__name__", provider))
    return hidden


register_hidden_actions(_hidden_actions)


async def _panel_gate(user: "discord.abc.User", path: str) -> str | None:
    """The hub panel's half of the two gates that refuse a press.

    Maintenance mode (v1.0.0-rc.41) and the closed-door lockout
    (v1.0.0-rc.56). A panel lives fifteen minutes, so both can become true
    under one that is already open; the check therefore belongs on the press
    and not on the open. Registered here because `hubs` sits below `runtime`
    and cannot reach `DB` itself.
    """
    refusal = await maintenance.refuse(DB, user, command=path)
    if refusal is not None:
        return refusal
    # And the player's own doors (v1.0.0-rc.56), for the same reason: a panel
    # drawn before the retreat began is still on screen when it starts, and
    # `_invoke_action` checks on the press.
    return await seclusion.refuse(DB, getattr(user, "id", 0), command=path)


register_panel_gate(_panel_gate)


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
    for name in ("begin", "me", "quests", "action", "check", "admin", "menu", "tribute", "cooldowns"):
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
