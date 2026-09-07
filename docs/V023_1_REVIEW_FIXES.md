# v0.23.1 — the second review

Eight logic errors, reported by an external review and each confirmed against
the code before anything was changed. All eight were real.

## The serious one: value crossing reincarnation

Reincarnation wipes `inventory` and `currency_wallets` because they belong to
one incarnation. Auctions, bids and caravans do not live in those tables. They
are asynchronous records keyed by the persistent Discord id, settled later by
the maintenance sweep, which pays whoever that id names **at settlement time**:

```
incarnation A lists a rare item on a long auction
  → dies → reincarnates → inventory and wallets are wiped
  → the auction closes
  → the item, or its proceeds, arrive in incarnation B
```

Bids are the same in reverse: a bid escrows currency, and being outbid refunds
it to whichever body holds the id when the refund fires. Caravans dispatch with
cargo consumed from one incarnation and settle into another.

**The fix is not a generation column.** The review suggested stamping an
`incarnation_id` on every asynchronous record and checking it at settlement,
and that would work — but it means every settlement path must remember to
compare it, which is the same omission that caused this, relocated somewhere
harder to see. Instead the escrow is resolved when the body dies
(`resolveIncarnationEscrowTx`, called inside `true_death`'s transaction):

- listings end, their current bidder is refunded, and the goods go to the corpse
- the dead player's own bids are released and those lots revert to no bid — a
  dead cultivator's offer must not keep pricing out the living
- caravans on the road are lost with their owner
- an active seclusion ends with reason `incarnation ended`

Nothing here pays the dead: refunds land in the dead character's own rows,
which the reincarnation wipe then clears. The point is that value leaves escrow
while it still belongs to the incarnation that put it there.

A missing table is tolerated — a database mid-migration must still be able to
kill a character. The first draft of this fix broke every old-age death on a
fixture without an `auctions` table, which the moderation suite caught.

## The other seven

| # | What was wrong | What it cost |
|---|---|---|
| 2 | `storage.upgrade` never compared the new container to the held one | 500 slots and a living world traded for 24 slots, pouch consumed |
| 3 | `wealth <= 0` guarding a `MAX(0, wealth-cost)` subtraction | a family with 1 stone paid for a 20-stone package |
| 4 | `seclusion_sessions` outside the incarnation wipe | an old body's retreat scored against a new body |
| 5 | completion required whole-day accounting to reach a non-whole-day end | a 1500-minute seclusion never finished, ever |
| 6 | the breach check sat behind the turn check | a duel deadlocked when the player holding the turn died |
| 7 | a nil event target short-circuited the objective comparison | one untargeted "talk" completed every targeted talk objective |
| 8 | no `quest_definitions` row meant both "static quest" and "not a quest" | the engine accepted any string as a quest |

Two of these deserve their own note.

**#6 is a hole in my own v0.22.4 fix.** That release added `checkPvpParticipants`
on every act specifically so a breached match would resolve instead of stranding
both players — and then placed it after the `turn_user_id` check, which is
exactly the case it exists for. When the player holding the turn dies they
cannot act; their opponent is told "it is not your turn" and never reaches the
check; the match stays active; and `pvp.challenge` refuses a new duel while one
is active. The survivor was locked out until the dead player reincarnated.

This is the third time in two releases that a guard existed, was correct, and
sat on a path the party it protected could not reach — the late-ack fix accepted
an ack inside a branch that returns, and the toxicity settle only ran if a
player opened a screen. So this one has a source-order contract test as well as
a behavioural one, because "validate, then check whose turn it is" is an
ordering a refactor can quietly reverse without failing anything else.

**#8 needed content, not just a guard.** Static quests (`app/rules/quests.py`)
had no `quest_definitions` row, so the engine could not distinguish
`first_steps` from `totally_fake_foobar`. They are now seeded through
`sync_commission_pool` with an empty `giver_npc` — still ordinary quests,
occupying no commission slot — so that "no row" can mean "no such quest".

## The test gap that explains all of it

Before this release there were **no Go tests at all** referencing
`family.support`, `storage.upgrade`, `seclusion.start` or `seclusion.settle`.
The quest suite tested a *wrong* target (`Elder Oak` against `Elder Pine`) and
never a missing one, which is precisely the case the wildcard bug needed.

Every fix here ships with tests verified against the defect: the fix was
temporarily reverted and the test confirmed to fail, then restored. That
practice caught one of my own mistakes again this round — a first draft of the
escrow fix that made death impossible on narrow fixtures.

## Verification

- Python: 845 passed
- Go: full suite green, including `-race`
- `gofmt`, `go vet`, `ruff`: clean
- Dashboard implementation gate: pass
- No schema change (still 31)

## Still not proven by play

Unchanged from v0.23.0, and now more true: this release touches death, the
auction house, caravans, seclusion, PvP and quest acceptance. All of it is
proven by tests and none by a live server. The test-server pass is overdue.
