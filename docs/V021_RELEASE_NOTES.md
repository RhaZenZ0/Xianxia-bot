# Xianxia RP Discord Bot — v0.21 release notes

Shipping as **v0.21.0**. The v0.20 line (v0.20.0 through v0.20.9) is in
`docs/V020_RELEASE_NOTES.md`. The release is stamped 0.21.0 in `app/version.py`,
`VERSION`, the `Dockerfile` and `docker-compose.yml`, and carries schema 28,
unchanged since v0.20.6.

v0.21 is the roadmap's **Authority I** milestone (`docs/ROADMAP_1_0.md`):
the last handlers where Python decides a gameplay outcome and writes the
tables itself each get an engine action. The milestone ships as point
releases, one row at a time; its gate is the allowlist in
`tests/python/contracts/test_authority_boundary.py` (`PLAYER_MUTATIONS`),
which lists every remaining Python-side gameplay write and must be empty
before v0.21 is tagged stable.

Release date: 2026-09-06 (v0.21.0).

## v0.21.0 — the gate, and `item.use`

**The gate.** `test_authority_boundary.py` now scans `app/database/` for
every method that runs an INSERT/UPDATE/DELETE (64 of them) and every call
to one of those from `app/bot/` and `app/ops/`. Each call site is either a
`PLAYER_MUTATIONS` row - a gameplay write with the engine action that will
replace it - or one of the `BOOKKEEPING_METHODS` (narration history, RAG
memory, Discord channel and thread ids, ops telemetry, the GM's quest-draft
review), which stay in Python. Anything else fails; a row whose write has
gone fails too until the row is deleted. The scan started at 27 rows across
the seven roadmap items and the test refuses to let it grow. Both directions
are mutation-tested.

**`item.use`.** The non-battle branch of `/use` ran five unguarded writes
in Python: consume the item, restore Qi/vitality, add the permanent life
extension, apply the item's effect, add pill toxicity and then re-derive the
toxicity penalty effect. The battle branch beside it had gone through
`combat.recovery_item` since v0.19; the rest never did. The Go engine now
owns the whole sequence as one transaction, `item.use`
(`go_core/internal/game/item_use_actions.go`), and the Discord handler is
"call, then format" - 22 lines where there were 70.

What moved, and what it preserved:

- The "no implemented active use" refusal happens before anything is
  consumed; storage treasures and array talismans keep their own actions.
- A stack's last item deletes the inventory row instead of leaving a
  zero-quantity row behind (Python's `consume_item` left the row; the
  combat path already deleted it).
- The restore clamps to the maxima and, if a battle is active, moves
  `battles.player_hp` in lockstep - so `/use` mid-battle now behaves like
  the battle panel's recovery option instead of taking a different path.
- Effects are keyed by the content's `effect_key` (default: the item id),
  sourced to the item, normalised to the same shape
  `app/rules/effects.py` produces, and `duration_game_minutes: 0` means
  the effect does not expire (a NULL end, as before).
- The medicinal-residue value is derived exactly as
  `app/rules/alchemy.py` did (24 for a lifespan pill, 18 risky, 12
  cultivation, 10 mental, 7 with an instant restore, 8 otherwise;
  `pill_toxicity` in the content overrides), the existing decay is
  settled *before* the add, and the engine's own toxicity curve
  (`settlePillToxicityEffectTx`, in place since v0.19) writes or clears the
  penalty effect afterwards. The reply carries the band label, so Python
  no longer computes it.
- The Go catalog gained the item `use` fields (`effect`, `effect_key`,
  `name`, `duration_game_minutes`, `lifespan_years`) and the optional
  `pill_toxicity` override; the world file did not change.

Seven Go tests cover the five steps, the penalty-band crossing, decay
order, the lifespan pill, the talisman (no expiry, not a pill), the
refusals, and the mid-battle HP bar; nine mutants (delete-at-zero, each
toxicity constant, decay order, the duration-zero rule, the HP lockstep,
the use gate, the stat-less modifier drop, the post-add settle) are all
killed. `PLAYER_MUTATIONS` shrinks from 27 rows to 22.

Still in Python after this release - the remaining v0.21 rows:
`/alchemy purge` (→ `alchemy.purge`), `/sect shadow` (→ `sect.shadow`),
`/law technique`'s self-applied effect, the toxicity sync in
`character_state.py` (→ engine-owned; `/use` no longer calls it), the eight
admin writes in `world_ops.py`, and the small ones (`set_gender`, abode
`set_location`, `discover_sect`, `accept_quest`).

Python full suite 641 tests, identical failure set; pytest-style 25/25 (the
three new gate tests); Go suite green. No schema change.
