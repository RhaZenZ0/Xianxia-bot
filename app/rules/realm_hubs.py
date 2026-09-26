from __future__ import annotations

from typing import Any, Mapping

# The four worlds, and the one place they are enumerated. Every per-world loop
# in the tree reads this: the access and presence roles, the capital channels,
# which world an auction floor belongs to, and - since v1.0.0-rc.52 - the
# per-world world-events channels, and since v1.7.0 the per-world market-stalls
# channels. A fifth world is one entry here, not five
# edits in five files.
REALM_HUBS: dict[str, dict[str, Any]] = {
    "Mortal World": {
        "location": "Azure Crown Imperial City",
        "channel_name": "mortal-world-capital",
        "display_name": "Azure Crown Imperial City",
        "topic": "Mortal World meeting city — markets, sect envoys, clans, duels of reputation, and public roleplay.",
        "min_realm_index": 0,
        "events_channel_name": "mortal-world-events",
        "events_topic": (
            "Mortal World news — "
            "what the living world did to itself: disasters, invasions, sect wars, discoveries and the rise and fall of its powers. Visible to anyone who has reached this world."
        ),
        "stalls_channel_name": "mortal-world-stalls",
        "stalls_topic": (
            "Mortal World market stalls — "
            "one card per open stall in this world, kept current by the bot. Buy from anywhere with /economy → Market Stalls → Buy; goods from farther off cost a little more a road."
        ),
    },
    "Spiritual World": {
        "location": "Spirit Jade Capital",
        "channel_name": "spiritual-world-capital",
        "display_name": "Spirit Jade Capital",
        "topic": "Spiritual World meeting city — ascended sects, ancient families, jade markets, and public roleplay.",
        "min_realm_index": 8,
        "events_channel_name": "spiritual-world-events",
        "events_topic": (
            "Spiritual World news — "
            "upheavals among the ascended sects and ancient families, and every event the world opens up here. Visible to anyone who has reached this world."
        ),
        "stalls_channel_name": "spiritual-world-stalls",
        "stalls_topic": (
            "Spiritual World market stalls — "
            "one card per open stall in this world, kept current by the bot. Buy from anywhere with /economy → Market Stalls → Buy; goods from farther off cost a little more a road."
        ),
    },
    "Immortal World": {
        "location": "Nine-Heavens Immortal Court",
        "channel_name": "immortal-world-court",
        "display_name": "Nine-Heavens Immortal Court",
        "topic": "Immortal World meeting court — immortal clans, law formations, trade, politics, and public roleplay.",
        "min_realm_index": 16,
        "events_channel_name": "immortal-world-events",
        "events_topic": (
            "Immortal World news — "
            "immortal clans, law formations and the events that shake the court. Visible to anyone who has reached this world."
        ),
        "stalls_channel_name": "immortal-world-stalls",
        "stalls_topic": (
            "Immortal World market stalls — "
            "one card per open stall in this world, kept current by the bot. Buy from anywhere with /economy → Market Stalls → Buy; goods from farther off cost a little more a road."
        ),
    },
    "Celestial World": {
        "location": "Celestial Mandate Palace",
        "channel_name": "celestial-world-palace",
        "display_name": "Celestial Mandate Palace",
        "topic": "Celestial World meeting palace — sovereign courts, heavenly factions, high trade, and public roleplay.",
        "min_realm_index": 24,
        "events_channel_name": "celestial-world-events",
        "events_topic": (
            "Celestial World news — "
            "sovereign courts, heavenly factions and the events that move them. Visible to anyone who has reached this world."
        ),
        "stalls_channel_name": "celestial-world-stalls",
        "stalls_topic": (
            "Celestial World market stalls — "
            "one card per open stall in this world, kept current by the bot. Buy from anywhere with /economy → Market Stalls → Buy; goods from farther off cost a little more a road."
        ),
    },
}


def realm_hub(world_name: str) -> dict[str, Any] | None:
    hub = REALM_HUBS.get(str(world_name))
    return dict(hub) if hub else None


def city_of_place(location: Any, locations: Mapping[str, Any] | None = None) -> str:
    """The city a place is part of - the engine's `cityOf`, twinned.

    A district, shop or auction house answers its `outside_location`; anything
    else answers itself. `locations` is injected because `rules` imports
    nothing above it; without it every place is its own city.
    """
    where = str(location or "").strip()
    place = (locations or {}).get(where) or {}
    if place.get("outside_location") and (place.get("district") or place.get("shop") or place.get("auction_house")):
        return str(place["outside_location"])
    return where


def realm_hub_by_location(location: Any, locations: Mapping[str, Any] | None = None) -> tuple[str, dict[str, Any]] | None:
    """The capital a place belongs to. Given `locations`, a gate, district,
    shop or auction hall of a capital is that capital (v1.7.2)."""
    target = city_of_place(location, locations)
    if not target:
        return None
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


def presence_world_for(location: Any, locations: Mapping[str, Any] | None = None) -> str | None:
    """Which world's capital the character is standing in, or None.

    A gate, district, shop or auction hall of the capital is the capital
    (v1.7.2): given the catalogue's `locations`, the place is first resolved to
    its city the way the engine's `cityOf` does - `outside_location` when the
    place is a district, shop or auction house. Matching the hub's name alone
    took the role off at the South Gate and in the capital's own inn, whose
    card links the very common room the role opens (reported as "no access to
    common room channel") - v1.0.9's "a city's gate is that city", missed here.
    A private residence, a road, or any other place is not the capital, so the
    role comes off. `locations` is injected because `rules` imports nothing
    above it; without it the match is exact.
    """
    hub = realm_hub_by_location(location, locations)
    return hub[0] if hub else None


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

