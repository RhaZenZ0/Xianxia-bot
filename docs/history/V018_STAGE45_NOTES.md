# v0.18 Stage 4.5 Notes

## Companion authority contract

`go_core/internal/game/beast_artifact_actions.go` now uses strict per-operation payload allowlists
(`validateCompanionPayload`, line 40) and a shared canonical living-actor loader
(`loadLivingCompanionActor`, line 60).

Current accepted payloads:

| Operation | Client fields |
|---|---|
| `beast.tame` | `encounter_id` |
| `beast.feed` | `beast_id`, `food` |
| `beast.train` | `beast_id` |
| `beast.evolve` | `beast_id` |
| `beast.active` | `beast_id` |
| `artifact.bond` | `item_id` |
| `artifact.awaken` | `item_id`, `spirit_name` |

All other fields are rejected.

Tame verifies canonical encounter owner, status, expiry against canonical world time, and current
character location. Feed/train/bond use server-defined cooldown constants. Training derives Beast Pen
level only from the cave abode at the character's actual current location and only with owner/guest
access.

Evolve/active/awaken now derive current actor/time/location too. Repeat artifact awakening is rejected
(line 588) so awakening cannot be farmed repeatedly for profession XP/resonance.

## Read-side hardening

`Database.get_wild_beast_encounters` (`app/database/core.py`, line 5262) no longer changes encounter
status. It filters `expires_game_minute` in the SELECT. The authoritative `beast.tame` mutation is
responsible for deciding whether an encounter is expired.

## Stage 3 hardening

`craft.resolve` (`go_core/internal/game/crafting_actions.go`, line 267) now treats `recipe` as its
entire payload allowlist (line 275). This prevents new/unknown caller fields from slipping through a
mechanical-field blacklist.

## Family city roads

`LocationDefinition` now loads a `roads` list from `content/world.json`.

Each current world's 11 family cities form one connected bidirectional road network. Higher-world
realm capitals also connect into their world's family-city network. Road references are same-world
and point only to canonical catalog locations.

`knownLocationsTx` exposes direct road neighbors as reachable known routes. Successful
`exploration.travel` persists the destination with discovery kind `road_travel`.

Python autocomplete mirrors the canonical road-neighbor visibility for UI presentation.

## Hub threshold hardening

Newborn-safe family cities intentionally have `min_realm_index = 0`, including higher worlds.
`worldMinRealm` now prefers that world's realm-hub threshold, preventing those cities from lowering
the direct `mode=hub` unlock threshold.

## Schema

- Schema version: 22.
- Schema 22 remains the family-homeland migration.
- No additional schema migration is needed for Stage 4.5 roads or companion hardening.
