"""The /begin character-creation flow: the name/concept modal, the
birth-family browser and the cultivation-style and birth-sex pickers.

Phase 8 of the main.py split (v0.19.43, docs/history/MAIN_SPLIT_PLAN.md). This is the
one flow allowed to reply ephemerally (see the allowlist in
tests/python/unit/test_command_cleanup.py - keyed by these class names, which
the move keeps). Definition order is the order these had in main.py.
"""
from __future__ import annotations

from typing import Any

import discord

from ...rules.birthfamily import family_tier_name
from ...rules.creation_ui import (
    cultivation_style_profile,
    family_emoji,
    family_root_tendencies,
    family_status_summary,
    location_theme,
    origin_vignette,
    recommended_cultivation_styles,
)
from ...ops.game_engine import GameEngineError
from ..channels import _report_game_ui_error, post_server_log
from ..discovery import LOCATION_DISCOVERY_IMAGES, location_discovery_embed, location_discovery_image_path
from ..runtime import DB, ENGINE, GENDER_CHOICES, WORLD, current_world_time, log, respond
from ..threads import ensure_birth_family_household_thread, ensure_expedition_thread

class CharacterModal(discord.ui.Modal):
    """Final text-only form after family and cultivation path are chosen.

    Discord modals cannot contain select menus, so all categorical creation
    choices happen before this form. The player's Spiritual Root is never typed:
    it is rolled at submission from family archetype, homeland, family standing
    and hidden lineage data, then revealed on the completed character sheet.
    """

    def __init__(self, birth_family: dict[str, Any], selected_style: str, selected_gender: str, offer_state_version: int):
        self.birth_family = dict(birth_family)
        self.selected_style = selected_style if selected_style in WORLD.paths else next(iter(WORLD.paths))
        self.selected_gender = selected_gender if selected_gender in {"male", "female"} else "male"
        self.offer_state_version = int(offer_state_version)
        location = str(self.birth_family.get("location") or WORLD.starting_location)
        family_name = str(self.birth_family.get("family_name") or "Family")
        super().__init__(title=f"{family_name} • {location}"[:45])

        profile = cultivation_style_profile(self.selected_style)
        self.name_input = discord.ui.TextInput(
            label="Character name",
            placeholder="e.g. Shen Rui",
            min_length=1,
            max_length=40,
        )
        self.concept_input = discord.ui.TextInput(
            label="Personal Dao / goal",
            placeholder=f"What does this {profile['emoji']} {self.selected_style} seek, fear or protect?",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=300,
        )
        for field in (self.name_input, self.concept_input):
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Ack before character.create: a modal submit carries the same
        # three-second token, and creating a character is not fast.
        await interaction.response.defer(ephemeral=False)
        normalized_path = WORLD.normalize_path(self.selected_style)
        if not normalized_path:
            await respond(interaction, 
                "That cultivation path is no longer available. Use **/begin** again.", ephemeral=True
            )
            return

        family = self.birth_family
        wt = await current_world_time()
        creation_envelope = await ENGINE.authoritative_action(
            "character.create",
            interaction.user.id,
            {
                "discord_name": interaction.user.display_name,
                "name": str(self.name_input.value).strip(),
                "concept": str(self.concept_input.value).strip(),
                "gender": self.selected_gender,
                "path": normalized_path,
                "family_choice_id": str(family.get("choice_id") or ""),
                
                "age_at_creation_years": 18,
            },
            action_id=f"discord:{interaction.id}:character.create",
            expected_version=self.offer_state_version,
        )
        creation = dict(creation_envelope.get("result") or {})
        created = bool(creation.get("created"))
        if not created:
            await respond(interaction, 
                "You already have a character. Use **/character → Overview** to view it.",
                ephemeral=True,
            )
            return
        try:
            await ENGINE.bootstrap_simulation(wt.total_minutes)
        except Exception:
            log.exception("Could not initialize Go-owned simulation bootstrap after character creation")

        name = str(creation.get("name") or self.name_input.value).strip()
        location = str(creation.get("location") or family.get("location") or WORLD.starting_location)
        normalized_root = str(creation.get("spiritual_root") or "Mortal Root")
        natural_lifespan = int(creation.get("natural_lifespan_years") or 75)
        aptitude_profile = dict(creation.get("aptitudes") or {})
        theme = location_theme(location)
        style_profile = cultivation_style_profile(normalized_path)
        location_description = str(WORLD.locations.get(location, {}).get("description") or theme.get("mood") or "")
        embed = discord.Embed(
            title=f"{theme['emoji']} {name} — First Step on the Dao",
            description=origin_vignette(family, normalized_path, normalized_root, self.selected_gender),
            color=int(theme["color"]),
        )
        embed.add_field(
            name=f"{family_emoji(family)} Birth Family",
            value=(
                f"**{family['family_name']} — {family['name']}**\n"
                f"{family_tier_name(int(family['tier']))} • **{location}**\n"
                f"{family_status_summary(family)}\n"
                f"{location_description[:320]}"
            ),
            inline=False,
        )
        embed.add_field(
            name="🧬 Birth Sex",
            value=f"**{self.selected_gender.title()}**",
            inline=True,
        )
        embed.add_field(
            name="☯️ Chosen Cultivation Path",
            value=f"{style_profile['emoji']} **{normalized_path}**\n{style_profile['summary']}\nFocus: {style_profile['focus']}",
            inline=True,
        )
        root_elements = ", ".join(str(x) for x in aptitude_profile["root"].get("elements", [normalized_root]))
        embed.add_field(
            name="💠 Heaven-Rolled Spiritual Root",
            value=(
                f"**{aptitude_profile['root']['grade']} {normalized_root}**\n"
                f"Purity **{aptitude_profile['root']['purity']}%** • Elements: **{root_elements[:120]}**\n"
                "Rolled from family lineage and homeland; never player-selected."
            ),
            inline=True,
        )
        if str(self.concept_input.value).strip():
            embed.add_field(name="🧭 Personal Dao", value=str(self.concept_input.value).strip()[:900], inline=False)
        embed.add_field(
            name="🌱 Starting State",
            value=(
                f"**{WORLD.realm_name(0, self.selected_gender)}, Stage 1** • Age **18** • Natural mortal lifespan **{natural_lifespan} years**\n"
                "Your family, location, household standing, rolled root and chosen path are now part of the AI narrator context."
            ),
            inline=False,
        )
        embed.add_field(
            name="Next steps",
            value=(
                "You begin **inside your birth household**. Look around first:\n"
                "**/character → Overview** • **/family → View** • **/cultivation → Cultivate**\n\n"
                "Exploring and hunting need the open world, so they stay closed until you step outside "
                "with **/family → Leave**. After that:\n"
                "**/world → Explore** • **/npc → Talk** • **/action**"
            ),
            inline=False,
        )
        embed.set_footer(text="The authoritative game engine owns mechanics; AI only narrates validated canonical results.")
        await respond(interaction, embed=embed, ephemeral=True)
        # First-sight art applies to every character, regardless of birthplace.
        # A character who starts in an illustrated location discovers it here;
        # everyone else receives the same art only when canonical exploration or
        # travel later records their first arrival/discovery of that location.
        if location in LOCATION_DISCOVERY_IMAGES:
            discovery_art = location_discovery_image_path(location)
            if discovery_art is not None:
                filename = discovery_art.name
                await interaction.followup.send(
                    embed=location_discovery_embed(location, filename=filename),
                    file=discord.File(discovery_art, filename=filename),
                    ephemeral=True,
                )

        # Character creation is the natural point to establish the player's
        # persistent private scene. Previously this was delayed until the first
        # exploration or /action, which made a successful /begin look incomplete.
        #
        # New characters always start *inside* their birth-family household (see
        # character.create in the Go engine) - a private residence where world
        # exploration is blocked. Pointing them at an "expedition journal" thread
        # with "use /world -> Explore" as the very first instruction sent every
        # new player straight into a dead-end error with no indication of how to
        # get out. The private scene created here now matches where the
        # character actually is: the shared household thread, not the
        # expedition journal, with guidance that leads them out of it first.
        private_thread: discord.Thread | None = None
        thread_is_household = False
        try:
            character = await DB.get_character(interaction.user.id)
            if character is not None:
                start_location = str(character.get("location") or "")
                if start_location.startswith("birth_family:"):
                    family = await DB.get_birth_family(interaction.user.id)
                    if family:
                        private_thread = await ensure_birth_family_household_thread(interaction, family)
                        thread_is_household = private_thread is not None
                if private_thread is None:
                    private_thread = await ensure_expedition_thread(interaction, character)
        except Exception:
            log.exception("Could not provision private starting thread after character creation")
        if private_thread is not None and thread_is_household:
            await interaction.followup.send(
                f"🏠 Your household scene is ready: {private_thread.mention}\n"
                "Every cultivator begins at home, indoors with their family — so exploring and hunting "
                "won't work yet, and that's not a bug. Meet them with **/family → View**, then "
                "**/family → Leave** when you want to step outside. The world opens up from there: "
                "**/world → Explore**, **/npc → Talk**, or **/action**.",
                ephemeral=True,
            )
        elif private_thread is not None:
            await interaction.followup.send(
                f"🧭 Your private expedition journal is ready: {private_thread.mention}\n"
                "Everything you explore is written there. Continue with **/world → Explore** or **/action**.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "⚠️ Your cultivator was created, but Discord did not create the private starting thread. "
                "Ask an administrator to run **/admin → Server → Setup Server** and confirm the bot has "
                "**Create Private Threads**, **Send Messages in Threads**, and **Manage Threads**.",
                ephemeral=True,
            )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await _report_game_ui_error(
            interaction,
            error,
            where="character-creation-modal",
            ephemeral=True,
        )


def _birth_family_preview_embed(
    family: dict[str, Any],
    *,
    index: int,
    total: int,
    selected_style: str | None = None,
    selected_gender: str | None = None,
    stage: str = "family",
) -> discord.Embed:
    """Render exactly one family card at a time."""
    location = str(family.get("location") or "Unknown")
    theme = location_theme(location)
    recommended = recommended_cultivation_styles(family)
    rec_text = " • ".join(
        f"{cultivation_style_profile(path)['emoji']} **{path}**" for path in recommended
    ) or "Any cultivation style can emerge from this household."
    tendencies = family_root_tendencies(family)
    root_text = " • ".join(f"**{root}**" for root in tendencies) or "No strong elemental tendency"
    location_description = str(WORLD.locations.get(location, {}).get("description") or theme.get("mood") or "Unknown homeland")

    embed = discord.Embed(
        title=f"{family_emoji(family)} Family {index + 1}/{total} — {family['family_name']}",
        description=(
            f"**{family['name']}**\n"
            f"{theme['emoji']} **{location}** — {location_description[:420]}"
        ),
        color=int(theme["color"]),
    )
    embed.add_field(
        name="Family standing",
        value=(
            f"**{family_tier_name(int(family.get('tier', 1)))}**\n"
            f"{family_status_summary(family)}\n"
            f"Wealth **{family.get('wealth', 0)}** • Influence **{family.get('influence', 0)}** • Stability **{family.get('stability', 0)}**"
        ),
        inline=False,
    )
    embed.add_field(name="Upbringing / help", value=str(family.get("boon", "None"))[:650], inline=False)
    embed.add_field(name="Pressure / risk", value=str(family.get("risk", "None"))[:650], inline=False)
    embed.add_field(
        name="💠 Spiritual-root tendencies",
        value=(
            f"Weighted toward {root_text}. **The actual root is rolled only when the character is created.** "
            "Every canonical root remains possible."
        )[:1000],
        inline=False,
    )
    embed.add_field(name="Recommended cultivation paths", value=rec_text[:1000], inline=False)
    if str(family.get("id", "")) == "alchemy_family":
        embed.add_field(
            name="⚗️ Inherited Alchemy Tradition",
            value="+2 Alchemy refinement checks and +2 medicinal-herb foraging checks.",
            inline=False,
        )
    if selected_style:
        profile = cultivation_style_profile(selected_style)
        embed.add_field(
            name="Selected cultivation path",
            value=f"{profile['emoji']} **{selected_style}** — {profile['summary']}",
            inline=False,
        )
    if selected_gender:
        embed.add_field(
            name="Birth sex",
            value=f"**{selected_gender.title()}**",
            inline=True,
        )

    if stage == "family":
        embed.set_footer(text="Browse one family at a time with Previous/Next, then press Choose Family.")
    else:
        embed.set_footer(text="Family locked. Choose a cultivation path and birth sex from the dropdowns, or go back to change family.")
    return embed


class BirthFamilyPreviousButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Previous", style=discord.ButtonStyle.secondary, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, BirthFamilyView):
            return
        view.current_index = (view.current_index - 1) % len(view.families)
        await interaction.response.edit_message(embed=view.current_embed(), view=view)


class BirthFamilyChooseButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Choose Family", style=discord.ButtonStyle.success, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, BirthFamilyView):
            return
        view.selected_index = view.current_index
        view.selected_style = None
        view.selected_gender = None
        view.stage = "style"
        view.rebuild_components()
        await interaction.response.edit_message(embed=view.current_embed(), view=view)


class BirthFamilyNextButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Next", style=discord.ButtonStyle.secondary, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, BirthFamilyView):
            return
        view.current_index = (view.current_index + 1) % len(view.families)
        await interaction.response.edit_message(embed=view.current_embed(), view=view)


class CultivationStyleSelect(discord.ui.Select):
    def __init__(self, family: dict[str, Any], selected_style: str | None):
        preferred = list(recommended_cultivation_styles(family))
        ordered = preferred + [path for path in WORLD.paths if path not in preferred]
        options = []
        for path in ordered:
            profile = cultivation_style_profile(path)
            marker = "Recommended • " if path in preferred else ""
            options.append(discord.SelectOption(
                label=path,
                value=path,
                description=(marker + profile["summary"])[:100],
                emoji=profile["emoji"],
                default=path == selected_style,
            ))
        super().__init__(placeholder="Choose your cultivation path", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, BirthFamilyView) or view.selected_index is None:
            await interaction.response.send_message("Choose a family first.", ephemeral=True)
            return
        view.selected_style = str(self.values[0])
        view.rebuild_components()
        await interaction.response.edit_message(embed=view.current_embed(), view=view)


class BirthSexSelect(discord.ui.Select):
    def __init__(self, selected_gender: str | None):
        options = [
            discord.SelectOption(label="Male", value="male", description="Born male", default=selected_gender == "male"),
            discord.SelectOption(label="Female", value="female", description="Born female", default=selected_gender == "female"),
        ]
        super().__init__(placeholder="Choose birth sex", min_values=1, max_values=1, options=options, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, BirthFamilyView) or view.selected_index is None:
            await interaction.response.send_message("Choose a family first.", ephemeral=True)
            return
        view.selected_gender = str(self.values[0])
        view.rebuild_components()
        await interaction.response.edit_message(embed=view.current_embed(), view=view)


class BirthFamilyBackButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Change Family", style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, BirthFamilyView):
            return
        if view.selected_index is not None:
            view.current_index = view.selected_index
        view.selected_index = None
        view.selected_style = None
        view.selected_gender = None
        view.stage = "family"
        view.rebuild_components()
        await interaction.response.edit_message(embed=view.current_embed(), view=view)


class BirthFamilyConfirmButton(discord.ui.Button):
    def __init__(self, *, disabled: bool = True) -> None:
        super().__init__(label="Open Character Form", style=discord.ButtonStyle.success, disabled=disabled, row=2)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, BirthFamilyView) or view.selected_index is None:
            await interaction.response.send_message("Choose a family first.", ephemeral=True)
            return
        if not view.selected_style:
            await interaction.response.send_message("Choose a cultivation path from the dropdown first.", ephemeral=True)
            return
        if view.selected_gender not in {"male", "female"}:
            await interaction.response.send_message("Choose Male or Female from the birth-sex dropdown first.", ephemeral=True)
            return
        await interaction.response.send_modal(CharacterModal(
            view.families[view.selected_index], view.selected_style, view.selected_gender, view.offer_state_version
        ))


class BirthFamilyView(discord.ui.View):
    def __init__(self, user_id: int, families: list[dict[str, Any]], offer_state_version: int):
        super().__init__(timeout=300)
        self.user_id = int(user_id)
        self.families = list(families)
        self.offer_state_version = int(offer_state_version)
        self.current_index = 0
        self.selected_index: int | None = None
        self.selected_style: str | None = None
        self.selected_gender: str | None = None
        self.stage = "family"
        self.rebuild_components()

    def current_embed(self) -> discord.Embed:
        if not self.families:
            return discord.Embed(title="No birth families available", color=0xAA0000)
        idx = self.selected_index if self.stage == "style" and self.selected_index is not None else self.current_index
        return _birth_family_preview_embed(
            self.families[idx],
            index=idx,
            total=len(self.families),
            selected_style=self.selected_style,
            selected_gender=self.selected_gender,
            stage=self.stage,
        )

    def rebuild_components(self) -> None:
        self.clear_items()
        if self.stage == "family":
            self.add_item(BirthFamilyPreviousButton())
            self.add_item(BirthFamilyChooseButton())
            self.add_item(BirthFamilyNextButton())
            return
        if self.selected_index is not None:
            self.add_item(CultivationStyleSelect(self.families[self.selected_index], self.selected_style))
            self.add_item(BirthSexSelect(self.selected_gender))
        self.add_item(BirthFamilyBackButton())
        self.add_item(BirthFamilyConfirmButton(
            disabled=not self.selected_style or self.selected_gender not in {"male", "female"}
        ))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("These character-creation choices belong to another player.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        await _report_game_ui_error(
            interaction,
            error,
            where=f"character-creation:{type(item).__name__}",
            ephemeral=True,
        )


