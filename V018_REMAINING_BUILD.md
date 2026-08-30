# v0.18 — Remaining Build Status

## Required staged authority work

Complete. Stages 1 through 8 are included in the final v0.18 release.

## Optional future mechanics

### Road and caravan depth

v0.18 includes canonical multi-hop pathfinding, server-derived travel duration/danger/costs,
route discovery, transit enforcement, and canonical caravan route/ETA/operating cost.

Possible future additions:
- supply consumption in addition to spirit-stone operating costs;
- richer caravan-specific road events and escort interactions;
- player-facing route-choice UI instead of canonical shortest-time routing;
- deeper climate/weather modifiers;
- persistent caravan crews, contracts, and market simulation.

### Explicit artifact-capable item classes

v0.18 permits bonding any carried canonical item. A future design may add catalog metadata such as
`artifact_capable` / artifact type and validate it in `artifact.bond`.

## Release state

Promoted to final v0.18 on 2026-08-30.
Schema version: 22.
