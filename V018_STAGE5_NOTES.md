# v0.18 Stage 5 Notes — Canonical Time + Road Authority

## Authority boundary

Stage 5 is implemented at the common authoritative Go boundary instead of duplicating clock reads in
every legacy handler.

For every authoritative mutation:
1. reject caller `game_minute`;
2. derive the canonical world minute under the authoritative transaction;
3. internally inject that minute only for legacy handlers whose structs still carry
   `json:"game_minute"`;
4. execute the mutation;
5. stamp the domain event with the same canonical action minute.

Stage 3/4 operations that already own all context keep their strict payload validators and receive no
injected mechanical fields.

Every authoritative query rejects caller `game_minute`. Current-time queries such as
`sense.status`, `character.lifespan`, and `effects.current` derive/read canonical state in Go.

`quest.progress` is a legacy non-authoritative action endpoint but represented one remaining
caller-time path. It now rejects caller time and reads the canonical clock.

Simulation bootstrap/run-due/force retain explicit time because that value is an authoritative
scheduler/simulation input, not a player-provided current-time claim.

## Replay behavior

Caller time is rejected before replay lookup. Reusing a valid action ID with a forged `game_minute`
therefore fails instead of returning the stored receipt.

## Direct-road profile

A direct road has a server-derived travel/danger profile. Terrain sets the base duration and danger;
higher worlds add both duration and danger; cultivation realm reduces them within capped bounds.
Safe-zone-to-safe-zone roads receive a danger reduction.

Current bounds:
- minimum travel duration: 30 game-minutes;
- danger/encounter chance: 5–45 percent.

## Transit enforcement

Road travel does not advance the shared world clock. Instead, Go stores
`world_state["road_transit:<actor_id>"]` with origin, destination, departure minute, and arrival
minute.

Until canonical time reaches arrival, every authoritative mutation for that actor fails with the
remaining journey time. On the first mutation at or after arrival, the transit state is deleted and
normal dispatch continues.

This makes duration mechanical without letting one player's journey fast-forward the world for
everyone else and without adding a schema migration.

## Encounters

The road encounter roll is server-owned and uses the derived danger percentage. Encounter selection
is also server-owned.

- `blocked_road`: delay only.
- `qi_weather`: delay and nonlethal Vitality damage.
- `spirit_beast`: delay and nonlethal Vitality damage.
- `road_ambush`: delay and nonlethal Vitality damage.

Encounter delay extends the canonical arrival minute. Damage is clamped so a road encounter cannot
reduce Vitality below 1; true death remains exclusively lifecycle-authoritative.

Each triggered road encounter is persisted as an `event_log` row with `event_type=road_encounter`.

## Python orchestration

Direct `authoritative_action` payloads in `app/` no longer contain `game_minute`. The
`GameEngineClient.authoritative_action` method also rejects such payloads client-side as a defense in
depth.

Legacy query calls for `character.lifespan` and `sense.status` now send empty payloads. The quest
service no longer sends time to `quest.progress`.

The `/travel` command displays the canonical travel duration, danger, encounter probability,
arrival minute, transit state, and encounter consequences.

## Schema

Schema remains 22.
