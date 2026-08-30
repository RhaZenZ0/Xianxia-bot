# v0.18 Lifespan Inactivity Pause Hotfix

## Purpose

Protect player characters from dying of old age solely because the player was away while canonical
world time advanced.

## Canonical behavior

- Player biological aging pauses after **7 real days** without a successful authoritative mutation.
- The global canonical world clock does **not** pause.
- Cooldowns, roads, world simulation, markets, NPCs, and other world-time systems are unchanged.
- On the player's next successful authoritative mutation, the protected game-minute interval is
  persisted in `world_state` and biological aging resumes from the current canonical world minute.
- `character.lifespan` previews the inactivity protection before the returning mutation, so the
  existing old-age gate cannot kill a long-inactive player before the pause is applied.
- NPC lifespan behavior is unchanged.

## Seclusion rule

Seclusion remains lived biological time.

If a long inactive gap overlaps seclusion, only the exact canonical game minutes spent in seclusion
continue to age the character. The rest of the eligible inactive gap is paused. This prevents
offline seclusion progression from also receiving free lifespan protection.

## Upgrade compatibility

No schema migration is required. Per-player pause state uses namespaced `world_state` keys:

`player_lifespan_clock:<user_id>`

For existing characters without pause state, activity is recovered conservatively in this order:

1. latest authoritative action receipt + domain event;
2. latest domain event for the actor;
3. legacy character `updated_at` + `created_game_minute`.

This protects pre-authority/upgraded characters on their first return.

## Player UI

The character sheet and `/lifespan` output show when inactivity protection is active and how many
game-years of biological aging are currently protected.

## Validation

```text
go test ./...
PASS

go vet ./...
PASS

go test -race ./...
PASS

python -m pytest -q
239 passed, 78 subtests passed
```

Schema remains **22**. Runtime release version remains **0.18**.
