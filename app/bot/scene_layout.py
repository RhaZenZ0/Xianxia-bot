"""Components V2 layout for the Scene Action panel.

Why this is its own module
--------------------------
The 17 command hubs moved to the action-list layout in v0.19.5-v0.19.7. The Scene
Action panel is not a hub, so it was missed entirely and kept opening with two
dropdowns - "Choose what you are trying to do" and "Choose a target" - which is
exactly the shape that redesign existed to remove.

The other half of the report was "scene action need to travel with the player".
The old view snapshotted character, location and the NPC/player target list at
construction and never looked again. ``_resolve_scene_action`` *does* re-read the
character and rejects a target who is no longer present, so a player who
travelled could still pick a stale NPC off the panel and only discover it after
writing their attempt into the modal - with the panel still showing the location
they had left. Every callback here re-reads state first.

Everything the panel needs about the game is injected, so this module imports
``discord`` and nothing else from the project. That is what lets the whole panel
be constructed and its component tree counted in a build sandbox that has no
discord.py, which is the only way the 40-component budget gets checked before a
player finds the limit for us.

No select menus anywhere, deliberately. A ``Select`` nested inside a V2
``Container`` has no precedent in this codebase - ``HubLayoutSystemSelect`` was
deleted rather than kept - and cannot be exercised here. Targets are buttons,
paged, using only the patterns the 17 hubs already prove in production.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping, Sequence

import discord

#: Discord counts every component in a message, nested ones included, against a
#: limit of 40. The worst case here is 8 actions (2 rows) plus a full target page
#: (2 rows) plus chrome; :func:`component_budget` measures it exactly.
COMPONENT_LIMIT = 40
TARGETS_PER_PAGE = 10
BUTTONS_PER_ROW = 5
ENVIRONMENT = "Environment"
SELF = "Self"

_LAYOUT_COMPONENT_NAMES = (
    "LayoutView", "Container", "Section", "TextDisplay", "Separator", "ActionRow",
)
LAYOUT_COMPONENTS_AVAILABLE = all(
    hasattr(discord.ui, name) for name in _LAYOUT_COMPONENT_NAMES
)

#: ``reload_state(owner_id)`` returns None (nothing changed / lookup failed) or a
#: mapping with ``character``, ``npcs`` and ``location_display``.
ReloadState = Callable[[int], Awaitable[Mapping[str, Any] | None]]
#: ``open_modal(interaction, action_key, target)``
OpenModal = Callable[[discord.Interaction, str, str], Awaitable[None]]


def target_label(target: str) -> str:
    """Player targets are stored as ``Player <id>: <name>``.

    Showing that raw is the same mistake Equip made when it asked players to type
    an equipment id: a database key is not a thing a person recognises.
    """
    text = str(target or "")
    if text.startswith("Player ") and ":" in text:
        return text.split(":", 1)[1].strip()[:78] or "Cultivator"
    return text[:78] or ENVIRONMENT


class SceneLayoutActionButton(discord.ui.Button):
    def __init__(self, view: "SceneActionLayoutView", key: str) -> None:
        profile = view.profiles[key]
        self.scene_view = view
        self.action_key = key
        super().__init__(
            label=str(profile["label"])[:80],
            emoji=str(profile.get("emoji") or ""),
            style=(
                discord.ButtonStyle.primary
                if key == view.action_key
                else discord.ButtonStyle.secondary
            ),
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.scene_view.action_key = self.action_key
        await self.scene_view.refresh_and_edit(interaction)


class SceneLayoutTargetButton(discord.ui.Button):
    def __init__(self, view: "SceneActionLayoutView", target: str) -> None:
        self.scene_view = view
        self.target_value = target
        emoji = "🌍" if target == ENVIRONMENT else ("🧘" if target == SELF else "👤")
        super().__init__(
            label=target_label(target),
            emoji=emoji,
            style=(
                discord.ButtonStyle.primary
                if target == view.target
                else discord.ButtonStyle.secondary
            ),
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.scene_view.target = self.target_value
        await self.scene_view.refresh_and_edit(interaction)


class SceneLayoutTargetPageButton(discord.ui.Button):
    def __init__(self, view: "SceneActionLayoutView") -> None:
        self.scene_view = view
        super().__init__(label="More targets", emoji="▶️", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.scene_view
        view.target_offset += TARGETS_PER_PAGE
        if view.target_offset >= len(view.all_targets()):
            view.target_offset = 0
        await view.refresh_and_edit(interaction)


class SceneLayoutRefreshButton(discord.ui.Button):
    def __init__(self, view: "SceneActionLayoutView") -> None:
        self.scene_view = view
        super().__init__(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.scene_view.refresh_and_edit(interaction)


class SceneLayoutResolveButton(discord.ui.Button):
    def __init__(self, view: "SceneActionLayoutView") -> None:
        self.scene_view = view
        super().__init__(
            label="Describe & Resolve", emoji="🎭", style=discord.ButtonStyle.success
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        # Re-read before opening the modal. A target who left while the panel sat
        # idle must be caught here, not after the player has written a paragraph
        # into the modal and pressed submit.
        changed = await self.scene_view.reload()
        if changed:
            self.scene_view.rebuild()
            await interaction.response.edit_message(view=self.scene_view)
            return
        await self.scene_view.open_modal(
            interaction, self.scene_view.action_key, self.scene_view.target
        )


_Base = discord.ui.LayoutView if LAYOUT_COMPONENTS_AVAILABLE else discord.ui.View


class SceneActionLayoutView(_Base):
    """Action-list Scene Action panel that re-reads state on every interaction."""

    def __init__(
        self,
        *,
        owner_id: int,
        profiles: Mapping[str, Mapping[str, Any]],
        character: Mapping[str, Any],
        npcs: Sequence[str],
        location_display: str,
        reload_state: ReloadState,
        open_modal: OpenModal,
        action_key: str = "observe",
        accent_colour: int = 0x6D78A8,
        timeout: float | None = 900,
    ) -> None:
        super().__init__(timeout=timeout)
        if not profiles:
            raise ValueError("Scene Action panel needs at least one action profile")
        self.owner_id = int(owner_id)
        self.profiles = dict(profiles)
        self.character = dict(character)
        self.npcs = list(npcs)
        self.location_display = str(location_display or "Unknown")
        self.reload_state = reload_state
        self.open_modal = open_modal
        self.accent_colour = accent_colour
        self.action_key = action_key if action_key in self.profiles else next(iter(self.profiles))
        self.target = ENVIRONMENT
        self.target_offset = 0
        self.notice = ""
        self.rebuild()

    # ------------------------------------------------------------------ state
    def all_targets(self) -> list[str]:
        return [ENVIRONMENT, SELF] + list(self.npcs)

    async def reload(self) -> bool:
        """Re-read the player's scene. True when something the panel shows moved."""
        state = await self.reload_state(self.owner_id)
        if not state:
            return False
        previous_location = str(self.character.get("location") or "")
        self.character = dict(state.get("character") or self.character)
        self.npcs = list(state.get("npcs") or [])
        self.location_display = str(state.get("location_display") or self.location_display)
        moved = str(self.character.get("location") or "") != previous_location
        changed = moved
        if moved:
            self.target_offset = 0
            self.notice = f"📍 You travelled. The scene is now **{self.location_display}**."
        if self.target not in {ENVIRONMENT, SELF} and self.target not in self.npcs:
            # Leaving a dead target selected only defers the failure to resolve
            # time, after the player has already described their attempt.
            self.target = ENVIRONMENT
            self.notice = (
                "📍 Your previous target is no longer in this scene — target reset to "
                f"**{ENVIRONMENT}** at **{self.location_display}**."
            )
            changed = True
        if not changed:
            self.notice = ""
        return changed

    async def refresh_and_edit(self, interaction: discord.Interaction) -> None:
        await self.reload()
        self.rebuild()
        await interaction.response.edit_message(view=self)

    # ------------------------------------------------------------------ render
    def header_text(self) -> str:
        profile = self.profiles[self.action_key]
        check = (
            "**Automatic success** · hidden information stays concealed"
            if self.target == SELF
            else f"**{str(profile.get('attribute', '')).title()}** vs **TN {profile.get('tn')}**"
        )
        lines = [
            "🎭 **Scene Action**",
            "-# The engine picks the stat and difficulty, then the read-only narrator "
            "describes the fixed outcome. Self-directed actions succeed automatically "
            "but cannot reveal hidden information.",
            "",
            f"{profile.get('emoji', '')} **{profile['label']}** → **{target_label(self.target)}**",
            f"-# {profile.get('description', '')}",
            f"Check: {check}",
            f"📍 **{self.location_display}**",
        ]
        if self.notice:
            lines.append(self.notice)
        lines.append("-# No reward or state change is invented by narration.")
        return "\n".join(lines)[:3500]

    def rebuild(self) -> None:
        self.clear_items()
        container = discord.ui.Container(accent_colour=self.accent_colour)
        container.add_item(discord.ui.TextDisplay(self.header_text()))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay("**Action**"))
        keys = list(self.profiles)
        for start in range(0, len(keys), BUTTONS_PER_ROW):
            row = discord.ui.ActionRow()
            for key in keys[start : start + BUTTONS_PER_ROW]:
                row.add_item(SceneLayoutActionButton(self, key))
            container.add_item(row)

        targets = self.all_targets()
        if self.target_offset >= len(targets):
            self.target_offset = 0
        visible = targets[self.target_offset : self.target_offset + TARGETS_PER_PAGE]
        container.add_item(discord.ui.Separator())
        shown = f" · showing {len(visible)} of {len(targets)}" if len(targets) > len(visible) else ""
        container.add_item(discord.ui.TextDisplay(f"**Target**{shown}"))
        for start in range(0, len(visible), BUTTONS_PER_ROW):
            row = discord.ui.ActionRow()
            for target in visible[start : start + BUTTONS_PER_ROW]:
                row.add_item(SceneLayoutTargetButton(self, target))
            container.add_item(row)

        container.add_item(discord.ui.Separator())
        controls = discord.ui.ActionRow()
        controls.add_item(SceneLayoutResolveButton(self))
        controls.add_item(SceneLayoutRefreshButton(self))
        if len(targets) > TARGETS_PER_PAGE:
            controls.add_item(SceneLayoutTargetPageButton(self))
        container.add_item(controls)

        self.add_item(container)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "This Scene Action panel belongs to another cultivator.", ephemeral=False
                )
            return False
        return True


def component_budget(view: SceneActionLayoutView) -> int:
    """Count every component in the message, nested ones included.

    Discord's limit is 40 and it counts nested components, so a panel that looks
    fine in source can be rejected at send time. This is what the tests measure.
    """
    total = 0
    stack: list[Any] = list(getattr(view, "children", []) or [])
    while stack:
        item = stack.pop()
        total += 1
        children = list(getattr(item, "children", []) or [])
        accessory = getattr(item, "accessory", None)
        if accessory is not None:
            children.append(accessory)
        stack.extend(children)
    return total
