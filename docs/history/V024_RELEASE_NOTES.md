# v0.24.0 — The Quests workbench

## What was wrong

A quest definition could be created, and its status could be changed. That was the whole vocabulary.
There was no edit anywhere in the system.

So a forged draft was take-it-or-leave-it. If it was ninety per cent right — a good hook, one
invented NPC, a reward twenty points over budget — the only move was to discard it and roll again,
and hope. The GM could see exactly what was wrong with it and could not touch it.

Two more symptoms sat on top of that:

- **The same quest was reviewable on two screens, depending on whether it had a giver.** The
  Commissions page queried `WHERE q.giver_npc<>''`, so a forged draft never appeared there. The
  Exploration page listed forged quests read-only, so the only place to approve one was the embed
  the Forge replied with in Discord, which offered Approve and Discard and nothing else. "What is
  waiting for me?" required knowing which of three surfaces a quest happened to land on.
- **Editing a definition rewrote deals that had already been struck.** Completion rewards were read
  from the *current* definition at the moment a player finished. Change a quest's reward while four
  people are carrying it and all four finish under the new one — up or down, without being told.

That last one had a sharper edge than it looks. The objectives were read the same way, so changing
an objective could take away work a player had already done, and retiring a definition froze every
holder: Python sent progress only for quests in the approved catalog, so a retired quest could not
be progressed, could not be completed, and could not be paid. The player held it for good.

And underneath all of it: the objectives and the rewards reached the engine as fields of the
caller's payload. Python was deciding what the work was and what it was worth. The engine took its
word for it.

## What changed

### Terms are pinned to the player who accepted them (schema 32)

`character_quests.terms_json` records the objectives and rewards a quest was accepted under.
`quest.progress` reads them off that row; the `objectives` and `rewards` fields are gone from its
payload, and anything a caller still sends is ignored.

A commission never had this problem — it has locked its variant and its deadline onto the player's
row since v0.22.0, precisely so a later edit could not change what a held commission pays. This
generalises that lock to every quest.

Rows accepted before this release carry no pin. They are backfilled from the current definition the
first time they are touched — which is exactly the deal they were already on — and pinned from then
on.

Two things follow immediately. Retiring a definition now means only what it says: nobody new may
take it, and everybody holding it can still finish it and be paid. And the engine, not Python, is
the authority on what a quest asks for and what it is worth.

### `admin.quest.save` — the missing verb

A definition can be created and edited. Every save is held to `validate_quest_definition`, the same
gate the Forge is held to, so a GM typing by hand and a model drafting cannot disagree about what a
valid quest is: the objective types the engine can actually complete, targets that exist in the
world, at most four objectives, and the reward budget.

A hand-written quest is keyed `quest_…` rather than `forge_…`. The prefix is not decoration — the
pool reads `forge_` as "a model wrote this", and it should not claim authorship the Forge never had.
A new key is checked against the existing ones, because `admin.quest.save` treats a known key as an
edit and a collision would silently overwrite a different quest.

Saving does not approve. The two used to be the same column and the same write; keeping them apart
is what stops "I fixed a typo" from putting something live.

### The hold policy

Pinning makes editing a live quest a real decision, so the save action takes one:

- **`keep`** — every holder stays on the terms they took. The new version is what anybody taking it
  from now on gets. The default, and the only policy that cannot cost a player anything.
- **`migrate`** — holders move onto the new terms. Progress carries across objective by objective
  wherever the objective still means the same thing; progress on one that now asks for something
  else is lost, and the count of what was lost comes back in the result so the GM is told rather
  than finding out. A migrated **commission** keeps the payment its giver agreed to — only the work
  moves, because the money was a promise a named person made.
- **`revoke`** — the quest is taken back. The row is removed rather than closed, so the fixed
  version can be accepted fresh; a closed row would leave "you have taken that quest before"
  standing between the player and the repair.

An unrecognised policy is refused rather than treated as the default: a typo must not silently keep
or silently revoke.

The holders are read **before** the definition is overwritten. A player who accepted before pinning
existed has nothing on their row, so "keep them as they are" has to capture the old definition
first — otherwise it quietly does the opposite of what it says.

### The Quests page

One view of every definition a player can be given, whatever produced it:

- **Needs your review** — the draft inbox, with objectives, rewards, origin and the review actions.
- **Live pool** — filterable by origin, realm band and status, with how many hold each quest and how
  many have completed it.
- **The editor** — title, summary, giver, band, tier, deadline, objectives (type, target, count) and
  rewards, with the pickers bound to the world's actual locations, NPCs, scene actions and items, so
  a target the world does not have cannot be typed in the first place.
- **Preview** — what the player is shown beside what the engine stores.
- **Where the pool is thin** — realm bands, objective types, public locations no approved quest
  sends anyone to, and any approved quest pointing at something that no longer exists.
- **Forge a draft** — the Forge, from the dashboard, without going to Discord.

`admin.quest.review` is the general name for the status change `admin.commission.review` was always
performing — it never looked at whether the row had a giver. The old name keeps working.

`quest_definitions` moves from the Exploration view's coverage registry to the Quests view's;
`character_quests` stays with Commissions, which owns the player-side view of a held one.

## Also in this release

The Python suite runs about 150 more tests on a minimal machine. `httpx` is constructed at import
time by `app/database/remote.py` and `app/ops/game_engine.py`, so on a machine without it
`from app.database import Database` raised during collection and took roughly twenty files — every
integration test above the transport — out of the run entirely. `tests/conftest.py` now installs a
stub that refuses to send a request, so a test that actually reaches the network still fails; the
two files that drive `httpx.MockTransport` skip with a reason instead of erroring.

## Upgrading

Schema 32 is one `ALTER TABLE`, applied on start like every other migration. Existing held quests
need no attention: they are pinned to the terms they were already on the first time they are
touched.

Nothing changes for players except that the deal they accepted is now the deal they finish.
