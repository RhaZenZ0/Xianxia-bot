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

See `V019_RELEASE_NOTES.md` for the current release and `V018_BUILD_HISTORY.md`
for the consolidated prior build/validation record.

Release version: 0.19.
Schema version: 24 (unchanged from v0.18).

## Samsara dynasty extension

The v0.18 patch line now includes persistent cross-incarnation ancestry, ancestral investigation
sites/quests, evidence-gated inheritance/restoration/revenge claims, and replacement-house legacy
conflicts. Replacement families remain blood-unrelated when the historical branch is extinct.

## v0.19 — Stage 18 consistency/coverage pass, folded into this release

On top of the v0.18 release above, v0.19 (see `V019_RELEASE_NOTES.md`) adds: real old-age
death enforcement (previously display-only), a fixed dynasty-quest RNG gap and a
dynasty-conflict rounds-based balance fix, a GM fate-grant path (`admin.player.fate`), five
closed dashboard coverage gaps (household threads/occupants, Party & Formations, PvP,
Conditions), the `npc.lifespan` query wired into Discord's NPC inspect command, a
package-wide RNG/IDOR/canonical-time audit, and a full cultivation-depth audit (realm
breakthroughs, tribulations, aptitude, techniques) that found and fixed five further bugs.
