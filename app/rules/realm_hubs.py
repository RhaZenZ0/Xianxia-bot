from __future__ import annotations

from typing import Any

REALM_HUBS: dict[str, dict[str, Any]] = {
    "Mortal World": {
        "location": "Azure Crown Imperial City",
        "channel_name": "mortal-world-capital",
        "display_name": "Azure Crown Imperial City",
        "topic": "Mortal World meeting city — markets, sect envoys, clans, duels of reputation, and public roleplay.",
        "min_realm_index": 0,
    },
    "Spiritual World": {
        "location": "Spirit Jade Capital",
        "channel_name": "spiritual-world-capital",
        "display_name": "Spirit Jade Capital",
        "topic": "Spiritual World meeting city — ascended sects, ancient families, jade markets, and public roleplay.",
        "min_realm_index": 8,
    },
    "Immortal World": {
        "location": "Nine-Heavens Immortal Court",
        "channel_name": "immortal-world-court",
        "display_name": "Nine-Heavens Immortal Court",
        "topic": "Immortal World meeting court — immortal clans, law formations, trade, politics, and public roleplay.",
        "min_realm_index": 16,
    },
    "Celestial World": {
        "location": "Celestial Mandate Palace",
        "channel_name": "celestial-world-palace",
        "display_name": "Celestial Mandate Palace",
        "topic": "Celestial World meeting palace — sovereign courts, heavenly factions, high trade, and public roleplay.",
        "min_realm_index": 24,
    },
}


def realm_hub(world_name: str) -> dict[str, Any] | None:
    hub = REALM_HUBS.get(str(world_name))
    return dict(hub) if hub else None


def realm_hub_by_location(location: str) -> tuple[str, dict[str, Any]] | None:
    target = str(location)
    for world_name, hub in REALM_HUBS.items():
        if str(hub["location"]) == target:
            return world_name, dict(hub)
    return None


def realm_hub_visibility(channel: Any, role: Any, everyone: Any) -> dict[str, Any]:
    """What a realm hub's overwrites say today, and whether it is actually gated.

    ``hidden`` is true only when @everyone is explicitly denied View Channel AND
    the world's access role is explicitly allowed it. Anything else - no
    overwrites, a deny without the allow, an allow without the deny - means the
    gate is not doing its job, whatever the roles look like.
    """
    overwrites = getattr(channel, "overwrites", {}) or {}
    everyone_view = getattr(overwrites.get(everyone), "view_channel", None) if everyone is not None else None
    role_view = getattr(overwrites.get(role), "view_channel", None) if role is not None else None
    return {
        "everyone_view": everyone_view,
        "role_view": role_view,
        "hidden": everyone_view is False and role_view is True,
        "role_present": role is not None,
    }


def realm_presence_role_name(world_name: str) -> str:
    """The role a cultivator holds only while standing in that world's capital.

    Distinct from the realm-access role ("Xianxia • <world>", earned by
    cultivation): this one follows the character's location and is what the
    capital channel's overwrites allow (v0.21.6).
    """
    hub = REALM_HUBS.get(str(world_name)) or {}
    return f"Xianxia • {hub.get('display_name') or world_name}"[:100]


def presence_world_for(location: Any) -> str | None:
    """Which world's capital the character is standing in, or None.

    Exact match on the hub's location: a private residence inside a city, a
    road, or any other place is not the capital, so the role comes off.
    """
    where = str(location or "").strip()
    for world, hub in REALM_HUBS.items():
        if where and where == str(hub.get("location") or ""):
            return world
    return None


# The channel permissions the presence role is granted on its capital. Everyone
# else is denied View Channel; the bot keeps an explicit allow for itself.
REALM_HUB_MEMBER_PERMISSIONS: dict[str, bool] = {
    "view_channel": True,
    "send_messages": True,
    "send_messages_in_threads": True,
    "read_message_history": True,
    "add_reactions": True,
    "embed_links": True,
    "attach_files": True,
    "use_application_commands": True,
}

