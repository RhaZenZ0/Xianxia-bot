<!--
Write the body as prose. The merged PRs in this repo are read months later by
one person trying to remember why something is the way it is, so lead with the
problem and let the diff be the list of changes. A bulleted dump of every file
touched tells that reader nothing the diff does not already say.

Delete every heading that does not apply. An empty section is noise.
-->

## What was wrong

<!--
The state of things before this branch, concretely enough that someone can see
the fault: the command that lied, the table nothing read, the path a player
could not reach. If this is a new feature rather than a fix, say what could not
be done without it.
-->

## What this does

<!--
The shape of the change, and the reasoning behind the decisions that were not
obvious - especially the ones where the other choice looks better at first
glance. Say plainly when something is deliberately left alone (an unchanged
command path, a cooldown key kept for compatibility); a reviewer cannot tell a
considered omission from an oversight.
-->

## Authority split

<!--
The rule this repo enforces hardest (CLAUDE.md). Answer only the lines this
branch actually touches, in prose; delete the whole section if it touches none.

- Go owns canonical mechanics and is the only thing that opens production
  SQLite. A new mechanic in Python, or new Python SQL, needs a reason here for
  why it is not a Go action, batch, or Go-hosted repository session.
- No Go shadow mode: a migrated mechanic executes once, in Go.
- AI is narration-only. It cannot write rewards, deaths, relationships, travel
  or history, and gameplay survives every route failing.
- RAG permissions stay deterministic: no hidden/participant/faction-only state
  reaches a narrator or a player who should not see it.
- World work is batched natively, not one HTTP/DB op per NPC.
- Layering holds: {rules, ops} <- ai <- database <- simulation <- dashboard <- bot.
-->

## Paired edits

<!-- Each of these is one change living in two places; the second half is the
half that gets forgotten. Delete the lines that do not apply to this branch. -->

- [ ] **Schema changed** - version bumped, the old migration kept so existing
      databases still upgrade in place, `VERSIONS.md` updated.
- [ ] **New `.env` key** - added to `.env.example` (keys and defaults only, a
      contract test holds it to that) *and* explained in `docs/CONFIGURATION.md`.
- [ ] **New Admin Console action** - writes to `admin_audit_log`; say below
      whether `undo_last` can reverse it.
- [ ] **Dashboard view or action** - `/api/capabilities` updated and
      `scripts/check_dashboard_implementation.py` passes.
- [ ] **`requirements.txt` edited** - `make lock` re-run (the Dockerfile
      installs `requirements.lock` under `--require-hashes`).
- [ ] **`content/world.json` changed** - valid JSON, and something in code
      actually reads the new roster.
- [ ] **Files added, moved or deleted** - `python scripts/release_manifest.py --write`.

## Tests

<!--
Which layer, and why there - native Go tests for Go-owned rules, pytest for the
Python-owned boundary rather than a second implementation of an engine formula.

Worth stating outright if you did it: that a new gate was verified to *fail*
when the thing it guards is removed, and what it said when it failed. A test
that can only pass is not a gate.

No test asserts that a random thing happened, however many iterations it is
given - `gamerng` is crypto/rand with no seed, so that test fails at some rate
you cannot drive to zero. Lend the dice with `gamerng.UseRoller(fn)` and defer
the restore, or make the outcome certain by the scenario instead.
-->

## Checks

<!-- What you ran, not what CI will run. Real numbers, please. -->

`make check` (ruff, gofmt, go vet, staticcheck, pytest, go test) -
_N_ Python tests, Go suite green. Dashboard gate PASS.
Release manifest verified at _N_ files. **No schema change.**
