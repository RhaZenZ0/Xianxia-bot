from __future__ import annotations

# Definitions deliberately use a small generic objective vocabulary.  Future sect,
# family and world-event quest generators can create the same shape without adding
# bespoke database tables or Discord commands.
QUEST_DEFINITIONS: dict[str, dict] = {
    "first_steps": {
        "title": "First Steps Beneath Heaven",
        "description": "Explore the world, speak to one persistent NPC, and make one deliberate Scene Action.",
        "source_type": "system",
        "source_key": "onboarding",
        "objectives": [
            {"id": "explore", "type": "explore", "count": 1, "label": "Complete an exploration"},
            {"id": "talk", "type": "talk", "count": 1, "label": "Speak with a persistent NPC"},
            {"id": "action", "type": "scene_action", "count": 1, "label": "Resolve a Scene Action"},
        ],
        "rewards": {"insight_xp": 25},
    },
    "road_to_a_sect": {
        "title": "A Road Toward a Sect",
        "description": "Discover a sect route and earn or attempt formal recruitment.",
        "source_type": "system",
        "source_key": "sect_recruitment",
        "objectives": [
            {"id": "sect_discovery", "type": "sect_discovery", "count": 1, "label": "Discover a sect"},
            {"id": "sect_trial", "type": "sect_trial", "count": 1, "label": "Attempt a sect entrance trial"},
        ],
        "rewards": {"insight_xp": 35},
    },
}
