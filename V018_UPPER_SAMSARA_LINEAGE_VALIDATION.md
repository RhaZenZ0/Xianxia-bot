# v0.18 Upper Samsara Lineage + Two-Death Validation

## Patch scope

- Mortal Samsara continues to select only the 11 canonical starting families and never uses Greenriver Town as a rebirth origin.
- Spiritual, Immortal, and Celestial Samsara use realm-local family pools rather than copied Mortal archetypes.
- Cross-realm ancestry is historical rather than guaranteed. Supported outcomes:
  - `distant_surviving_branch`
  - `fallen_severed_branch`
  - `extinct_branch_replaced`
  - `no_known_connection`
- `extinct_branch_replaced` explicitly has no blood continuity with the extinct house.
- Lower-world noble/royal status does not automatically propagate into an upper realm.
- New lives spawn inside `birth_family:<family_id>` while the scene's physical location remains the homeland city.
- Leaving the family household places the character in the homeland city.
- All 44 Mortal/Spiritual/Immortal/Celestial family homeland cities now contain local exploration encounters and a local family steward NPC. Greenriver remains outside the family-homeland set.

## Integration playthrough

Actor: `9001`

### Life 1 — Mortal World

- Character: Han Shuren
- Family: Han Family
- Household: `birth_family:1`
- Homeland: Riverguard City
- Tested:
  - household exit
  - road travel
  - exploration/random encounter flow
  - storage deposit/withdraw
  - spatial storage upgrade
  - manual study and forbidden-manual karma
  - forging/crafting
  - beast taming, feeding and training
  - family child generation
  - sect recommendation and discovery
  - Azure Cloud Sect entrance trial
  - sect contribution and treasury redemption
  - master-disciple request and acceptance
- True death #1 completed through the authoritative lifecycle action.

For deterministic upper-world coverage, the integration harness changed the already-created Samsara target to Spiritual World and set the real-time readiness timestamp to zero. Family generation and reincarnation themselves still ran through the authoritative game action.

### Life 2 — Spiritual World

- Character: Qiao Yichen
- Family: Ji Stone-Marrow House
- Household: `birth_family:12`
- Homeland: Stoneheart Spirit City
- Previous family: Han Family
- Lineage result: `no_known_connection`
- Verified:
  - old sect membership cleared
  - old master lineage cleared
  - old beast contracts cleared
  - old learned manuals cleared
  - new family relationship installed
  - household exit enters Stoneheart Spirit City
  - family child Qiao Lumen generated in the new family
  - Stoneheart `/explore` succeeds after the homeland-content fix
- True death #2 completed through the authoritative lifecycle action.

For deterministic third-life coverage, the integration harness changed the second Samsara target to Immortal World and set the real-time readiness timestamp to zero.

### Life 3 — Immortal World

- Character: Lu Zhaoran
- Family: He Tide-Listening House
- Household: `birth_family:13`
- Homeland: Immortal River City
- Previous family: Ji Stone-Marrow House
- Lineage result: `no_known_connection`
- Household exit correctly entered Immortal River City.
- `/explore` succeeded and resolved the local encounter: a damaged river ward problem.

Observed ancestry chain:

`Han Family -> Ji Stone-Marrow House -> He Tide-Listening House`

Neither upper family reused the lower family identity or rank.

## Extinction/replacement verification

The lineage selector has deterministic regression coverage for all four historical outcomes. The replacement case verifies that an older branch can die out and an unrelated family can later occupy its estate, trade, military, or political niche with **no blood continuity**.

A lower-world noble case also verifies that a surviving upper branch does not inherit royal status automatically.

## Regression results

- `go test ./...` — PASS
- Upper Samsara + homeland Python tests — 7 PASS
- Full Python suite — 246 passed + 78 subtests passed

The Python environment printed an unrelated `artifact_tool` spreadsheet warmup traceback on interpreter startup; pytest itself exited successfully with status 0.
