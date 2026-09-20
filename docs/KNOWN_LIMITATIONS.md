# Known limitations — the punch list

The v0.34 playtest's findings (`docs/history/ROADMAP_1_0.md`, Gameplay-complete II). Every entry is
either **fixed** in the release named, or **deferred** past 1.0 with the reason. Nothing is left
open without one of those two words; `tests/python/contracts/test_playtest_gate.py` holds the
file to that. Add to it from the live pass (`docs/playtest/`) as findings come in.

The engine half of the playtest is `scripts/playtest_engine.py --launch`: every loop the roadmap
names, driven through the engine's HTTP API the way the bot's handlers drive it, against a scratch
database. Its findings are the first two entries.

## Findings

- **fixed (v1.0.1)** — *No gate held a parsed content field to having a reader, and the class
  kept producing findings.* v1.0.0-rc.55 found `RootGrade.CultivationMult` and
  `RootGrade.BreakthroughBonus` parsed and read by nothing — the grade decided how a cultivator was
  made and nothing about what they were — and it was found by hand. rc.58 then built exactly this
  gate one level down, for modifier *stats* (`modifier_vocabulary_test.go`, which requires each to be
  **fetched** by a rule rather than merely named). Nobody built it for the fields themselves. A sweep
  of `worlddata` finds 439 parsed fields across 63 structs, of which **seven** are never read through
  a selector anywhere in production Go; four of those are legitimately read by Python for display
  (`advantage`, `drawback`, `objective`, `channel_name`), which is the distinction a gate has to
  make and the reason a naive one would be noise. The three that survive are the two entries below.
  `field_readers_test.go` is that gate, in the rc.58 shape: a read is an `*ast.SelectorExpr`, never a
  substring, and a composite-literal key is deliberately not one — writing a field is not reading it,
  which is the whole distinction rc.55 turned on. `fieldsReadByPresentation` names the four and the
  file that prints each; `unreadContentFields` names the three below with the decision each waits on.
  It is a **floor, not a proof**: without `go/types` it cannot tell `PhysiqueDefinition.Name` from the
  forty other structs with a `Name`, so a field sharing a read name passes unexamined. It never calls
  a read field unread, and it catches the uniquely-named orphan — which is what every finding of this
  class has been.
- **deferred (planned)** — *Seven cultivation paths each name a skill, and the name reaches nothing.*
  `worlddata.Path.Skill` is parsed from `paths.<name>.skill` — Sword, Spiritual Arts, Martial Arts,
  Soul Arts, Beastcraft, Formations, Ghost Arts — and is read by no rule, no card and no Python
  reader. Each of the seven strings occurs **exactly once in the whole 2.5 MB content file**: its own
  declaration. So they do not name a manual, a technique, a profession or any roster the game has;
  they are a vocabulary with nothing behind it, which is `/learn`'s shape (rc.43) with no mechanism
  waiting at the other end rather than one. Deciding what a path's skill *is* — a display line on
  `/sheet`, a bonus, or a field to delete — is content design, not a wiring fix, which is why this is
  recorded rather than quietly wired.
- **deferred (planned)** — *Both of an auction house's door fields are read by nothing.*
  `protected_interior` and `door_rule` are set on **all 48** authored houses and neither is read in
  Go or Python. The door half of that fiction does work: `advanced_maintenance.go` writes an
  `auction_door_risks` row when a legendary lot is struck, and `auction.leave` consumes it and can
  stand a hunter at `house.EntranceLocation` — outside, which is the point. What is unread is the
  *inside* half. Two things keep this small and are worth stating rather than discovering: `door_rule`
  is unanimously `true`, so reading it would change nothing until a house sets it false; and nothing
  today can attack a player who has not consented — `/battle challenge` targets NPCs and a duel needs
  `respond` — so `protected_interior` may be protecting against a mechanic the game does not have.
  Either wire them or retire them; leaving a switch in content that code ignores is the decoration
  this file exists to name.
- **deferred (planned)** — *Nothing can grant a physique, not even a GM.* `admin.player.set_physique`
  writes `evolution_stage`, `progress` and `stability` and nothing else (`actions.go:2212`), and the
  only statements that ever write `physique_id` are character creation and samsara —
  `aptitude.awaken` and `aptitude.evolve` both pass the loaded bundle back through `savePhysique`, so
  they move the state and the stage and never the identity. This is **not** dead content: all eight
  non-ordinary physiques are drawable at creation, because `generatePhysique` gives every one weight
  at least 1 and favoured paths and roots only raise it. So it is a missing lever rather than a
  `/learn`-class fault — a GM cannot hand somebody `nine_yang_solar_body`, cannot correct one rolled
  wrong, and cannot stage one for a playtest. Adding it means `physique_id` and `name` on the
  payload, the catalogue check that `aptitude_actions.go:203` already makes, and `physique_id` in
  the undo snapshot, which today carries only the three numbers it can restore.
- **deferred (planned)** — *`admin.player.set_spiritual_root` writes a grade the ladder may not
  carry.* The lever upserts `grade`, `purity` and `mutation` with no check against
  `spiritual_root_system.grades` (`actions.go:2119`), and `gradeIndex` answers 0 for a name it does
  not know — so a typo'd grade is silently worth the bottom rung's `cultivation_mult` and
  `breakthrough_bonus` rather than erroring. v1.0.0-rc.55 found and wrote down exactly this shape
  ("a fallback that looks like a value is not a sentinel") and gated the *fixtures* with
  `TestEveryFixtureRootStandsOnTheLadder`; the lever that a GM actually types into was left
  ungated, so the one writer a human drives is the one nothing holds. The fix is the same check the
  ladder already makes, at the lever.
- **deferred (planned)** — *🗺️ Cultivation World is open to somebody who has never played, and no
  role says otherwise.* `#player-homes` and `#expeditions` sit in that category, read-only since
  v1.0.0-rc.59 but visible to everyone, so a newcomer's sidebar advertises rooms they cannot use
  directly above the `#begin-here` they are meant to go to — while every other player-facing category
  is gated (Realm Capitals by the presence role, the four World Events feeds by the realm-access
  role, Admin by administrator). The deeper half is that **no "has a character" role exists at all**:
  `_sync_realm_access_roles` and `_sync_realm_presence_roles` both run from `require_character` and so
  only ever fire for somebody who already has one, which means nothing in the server can be gated on
  having played. The plan is one generated role (`Xianxia • Cultivator`), granted from the same
  `require_character` sync block and from `/begin`, revoked at `admin_erase` (which cannot ride
  `require_character` — after erasure that call never fires again), and backfilled through
  `_sync_all_realm_access_roles`, the sweep Full Setup and `/admin server sync_roles` already call.
  Four traps are known and none is optional: the bot must allow **itself** before denying `@everyone`
  or it 403s itself out of the channel (rc.52); the overwrite has to reach channels that already
  exist, not only ones the run creates (rc.59, same file); both anchors carry `private_thread`s whose
  members still need to see the parent, so the grant must precede thread creation; and
  `realm_presence_role_name` already falls back to `world_name`, so a fifth hub with no `display_name`
  would collide with `_realm_access_role_name` and silently merge two gates — adding a third name to
  that family is the moment to gate it.
- **fixed (v1.0.1)** — *The release after a live pass would have deleted it.* The checklist is named
  after `RELEASE_VERSION`, so the filename held still across all fifty-nine release candidates of
  1.0.0 and `merge_ticks` carried every tick; on the first bump that renames it the target does not
  exist, `old` is empty, and a fresh checklist is written with every box blank. The ticks are
  inherited from the newest older checklist now, dated with the release each was walked on so
  carrying one is not a claim that the new release was walked, and the superseded file is removed
  because `docs/playtest/` is one checklist.
- **fixed (v1.0.1)** — *No clan alliance had ever been formed at runtime, in any world.*
  `martial_clan_relations` was seeded once per household behind a `COUNT(*)==0` guard, with an
  **invented** partner — a surname off a list, `partner_family_id` left NULL although the column is
  foreign-keyed to `birth_families` — and the only statement that could insert another was
  `combat_aftermath.go`, which writes `blood_feud` alone. The `clan_dynamics` batch nudged existing
  scores ±1 and created nothing. So four households meant four relations on the day the world opened
  and four for ever after. `clanDiplomacy` is the step that forms one between two real households,
  written from both sides; `npcPoliticalMarriages` had done the same for `sect_relations` since
  rc.24, in the file whose own comment names the clan fault it did not fix.
- **deferred (design)** — *Nothing ends a clan relation.* `martial_clan_relations.active` is written
  1 by every INSERT, read by every SELECT, and set to 0 by nothing in the tree. So an alliance warms
  toward 100 and a rivalry cools toward −100 and neither can ever become the other, because no rule
  re-types a row either. What a broken alliance leaves behind — a rivalry, or simply nothing — is a
  decision about the fiction rather than a default, and adding a dissolution without making it would
  be picking one silently.
- **fixed (v1.0.0-rc.52)** — *Nothing in either harness reached a `create_category` call.*
  Every provisioning helper defaults to `create_missing=False` and the one caller that passes True
  is the GM dashboard's Full Setup, so no slash command and no hub button could reach it — the
  categories, the four realm capitals, the nine auction channels and (now) the four per-world
  events channels were provisioned by code no test had ever run. rc.51 recorded it as deferred
  because the Discord sweep's own rule is that a loop goes through a surface rather than a handler.
  The resolution is that the bot's **control plane is a surface** — it is just not a Discord one:
  section 2b of `scripts/playtest_discord.py` posts `{"action": "setup"}` to
  `POST /control/discord` with `X-Xianxia-Control`, exactly as the dashboard does, then asserts that
  every category exists and stands in its stated order, that each capital and each world feed sits in
  the right one, that nine auction channels were made, and that a second Repair over the same layout
  creates nothing new.
- **fixed (v1.0.0-rc.51)** — *Teardown never deleted an auction channel, so it never deleted the
  capitals' category either.* `clear_discord_bindings` has `DELETE`d from `auction_house_channels`
  since v0.33.1, while `teardown_managed_discord_layout` built its targets from the base bindings,
  the realm hubs and `#bugs` and named no auction channel — so the nine of them were left standing
  with their bindings forgotten, and because they sat inside it, `🌌 Realm Capitals` always had
  "9 other channel(s) inside" and was never once deleted. Found while giving the auctions a
  category of their own, which would have inherited the same fault.

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
- **fixed (v1.0.0-rc.47)** — *The three live columns of the checklist asked a person for what a
  machine now does.* Reachable from the hub, error text actionable, narration or fallback fired:
  248 actions times three, seven hundred and forty-four boxes, written in v0.34.0 when nothing in
  the tree could press a button. `scripts/playtest_discord.py` has pressed every leaf since rc.33
  and `test_playtest_coverage.py` holds it to the live definitions, so the first column was asking
  for work already done and the second for the wiring half of work already done. Not one box was
  ever ticked, across twelve regenerations - and `merge_ticks` was carefully preserving them while
  silently dropping the live-table ticks, the only ones that were ever a person's. The per-action
  columns are one machine-filled `Swept` column now, read off the harness's own `DEFERRED_LEAVES`
  so it cannot claim more than the sweep drives.
- **fixed (v1.0.0)** — *The live pass on the NAS is unticked.* It is walked and ticked: every row
  of the four live tables in `docs/playtest/v1.0.0.md`, against a real server running the release.
  What it covers is what the sweep structurally cannot reach — real Discord, a live AI route (it
  runs `NARRATOR_PROVIDER=procedural`), the auction channels, `/vote`, a mute expiring on its own,
  `update.sh --fetch` against the published archive and its `.sha256` sidecar — plus the one
  judgement neither harness can make, whether a refusal reads helpfully to a human, made against
  the sweep's log rather than against 248 empty boxes. The ticks then survived their first
  regeneration, which is rc.47's `merge_ticks` fix doing on real ticks what could only be argued
  about while there were none.
- **fixed (v1.0.0)** — *A release could ship a thing only a person can check with nothing asking a
  person to check it.* The live tables are prose a release has to remember to extend, and rc.59 did
  not: the category split, the re-parent of channels that already existed, the read-only lock on
  ones the bot did not create and the retired `#event-scenes` had **no rows at all**, and were
  walked off a hand-written page instead. The upgrade table was stale in the other direction — it
  named `--channel beta`, where the release candidates went out, while a tag with no `-` in it is
  published as GitHub's latest, so 1.0.0 is on `stable` and the row would have sent the next walker
  to a channel with nothing newer on it. Both are in `scripts/playtest_checklist.py` now, the
  layout as its own five-row table saying to walk it on a guild with history. There is no gate
  behind this and deliberately so: which features need a person cannot be derived from the code,
  which is exactly why the rule is written here instead.
- **fixed (v1.0.0-rc.39)** — *`get_world_clock` in Python re-anchored the clock the engine owns
  when the configured scale changed.* Named on the roadmap's remaining-authority list since v0.30.0.
  The method is gone: `world.clock` is a read-only engine query and every surface reads through it,
  `WORLD_TIME_SCALE` is the engine's key (compose passes it) and seeds a new world only, and
  `/admin world advancetime` changes the rate only when asked to. It had one live consequence: a
  rate a GM set on the dashboard was undone by the next command that asked the time.
- **fixed (v1.0.0-rc.48)** — *The simulation's force and bootstrap requests trusted a
  caller-supplied `game_minute`.* `RunDue` ignored one by design since the v0.22.2 review, with the
  reason written on the field — *"a scheduled tick must not be able to tell the world what time it
  is"* — while `ForceRequest` and `BootstrapRequest` carried the same field and used it, for
  twenty-six more releases. `runSystem`'s own comment said it stamped the anchor "at a caller-chosen
  minute", two hundred lines under the field that said the opposite. Both derive
  `game.CanonicalWorldGameMinute` now, the one door `RunDue` already read. The wire still accepts the
  field so an older bot mid-upgrade keeps working; the three Python client methods no longer take one,
  and nine call sites stopped computing a minute the engine throws away. `caller_minute_test.go` sends
  a wild minute and asserts the canonical one landed (it fails with `Force stamped 9999999` against
  the old code), and `test_simulation_minute.py` holds the Python side — where the assertion that
  encoded the fault lived, two tests below one refusing a forged minute on `/v1/game/action`.
- **fixed (v1.0.0-rc.58)** — *An authored modifier stat could reach no rule, and nothing said so.*
  `active_effects` modifiers were a vocabulary with no gate: content authored twenty stats and
  production Go wrote twelve, and seven reached nothing. Two were the `characters` column name
  written into a modifier slot — `sense_power_bonus` on `space_domain`, `sense_precision_bonus` in
  the engine's own Soul Wound — which is why the gate has to read an argument position and a map
  key rather than a name: that identifier occurs three times in production Go, twice as a column
  inside a SQL string. `modifier_vocabulary_test.go` holds the vocabulary from both sides with an
  empty allowlist, and its own drill found the last fault in it — pointing the content path at
  nothing made the whole test SKIP, green and useless, so a read that fails is a `t.Fatalf` now.
- **deferred (design)** — *A Law control effect's modifiers are never applied to anybody.*
  `special_effects.spatial_lockdown` and `.spatial_strangulation` carry modifiers describing a
  *target*, and `combat.technique` resolves both mechanically (suppression turns, damage) while
  writing no `active_effects` row — a battle opponent is a name on `battles`, not a row anything
  can modify, and there are no PvP techniques. So the modifiers describe something the engine has
  nowhere to put. `combat.technique` names the effect that landed now, so the content reaches a
  player as prose; applying it is a mechanic rather than a wiring, and wants its own change.
- **deferred (design)** — *`nine_yang_solar_body` scorches only through the purge.* Its drawback
  prose says "excess yang scorches the meridians" and v1.0.0-rc.58 gave it `fire_resistance -5`, so
  it bites on `alchemy.purge` and nowhere else. A physique that scorches on its own schedule needs a
  simulation pass over a body nothing currently ticks, which is a different change.
- **fixed (v1.0.0-rc.59)** — *A category or a read-only lock reached a fresh guild and no other.*
  Three of the five helpers that place a channel in a category computed it only inside
  `if channel is None and can_create:`, so a channel that already existed — pre-existing,
  name-matched, or bound by a GM — was bound where it lay and stayed there. The same line held
  `READ_ONLY_BASE_CHANNELS`, so `#xianxia-info`, `#expeditions` and `#player-homes` were read-only
  only where the bot had created them, and `ensure_base_xianxia_channels` returned `"repaired": []`
  as a hardcoded empty list into an audit row that had therefore never carried anything. rc.51 had
  already found and fixed this for the auction floors and rc.52 for the world feeds; the other
  three never got it. `test_the_layout_reaches_an_existing_server.py` now holds all five, with an
  empty allowlist.
- **deferred (design)** — *An event scene is only visible to somebody who has reached that world.*
  v1.0.0-rc.59 anchors an event's scene and its thread in that world's own feed, which is where
  rc.52 already sent the announcement, and retires `#event-scenes`. Those feeds are gated by the
  realm **access** role, so a scene in a world a player has not reached is now invisible to them
  where the shared channel was not. That is the trade the change makes rather than an oversight:
  an event is somewhere, and a cultivator who cannot reach the somewhere could not have joined it.
- **deferred (design)** — *Moderation is a nudge on the engine's dispatch layer, not anti-cheat.*
  A muted or frozen player is blocked from the ~150 authoritative ops; raw `/v1/db` writes the
  bot makes on their behalf and the simulation runner are not intercepted. Stated in
  `moderation.go`; a stronger guarantee would need every presentation write to carry the actor.
- **fixed (v1.0.0-rc.2)** — *The forty-three local auction floors share archetype prose.* Was:
  twelve archetypes, four worlds; a riverside hall read like a riverside hall in every world. Each
  floor now has a paragraph of its own - its river, crater, terrace or court, its broker by name, and
  where its protection ends - and the content gate holds the forty-three apart.
- **fixed (v0.39.0)** — *Typed play fills one argument, not two.* Was: `$ I give the pill to Qiao`
  routed to talk or use. A root may now declare several arguments, each with its own source (a
  known place, a carried item, an NPC present, a player present); `$ I give the pill to Li Feng`
  is a trade offer to Li Feng, and a line that names an NPC instead says what it lacks rather than
  guessing a cultivator.
