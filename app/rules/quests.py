from __future__ import annotations

import re
from typing import Any

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


def static_quest_seed_rows(definitions: dict[str, dict] | None = None) -> list[dict[str, Any]]:
    """The static quests, shaped for `Database.sync_commission_pool`.

    They are seeded so the engine can tell a real quest key from an invented
    one. `commission.accept` decides "is this a commission?" by looking for a
    `quest_definitions` row with a giver; before v0.23.1 it decided "does this
    quest exist?" the same way, so a key with no row - `first_steps` and
    `totally_fake` alike - was accepted as an ordinary quest.

    `giver_npc` is deliberately empty: these are not commissions, they occupy
    no commission slot and carry no deadline. The row exists to be found.
    """
    source = QUEST_DEFINITIONS if definitions is None else definitions
    rows: list[dict[str, Any]] = []
    for key, definition in source.items():
        rows.append({
            "quest_key": key,
            "title": str(definition.get("title", "")),
            "description": str(definition.get("description", "")),
            "source_type": str(definition.get("source_type", "system")),
            "source_key": str(definition.get("source_key", "")),
            "objectives": list(definition.get("objectives", [])),
            "rewards": dict(definition.get("rewards", {})),
            "giver_npc": "",
            "realm_band": "",
            "tier": 1,
            "deadline_game_minutes": 0,
            "variants": [],
        })
    return rows


# ---------------------------------------------------------------------------
# Quest Forge (v0.20.6): the vocabulary a drafted quest may use, validation
# of a draft against the world, and a procedural draft for when no model is
# available. Pure: reads the World object and dicts, decides nothing at
# runtime except "is this draft acceptable".
# ---------------------------------------------------------------------------

# objective type -> what its `target` must name (None = no target allowed).
# The engine matches targets case-insensitively (go_core/internal/core,
# progressQuest); the bot reports these types from the commands named.
OBJECTIVE_TYPES: dict[str, dict[str, Any]] = {
    "explore": {"target": "location", "label": "Explore {target}", "untargeted": "Complete an exploration"},
    "talk": {"target": "npc", "label": "Speak with {target}", "untargeted": "Speak with a persistent NPC"},
    "scene_action": {"target": "scene_action", "label": "Resolve a Scene Action: {target}", "untargeted": "Resolve a Scene Action"},
    "sect_discovery": {"target": None, "label": "", "untargeted": "Discover a sect route"},
    "sect_trial": {"target": None, "label": "", "untargeted": "Attempt a sect entrance trial"},
}
SCENE_ACTION_KEYS = ("observe", "investigate", "influence", "stealth", "physical", "qi", "resolve", "aid")
REWARD_KEYS = ("insight_xp", "spirit_stones", "items")
MAX_OBJECTIVES = 4
MAX_OBJECTIVE_COUNT = 5


def quest_key_for(title: str, existing: set[str] | None = None) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", str(title).lower()).strip("_")[:48] or "forged_quest"
    key = f"forge_{base}"
    if existing:
        n = 2
        while key in existing:
            key = f"forge_{base}_{n}"
            n += 1
    return key


def rewardable_items(world: Any) -> dict[str, dict[str, Any]]:
    """Items a forged quest may hand out: catalog items that are not market-
    excluded (the Bugslayer Sword and its like) and carry no unique flag."""
    out = {}
    for item_id, item in dict(getattr(world, "items", {}) or {}).items():
        if item.get("market_excluded") or item.get("unique") or item.get("indestructible"):
            continue
        out[str(item_id)] = item
    return out


def public_locations(world: Any) -> dict[str, dict[str, Any]]:
    return {name: loc for name, loc in dict(world.locations).items() if not loc.get("private")}


def validate_quest_definition(draft: dict[str, Any], world: Any, budget: dict[str, int]) -> tuple[dict[str, Any] | None, list[str]]:
    """Normalise a draft into the catalog shape, or explain why it cannot be.

    Returns (definition, errors). `definition` is None when any error is
    fatal. The budget keys are max_xp, max_stones, max_items (total item
    quantity). Anything the draft names that the world does not have is an
    error, not a silent drop - a quest that points at a location that does not
    exist is a dead end, which is the one thing the Forge must never make.
    """
    errors: list[str] = []
    if not isinstance(draft, dict):
        return None, ["draft is not an object"]
    title = str(draft.get("title") or "").strip()
    if not 3 <= len(title) <= 80:
        errors.append("title must be 3-80 characters")
    description = str(draft.get("description") or "").strip()
    if not 10 <= len(description) <= 600:
        errors.append("description must be 10-600 characters")

    locations = public_locations(world)
    npcs = {name: npc for name, npc in dict(world.npcs).items() if not isinstance(npc.get("hidden_master"), dict)}
    items = rewardable_items(world)
    location_lookup = {name.lower(): name for name in locations}
    npc_lookup = {name.lower(): name for name in npcs}

    objectives_in = draft.get("objectives")
    objectives: list[dict[str, Any]] = []
    if not isinstance(objectives_in, list) or not objectives_in:
        errors.append("at least one objective is required")
        objectives_in = []
    if len(objectives_in) > MAX_OBJECTIVES:
        errors.append(f"at most {MAX_OBJECTIVES} objectives")
    seen_ids: set[str] = set()
    for index, raw in enumerate(objectives_in[:MAX_OBJECTIVES]):
        if not isinstance(raw, dict):
            errors.append(f"objective {index + 1} is not an object")
            continue
        kind = str(raw.get("type") or "").strip().lower()
        spec = OBJECTIVE_TYPES.get(kind)
        if spec is None:
            errors.append(f"objective {index + 1}: unknown type {kind!r} (allowed: {', '.join(OBJECTIVE_TYPES)})")
            continue
        try:
            count = int(raw.get("count", 1))
        except (TypeError, ValueError):
            count = 0
        if not 1 <= count <= MAX_OBJECTIVE_COUNT:
            errors.append(f"objective {index + 1}: count must be 1-{MAX_OBJECTIVE_COUNT}")
            continue
        target_raw = raw.get("target")
        target: str | None = None
        if target_raw not in (None, ""):
            target_text = str(target_raw).strip()
            if spec["target"] is None:
                errors.append(f"objective {index + 1}: {kind} takes no target")
                continue
            if spec["target"] == "location":
                target = location_lookup.get(target_text.lower())
                if target is None:
                    errors.append(f"objective {index + 1}: unknown location {target_text!r}")
                    continue
            elif spec["target"] == "npc":
                target = npc_lookup.get(target_text.lower())
                if target is None:
                    errors.append(f"objective {index + 1}: unknown NPC {target_text!r}")
                    continue
            elif spec["target"] == "scene_action":
                target = target_text.lower()
                if target not in SCENE_ACTION_KEYS:
                    errors.append(f"objective {index + 1}: unknown scene action {target_text!r} (allowed: {', '.join(SCENE_ACTION_KEYS)})")
                    continue
        objective_id = re.sub(r"[^a-z0-9_]+", "_", str(raw.get("id") or f"{kind}_{index + 1}").lower()).strip("_") or f"{kind}_{index + 1}"
        if objective_id in seen_ids:
            objective_id = f"{objective_id}_{index + 1}"
        seen_ids.add(objective_id)
        label = str(raw.get("label") or "").strip()[:90]
        if not label:
            label = spec["label"].format(target=target.title() if spec["target"] == "scene_action" else target) if target else spec["untargeted"]
        objective = {"id": objective_id, "type": kind, "count": count, "label": label}
        if target is not None:
            objective["target"] = target
        objectives.append(objective)

    rewards_in = draft.get("rewards") if isinstance(draft.get("rewards"), dict) else {}
    rewards: dict[str, Any] = {}
    unknown = sorted(set(rewards_in) - set(REWARD_KEYS))
    if unknown:
        errors.append(f"unknown reward keys: {', '.join(unknown)} (allowed: {', '.join(REWARD_KEYS)})")
    for key, cap_key in (("insight_xp", "max_xp"), ("spirit_stones", "max_stones")):
        if key in rewards_in:
            try:
                value = int(rewards_in[key])
            except (TypeError, ValueError):
                errors.append(f"reward {key} must be a whole number")
                continue
            cap = int(budget.get(cap_key, 0))
            if value < 0:
                errors.append(f"reward {key} cannot be negative")
            elif value > cap:
                errors.append(f"reward {key}={value} exceeds the GM budget ({cap})")
            elif value:
                rewards[key] = value
    if "items" in rewards_in:
        raw_items = rewards_in["items"]
        if not isinstance(raw_items, dict):
            errors.append("reward items must be an object of item_id -> quantity")
        else:
            total = 0
            clean_items: dict[str, int] = {}
            for item_id, qty in raw_items.items():
                key = str(item_id).strip().lower().replace(" ", "_")
                if key not in items:
                    errors.append(f"reward item {item_id!r} is not a rewardable catalog item")
                    continue
                try:
                    amount = int(qty)
                except (TypeError, ValueError):
                    errors.append(f"reward item {key}: quantity must be a whole number")
                    continue
                if amount <= 0:
                    continue
                clean_items[key] = amount
                total += amount
            cap = int(budget.get("max_items", 0))
            if total > cap:
                errors.append(f"reward items total {total} exceeds the GM budget ({cap})")
            elif clean_items:
                rewards["items"] = clean_items
    if not rewards and not errors:
        rewards = {"insight_xp": min(int(budget.get("max_xp", 0)), 10)} if int(budget.get("max_xp", 0)) else {}

    if errors:
        return None, errors
    return {
        "title": title,
        "description": description,
        "source_type": "forge",
        "source_key": str(draft.get("source_key") or "gm_prompt")[:120],
        "objectives": objectives,
        "rewards": rewards,
    }, []


def procedural_quest_from_event(event: dict[str, Any], world: Any, budget: dict[str, int]) -> dict[str, Any]:
    """A draft built from a world-history row without a model: explore the
    place it happened, speak to whoever is there, resolve one fitting scene
    action. Always validates (the same function above proves it)."""
    location = str(event.get("location") or "")
    locations = public_locations(world)
    if location not in locations:
        location = next(iter(locations), "")
    npc_here = next((name for name, npc in dict(world.npcs).items()
                     if str(npc.get("location") or "") == location and not isinstance(npc.get("hidden_master"), dict)), None)
    kind = str(event.get("event_type") or "event")
    action = {"war_started": "resolve", "leadership_change": "influence", "discovery": "investigate",
              "inheritance": "investigate", "betrayal": "investigate"}.get(kind, "observe")
    title = str(event.get("title") or "Echoes of the Recent Past").strip()[:80]
    objectives: list[dict[str, Any]] = [{"id": "visit", "type": "explore", "count": 1, "target": location}]
    if npc_here:
        objectives.append({"id": "ask", "type": "talk", "count": 1, "target": npc_here})
    objectives.append({"id": "act", "type": "scene_action", "count": 1, "target": action})
    summary = str(event.get("summary") or "").strip()
    description = (f"Word of this has spread: {summary} " if summary else "") + (
        f"Go to {location}, learn what the locals know, and act on it."
    )
    return {
        "title": f"Aftermath: {title}"[:80],
        "description": description[:600],
        "source_key": f"history:{event.get('history_id', '')}",
        "objectives": objectives,
        "rewards": {"insight_xp": min(int(budget.get("max_xp", 0)), 25)},
    }
