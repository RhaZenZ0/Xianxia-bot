# v0.18 Stage 1 Notes — Forage Input Authority

## Before

The v0.17 `alchemy_forage` orchestration supplied five mechanical inputs to `forage.resolve`:

- `context_bonus`
- `location`
- `realm_index`
- `garden_level`
- `cooldown_seconds`

Go owned the roll, TN/resource lookup, loot, rare RNG, awards, profession progress, and cooldown write,
but a direct caller could still forge several upstream inputs. Go also did not reject a second forage
attempt based on the existing cooldown; Python performed that pre-check.

## After

The Discord caller sends only:

```json
{"effect_bonus": <active-effect contribution>}
```

Go now:
1. loads the canonical character;
2. rejects non-living characters;
3. enforces the existing `alchemy_forage` cooldown;
4. derives the physical location and realm index;
5. resolves an accessible cave abode and canonical base region;
6. derives herb-garden level;
7. derives Alchemy Family herb-lore bonus;
8. computes the combined context bonus;
9. performs the existing regional resource, TN, RNG, loot, inventory, and profession mutations;
10. writes a fixed 20-minute cooldown.

The result returns the derived authority inputs so Python can present them without recomputing mechanics.

## New negative boundary

Go rejects client-supplied:
- `context_bonus`
- `location`
- `realm_index`
- `garden_level`
- `cooldown_seconds`
- existing forged outcome fields (`tn`, `spirit_resources`, `loot`, `rare_found`)

Python contract tests also prohibit the old forage input construction.

## Validation

- Python: `237 passed, 78 subtests passed`
- Go: `go test ./...` PASS
- Go: `go vet ./...` PASS
