# v0.18 Final Release Validation

Release candidate promoted from the audited Stage 6–8 checkpoint and revalidated after final
release/security hardening.

## Release metadata

```text
VERSION: 0.18
Python RELEASE_VERSION: 0.18
Docker image label: 0.18
Docker Compose release labels: 0.18
Schema: 22
Release date: 2026-08-30
```

The active README, release manifest, bot UI, Docker metadata, and runtime version no longer contain
v0.17 release identifiers. Historical v0.17 documents are intentionally retained as history.

## Go

```text
go test ./...
PASS

go vet ./...
PASS

go test -race ./...
PASS

go test ./internal/game -run 'TestStage5|TestStage6|TestCaravan' -count=25
PASS
```

The final release adds HTTP-boundary regression coverage for:
- trailing extra JSON values;
- oversized simulation mutation bodies;
- oversized database-maintenance bodies.

## Python

```text
python -m pytest -q
239 passed, 78 subtests passed

python -m pytest   tests/python/contracts/test_release_version.py   tests/python/contracts/test_startup_health.py   tests/python/integration/test_dashboard.py   tests/python/contracts/test_authority_boundary.py   tests/python/contracts/test_admin_server_setup_tool.py -q
30 passed

python -m compileall -q app
PASS
```

The unrelated `artifact_tool` spreadsheet warmup in the execution environment emits a startup warning;
pytest/compileall exit successfully and repository validation is unaffected.

## Packaging / shell

```text
sh -n startup.sh stop.sh update.sh
PASS
```

Static source scans return no active-source matches for:
- Python `eval` / `exec`;
- `pickle`;
- `os.system`;
- `subprocess(..., shell=True)`;
- bot `create_text_channel`, `create_category`, or `set_permissions`;
- deleted Python lifespan authority references.

Generated Python bytecode/test caches are removed before packaging.

## Compatibility

- Schema version: 22.
- Schema 21 -> 22 migration remains automatic.
- Existing players are not teleported by family-homeland canonicalization.
- Standalone Go engine now defaults to loopback; Docker explicitly binds the engine inside the
  private Compose network with `ENGINE_ADDR=:8081`.
