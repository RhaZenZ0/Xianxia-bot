# v0.18 — Final Release

The staged v0.18 authority roadmap is complete and this tree is promoted to the final v0.18 release.

## Completed authority stages

- Stage 1: canonical forage inputs.
- Stage 2: forage effect authority.
- Stage 3: `craft.resolve` recipe-only authority.
- Stage 4/4.5: companion/artifact cooldown, context, and adversarial hardening.
- Stage 5: canonical Go-owned game-time authority.
- Stage 6: unified player/NPC lifespan authority plus multi-hop roads and caravans.
- Stage 7: Discord channel/category setup moved to the admin dashboard; bot auto-provisioning removed.
- Stage 8: obsolete Python authority code removed after Go migration.

## Release hardening

The final release gate additionally:
- defaults standalone Go-engine binding to `127.0.0.1:8081`;
- keeps Docker's Go engine on the private Compose network via explicit `ENGINE_ADDR=:8081`;
- enforces bounded JSON request bodies on all Go JSON mutation/data endpoints;
- rejects trailing extra JSON values instead of accepting an ambiguous request body.

## Validation

See:
- `V018_RELEASE_VALIDATION.md`
- `V018_RELEASE_SECURITY_LOAD_AUDIT.md`
- `V018_RELEASE_NOTES.md`

Release version: 0.18.
Schema version: 22.
