# v0.18 Build History (consolidated)

This file replaces 19 separate per-stage AUDIT/VALIDATION/CHECKPOINT files that
accumulated during the v0.18 build. Those files were largely redundant with
each other (multiple validation passes reporting on the same stage) and with
the six `V018_STAGE*_NOTES.md` files, which are kept as-is — they document
*why* each authority boundary was built the way it was and remain the
higher-value record. This file keeps only what those NOTES files don't
already cover: what each stage's own test run claimed, and a note on which
claims have since actually been independently re-verified.

**Important caveat:** the "reported" lines below are what the original build
process's own docs claimed (`go test`, `go vet`, `pytest` pass counts). They
were not independently re-run before this consolidation. Where something
*was* independently verified in this repository (by running the actual code,
not reading it), that is called out explicitly and separately.

## Stage-by-stage (design rationale in the matching `*_NOTES.md`)

- **Stage 1 — Forage input authority.** Removed forgeable `context_bonus`/
  `location`/`realm_index`/`garden_level`/`cooldown_seconds` from the client
  payload; Go derives all of it. Reported: 237 passed, 78 subtests; `go
  test`/`go vet` PASS.
- **Stage 2 — Forage effect authority.** Moved `effect_bonus` computation
  (active effects, bloodline/physique modifiers, deployed arrays, pill
  toxicity decay) fully into Go; added the read-only `effects.current` query.
  Reported: 238 passed, 78 subtests; PASS. Audited line-by-line before Stage 3
  (`V018_STAGE2_AUDIT.md`, folded in here) with no unresolved findings noted.
- **Stage 3 — Craft resolve authority.** Same treatment as Stage 2, applied to
  `craft.resolve`: location, game_minute, and every facility/family/mastery
  bonus now Go-derived. Client sends only the recipe name.
- **Stage 4 / 4.5 — Companion & artifact authority + family-city roads.**
  Introduced per-operation payload allowlists (`validateCompanionPayload`)
  and a shared canonical living-actor loader for beast/artifact actions; added
  family-city travel integration. An adversarial audit against Stages 3+4
  together (`V018_STAGE34_ADVERSARIAL_AUDIT.md`) and a dedicated checkpoint
  doc were folded into this entry; both reported a clean pass with no
  unresolved findings.
- **Stage 5 — Canonical time + road authority.** Centralized canonical-time
  enforcement at the shared authoritative boundary (`rejectCallerGameMinute`
  + `stage5CanonicalizeMutationPayload`) instead of duplicating clock reads
  per handler; added direct-road duration/danger/encounter integration.
  **This boundary was independently re-verified this session** — traced the
  full `applyAuthoritative` dispatch path directly and confirmed every
  non-exempt mutation is forced onto canonical `game_minute`, and separately
  confirmed by exhaustive check (not sampling) that all 9 operations in the
  `stage5CanonicalTimeNativeOperations` exemption list correctly resolve
  their own canonical time internally via `canonicalWorldGameMinute` rather
  than silently reading a zero-value field.
- **Stages 6–8 — Lifespan authority, road/caravan/dashboard, Python
  cleanup.** Added the shared Go lifespan model (later extended this session
  with the activity-pause clock and actual old-age death enforcement, since
  the original Stage 6 work covered NPC natural-death only — see
  `V018_LIFESPAN_PAUSE_FIX.md`'s intent, now implemented). Added
  dashboard-owned Discord setup and began Python dead-code removal for
  migrated authority. An adversarial audit and two validation passes for
  Stages 6–8 were folded into this entry; all reported clean.

## Release-level

- **`V018_RELEASE_VALIDATION.md`** (folded in): final regression pass
  promoting the audited Stage 6–8 checkpoint to release, reported clean.
- **`V018_RELEASE_SECURITY_LOAD_AUDIT.md`** (folded in): a security/load
  pass dated 2026-08-30, reported no findings. Not independently re-run.
- **`V018_REMAINING_BUILD.md`** (folded in, now superseded): originally
  stated the required staged authority work was complete as of Stage 8. That
  is still true for the *migration* work; it does not reflect the dashboard
  coverage, fate-system, and lifespan-death work added in this session (see
  below).

## Feature validation docs folded in (post-release additions)

These described real, implemented features (each checked and confirmed
actually present in the code, not just claimed) but their own "N passed"
test claims are, again, not independently re-run:

- `V018_ANCESTRAL_DYNASTY_CONFLICT_VALIDATION.md`, `V018_SAMSARA_DYNASTY_FOUR_WORLD_VALIDATION.md`,
  `V018_UPPER_SAMSARA_LINEAGE_VALIDATION.md` — the samsara dynasty/lineage system.
- `V018_DYNASTY_RISK_RESOLUTION_VALIDATION.md` — dynasty quest/conflict roll-based resolution
  (independently reviewed and fixed this session — see the conversation record for the
  rounds-bias balance fix; the roll mechanics themselves checked out sound).
- `V018_DASHBOARD_IMPLEMENTATION_GATE_VALIDATION.md`, `V018_DASHBOARD_SYSTEM_COVERAGE_VALIDATION.md` —
  the `dashboard_contract.py` coverage gate. **Independently re-verified this session** by
  actually running `dashboard_implementation_issues()` (pure-stdlib Python, runs directly),
  repeatedly, after each dashboard change — this one's claims are the most solidly confirmed
  of anything in this file, because it's the one part of the stack this environment could
  actually execute rather than only read.
- `V018_LIFESPAN_PAUSE_FIX.md` — superseded by the actual implementation now in
  `lifespan.go`/`authoritative.go` (activity-pause clock plus real old-age death enforcement
  routed through `recordTrueDeathAuthoritative`), which goes further than this doc's original
  scope (it only covered the pause display, not death enforcement).
- `V018_MORTAL_CAPITAL_DISCOVERY_IMAGE_VALIDATION.md` — confirmed real: both the packaged
  asset (`assets/locations/azure_crown_imperial_city.png`) and the matching code in
  `app/bot/main.py` (`LOCATION_DISCOVERY_IMAGES`) exist as described.

## What this session added on top of the above (not covered by any prior doc)

- Fixed the forage rare-find bug (result reported a rare item on a failed roll).
- Implemented player old-age death enforcement (was previously display-only), routed through
  the existing true-death/samsara pipeline.
- Renamed GM→Admin dashboard terminology throughout.
- Fixed the dynasty conflict system's rounds-based balance bias (opponent-only escalation).
- Added the `admin.player.fate` GM grant path (`fate.adjust` had zero callers anywhere).
- Closed five dashboard coverage gaps: birth-family household threads/current occupants, and
  new Party & Formations / PvP / Conditions views.
- Wired `npc.lifespan` into the existing `/admin npc npcinspect` command (was an orphaned query).
- Ran a targeted RNG audit (found and fixed the dynasty "danger" display gap; found no other
  instance of a displayed-but-unconsumed risk value in `go_core/internal/game`), an IDOR
  sample audit (caravan/dynasty/pvp/party all clean via consistent safe patterns), and an
  exhaustive canonical-time audit (all 9 time-exempt operations correctly self-resolve).
