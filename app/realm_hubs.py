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


def realm_hub_by_channel_name(channel_name: str) -> tuple[str, dict[str, Any]] | None:
    target = str(channel_name)
    for world_name, hub in REALM_HUBS.items():
        if str(hub["channel_name"]) == target:
            return world_name, dict(hub)
    return None
