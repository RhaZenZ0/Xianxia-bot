# v0.23.0 — Authority I, closed

`PLAYER_MUTATIONS` is empty. Nothing under `app/bot` or `app/ops` writes a
gameplay table any more, and the gate that tracked the backlog now asserts the
absence rather than counting down toward it: a new Python-side gameplay write
fails `test_the_v0_21_backlog_stays_closed` rather than being added to a list.

That milestone opened at 27 rows in v0.21.0 and stood at 21 after v0.22.0
closed `accept_quest`. This release closes the remaining 21.

## What replaced them

| Engine action | Rows | Notes |
|---|---|---|
| `alchemy.purge` | 2 | The whole purge cycle in one transaction |
| `admin.player.set_sect` | 2 | Already existed in Go; only the wiring was missing |
| `admin.player.set_master` | 2 | Set and clear, with the cycle and cross-sect checks |
| `admin.player.set_sect_rank` | 1 | |
| `admin.player.master_attention` | 1 | |
| `admin.player.grant_storage` | 1 | |
| `admin.world.spawn_realm` | 1 | The engine mints the event key and the closing time |
| `character.set_gender` | 1 | |
| `sect.discover` | 2 | Batched, with per-sect source locations |
| `sect.abode.enter` / `sect.abode.leave` | 1 | |
| `law.technique` | 1 | The out-of-battle half |
| `sect.shadow` | 4 | Status and initiation |
| *(deleted)* | 2 | The Python mirror of the toxicity curve |

## Why it mattered

Not tidiness. Every one of these was a sequence of separate writes that could
half-happen, over a process boundary, with no receipt:

- **`/alchemy purge`** spent Qi, reduced toxicity, rewrote a shared effect row
  and set an hour's cooldown as four round trips. An interruption anywhere in
  the middle left a player who had paid for something they did not get, or the
  reverse. One transaction now, with a receipt, so a retried click replays.
- **`/sect shadow`** wrote a membership, an item and its provenance as three
  calls — a failure between them left a Shadow Initiate holding a forbidden
  manual with no record of where it came from.
- **Every admin command** changed the world and then, separately, wrote the
  audit row saying who did it. Rule 6 in `CLAUDE.md` exists to prevent exactly
  the gap between those two writes. Each action now audits inside its own
  transaction, and the tests assert both directions: the row exists after a
  change, and a refused action leaves none.

## Behaviour that changed, deliberately

Five places where the old code was not merely in the wrong layer but wrong.
Each is noted at its call site:

- `set_gender` validated nothing and coerced an unrecognised value to
  `"neutral"`, so a typo quietly changed the player's character.
- `adjust_master_attention` updated nothing for a disciple with no master and
  reported `0`, so a GM saw a success they had not had.
- `set_master`'s lineage-cycle walk gave up **silently** after 64 links and
  accepted an assignment it had not finished checking.
- The pill-toxicity penalty was only recomputed when someone opened
  `/alchemy status`, so a player who took a heavy dose and then waited kept the
  full penalty on every cultivation attempt. The engine now settles the curve
  wherever it reads the effect table.
- `discover_sect`, called once per sect, could not tell a caller which sects
  were new, so screens announced sects the player already knew.

Everything else is transcribed unchanged, including formulas that could have
been "improved" on the way past. `/alchemy purge` still reads `will` and
`spirit` from stored attributes rather than canonical ones — toxicity applies a
will penalty, so canonical attributes would make a heavy dose harder to purge
in a way the old code never did. That is a balance decision and belongs in its
own change.

## Two older defects, found while testing this one

**`fmt.Sprint` on a missing map key returns `<nil>`.** Every required-field
guard written as `strings.TrimSpace(fmt.Sprint(p[key])) == ""` therefore passed
on exactly the payload it existed to reject, and the action carried on with the
four characters `<nil>` as a currency id, an event key or a location. 25 sites
across `internal/game`; all now go through `stringField`, with a test pinning
the behaviour.

**The late-ack guard had a blind spot.** `test_ack_before_mutation` accepted an
ack anywhere earlier in a handler than the mutation — including inside an
`if ...: reply; return` branch, which never runs on the path that mutates.
Three handlers had that shape: `use_item_command`, `sense_command`, and the
character-creation modal's `on_submit`. All three acked on every path except
the one that committed a gameplay write. They defer first now and reply through
`respond()`; a second test walks the blocks enclosing the mutation from the
inside out and requires an ack that dominates it, which is what "acked on the
path that mutates" actually means. No exemption list — `require_character`'s
`is_done()`-guarded defer passes on its merits.

## Verification

Every action's tests were verified against the defect, not the fix: the guard
or write under test was temporarily removed and the test confirmed to fail,
then restored. That caught three of my own mistakes during the release — a
`sect.discover` de-duplication test that passed either way, an abode test whose
name promised more than it checked, and a test filter that silently matched
only half the cases it claimed to.

- Python: 842 passed
- Go: full suite green, including `-race`
- `gofmt`, `go vet`, `ruff`: clean
- Dashboard implementation gate: pass
- No schema change (still 31)

## What this does not prove

None of v0.22.0 through v0.23.0 has run against a live Discord server. That is
a dozen new engine actions, three schema migrations, a request middleware and a
changed shutdown path, all proven by tests and none by play. A test-server pass
is the next thing this code needs.
