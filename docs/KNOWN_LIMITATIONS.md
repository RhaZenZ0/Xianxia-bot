# Known limitations — the punch list

The v0.34 playtest's findings (`docs/ROADMAP_1_0.md`, Gameplay-complete II). Every entry is
either **fixed** in the release named, or **deferred** past 1.0 with the reason. Nothing is left
open without one of those two words; `tests/python/contracts/test_playtest_gate.py` holds the
file to that. Add to it from the live pass (`docs/playtest/`) as findings come in.

The engine half of the playtest is `scripts/playtest_engine.py --launch`: every loop the roadmap
names, driven through the engine's HTTP API the way the bot's handlers drive it, against a scratch
database. Its findings are the first two entries.

## Findings

- **fixed (v0.36.1)** — *Greenriver Town had no roads.* The starting town sat outside the road
  graph, so it had no gates (v0.36.0), could only be reached by direct travel, and a fresh
  character's first road journey was the capital's rather than their own town's. It now has roads
  to Azure Crown Imperial City and Riverguard City, with the gates to match. Found by asking why
  the gate test skipped it; the engine playtest's city section (v0.36.1) now starts a road journey
  from a real gate.
- **fixed (v0.34.0)** — *A commission's last objective could never be turned in.* `quest.progress`
  wrote the row `completed` and then asked `resolveCommissionTx` for an `active` row, which failed
  with "no active commission by that name" and rolled the whole progress back. Every commission
  since v0.24.0 could be taken and worked but not finished; only the dashboard's manual close
  completed one. The row now stays `active` until the resolution flips it, and
  `TestACommissionCompletesThroughProgressAlone` drives one through progress alone.
- **fixed (v0.34.0)** — *Reset Cooldowns did not reset the sect trial's retry wait.* The wait is
  read off the last failed `sect_recruitment_attempts` row, not the `cooldowns` table, so a GM
  resetting a disciple's cooldowns left them waiting a day anyway. A full reset ages the failed
  attempts out (the history stays) and reports `trial_retries_cleared`;
  `TestResetCooldownsClearsTheTrialRetryWait` pins it.
- **fixed (v0.33.1)** — *The Quest Forge's prompt listed forty-eight auction floors before the
  town a story is set in.* Floors and their stewards are off the capped lists; validation accepts
  them regardless.
- **deferred (past 1.0)** — *The three live columns of the checklist are unticked.* Reachable
  from the hub, error text actionable, narration or fallback fired: a person at the keyboard on
  the live server ticks these, hub by hub. The static columns and the engine loops are done here;
  the live pass is Mitchell's and the file keeps his ticks across regeneration.
- **deferred (roadmap, Authority)** — *`get_world_clock` in Python re-anchors the clock the
  engine owns when the configured scale changes.* Named on the roadmap's remaining-authority list
  since v0.30.0; a read-through of the engine's clock is the fix, and it is not a gameplay defect.
- **deferred (design)** — *Moderation is a nudge on the engine's dispatch layer, not anti-cheat.*
  A muted or frozen player is blocked from the ~150 authoritative ops; raw `/v1/db` writes the
  bot makes on their behalf and the simulation runner are not intercepted. Stated in
  `moderation.go`; a stronger guarantee would need every presentation write to carry the actor.
- **deferred (content)** — *The forty-three local auction floors share archetype prose.* Twelve
  archetypes, four worlds; a riverside hall reads like a riverside hall in every world, with the
  world's stones and wardens swapped in. Hand-written floors are content work for the content
  track after rc.
- **fixed (v0.39.0)** — *Typed play fills one argument, not two.* Was: `$ I give the pill to Qiao`
  routed to talk or use. A root may now declare several arguments, each with its own source (a
  known place, a carried item, an NPC present, a player present); `$ I give the pill to Li Feng`
  is a trade offer to Li Feng, and a line that names an NPC instead says what it lacks rather than
  guessing a cultivator.
