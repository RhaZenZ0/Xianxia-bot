# v0.18 Stages 6–8 Final Validation

Final regression run after the Stage 6–8 implementation and adversarial fixes.

## Go

```text
go test ./...
PASS

go vet ./...
PASS
```

Targeted authority tests also pass:

```text
go test ./internal/game -run 'TestStage6|TestCaravan|TestStage5' -count=1
PASS

go test ./internal/lifespan ./internal/simulation -count=1
PASS
```

## Python

```text
python -m pytest -q
239 passed, 78 subtests passed
```

Focused authority/setup/dead-code contracts:

```text
python -m pytest   tests/python/contracts/test_admin_server_setup_tool.py   tests/python/unit/test_command_cleanup.py   tests/python/contracts/test_authority_boundary.py -q
24 passed
```

The unrelated `artifact_tool` spreadsheet-runtime warmup prints an environment warning during Python
startup in this execution environment; pytest exits 0 and the repository test suite passes.

## Static cleanup gates

No matches remain for:
- `app.lifespan`, `from .lifespan`, or `realm_lifespan`;
- `app.npc_life` / `from .npc_life`;
- `guild.create_text_channel` / `guild.create_category` in `app`;
- `.set_permissions(` in `app`;
- `auto-provision` or the obsolete `Create / repair` channel setup wording in the bot.

The deleted files `app/lifespan.py` and `app/npc_life.py` remain absent.

## Stage 6 coverage

- shared Go lifespan model for natural lifespan, realm/stage extension, bootstrap age, and old age;
- player/NPC lifespan consumers share that model;
- NPC bootstrap and natural death share one authority;
- canonical multi-hop road pathfinding;
- canonical per-leg duration, danger, encounters, costs, discovery, and transit;
- canonical caravan route, ETA, risk, operating cost, bounded dispatch inputs, and living-actor gate.

## Stage 7 coverage

- Discord guild channel/category creation removed from the bot;
- setup validates/binds admin-dashboard-owned channels;
- startup realm-hub auto-provisioning removed;
- channel permission-overwrite repair removed;
- private gameplay threads remain supported.

## Stage 8 coverage

- obsolete Python lifespan authority modules removed;
- live lifespan consumers migrated to Go;
- test-support fallback to the removed Python lifespan model removed;
- existing Python authority-boundary contract remains green.

## Compatibility

- Schema version: 22.
- Runtime `VERSION`: 0.17.
- This remains a cumulative v0.18 development checkpoint until explicit release promotion.
