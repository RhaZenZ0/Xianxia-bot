# v0.18 Staged Authority Cleanup Roadmap

## Stage 1 — Forage canonical inputs — BUILT

Go owns canonical forage context.

## Stage 2 — Forage effect authority — BUILT + AUDITED

`forage.resolve` derives persisted aptitude/effect/toxicity context in Go.

## Stage 3 — Craft authority — BUILT + ADVERSARIALLY AUDITED

`craft.resolve` accepts only `recipe`; Go owns context, RNG, inventory, quality, and progression.

## Stage 4 / 4.5 — Companion authority — BUILT + ADVERSARIALLY AUDITED

All companion/artifact mutations use canonical actor/context/cooldowns, strict payload allowlists,
living-actor checks, replay protection, and Go-owned mutation.

## Stage 5 — Canonical game-time authority — BUILT + VALIDATED

Authoritative gameplay mutations and queries reject caller `game_minute`. Go derives canonical
current time before dispatch/replay; scheduler/import simulation time remains explicit protocol data.

## Stage 6 — Unified NPC lifespan authority — BUILT + AUDITED

One Go lifespan package now owns:
- natural lifespan input;
- realm/stage lifespan extension;
- effective lifespan ceilings;
- starting/bootstrap age bounds;
- old-age expiry/death checks.

Player lifespan projection, NPC lifespan query, NPC bootstrap, and NPC natural-death simulation use
the shared model. Richer high-realm bootstrap ages are therefore bounded by the same model that later
evaluates natural death.

## Stage 6 — Multi-hop roads, costs, and caravans — BUILT + AUDITED

Canonical road routing uses server-owned pathfinding and per-leg travel profiles. Go derives:
- route nodes and hops;
- travel duration;
- danger and encounter resolution;
- spirit-stone route cost;
- discovery of traversed locations;
- canonical departure/arrival and transit enforcement.

Caravan dispatch uses the canonical road graph for route, ETA, operating cost, and risk. Dispatch
quantity/escort bounds are enforced in Go, caller duration is rejected, and only living incarnations
may dispatch.

## Stage 7 — Admin-dashboard-owned Discord setup — BUILT + AUDITED

The bot no longer creates guild text channels/categories or repairs channel permission overwrites.
Configured channels are validated/bound from dashboard-owned setup. Startup no longer runs realm-hub
channel provisioning. Private gameplay threads inside configured channels remain supported.

## Stage 8 — Remove obsolete Python authority code — BUILT + AUDITED

Deleted Python lifespan authority modules and migrated live lifespan callers to Go. Contract/static
scans ensure removed lifespan helpers are not retained by production or test support and existing
authority-boundary tests verify migrated gameplay mutators are absent from Python.

## Migration rule used for each stage

1. strengthen the Go authority operation;
2. derive canonical validation/context in Go;
3. migrate Python callers;
4. add forged-input/identity/state negative tests;
5. remove Python helpers only after repository scans prove them dead;
6. run Go test/vet plus the complete Python suite.
