# The eight professions — an audit

Every finding below was **fixed in v1.0.0-rc.19**, except the learning step, which was a design
call rather than a fault and was made in **v1.0.0-rc.20** (see §7). The audit is kept as the record
of what was wrong and why, because four of the seven were invisible to a gate that existed
specifically to catch them. Each section ends with what was actually done. Verified against the tree
at the merge of v1.0.0-rc.18; fixed on top of it.

## The bar

`VERSIONS.md` set it when rc.15 repaired Appraisal and Inscription: a live profession has **a recipe,
a call that grants it, and a check that rolls it.** Those two had none of the three — "a word in a
list", in that release's phrase. This is the same audit run across all eight, plus the balance
question rc.15 did not ask: given that a profession is alive, is it *worth practising*?

The roster is eight, and it lives in exactly one place — `app/rules/progression_systems.py:50-53`.
There is no roster in `content/world.json` and none in Go.

## Verdict

| Profession | Recipes | Granted | Level read? | In content | Was | Now |
|---|---|---|---|---|---|---|
| Alchemy | 11 (TN 12–26) | ✓ | ✓ | recipes, gift, family bonus | healthy | 9 dead outputs given buyers |
| Forging | 8 (TN 14–27) | ✓ | ✓ | recipes, gift | healthy | 3 dead armours given buyers |
| Inscription | 6 (TN 12–25) | ✓ | ✓ | recipes, gift | no workshop buff | buff reachable; Python mirror fixed |
| Formation | 2 → **6** (TN 12–26) | ✓ | ✓ | recipes, gift | **Mortal tier only** | a ladder in all four worlds |
| Foraging | 0 (by design) | ✓ | ✓ | gift only | tier-1 herb everywhere | gathers its own world's herb |
| Appraisal | 0 (by design) | ✓ | ✓ | **nothing** | **no content at all** | gift added; trains only on a landed reading |
| Beast Taming | 0 (by design) | ✓ | **✗ → ✓** | gift only | **vanity counter** | level enters the tame roll and the train gain |
| Artifact Refining | 0 (by design) | ✓ | **✗ → ✓** | gift only | **vanity counter** | level draws the bond tighter |

rc.15's repair held for what it targeted: all eight were grantable and none unreachable from
Discord. Three were nonetheless not good, for three different reasons — all three are now.

---

## 1. Appraisal has no content, and the gate built to catch that omits it

`patron_gift.by_profession` (`content/world.json:61220`) holds seven keys. **Appraisal is the only
profession missing.** A cultivator whose only practised profession is Appraisal falls through
`PatronGift.Material` (`go_core/internal/worlddata/catalog.go:369`) to the path table and silently
receives a different gift.

The guard for this exists and is blind — `tests/python/unit/test_world_content_gate.py:609`:

```python
ENGINE_PROFESSIONS = {"Beast Taming", "Artifact Refining", "Foraging"}
```

Its comment is wrong twice. It says "the three that recipes name" — recipes name **four** (Alchemy,
Forging, Inscription, Formation). It says "the three the engine hardcodes" — the engine hardcodes
**four**: `appraisal_actions.go:209` passes `appraisalProfession` into `advanceProfessionTx` exactly
as `crafting_actions.go:750` passes `"Foraging"`. Appraisal is in neither the hand-written set nor
any recipe, so `test_every_profession_the_engine_can_write_has_a_gift` never subTests it.

So rc.15 gave Appraisal an implementation and a test, and gave it no content — and did not update
the one gate whose docstring promises to cover "every profession the engine can write".

**Fixed (rc.19).** `"Appraisal": "@core"` — a beast core is the one material every tier trades,
which suits the profession whose trade is value itself rather than making anything. And
`ENGINE_PROFESSIONS` is now *derived*: two scanners read the grant sites and the level reads out of
`go_core`, resolving both bare literals and the named constants rc.19 introduced. A set read off the
code cannot drift from it. A companion test asserts the scanners still resolve both argument shapes,
because a scanner that matches nothing passes forever.

Separately, at the owner's direction, a reading that *misses* now teaches 3 rather than 5 against a
hit's 12: finding the right information is what moves the profession, failing at it only nudges.

## 2. Beast Taming and Artifact Refining are written by six call sites and read by no rule

Both accumulate levels that change nothing:

- `beastTameAction`'s modifier (`beast_artifact_actions.go:173`) is
  `spirit + presence + will/2 + pathBonus + bondExperience`. **No profession term.** A Grandmaster
  tamer tames no better than a first-timer.
- `beastTrainAction`'s gain (`:351`) is `clampI64(6 + spirit/3 + contextBonus, 1, 25)`. No
  profession term.
- `artifactBondAction` and `artifactAwakenAction` (`:528`, `:594`) contain **no roll at all** — the
  bond is a deterministic `bond_level=MIN(10, bond_level+1)` and awakening is a fixed gate. There is
  nothing for a level to enter.

Every read of `profession_progress` in that file (`:68`, `:211`, `:268`, `:390`, `:450`) goes into
the response payload for display. The levels are printed and never consulted.

This is the `item_provenance.authenticity` and `tracking_strength` shape exactly — a column set with
care by every writer and read by no rule — which the last two releases each fixed once.

**Fixed (rc.19).** Taming had a roll already, so the level joins it, and the training gain with it.
Artifact Refining had no roll, so the level moves the one number the action does produce: the
resonance each bonding gains, which is what awakening gates on — a refiner who knows the work reaches
an awakened artifact sooner, not a different one. `profession_levels_test.go` runs each action twice
against the same fixture, differing only in the practitioner's level, and a fourth test pins that an
unpractised cultivator is affected not at all.

## 3. Formation was hollowed out by rc.15's own fix

| Profession | Recipes | TN range | Tiers covered |
|---|---|---|---|
| Alchemy | 11 | 12–26 | all four worlds |
| Forging | 8 | 14–27 | all four worlds |
| Inscription | 6 | 12–25 | all four worlds |
| **Formation** | **2** | **15–15** | **Mortal only** |

Formation's two recipes are the same TN. There is no low end to learn on and no high end to reach,
and levelling the profession only improves the odds on those same two disks. Its three peers each
span a 12–15 point range across all four worlds.

This is a consequence of rc.15 rather than a pre-existing hole: six of Formation's eight recipes were
talismans, and that release correctly moved them to Inscription. The move was right. Nothing refilled
what it emptied.

The world is already built for the content that is missing. **Eight array workshops stand across all
four worlds** — `stoneback` and `ashenwall` (Mortal), `broken_halo` and `stoneheart` (Spiritual),
`adamant_body` and `fallen_star` (Immortal), `worldstone` and `ruined_constellation` (Celestial) —
and every one of them buys only the two Mortal-tier disks, because those are the only disks that
exist. A Celestial array workshop has nothing tier-appropriate to trade.

**Fixed (rc.19)** — the ladder below was built as proposed.

## 4. `formation_bonus` is an effect nothing can grant

`craftEffectStat` (`crafting_actions.go:29`) returns `"formation_bonus"` for Formation and
Inscription. That stat occurs **zero times** in `content/world.json`; `alchemy_bonus` and
`forging_bonus` occur once each, inside `special_effects.alchemy_inspiration` and `forge_inspiration`.

The cause is one missing key. `abode.focus` (`property_storage_actions.go:504`):

```go
effectID := map[string]string{"cultivation": "abode_cultivation_focus", "alchemy": "alchemy_inspiration", "forge": "forge_inspiration"}[p.Facility]
```

`"formation"` is a valid facility in `abode_system.facilities`, so it passes every check above this
line and then falls through the map to `""`. The action **succeeds and applies nothing**.

Net effect: Formation and Inscription crafts structurally always have `effect_bonus == 0`, while
Alchemy and Forging crafts get +2. `authenticity_test.go:148` asserts Inscription and Formation
return the same effect stat — true, and both are equally unreachable.

**Fixed (rc.19).** Both halves: `formation_inspiration` grants `formation_bonus` +2 in content, and
`"formation"` is in the focus map beside `"alchemy"` and `"forge"`.

## 5. The roster is dead code, and the gates assert the bug class

`PROFESSIONS` (`app/rules/progression_systems.py:50`) is the only canonical list of eight. **No
production module imports it.** Its only importers are two test files.

The gates around professions do not test what their names claim:

- `ProfessionsAreAllLiveTests` (`tests/python/unit/test_world_content_gate.py:184`) has the docstring
  "Every declared profession makes something… eight professions, six of them real" — and then
  iterates a **hardcoded four-tuple written inline in the test**. It never reads `PROFESSIONS`.
- `tests/python/unit/test_profession_crossloops.py` asserts, in its entirety, that three strings are
  members of a tuple. That is the bug class of rc.15 encoded as a passing test.
- `test_the_talismans_belong_to_the_inscribers` loops over recipe keys containing `"Talisman"` and
  asserts their profession. If every talisman were deleted the loop body never runs and it **passes
  vacuously** — it never asserts that any talisman exists.

So a ninth profession added tomorrow with no recipe, no grant and no effect passes the entire suite.

**Fixed (rc.19).** `ProfessionRosterTests` iterates `PROFESSIONS` and asks exactly those three
questions. Mutation-verified against all three faults: removing Appraisal's gift, making Beast Taming
unread, and breaking the scanner each fail it. The two weak tests are left in place — they assert
nothing false, they were simply never enough on their own.

## 6. Python and Go disagree about Inscription

`manor_craft_bonus` (`app/rules/sect_manor.py:57`) maps `alchemy`, `forging` and `formation`, and
**omits `inscription`**. `craftManorFacilityColumn` (`crafting_actions.go:53`) explicitly includes it
— and its sibling `craftAbodeFacilityColumn` carries a comment citing `sect_manor.py` by line number.
The Python helper the comment points at never received the same edit.

Latent today: `manor_craft_bonus` is only ever called with the literal `"Alchemy"`
(`app/bot/commands/exploration.py:744`). Ungated, and divergent from the authority.

**Fixed (rc.19).** `manor_craft_bonus` now matches `("formation", "inscription")`, agreeing with the
Go authority its sibling's comment already pointed at.

## 7. Balance — the top of every crafting ladder has no sink

Verified against all 120 shops:

| Profession | Outputs | With no buyer anywhere |
|---|---|---|
| Alchemy | 11 | **9** — only `qi_pill` and `recovery_pill` are bought back |
| Forging | 8 | **3** — and they are exactly the three armours above tier 1 |
| Inscription | 6 | 1 — `spirit_focus_talisman` |
| Formation | 2 | 0 |

The Forging case is the sharpest: **every blade sells and no armour above tier 1 does.**
`spirit_iron_armor` is bought by 16 shops; `spirit_crystal_mail`, `immortal_gold_plate` and
`starsteel_aegis` by none, while their matching swords sell in 16, 11 and 6. A smith who climbs the
armour ladder crafts three things the world will not take back.

Alchemy is the widest: nine of eleven pills, including every pill above the first tier, have no
buyer. An alchemist's entire late career produces goods only another player will take.

Two more, adjacent:

- **Foraging** yields the same thing in every world. Its loot table is hardcoded
  (`crafting_actions.go:679-700`) and the common drop is always `spirit_herb` — never
  `moonveil_herb`, `dawnlotus_herb` or `heavenpetal_herb`. A Celestial forager gathers Mortal weeds.
  (Its one genuine niche: `jade_life_herb` and `twin_extremes_fruit` are sold by no shop, so foraging
  is their only source.)
- **Nothing is learned.** There is no `known_recipes` or `required_profession` anywhere; every recipe
  is craftable by anyone from character creation, materials permitting. A profession row springs into
  existence the first time its verb is used. Whether that is a gap or a deliberate simplification is
  a design call, but `/craft`'s own description says "from a known recipe", which is not true of
  anything.

**Fixed (rc.19), except the last.** All thirteen orphans now have a buyer, by the rule the two
working pills already followed: a shop that stocks a thing buys it back, at the quarter of base price
every keeper in the catalogue already pays (the median of all 748 existing `buys` entries is exactly
0.25, and it is the same quarter `auctionReserve` uses). No new shops — every orphan was already *on
sale* somewhere, which is what made the hole so odd.

The gate is the per-world form rather than the per-shop one: a provisioner stocking pills that an
apothecary buys is correct, so what is held is that a crafted good is never stranded in a world with
no counter for it.

Foraging now resolves its common drop through `EventSites.Material`, so a Celestial forager brings
back `heavenpetal_herb`.

**The learning step, fixed in rc.20.** It was left here as a design call rather than a fault, and
the call came back the other way: a method is now two things, one you were taught and one your level
reaches, asked separately because the two refusals send a player to different places. `character_recipes`
(schema 46) records the first; a `min_level` derived from each recipe's own TN gates the second.
Knowledge arrives by jade slip - thirty-one items, one per recipe, sold by the shop kind that already
trades that profession's goods, and not consumed by reading - or from the household you were born to,
which teaches its trade's entry methods plus the lowest method of that trade in its own world, so a
samsara into a Celestial forging house is taught what that world actually uses. Migration 46
grandfathers every character alive at the time with the entry methods and everything their profession
level already reached, so nothing was taken from anyone. `/craft`'s description is true now.

---

## Proposed: the Formation ladder

Mirrors the Inscription ladder exactly — one tier material added per world, the same TN spacing,
prices anchored by doubling per tier off the existing disks (45/50) at the catalogue's existing
~2.5:1 base-price-to-sect-value ratio. Six recipes spanning TN 12–26, level with Inscription's six at
12–25.

| Recipe | TN | Cost | Output | base_price / sect_value |
|---|---|---|---|---|
| Guiding Chalk Array | 12 | `array_disk_blank` 1, `spirit_ink` 1 | `guiding_chalk_array` | 22 / 9 |
| *Minor Qi-Gathering Array Disk* | 15 | *(existing, unchanged)* | — | 45 / 18 |
| *Minor Warding Array Disk* | 15 | *(existing, unchanged)* | — | 50 / 20 |
| Moonveil Concealment Array | 18 | `array_disk_blank` 2, `spirit_ink` 2, `spirit_crystal_ore` 1 | `moonveil_concealment_array` | 110 / 44 |
| Golden Bastion Array | 22 | `array_disk_blank` 2, `spirit_ink` 2, `immortal_gold_ore` 1 | `golden_bastion_array` | 220 / 88 |
| Starfall Bulwark Array | 26 | `array_disk_blank` 2, `spirit_ink` 2, `starsteel_ore` 1 | `starfall_bulwark_array` | 440 / 176 |

For comparison, the ladder it mirrors: Swift-Wind 12, Stone-Skin 13, Spirit-Focus 14, Crystal-Ward
17, Golden-Edge 21, Starfall 25, priced 18 → 20 → 24 → 48 → 96 → 192.

Each new disk should be added to the `buys` list of the array workshops in its own world — they
already stand at every tier and currently buy only Mortal goods, so this costs no new shops.

One thing to decide separately, and it is wider than Formation: `array_disk_blank`, `spirit_ink` and
`talisman_paper` drop from **no** secret realm and **no** event site. Shops are their only source. So
Formation *and* Inscription have no gathering path into their own base materials, while Alchemy
(`spirit_herb`) and Forging (`spirit_iron`) both do. A player can forage their way into an
alchemist's career and must buy their way into an inscriber's.

## What was done

All seven, in the release that followed the audit. In the order they were repaired:

1. **Appraisal's patron gift**, and `ENGINE_PROFESSIONS` derived from the Go sources instead of typed
   out (§1).
2. **`formation_inspiration`** in content and the `"formation"` key in the focus map (§4).
3. **The two vanity levels** given a term in the rules they belonged to (§2), and the
   `sect_manor.py` mirror aligned with the Go authority (§6).
4. **The Formation ladder** built as proposed below (§3).
5. **Sinks for the thirteen orphans** and a tiered foraging drop (§7).
6. **The roster gate** that iterates `PROFESSIONS` and asks all three questions (§5) — written last,
   because until step 3 landed it would have failed on Beast Taming and Artifact Refining, which is
   exactly the point of it.

Left deliberately at the time: the learning step (§7), which was a design decision rather than a
fault. It was made in rc.20 and is described at the end of §7.

`docs/KNOWN_LIMITATIONS.md` still is not the right home for any of this —
`tests/python/contracts/test_playtest_gate.py` holds every entry there to being either *fixed* or
*deferred*, and it is a playtest punch list rather than an audit record.

## Re-running the checks

Every number above is reproducible. The counts and TN spreads:

```bash
python3 -c "
import json, collections
w=json.load(open('content/world.json'))
c=collections.Counter(v['profession'] for v in w['recipes'].values())
print(c, len(w['recipes']))
print('missing gift:', [p for p in ('Alchemy','Forging','Formation','Inscription','Foraging','Beast Taming','Artifact Refining','Appraisal') if p not in w['patron_gift']['by_profession']])
"
grep -c formation_bonus content/world.json          # 0
grep -rn 'advanceProfessionTx(conn, userID, "' go_core/internal/game/   # the hardcoded grants
```
