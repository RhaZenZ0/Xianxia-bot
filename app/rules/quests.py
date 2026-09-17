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


def next_objective_label(objectives: Any, progress: Any) -> str:
    """The first objective still short of its count, or "" when none is.

    This is the whole of "which door, now". A new character faces sixteen
    equally-weighted hubs with nothing saying which one is theirs today, and
    until v1.0.0-rc.26 a quest advancing said only "Quest progress: <title>" -
    it confirmed that something counted and then left the player exactly where
    they were. Naming what is still outstanding turns the journal into a
    pointer, and because the labels are written as hub paths, the reply names
    a real command (and, inside a hub panel, earns it as a button through
    `suggested_actions`).

    Objectives are taken in their authored order, so the first one outstanding
    is the one the author meant to come next.
    """
    done = dict(progress or {})
    for objective in list(objectives or []):
        if not isinstance(objective, dict):
            continue
        required = max(1, int(objective.get("count", 1) or 1))
        try:
            current = int(done.get(str(objective.get("id") or ""), 0) or 0)
        except (TypeError, ValueError):
            current = 0
        if current < required:
            label = str(objective.get("label") or "").strip()
            if not label:
                return ""
            if required > 1:
                return f"{label} ({current}/{required})"
            return label
    return ""


def household_errand_seed_rows(world: Any) -> list[dict[str, Any]]:
    """The household errands from `content/world.json`, shaped for the same seeder.

    `household_errands` is keyed by the trade a house teaches; each entry is
    an ordinary quest definition with no giver, which `family.errand` hands
    over at home one at a time through `grantOrdinaryQuestTx`. The key carries
    the `errand_` prefix, which is what lets the reward `household_standing`
    exist at all (see `validate_quest_definition`).
    """
    rows: list[dict[str, Any]] = []
    pools = dict(getattr(world, "data", {}).get("household_errands") or {})
    for trade in sorted(pools):
        for errand in list(pools[trade] or []):
            key = str(errand.get("quest_key") or "").strip()
            if not key.startswith(HOUSEHOLD_ERRAND_PREFIX):
                continue
            rows.append({
                "quest_key": key,
                "title": str(errand.get("title", "")),
                "description": str(errand.get("description", "")),
                "source_type": "system",
                "source_key": f"household_errand:{trade}",
                "objectives": list(errand.get("objectives", [])),
                "rewards": dict(errand.get("rewards", {})),
                "giver_npc": "",
                "realm_band": "",
                "tier": 1,
                "deadline_game_minutes": 0,
                "variants": [],
                "seed": {"trade": str(trade), "opening": str(errand.get("opening") or "")},
            })
    return rows


def beginner_path_seed_rows(world: Any) -> list[dict[str, Any]]:
    """The beginner path from `content/world.json`, shaped for the same seeder.

    The stages are content, not code, for the reason everything else in this
    file's neighbourhood is: the prose is the whole point of them and prose
    belongs in `content/world.json` beside the birth-family send-off, which is
    the thing they follow on from. What they are *not* is a new pipeline - a
    stage is an ordinary `quest_definitions` row, seeded exactly the way the
    authored commission pool is, and once a player is holding it the engine
    pins, progresses, completes and pays it as it would any other quest.

    `seed.follow_on` is the chain, and it lands in `seed_json`, which is where
    the engine reads it back from. That indirection is deliberate: a GM who
    re-points a chain in the dashboard workbench edits the row, and the shipped
    file is only ever the starting shape.
    """
    rows: list[dict[str, Any]] = []
    for stage in list(getattr(world, "data", {}).get("beginner_path") or []):
        key = str(stage.get("quest_key") or "").strip()
        if not key:
            continue
        rows.append({
            "quest_key": key,
            "title": str(stage.get("title", "")),
            "description": str(stage.get("description", "")),
            "source_type": "system",
            "source_key": "beginner_path",
            "objectives": list(stage.get("objectives", [])),
            "rewards": dict(stage.get("rewards", {})),
            # No giver, so it is an ordinary quest: it occupies no commission
            # slot, carries no deadline, and `grantOrdinaryQuestTx` will hand
            # it over. A giver here would make the engine refuse it, which is
            # the guard rather than the bug.
            "giver_npc": "",
            "realm_band": "",
            "tier": 1,
            "deadline_game_minutes": 0,
            "variants": [],
            "seed": {"follow_on": str(stage.get("follow_on") or "")},
        })
    return rows


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
#
# This list is the ceiling on everything the quest system can ask for - static
# quests, commissions and the Quest Forge alike - because a type nothing
# reports can never be progressed. Until v1.0.0-rc.25 it was the first five,
# and that was honest rather than stale: there were exactly five
# `QUESTS.progress(...)` calls in the whole bot and they were these. It also
# meant cultivation, combat, crafting, travel, the shops and the hills were
# invisible to every quest in the game - nobody could be asked to meditate,
# win a fight, make something, walk somewhere, buy something or pick a herb.
#
# The six below close that, and they cost the engine nothing: `progressQuest`
# matches `objective.Type` against no whitelist at all, so a new type is a
# vocabulary entry here plus one line at the command that already does the
# work. What each type costs is a *reporter*, and the rule is that the report
# is written after the authoritative action has already succeeded - the engine
# decides that something happened, and this only says so.
OBJECTIVE_TYPES: dict[str, dict[str, Any]] = {
    "explore": {"target": "location", "label": "Explore {target}", "untargeted": "Complete an exploration"},
    "talk": {"target": "npc", "label": "Speak with {target}", "untargeted": "Speak with a persistent NPC"},
    "scene_action": {"target": "scene_action", "label": "Resolve a Scene Action: {target}", "untargeted": "Resolve a Scene Action"},
    "sect_discovery": {"target": None, "label": "", "untargeted": "Discover a sect route"},
    "sect_trial": {"target": None, "label": "", "untargeted": "Attempt a sect entrance trial"},
    "cultivate": {"target": None, "label": "", "untargeted": "Sit one cultivation session"},
    "travel": {"target": "location", "label": "Travel to {target}", "untargeted": "Travel to another known place"},
    # Untargeted on purpose. The reporter passes the opponent's name, which an
    # untargeted objective accepts and a later targeted one could use - but
    # there is no roster to validate a draft against, because an opponent may
    # be a catalogue NPC, an event manifestation or a beast off the hunt
    # roster, and only the first of those is in `world.npcs`. A quest that
    # names a beast the validator cannot find would be refused; one that names
    # an NPC would pass and then be unreachable for everybody who met a beast.
    "combat_win": {"target": None, "label": "", "untargeted": "Win a battle"},
    "craft": {"target": "recipe", "label": "Craft {target}", "untargeted": "Craft something from a method you know"},
    "trade": {"target": "item", "label": "Buy or sell {target}", "untargeted": "Buy or sell at a shop"},
    "gather": {"target": "item", "label": "Gather {target}", "untargeted": "Forage a material out of the hills"},
    # Reported by `/family → Enter` and by a Hearth-Return Talisman
    # (v1.0.0-rc.32). Untargeted by construction: the household is the
    # player's own, and `birth_family:<id>` is not a catalogue location.
    "return_home": {"target": None, "label": "", "untargeted": "Return to your birth household"},
    # Reported by `/family → Hearth → Lesson` only when the head's test is
    # passed (v1.0.0-rc.34). Untargeted: the head of the house is the player's
    # own, a generated name no catalogue could validate.
    "family_lesson": {"target": None, "label": "", "untargeted": "Take the head of the house's last lesson and pass its test"},
}
SCENE_ACTION_KEYS = ("observe", "investigate", "influence", "stealth", "physical", "qi", "resolve", "aid")
REWARD_KEYS = ("insight_xp", "spirit_stones", "items")
# What a household errand may pay besides those (v1.0.0-rc.32): standing with
# the house. Only an errand's key may carry it - the engine pays it on no other
# key and the validator refuses it on any other draft - so the Forge cannot
# inflate a house's opinion of a player by drafting a quest that says so.
HOUSEHOLD_ERRAND_PREFIX = "errand_"
HOUSEHOLD_STANDING_REWARD_KEY = "household_standing"
HOUSEHOLD_STANDING_REWARD_MAX = 25
MAX_OBJECTIVES = 4
MAX_OBJECTIVE_COUNT = 5


def quest_key_for(title: str, existing: set[str] | None = None, *, prefix: str = "forge") -> str:
    """A stable key for a new quest, namespaced so it cannot shadow a static one.

    `prefix` says who made it, and it is not decoration: `_quest_source` and the
    GM reading the table both take `forge_` to mean a model wrote this. A quest
    typed by hand in the dashboard passes `quest` (v0.24.0), so the pool does
    not claim authorship the Forge never had.
    """
    base = re.sub(r"[^a-z0-9]+", "_", str(title).lower()).strip("_")[:48] or "forged_quest"
    namespace = re.sub(r"[^a-z0-9]+", "", str(prefix).lower()) or "forge"
    key = f"{namespace}_{base}"
    if existing:
        n = 2
        while key in existing:
            key = f"{namespace}_{base}_{n}"
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
    # Recipes are keyed by their own display name ("Recovery Pill"); items are
    # keyed by id with the name beside it, so a `gather spirit_herb` objective
    # stores the id the reporter will send and prints "Gather Spirit Herb".
    recipe_lookup = {name.lower(): name for name in dict(getattr(world, "recipes", {}) or {})}
    # Every catalogue item, not `rewardable_items`: that set exists to stop a
    # quest *paying out* something unique or market-excluded, which is a
    # different question from whether a quest may ask you to pick one up.
    all_items = dict(getattr(world, "items", {}) or {})
    item_lookup = {str(key).lower(): str(key) for key in all_items}

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
        # What the label prints, which is the target itself everywhere except
        # an item: the objective has to store `spirit_herb`, because that is
        # what the reporter sends, and the player has to read "Spirit Herb".
        target_label: str | None = None
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
            elif spec["target"] == "recipe":
                target = recipe_lookup.get(target_text.lower())
                if target is None:
                    errors.append(f"objective {index + 1}: unknown recipe {target_text!r}")
                    continue
            elif spec["target"] == "item":
                target = item_lookup.get(target_text.lower().replace(" ", "_"))
                if target is None:
                    errors.append(f"objective {index + 1}: unknown item {target_text!r}")
                    continue
                target_label = str(all_items[target].get("name") or target)
        objective_id = re.sub(r"[^a-z0-9_]+", "_", str(raw.get("id") or f"{kind}_{index + 1}").lower()).strip("_") or f"{kind}_{index + 1}"
        if objective_id in seen_ids:
            objective_id = f"{objective_id}_{index + 1}"
        seen_ids.add(objective_id)
        label = str(raw.get("label") or "").strip()[:90]
        if not label:
            printed = target_label or target
            label = spec["label"].format(target=printed.title() if spec["target"] == "scene_action" else printed) if target else spec["untargeted"]
        objective = {"id": objective_id, "type": kind, "count": count, "label": label}
        if target is not None:
            objective["target"] = target
        objectives.append(objective)

    rewards_in = draft.get("rewards") if isinstance(draft.get("rewards"), dict) else {}
    rewards: dict[str, Any] = {}
    allowed_rewards = set(REWARD_KEYS)
    is_errand = str(draft.get("quest_key") or "").startswith(HOUSEHOLD_ERRAND_PREFIX)
    if is_errand:
        allowed_rewards.add(HOUSEHOLD_STANDING_REWARD_KEY)
    unknown = sorted(set(rewards_in) - allowed_rewards)
    if unknown:
        errors.append(f"unknown reward keys: {', '.join(unknown)} (allowed: {', '.join(sorted(allowed_rewards))})")
    if is_errand and HOUSEHOLD_STANDING_REWARD_KEY in rewards_in:
        try:
            standing = int(rewards_in[HOUSEHOLD_STANDING_REWARD_KEY])
        except (TypeError, ValueError):
            standing = -1
        if not 0 <= standing <= HOUSEHOLD_STANDING_REWARD_MAX:
            errors.append(f"reward {HOUSEHOLD_STANDING_REWARD_KEY} must be 0-{HOUSEHOLD_STANDING_REWARD_MAX}")
        elif standing:
            rewards[HOUSEHOLD_STANDING_REWARD_KEY] = standing
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
