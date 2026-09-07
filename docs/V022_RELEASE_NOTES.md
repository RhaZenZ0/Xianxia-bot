# Xianxia RP Discord Bot — v0.22 release notes

Shipping as **v0.22.5**. The v0.21 line (v0.21.0 through v0.21.6) is in
`docs/V021_RELEASE_NOTES.md`. The release is stamped 0.22.5 in
`app/version.py`, `VERSION`, the `Dockerfile` and `docker-compose.yml`, and
carries **schema 31**.

Release dates: 2026-09-06 (v0.22.0 through v0.22.5).

## v0.22.0 — Commissions

The design is `docs/COMMISSIONS_DESIGN.md`, written at v0.21.0 and built here
minus its last producer (seeded invention, deliberately deferred — see "What
this release does not do"). The player-facing shape is the design canvas from
the same conversation: offer → accepted → progress check → abandon confirm /
cooldown refusal → completed, plus the GM review tab.

A commission is a quest a giver NPC offers you in character. Three things about
it are engine facts, and all three now live in Go:

- **You hold one at a time.** Not a cooldown — the slot. Completion rate is
  what limits how much work the world hands out.
- **The terms are fixed at accept.** Which set of terms you took and when it is
  due are stored on your row, so a later edit to the definition cannot change
  what a held commission pays.
- **Four outcomes, one table.** `active → completed | failed | abandoned`, and
  **failed and abandoned cost exactly the same**. Abandoning is allowed — it is
  kinder than making someone carry work they do not want — but it must not be
  the cheap reroll, so it costs what running out the clock costs.

### The engine

`go_core/internal/game/commission_actions.go` is new and owns all of it.

`commission.accept` (authoritative) **replaces `DB.accept_quest`**, which was
the last v0.21 row on `ops/core_services.py`. It refuses a second commission,
refuses during the giver's cooldown, refuses a commission already taken, and
refuses another player's personal one with the same "unknown commission" it
gives for a key that does not exist — the refusal does not confirm that someone
else's commission exists. It then inserts the row with `commission=1`, the
absolute deadline computed from the accepted variant, and the variant index.
**Nothing is paid at accept**, which is what makes a GM retire free.

It also accepts an ordinary quest: one with no giver, or one from the static
catalog in `app/rules/quests.py` with no `quest_definitions` row at all. Those
get `commission=0`, no deadline, and do not take the slot. One accept path for
every quest in the game was the point of building on `accept_quest` rather than
beside it.

`commission.resolve` (authoritative) is the single place a commission leaves
`active`. It sets the status and the resolved minute, pays the locked terms on
`completed` and nothing otherwise, moves standing through the outcome table,
sets the refusal cooldown for `failed`/`abandoned`, and bumps the per-outcome
counter. `admin_retire: true` runs the same path with the charge switched off:
the record still reads `abandoned`, the player pays nothing and receives
nothing, and the slot is free.

`quest.progress` now routes a completing commission through that same resolve
path instead of flipping the status itself, so completion by objectives and
completion by any other route cannot drift apart. Python no longer grants the
reward for these — the transition carries what the engine already paid.

`ExpireDueCommissions` runs on the world tick (`advancedMaintenance`) and fails
every commission whose deadline has passed. It is the only path that makes
`failed` exist without anyone pressing anything, and it reads the stored
deadline rather than a timer, so a restart cannot lose one.

Two GM actions, both audited: `admin.commission.review` (draft ↔ approved ↔
retired ↔ discarded) and `admin.commission.retire` (end this player's held
commission at no cost).

### Selection, and what the narrator is told

`app/rules/commissions.py` is pure and decides nothing the engine owns. It runs
the ladder from the design, in order, on stored state only:

1. holding one → progress check
2. on cooldown → refusal, with the reason
3. a pool match within the standing-derived tier ceiling and the realm band →
   offer, picked deterministically per player per world-day
4. nothing → "he has nothing for you today"

No model call happens for any branch, including the one that ends in an offer.
`Narrator.talk_to_npc` gains a `commission_context` block rendered by
`format_commission_block`: canonical, not fenced (unlike the player's dialogue
and the channel history, every line of it was computed by the engine), and
carrying the standing as a **band** rather than a number so the Steward cannot
read a statistic aloud. The model narrates the block. It never decides whether
there is work, what it pays, or how the last one ended.

**The buttons come from the block, not from the reply.** The offer card is
posted as its own message with the view attached, built from the definition;
`app/bot/ui/commissions.py` imports nothing from `app.ai` and has no way to see
what the narrator wrote. The accept payload names its terms by index.

### The player's side

`/talk` to a giver runs the ladder and attaches the card. Accept has one button
per approved set of terms (standard, harder terms, rushed) and a Decline that
costs nothing. `/quests` marks a held commission, names its giver, shows the
clock, and carries the abandon button — which opens a confirmation that states
the cost in the same words failing gets, because they are the same thing.

### The GM's side

A new **Commissions** tab (`/api/commissions`): what is waiting for approval,
what players are carrying and how overdue it is, standing and outcome counters
per giver, and the engine's outcome numbers shown read-only, because they are
compiled constants rather than settings and a form that silently did nothing
would be worse than no form. Approve / retire / discard and the per-player
retire run through the two audited engine actions.

### Schema 29

`quest_definitions` gains `giver_npc`, `realm_band`, `tier`, `owner_user_id`,
`deadline_game_minutes`, `variants_json`, `seed_json`. `character_quests` gains
`commission`, `deadline_game_minute`, `variant_index`, `resolved_game_minute`.
`npc_relationships` gains `commission_cooldown_until_game_minute`, the three
outcome counters and `last_commission_outcome`. Additive; existing rows keep
the non-commission shape.

### Content

Three givers — Steward Qiao at the Golden Pavilion (whose secret already said
he ran a private board), Magistrate Xu Wenbo and Madam Pei Suyin in Greenriver
— and nine authored commissions across tiers 1–3, every one of them validating
against `validate_quest_definition` on every variant. They seed into
`quest_definitions` at startup **insert-only**, so a GM's edits, approvals and
retirements survive every restart.

### The gate

- `PLAYER_MUTATIONS` in `test_authority_boundary.py` has lost the `accept_quest`
  row and gained none. Python writes no `character_quests` row anywhere.
- Go: accept stores the accepted variant's deadline (not the definition's);
  refuses a second, refuses on cooldown, refuses a completed one; `failed` and
  `abandoned` compared as whole values and again through the database; a GM
  edit after acceptance does not change the payout; retire costs nothing and
  frees the slot; expiry fires once and never on a deadline-free commission;
  an ordinary quest does not take the slot; both GM actions audit.
- Python: the ladder table-driven across every branch and its ordering, the
  determinism of the daily pick, the voice block (band not number, no offer in
  a refusal, `steward_initiates` only after a failure), and a source contract
  that the surface cannot reach the narrator and every engine argument is state
  it owns.
- Content: every giver is a real NPC at the location the table claims, with the
  five fields the voice call reads and no `hidden_master`; every commission has
  a clock; rushed pays less for less time; harder pays more for the same time.

**Python 819 passed. Go suite green.** Two pre-existing failures were fixed on
the way: the bootstrap test still asserted 148 manual items after v0.21.4 added
six authored ones (it now derives the count from the catalog, so it cannot
drift again), and `gofmt` had been flagging `internal/worlddata/catalog.go`.

### What this release does not do

- **No seeded invention.** The design's second producer is not built. The
  columns exist (`owner_user_id`, `seed_json`), the visibility rule is enforced
  and tested, and the dashboard already shows personal commissions with their
  owner — but nothing writes one yet. The design says to see the pool and the
  voice call read well first, and that is the right order.
- No model decides eligibility, tier, variant or outcome.
- No player text reaches any drafter.
- No reward moves at accept.
- No second commission while one is active — there is no override, not even for
  a GM: retiring is how a slot is freed, and it is free for the player.

### Upgrading

Additive schema; no action needed beyond the normal upgrade. The commission
pool seeds itself on first start. Existing quests are unaffected: they accept
through the new action with `commission=0` and behave exactly as before.


## v0.22.1 — the rest of the givers

Eleven givers instead of three, two of whom will not tell you what the work
pays, and a board for every public sect that never appears in the quest
journal.

### Undisclosed terms

A commission may carry `reward_visibility: "hidden"`. That is a **presentation
rule and nothing else**: the engine still locks exact terms at accept and pays
exactly those, the deadline is never hidden, and completion states the payout
in full. What changes is the offer card, which says *undisclosed — they will
not say what it pays* and tells the player, in as many words, that it may be
worth far more than it looks or far less. Taking work on trust is a gamble the
player can see they are making; it is not the interface withholding something
it knows.

The narrator is handed the same fact and an explicit prohibition: **name no
number, no item, no comparison, no promise about generosity**. A giver may
refuse to discuss payment, be evasive, or change the subject — all in
character. What he may not do is invent a price, in either direction. An
authored `boast` line rides along for the givers who claim something grand;
the model may echo it and may not make it specific.

There is nothing to negotiate with someone who will not name a figure, so an
undisclosed commission carries exactly one set of terms.

### Two old men in Greenriver

**Old Beggar Chen** — who is, though nobody has established it, the Void Sword
Venerable — hands out errands that look like nothing and pay like a great deal
(150 stones, 40 insight and a pill for carrying a bowl across town without
making a performance of it). **Old Gou**, the self-declared hidden expert with
an aura-projection talisman, offers the recovery of a Nine-Yang Fragment that
emperors have died for and pays **8 spirit stones**.

From the offer card the two are indistinguishable — same undisclosed line,
same shape, same street. That is the point. And Old Gou is not simply a wall:
his *other* commission, the one he is embarrassed to describe, pays 70,
because his rumours really are good. He is a fraud, not an enemy; standing
with him still rises when you finish something, and the payout line is stated
in full precisely when it is a disappointment.

Neither old man is ever a quest *target* — `validate_quest_definition` refuses
a hidden master as an objective, and sending a player back to one would tell
them he mattered. Their commissions complete on the work itself.

### A board for every sect

Each of the six public sects gets its gate NPC as its quest giver — Gate Elder
Jian Mu, Furnace Examiner Huo Ren, Moon Warden Lin Yue, Handler She Ruo, Blood
Gatekeeper Yan Luo, Lantern Registrar Wu Ming — with **two standing tasks
each**, tiers 1 and 2, terms stated plainly. Sect work is not in `/quests`: the
board is a person, and you have to be a disciple.

`requires_sect` is checked **in the engine at accept**, not only by the offer
ladder — the ladder decides what a giver raises, the engine decides what may be
taken on, and a rival sect's disciple is refused by both. The refusal is
distinguishable from "nothing today", so a member of the wrong sect is told
why rather than left asking.

### Schema 30

`quest_definitions` gains `requires_sect`, `reward_visibility` and `boast`.
Additive.

### The gate

Go: an outsider and a rival disciple are both refused sect work and a member
takes it; hiding the terms changes neither the deadline nor the payout (the
locked rushed terms still pay 24/5). Python: the ladder's sect branch and its
distinct refusal, case-insensitive but not loose sect matching, hidden terms
discarding the real figures, and a check that no number from a hidden reward
reaches the prompt. Content: every public sect has a board whose giver belongs
to it; both old men are hidden masters, are never their own target, and are a
gamble in **both** directions — asserted, because if every hidden reward were
bad it would be a trap and if every one were good it would be a free lunch.

**Python 842 passed. Go suite green.**


## v0.22.2 — the P0 concurrency and integrity findings

An external review of v0.21.6 found four data-integrity defects, two of them
reproduced under load. This release fixes the P0 set and the scheduled-time
finding that sits next to them. No schema change; no gameplay change.

Two of the findings were already closed by v0.22.0 and are recorded here so
they are not fixed twice: **quest acceptance bypassing the authoritative
ledger** (finding #7) went away with `commission.accept`, and **quest
completion and reward in separate commits** (#3) was already one transaction
for commissions. The ordinary-quest half of #3 was still open and is fixed
below.

### The simulation could apply the same interval several times

`RunDue` read every system's anchor at the top of the call and only then
opened the write transaction that applied and advanced it. SQLite serialised
the writes perfectly and it did not help: both callers had already decided,
from the same stale anchor, that one interval was due. The reviewer measured
the same interval applied **7 to 14 times** with 32 concurrent callers.

The whole decide-and-apply cycle now happens inside one `BEGIN IMMEDIATE` per
system (`runDueSystem`), so the read is under the lock that the write needs.
The anchor update is additionally guarded on the value the decision was made
from — `WHERE system=? AND last_game_minute=?` — so if this is ever broken
again the run fails loudly instead of quietly doubling. `runSystem` remains
for the GM's Force path, which legitimately stamps a caller-chosen minute.

The regression test fires 32 concurrent `RunDue` calls at one due interval and
asserts that exactly one caller claims it, that the anchor moved exactly one
interval, and that the `runs` counter is 1. Reverting the fix makes it fail.

### A duplicate request in flight failed instead of replaying

The authoritative contract is that the same `action_id` returns the same
result. It held for a retry arriving after the original committed, and broke
for one arriving while it was still in flight: both callers missed the receipt
check, both queued for the write lock, and the loser re-ran the mutation and
died on `UNIQUE constraint failed: domain_events.event_uid`. State stayed
correct — the loser rolled back — but the caller got an error where the
contract promises a result. The reviewer measured 10–23 failures out of 24.

The receipt is now checked **again inside the transaction**, where the
winner's receipt is visible. The cheap pre-lock check stays, because most
duplicates are late retries that never need the lock at all.

With 32 concurrent identical requests the test asserts the whole contract, not
just the absence of an error: 1 original, 31 replays, every caller holding the
same result, one `character_quests` row, one domain event, one receipt, and
the actor's `state_version` incremented exactly once. A companion test checks
the mirror — ten *distinct* actions do not collapse into each other — and a
third that reusing an `action_id` across actors is still an error, because
returning the first caller's result would be worse than failing.

### An ordinary quest could complete and never pay

`quest.progress` committed the completion, then Python made a second engine
call for `cultivation.reward`. Anything that interrupted the gap — a dropped
connection, a restart, a killed worker — left a quest marked `completed` with
no payout **and no way to recover**, because the next progress report only
looks at active quests.

The declared rewards now travel with every progress report and are granted by
the same transaction that completes the quest, exactly as commissions have
been since v0.22.0. `QuestService._grant_rewards` is gone; Python grants
nothing. Because the rewards arrive from the caller's catalog, the engine
keeps its own ceiling (2000 stones / 500 insight / 20 items — far above the
GM budget) so that a bug or a compromised caller cannot mint an economy
through the quest path, and negative values are clamped to zero rather than
taking anything away.

### The world clock is not a request parameter

`RunDue` accepted `game_minute` from the caller and caught up to it. The
authoritative action path has rejected a caller-supplied `game_minute` since
v0.18; the simulation now does the same, deriving the canonical minute in Go
via the new `game.CanonicalWorldGameMinute`. The request field is kept for
wire compatibility and ignored — a test asserts that a caller claiming minute
50,000,000 changes neither the work done nor the anchor.

### The gate

Both concurrency tests were verified against the defect, not just against the
fix: reintroducing each bug makes its test fail. `go test -race` is green on
`internal/game` and `internal/simulation`, which is worth stating plainly —
these were transaction-ordering races, not Go memory races, so `-race` alone
was never going to catch them.

**Python 839 passed. Go suite green, including under `-race`.**

### Still open after v0.22.2

Restore quiescence (#4) landed in v0.22.3, below. PvP revalidation on accept and
on each action (#5), the engine credential split (#8) and graceful HTTP shutdown
(#9) remain.


## v0.22.3 — restore no longer loses acknowledged writes

The review's finding #4, and the one with the worst failure shape on the list.

Restore was careful about the things that are easy to be careful about — path
traversal, a safety backup before touching the live file — and not about the
one that mattered: nothing stopped ordinary traffic from committing while it
worked. The window was:

```
restore:  take the safety backup      ← snapshot of the world
action:   commit a reward, return 200 ← the player is told it happened
restore:  overwrite the live database ← the reward is gone
```

Because the write landed *after* the snapshot, the safety backup that exists
precisely to undo a bad restore did not contain it either. The player was told
yes, and there was no copy of the yes anywhere.

### The barrier

`internal/server/maintenance.go` adds a process-wide maintenance barrier: every
request that can write takes it **shared**, restore and VACUUM take it
**exclusively**. Shared holders do not contend with each other, so ordinary
traffic runs exactly as concurrently as before; a restore waits for every
in-flight request and holds off every new one.

It is taken in the middleware, by path, rather than inside each handler —
a new endpoint that forgets to take it is precisely the bug this exists to
prevent, so everything under `/v1/` is treated as a possible writer unless it
is on the short maintenance list. `/livez` and `/readyz` are deliberately
outside it: a readiness probe that blocks for the length of a restore reads as
an outage and gets the container killed. `/readyz` reports `maintenance`
instead.

The barrier is process-wide, which is the right scope. SQLite is the
serialisation point *between* processes, and a second engine writing to the
same file during a restore is a deployment mistake rather than a race this code
can fix. What it can fix is its own traffic, and that was the leak.

### The order

`dbRestore` now: barrier (exclusive) → close every db session, whose
connections outlive the request that made them and can be sitting
mid-transaction → **then** the safety backup, of a database nothing is writing
to → then the restore. The safety backup moved from first to third, which is
the whole fix.

### The gate

The invariant is deliberately not "no write is ever discarded" — discarding
writes is what a restore is *for*. It is: **an acknowledged write is never in
neither place.** Either it finished before the barrier and is in the safety
backup, or it ran after the restore and is in the live database.

`TestARestoreNeverLosesAnAcknowledgedWrite` fires 24 concurrent writes through
the real `Handler()` (the existing restore tests call handlers directly and so
would never see the middleware), lands a restore in the middle of them,
collects the labels the server answered 200 to, and asserts every one of them
is in the live database or the safety backup. Against the old ordering it fails
with 2–8 of 24 acknowledged writes in neither — the defect, reproduced. Three
more tests cover the barrier's ordering directly, the path classification
(including that health checks stay outside), and that a db session cannot
survive a restore and write into the replaced database.

`go test -race` green across `internal/server`, `internal/game` and
`internal/simulation`.

**Python 842 passed. Go suite green.** No schema change, no gameplay change.

### Still open after v0.22.3

PvP revalidation (#5) landed in v0.22.4, below. The credential split (#8) and
graceful HTTP shutdown (#9) remain.


## v0.22.4 — a duel is checked all the way through

The review's finding #5. Location, life status and safe-zone were checked when
a challenge was *created* and never again — and a challenge lives five minutes,
which is comfortably enough time to walk into a city:

```
A and B stand together outside a safe zone
A challenges B
B travels elsewhere
B accepts
the duel begins between two people in different places, one of them standing
inside formations that are supposed to make this impossible
```

`go_core/internal/game/pvp_invariants.go` is one validator — both alive, both
in the same place, that place not a safe zone, not the same person — called at
all three gates: challenge, accept, and **every** action.

### What happens when a check fails mid-duel

This is the half worth arguing about, and the review left it open. Returning an
error on every action would leave the match permanently `active`, and since a
player may hold only one duel at a time, both participants would be locked out
of duelling forever. That is a worse bug than the one being fixed and the
player has no way out of it.

So a breached match is **resolved**, deterministically:

- **Someone left** — they forfeit; the one who stayed wins. This is the shape
  that matters, because walking away from a duel you are losing must not be
  cheaper than losing it.
- **Someone died** — the living one takes it.
- **Both left, or both are dead, or the ground is a safe zone** — nobody is at
  fault, so the duel is **void**: it ends with no winner.

Neither a forfeit nor a void pays reputation. Reputation rewards an honourably
fought duel; it should not reward one that ended because a participant walked
off, and the existing `version > 0` rule for surrenders already draws that line.

At accept, a failed check does not create a match at all: the challenge closes
as `void` with a reason, rather than erroring (which would roll back and leave
it pending for the next attempt) or starting an illegitimate duel.

### Schema 31

`pvp_matches` gains `location`. A duel is fought *somewhere*, and nothing
recorded where — so "are you both still here" had no `here`. Without it, two
people who separately walked to the same distant city would still count as
duelling in the street they left. Matches created before this migration have no
recorded location and skip the location rule rather than failing every action;
the life and safe-zone rules still apply to them.

### The player's side

`/duel respond` reports a lapsed challenge in its own words instead of raising
a `KeyError` on a `match_id` that was never created. `/duel act` reports a
breach — forfeit or void — instead of building a damage line out of a result
that has no roll in it.

### The gate

Ten Go tests: accepting from elsewhere, from inside a safe zone, and after a
death each fail to start a duel and close the challenge; an accepted duel
records where it is fought; leaving forfeits in both directions; a death
mid-duel resolves to the living participant; both leaving voids it with no
winner; a breached match does not leave the participants locked out of duelling
again; a legitimate duel is untouched; and a pre-schema-31 match still works.

Verified against the defect: removing the two re-checks makes five of them
fail, including a duel starting between Greenriver Town and the Cloudspine
Foothills, and one starting inside the Golden Pavilion.

**Python 842 passed. Go suite green.**

### Still open after v0.22.4

Graceful shutdown (#9) landed in v0.22.5, below. Only the credential split (#8)
remains.


## v0.22.5 — the engine drains instead of severing

The review's finding #9, and the last of its list bar the credential split.

`httpServer.Close()` severs every open connection at once — including one whose
transaction has already committed but whose response has not been written. The
caller is then left unable to tell **"it did not happen"** from **"it happened
and I did not hear"**, which for a mutation is the worse of the two.

`server.Drain` replaces it: stop accepting connections, wait for the requests
already running, and only then let the process go. The wait is bounded — a
shutdown that waits forever is not a shutdown — and exceeding it is logged
rather than swallowed, because that is the one remaining path that can still
cut a response and an operator should see it happen.

Two orderings matter and neither is obvious:

- **Storage closes after the drain, not with it.** `http.Server.Shutdown`
  makes `ListenAndServe` return as soon as it is *called*, so treating that
  return as "everything has stopped" closes the database out from under
  handlers that are still using it. `main` now waits on its own `drained`
  channel before `engine.Close()`, and the `defer engine.Close()` that would
  have fired too early is gone.
- **The grace must fit inside the orchestrator's.** Docker's default is a 10s
  SIGTERM-to-SIGKILL window, which would have killed a 20s drain two thirds of
  the way through — and, since v0.22.3, could kill a *restore* halfway, because
  a restore holds the maintenance barrier for its whole duration.
  `docker-compose.yml` now gives the engine `stop_grace_period: 30s`.

`ENGINE_SHUTDOWN_GRACE_SECONDS` tunes the wait, defaulting to 20 seconds; a
value that will not parse falls back to the default rather than to zero,
because a typo in a deployment variable should not cost a committed response.

### Context

This was already the least dangerous item on the list by the time it was
reached. Since v0.22.2 an interrupted authoritative action is recoverable —
retrying with the same `action_id` replays the original result instead of
failing — so the ambiguity had a cure. Legacy mutation paths have no receipt,
which is why it was still worth not creating the ambiguity in the first place.

### The gate

Four tests in `internal/server`: an in-flight request that has "committed" gets
its full response written despite a shutdown beginning mid-flight; a new
request is refused once draining has started; an over-running handler causes
`Drain` to report the expired grace instead of hanging; and the grace knob is
configurable and fails safe on garbage. A fifth reads `main.go` in source for
the ordering no runtime test can observe — that storage is not closed until the
drain has finished, and that nothing severs connections directly any more.

Verified against the defect: swapping `Drain` back to `Close()` fails the first
test with `Get "http://…/mutate": EOF` — the caller cut off mid-answer, which
is precisely the reported bug.

**Python 842 passed. Go suite green, including under `-race`.** No schema
change, no gameplay change.

### Where the review ended

Eight of the nine findings are closed. Two of them (#7, and the commission half
of #3) were already gone before the review's fixes began, having been closed by
v0.22.0; the other six each ship with a regression test that was verified
against the defect rather than only against the fix.

**#8, the credential split, is declined** — the operator's call, taken on
2026-09-07 and recorded in `docs/ROADMAP_1_0.md` under v0.25 rather than left
on a list as though it were pending. One `ENGINE_AUTH_TOKEN` continues to grant
gameplay, arbitrary SQL, migrations, backup and restore. The reasoning both
ways, and the cheaper middle option if it is ever wanted, are written down
there.

The next thing this code needs is not another finding. It is a test-server
pass: v0.22.0 through v0.22.5 added three authoritative actions, three schema
migrations, a request middleware and a changed shutdown path, all proven by
tests and none of it by play.
