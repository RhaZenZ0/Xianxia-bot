from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .advanced_catalog import augment_advanced_catalog


@dataclass(frozen=True)
class RollResult:
    die1: int
    die2: int
    modifier: int
    total: int
    tn: int

    @property
    def success(self) -> bool:
        return self.total >= self.tn

    @property
    def margin(self) -> int:
        return self.total - self.tn

    @property
    def degree(self) -> str:
        if self.success:
            if self.margin >= 10:
                return "Overwhelming Success"
            if self.margin >= 5:
                return "Strong Success"
            return "Success"
        if self.margin >= -3:
            return "Soft Failure"
        if self.margin >= -7:
            return "Hard Failure"
        return "Severe Failure"


class D10Source(Protocol):
    """Injected randomness boundary used by parity and replay tests."""

    def d10(self) -> int: ...


class SecretsD10Source:
    def d10(self) -> int:
        return secrets.randbelow(10) + 1


DEFAULT_D10_SOURCE = SecretsD10Source()


def d10(source: D10Source | None = None) -> int:
    value = int((source or DEFAULT_D10_SOURCE).d10())
    if not 1 <= value <= 10:
        raise ValueError("d10 source returned a value outside 1..10")
    return value


def roll_2d10(modifier: int, tn: int, *, source: D10Source | None = None) -> RollResult:
    a, b = d10(source), d10(source)
    return RollResult(a, b, modifier, a + b + modifier, tn)


class World:
    def __init__(self, content_path: Path):
        self.content_path = content_path
        self.data = json.loads(content_path.read_text(encoding="utf-8"))
        augment_advanced_catalog(self.data)

    @property
    def name(self) -> str:
        return str(self.data["world_name"])

    @property
    def starting_location(self) -> str:
        return str(self.data["starting_location"])

    @property
    def paths(self) -> dict[str, dict[str, Any]]:
        return self.data["paths"]

    @property
    def roots(self) -> list[str]:
        return list(self.data["roots"])

    @property
    def spiritual_root_system(self) -> dict[str, Any]:
        return dict(self.data.get("spiritual_root_system", {}))

    @property
    def bloodlines(self) -> dict[str, dict[str, Any]]:
        return dict(self.data.get("bloodlines", {}))

    @property
    def physiques(self) -> dict[str, dict[str, Any]]:
        return dict(self.data.get("physiques", {}))

    @property
    def realms(self) -> list[dict[str, Any]]:
        return list(self.data["realms"])

    @property
    def body_realms(self) -> list[dict[str, Any]]:
        return list(self.data.get("body_realms", []))

    @property
    def body_perfection(self) -> dict[str, Any]:
        return self.data.get("body_perfection", self.perfection)

    @property
    def locations(self) -> dict[str, dict[str, Any]]:
        return self.data["locations"]

    @property
    def npcs(self) -> dict[str, dict[str, Any]]:
        return self.data["npcs"]


    @property
    def hidden_masters(self) -> dict[str, dict[str, Any]]:
        return {
            name: npc for name, npc in self.npcs.items()
            if isinstance(npc.get("hidden_master"), dict)
        }

    @property
    def items(self) -> dict[str, dict[str, Any]]:
        return self.data["items"]

    @property
    def recipes(self) -> dict[str, dict[str, Any]]:
        return self.data["recipes"]

    @property
    def perfection(self) -> dict[str, Any]:
        return self.data["perfection"]

    @property
    def unexpected_events(self) -> list[dict[str, Any]]:
        return list(self.data.get("unexpected_events", []))

    @property
    def secret_realms(self) -> dict[str, dict[str, Any]]:
        return self.data.get("secret_realms", {})

    @property
    def inheritances(self) -> dict[str, dict[str, Any]]:
        return self.data.get("inheritances", {})

    @property
    def currencies(self) -> dict[str, dict[str, Any]]:
        return self.data.get("currencies", {})

    @property
    def sect_system(self) -> dict[str, Any]:
        return self.data.get("sect_system", {})

    @property
    def sects(self) -> dict[str, dict[str, Any]]:
        return self.data.get("sects", {})

    @property
    def auction_houses(self) -> dict[str, dict[str, Any]]:
        return self.data.get("auction_houses", {})

    @property
    def world_rulers(self) -> dict[str, str]:
        return self.data.get("world_rulers", {})

    @property
    def law_system(self) -> dict[str, Any]:
        return self.data.get("law_system", {})

    @property
    def special_effects(self) -> dict[str, dict[str, Any]]:
        return self.data.get("special_effects", {})

    @property
    def teleport_arrays(self) -> dict[str, dict[str, Any]]:
        return self.data.get("teleport_arrays", {})

    @property
    def abode_system(self) -> dict[str, Any]:
        return self.data.get("abode_system", {})

    @property
    def world_rules(self) -> dict[str, Any]:
        return self.data.get("world_rules", {})

    @property
    def technique_system(self) -> dict[str, Any]:
        return self.data.get("technique_system", {})

    @property
    def manuals(self) -> dict[str, dict[str, Any]]:
        return self.technique_system.get("manuals", {})

    @property
    def techniques(self) -> dict[str, dict[str, Any]]:
        return self.technique_system.get("techniques", {})

    def manual_definition(self, manual_id: str) -> dict[str, Any] | None:
        data = self.manuals.get(manual_id)
        return dict(data) if data else None

    def technique_definition(self, technique_id: str) -> dict[str, Any] | None:
        data = self.techniques.get(technique_id)
        return dict(data) if data else None

    def law_definition(self, law_id: str) -> dict[str, Any] | None:
        return self.law_system.get("laws", {}).get(law_id)

    def law_stage(self, comprehension: int) -> dict[str, Any]:
        stages = list(self.law_system.get("stages", []))
        current = stages[0] if stages else {"index": 0, "name": "Unawakened", "min": 0}
        for stage in stages:
            if int(comprehension) >= int(stage.get("min", 0)):
                current = stage
        return dict(current)

    def law_affinity_bonus(self, character: dict[str, Any], law_id: str) -> int:
        law = self.law_definition(law_id) or {}
        bonus = 0
        if character.get("spiritual_root") in law.get("affinity_roots", []): bonus += 3
        if character.get("path") in law.get("affinity_paths", []): bonus += 3
        return bonus

    def law_technique(self, technique_id: str) -> dict[str, Any] | None:
        data = self.law_system.get("techniques", {}).get(technique_id)
        return dict(data) if data else None

    def special_effect(self, effect_id: str) -> dict[str, Any] | None:
        data = self.special_effects.get(effect_id)
        return dict(data) if data else None

    def item_name(self, item_id: str) -> str:
        return str(self.items.get(item_id, {}).get("name", item_id.replace("_", " ").title()))

    def item_sect_value(self, item_id: str) -> int:
        return max(1, int(self.items.get(item_id, {}).get("sect_value", 1)))

    def auction_door_profile(self, item_id: str) -> dict[str, Any] | None:
        item = self.items.get(item_id, {})
        level = str(item.get("auction_interest", "none")).strip().lower()
        if level not in {"special", "legendary"}:
            return None
        defaults = {"special": 35, "legendary": 60}
        return {
            "level": level,
            "chance_percent": max(1, min(100, int(item.get("door_event_chance", defaults[level])))),
            "hunter_realm_bonus": int(item.get("hunter_realm_bonus", 1 if level == "special" else 2)),
        }

    @staticmethod
    def power_key(realm_index: int, stage: int) -> tuple[int, int]:
        return int(realm_index), int(stage)

    @classmethod
    def is_higher_power(cls, npc_realm_index: int, npc_stage: int, character: dict[str, Any]) -> bool:
        return cls.power_key(npc_realm_index, npc_stage) > cls.power_key(int(character.get("realm_index", 0)), int(character.get("phase", 1)))

    def currency_name(self, currency_id: str) -> str:
        return str(self.currencies.get(currency_id, {}).get("name", currency_id.replace("_", " ").title()))

    def location_min_realm(self, name: str) -> int:
        return max(0, int(self.locations.get(name, {}).get("min_realm_index", 0)))

    def location_world(self, name: str) -> str:
        return str(self.locations.get(name, {}).get("world", "Mortal World"))

    def location_safe_zone(self, name: str) -> bool:
        return bool(self.locations.get(name, {}).get("safe_zone", False))

    def auction_house_at(self, location: str) -> tuple[str, dict[str, Any]] | None:
        for house_id, data in self.auction_houses.items():
            if str(data.get("location")) == location:
                return house_id, data
        return None

    def sect_rank(self, name: str) -> dict[str, Any] | None:
        needle = name.strip().casefold()
        for rank in self.sect_system.get("ranks", []):
            if str(rank.get("name", "")).casefold() == needle:
                return dict(rank)
        return None

    def npc_location_at(self, npc_name: str, period: str) -> str | None:
        npc = self.npcs.get(npc_name)
        if not npc:
            return None
        schedule = npc.get("schedule", {})
        return str(schedule.get(period, npc.get("location"))) if schedule.get(period, npc.get("location")) else None

    def normalize_path(self, raw: str) -> str | None:
        value = raw.strip().lower()
        for name in self.paths:
            if name.lower() == value:
                return name
        aliases = {
            "sword": "Sword Cultivator",
            "qi": "Qi Refiner",
            "body": "Body Refiner",
            "soul": "Soul Cultivator",
            "beast": "Beast Binder",
            "formation": "Formation Adept",
        }
        return aliases.get(value)

    def normalize_root(self, raw: str) -> str | None:
        value = raw.strip().lower()
        for root in self.roots:
            if root.lower() == value:
                return root
        return None

    def normalize_secret_realm(self, raw: str) -> str | None:
        value = raw.strip().lower()
        for realm_id, data in self.secret_realms.items():
            if realm_id.lower() == value or str(data["name"]).lower() == value:
                return realm_id
        return None

    def starting_stats(self, path: str) -> tuple[dict[str, int], int, int]:
        attrs = {
            k: int(v)
            for k, v in self.paths[path].items()
            if k in {"body", "agility", "spirit", "insight", "will", "presence"}
        }
        qi_max = 8 + attrs["spirit"] * 2
        vitality_max = 10 + attrs["body"] * 2
        return attrs, qi_max, vitality_max

    @staticmethod
    def _gendered_name(entry: dict[str, Any], gender: str | None = None) -> str:
        gender = (gender or "neutral").strip().lower()
        if gender == "female":
            return str(entry.get("gendered_names", {}).get("female", entry["name"]))
        if gender == "male":
            return str(entry.get("gendered_names", {}).get("male", entry["name"]))
        return str(entry["name"])

    def realm_name(self, realm_index: int, gender: str | None = None) -> str:
        realm_index = max(0, min(realm_index, len(self.realms) - 1))
        return self._gendered_name(self.realms[realm_index], gender)

    def realm_world(self, realm_index: int) -> str:
        realm_index = max(0, min(realm_index, len(self.realms) - 1))
        return str(self.realms[realm_index].get("world", "Mortal World"))

    def body_realm_name(self, realm_index: int, gender: str | None = None) -> str:
        if not self.body_realms:
            return "Untrained Body"
        realm_index = max(0, min(realm_index, len(self.body_realms) - 1))
        return self._gendered_name(self.body_realms[realm_index], gender)

    def body_realm_world(self, realm_index: int) -> str:
        if not self.body_realms:
            return "Mortal World"
        realm_index = max(0, min(realm_index, len(self.body_realms) - 1))
        return str(self.body_realms[realm_index].get("world", "Mortal World"))

    def phase_cost(self, realm_index: int, phase: int) -> int:
        realm = self.realms[realm_index]
        costs = realm["phase_costs"]
        phase = max(1, min(int(phase), len(costs)))
        return int(costs[phase - 1])

    def breakthrough_tn(self, realm_index: int, phase: int) -> int:
        base = int(self.realms[realm_index]["base_tn"])
        return base + (3 if phase == 9 else 0)

    def next_stage(self, realm_index: int, phase: int) -> tuple[int, int] | None:
        if phase < 9:
            return realm_index, phase + 1
        if realm_index + 1 >= len(self.realms):
            return None
        return realm_index + 1, 1


    def body_phase_cost(self, realm_index: int, phase: int) -> int:
        realm = self.body_realms[realm_index]
        costs = realm["phase_costs"]
        phase = max(1, min(int(phase), len(costs)))
        return int(costs[phase - 1])

    def body_breakthrough_tn(self, realm_index: int, phase: int) -> int:
        base = int(self.body_realms[realm_index]["base_tn"])
        return base + (3 if phase == 9 else 0)

    def body_next_stage(self, realm_index: int, phase: int) -> tuple[int, int] | None:
        if phase < 9:
            return realm_index, phase + 1
        if realm_index + 1 >= len(self.body_realms):
            return None
        return realm_index + 1, 1

    def body_perfection_training_cap(self) -> int:
        return int(self.body_perfection.get("training_cap", 20))

    def body_perfection_quest_count(self) -> int:
        return len(self.body_perfection.get("quests", []))

    def body_perfection_total_preparation(self) -> int:
        return sum(int(q.get("preparation_required", 0)) for q in self.body_perfection.get("quests", []))

    def body_perfection_quest(self, realm_index: int, quest_index: int, character: dict[str, Any]) -> dict[str, Any]:
        templates = self.body_perfection["quests"]
        quest = dict(templates[max(0, min(quest_index, len(templates) - 1))])
        quest["realm_name"] = self.body_realm_name(realm_index, character.get("gender"))
        quest["title"] = str(quest["title"]).format(
            realm=quest["realm_name"],
            path=character.get("path", "Cultivator"),
            root=character.get("spiritual_root", "spiritual"),
        )
        quest["description"] = str(quest["description"]).format(
            realm=quest["realm_name"],
            path=character.get("path", "cultivation"),
            root=character.get("spiritual_root", "spiritual"),
        )
        quest["progress_reward"] = int(self.body_perfection["quest_progress"][quest_index])
        return quest

    @staticmethod
    def dual_resonance_active(character: dict[str, Any]) -> bool:
        return (
            int(character.get("realm_index", 0)) == int(character.get("body_realm_index", 0))
            and int(character.get("phase", 1)) == int(character.get("body_phase", 1))
        )

    @classmethod
    def dual_resonance_training_bonus(cls, character: dict[str, Any], base_gain: int) -> int:
        if not cls.dual_resonance_active(character):
            return 0
        return max(1, int(round(max(0, base_gain) * 0.10)))

    @classmethod
    def dual_resonance_check_bonus(cls, character: dict[str, Any]) -> int:
        return 1 if cls.dual_resonance_active(character) else 0

    def random_encounter(self, location: str) -> str:
        entries = self.locations[location]["encounters"]
        return secrets.choice(entries)

    def random_explore_rewards(self) -> tuple[int, int, dict[str, int]]:
        cultivation = secrets.randbelow(9) + 5
        stones = secrets.randbelow(5) + 2
        table = [
            {"spirit_herb": 1},
            {"spirit_herb": 2},
            {"spirit_iron": 1},
            {"beast_core": 1},
            {},
        ]
        return cultivation, stones, secrets.choice(table)

    def random_hunt(self, realm_index: int) -> dict[str, Any]:
        beasts = [
            ("Iron-Horn Boar", 12, {"beast_core": 1, "spirit_iron": 1}, "Earth", "stubborn", "Ironhide", 11),
            ("Mistclaw Wolf", 13, {"beast_core": 1}, "Wind", "cautious", "Mistclaw", 16),
            ("Riverfang Serpent", 14, {"beast_core": 1, "spirit_herb": 1}, "Water", "calculating", "Riverfang", 18),
            ("Stoneback Ape", 15, {"beast_core": 2}, "Earth", "proud", "Stoneback", 20),
        ]
        name, base_tn, loot, element, temperament, bloodline, intelligence = secrets.choice(beasts)
        scaled = min(max(0, int(realm_index)), 12)
        return {
            "name": name,
            "tn": base_tn + min(scaled, 4),
            "taming_tn": base_tn + 2 + min(scaled, 5),
            "rank": max(0, scaled // 4),
            "element": element,
            "temperament": temperament,
            "bloodline": bloodline,
            "intelligence": min(60, intelligence + scaled),
            "loot": loot,
            "stones": 4 + secrets.randbelow(7),
            "cultivation": 8 + secrets.randbelow(10),
        }

    def item_names(self, items: dict[str, int]) -> str:
        if not items:
            return "none"
        parts = []
        for item_id, qty in items.items():
            name = self.items.get(item_id, {}).get("name", item_id)
            parts.append(f"{name} x{qty}")
        return ", ".join(parts)


    # ---------- Perfect Realm ----------
    def perfection_training_cap(self) -> int:
        return int(self.perfection.get("training_cap", 20))

    def perfection_quest_count(self) -> int:
        return len(self.perfection.get("quests", []))

    def perfection_total_preparation(self) -> int:
        return sum(int(q.get("preparation_required", 0)) for q in self.perfection.get("quests", []))

    def perfection_quest(self, realm_index: int, quest_index: int, character: dict[str, Any]) -> dict[str, Any]:
        templates = self.perfection["quests"]
        quest = dict(templates[max(0, min(quest_index, len(templates) - 1))])
        quest["realm_name"] = self.realm_name(realm_index, character.get("gender"))
        quest["title"] = str(quest["title"]).format(
            realm=self.realm_name(realm_index, character.get("gender")),
            path=character.get("path", "Cultivator"),
            root=character.get("spiritual_root", "spiritual"),
        )
        quest["description"] = str(quest["description"]).format(
            realm=self.realm_name(realm_index, character.get("gender")),
            path=character.get("path", "cultivation"),
            root=character.get("spiritual_root", "spiritual"),
        )
        quest["progress_reward"] = int(self.perfection["quest_progress"][quest_index])
        return quest

    # ---------- Unexpected events ----------
    def eligible_unexpected_events(
        self, *, location: str | None = None, character: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        world_name = str(self.locations.get(str(location), {}).get("world", "")) if location else ""
        realm_index = int((character or {}).get("realm_index", 0))
        for event in self.unexpected_events:
            locations = [str(value) for value in event.get("locations", [])]
            worlds = [str(value) for value in event.get("worlds", [])]
            if locations and (not location or str(location) not in locations):
                continue
            if worlds and world_name not in worlds:
                continue
            if character is not None and realm_index < int(event.get("min_realm_index", 0)):
                continue
            if character is not None and realm_index > int(event.get("max_realm_index", len(self.realms)-1)):
                continue
            if event.get("kind") == "secret_realm" and location:
                realm_id = event.get("secret_realm_id")
                realm = self.secret_realms.get(str(realm_id), {})
                if realm.get("location") != location:
                    continue
                if character is not None and realm_index < int(realm.get("min_realm_index", 0)):
                    continue
            events.append(dict(event))
        return events

    def roll_unexpected_event(
        self, *, location: str | None = None, character: dict[str, Any] | None = None,
        chance_percent: int = 28,
    ) -> dict[str, Any] | None:
        events = self.eligible_unexpected_events(location=location, character=character)
        if not events or secrets.randbelow(100) >= max(0, min(chance_percent, 100)):
            return None
        weighted: list[dict[str, Any]] = []
        for event in events:
            weighted.extend([event] * max(0, int(event.get("weight", 1))))
        if not weighted:
            return None
        return dict(secrets.choice(weighted))

    def secret_realm_choices(self) -> list[tuple[str, str]]:
        return [(realm_id, str(data["name"])) for realm_id, data in self.secret_realms.items()]

    def secret_room(self, realm_id: str, room_index: int) -> dict[str, Any] | None:
        rooms = self.secret_realms[realm_id]["rooms"]
        if room_index < 0 or room_index >= len(rooms):
            return None
        return dict(rooms[room_index])

    def secret_realm_modifier(self, character: dict[str, Any], room: dict[str, Any]) -> int:
        attr = str(room.get("attribute", "insight"))
        modifier = int(character["attributes"].get(attr, 0)) + 2
        preferred_paths = room.get("preferred_paths", [])
        preferred_roots = room.get("preferred_roots", [])
        if character.get("path") in preferred_paths:
            modifier += 2
        if character.get("spiritual_root") in preferred_roots:
            modifier += 1
        return modifier

    def secret_realm_reward(self, room: dict[str, Any]) -> tuple[int, int, dict[str, int], int]:
        cultivation = int(room.get("cultivation", 0))
        stones = int(room.get("spirit_stones", 0))
        insight = int(room.get("insight_xp", 0))
        items = dict(room.get("items", {}))
        return cultivation, stones, items, insight
