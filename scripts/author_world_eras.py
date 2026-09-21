#!/usr/bin/env python3
"""Author `world_era_cycles` into content/world.json (v1.0.7).

Until now there was **one** era for the whole game, four entries long, held in
a Go literal (`eraCycle` in `internal/simulation/advanced_maintenance.go`), and
its cycle ran 540 world days - a year and a half, matching nothing. A Demon
Invasion in the Celestial World and a quiet century in the Mortal one shared a
single row, which is the shape v1.0.0-rc.52 removed for a world's *news* and
schema 4 removed for its *capitals*.

Four cycles now, one per world, each exactly **one world year**:
`minutesPerYear` is 12 months x 30 days, so 360 world days, six eras of sixty.
The gate holds that sum exactly, per world, rather than trusting the numbers
look right.

**Only modifiers a rule reads are authored**, which is the reason this script
exists rather than four more Go literals. Of the eight keys the old cycle
carried, four reached no rule anywhere in the tree - `secret_realm_frequency`,
`market_volatility`, `beast_encounter_rate` and `recovery_rate` each occurred
exactly once in `go_core`, in their own declaration - so every one of the four
eras carried one live modifier and one dead one, and a Beast Tide did nothing
whatever to beasts. v1.0.7 wires two of them (`recovery_rate` into v1.0.4's
vitality recovery, `beast_encounter_rate` into the hunt roll) and authors
nothing against the two it does not; `era_vocabulary_test.go` is what
holds that shut, in the shape `modifier_vocabulary_test.go` (rc.58) already had.

The Mortal World keeps its four original names and their numbers. That is not
sentiment: `advanceEra` finds a world's place in its cycle by matching the
active row's `name`, and the boot seed writes "Jade Meridian Awakening Era", so
a live world carries on from where it stands instead of being silently reset to
the top of the cycle.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content" / "world.json"

# One world year. `minutesPerYear` is 1440 * 30 * 12, so 360 days of 1440.
YEAR_DAYS = 360
ERAS_PER_WORLD = 6
ERA_DAYS = YEAR_DAYS // ERAS_PER_WORLD  # 60

# Every key here is fetched by a rule; the gate holds the set, not this comment.
#   cultivation_gain     -> eraCultivationMultiplier (cultivation_actions.go)
#   war_pressure         -> advanceWars + siege power (territory_actions.go:195)
#   caravan_risk         -> advanceCaravans + territory_actions.go:438
#   crime_pressure       -> advanceHunters
#   recovery_rate        -> vitality_recovery.go            (wired in v1.0.7)
#   beast_encounter_rate -> explorationHuntAction           (wired in v1.0.7)

CYCLES: dict[str, list[tuple[str, str, dict[str, float]]]] = {
    "Mortal World": [
        ("Jade Meridian Awakening Era",
         "Spiritual veins wake beneath the old roads and new inheritances surface in the hill country.",
         {"cultivation_gain": 1.05}),
        ("Hundred Sects Strife Era",
         "Competition over the woken veins hardens into open territorial war between the halls.",
         {"war_pressure": 1.25}),
        ("Beast Tide Era",
         "Ancient bloodlines stir in the deep ranges and spirit beasts come down the passes in tides.",
         {"beast_encounter_rate": 1.35, "caravan_risk": 1.15}),
        ("Quiet Heaven Era",
         "After the upheaval the heavens settle, the orthodox halls rebuild, and the roads are safe again.",
         {"recovery_rate": 1.10, "crime_pressure": 0.85}),
        ("Lean Harvest Era",
         "A thin year in the valleys. Granaries empty early and the hungry take to the passes.",
         {"caravan_risk": 1.20, "crime_pressure": 1.15}),
        ("Gathering Storm Era",
         "Qi thins and the sky stays the colour of old iron; every diviner in the world reads the same omen.",
         {"cultivation_gain": 0.95, "war_pressure": 1.10}),
    ],
    "Spiritual World": [
        ("Spirit Vein Flood Era",
         "The great veins overflow their courses and even a mortal breathes cultivator's air.",
         {"cultivation_gain": 1.10}),
        ("Array Masters' Accord Era",
         "The formation halls sign a common ward, and for a while nobody's caravan is anybody's prey.",
         {"war_pressure": 0.80, "caravan_risk": 0.85}),
        ("Beast Lord Migration Era",
         "Something older than the sects moves through the spirit wilds, and everything else moves aside.",
         {"beast_encounter_rate": 1.45, "caravan_risk": 1.25}),
        ("Sealed Sky Era",
         "A vault of grey closes over the world. Qi answers slowly and wounds close slower.",
         {"cultivation_gain": 0.92, "recovery_rate": 0.90}),
        ("Thousand Halls Contention Era",
         "Every hall with a banner raises it, and the accords of the last age are read as invitations.",
         {"war_pressure": 1.30, "crime_pressure": 1.10}),
        ("Clearwater Repose Era",
         "The contention burns out. Healers are busier than swordsmen and the roads go quiet.",
         {"recovery_rate": 1.15, "crime_pressure": 0.80}),
    ],
    "Immortal World": [
        ("Ascendant Dawn Era",
         "The gates above stand a little further open, and those who look up can feel it.",
         {"cultivation_gain": 1.12}),
        ("Law Decree Era",
         "An immortal court publishes its law, and for once there are enforcers enough to mean it.",
         {"crime_pressure": 0.70, "war_pressure": 0.85}),
        ("Void Beast Incursion Era",
         "Things with no bloodline and no name come through the thin places between the courts.",
         {"beast_encounter_rate": 1.55, "caravan_risk": 1.30}),
        ("Immortal Schism Era",
         "The court divides over a point of law, and the point is settled the usual way.",
         {"war_pressure": 1.35, "crime_pressure": 1.20}),
        ("Withering Dao Era",
         "Insight comes hard and heals harder. The old immortals say they have seen this before.",
         {"cultivation_gain": 0.90, "recovery_rate": 0.85}),
        ("Nine Palaces Concord Era",
         "Nine palaces agree on a single road tax, which is a stranger miracle than any of them admit.",
         {"recovery_rate": 1.20, "caravan_risk": 0.80}),
    ],
    "Celestial World": [
        ("Mandate Renewal Era",
         "The Mandate is renewed above the sovereign courts and the whole sky is easier to breathe.",
         {"cultivation_gain": 1.15, "recovery_rate": 1.10}),
        ("Heavenly Audit Era",
         "Heaven counts. Nothing hidden stays hidden, and nobody with a debt sleeps well.",
         {"crime_pressure": 0.60, "war_pressure": 0.75}),
        ("Primordial Beast Waking Era",
         "Something that predates the courts turns over in its sleep, and the star roads scatter.",
         {"beast_encounter_rate": 1.65, "caravan_risk": 1.35}),
        ("Sovereign Contention Era",
         "Two sovereigns claim one mandate. The heavens do not intervene; they watch.",
         {"war_pressure": 1.40, "crime_pressure": 1.25}),
        ("Silence of Heaven Era",
         "The Mandate says nothing at all, for sixty days, and every court reads the silence differently.",
         {"cultivation_gain": 0.88, "recovery_rate": 0.80}),
        ("Star Court Ascension Era",
         "A new court is raised among the stars and the roads to it are swept clean for the climb.",
         {"cultivation_gain": 1.08, "caravan_risk": 0.75}),
    ],
}


def build() -> dict[str, list[dict[str, object]]]:
    out: dict[str, list[dict[str, object]]] = {}
    for world, entries in CYCLES.items():
        if len(entries) != ERAS_PER_WORLD:
            raise SystemExit(f"{world} has {len(entries)} eras, not {ERAS_PER_WORLD}")
        out[world] = [
            {
                "name": name,
                "description": description,
                "duration_days": ERA_DAYS,
                "modifiers": modifiers,
            }
            for name, description, modifiers in entries
        ]
        total = sum(int(e["duration_days"]) for e in out[world])
        if total != YEAR_DAYS:
            raise SystemExit(f"{world} runs {total} world days, not one world year ({YEAR_DAYS})")
    return out


def main() -> int:
    raw = json.loads(CONTENT.read_text(encoding="utf-8"))
    raw["world_era_cycles"] = build()
    CONTENT.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    worlds = len(raw["world_era_cycles"])
    eras = sum(len(v) for v in raw["world_era_cycles"].values())
    print(f"wrote world_era_cycles: {worlds} worlds, {eras} eras, {YEAR_DAYS} world days each")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
