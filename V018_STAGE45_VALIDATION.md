# v0.18 Stage 4.5 Validation

Validation run after Stage 4.5 hardening and family-city road integration.

## Go

```text
go test ./...
PASS

go vet ./...
PASS
```

Targeted Stage 4/4.5 authority/family tests also pass.

## Python

```text
python -m pytest -q
239 passed, 78 subtests passed
```

The authority-boundary contract test file passes independently:

```text
python -m pytest tests/python/contracts/test_authority_boundary.py -q
4 passed
```

## Covered regressions

- forged/unknown payload fields rejected for all seven companion/artifact mutations;
- dead actors rejected for all companion/artifact mutations;
- canonical tame encounter location/time/expiry;
- server-owned tame/feed/train/artifact-bond cooldowns;
- canonical Beast Pen training context and access;
- canonical companion result time/location;
- one-way artifact awakening;
- Python wild-beast encounter reader is read-only;
- strict Stage 3 `craft.resolve` recipe-only contract;
- all 11 family archetypes use distinct standard public family cities in all four worlds;
- family-city roads are bidirectional, same-world and connected;
- direct road neighbor travel persists discovery;
- realm-hub unlock cannot be lowered by realm-0-safe family cities.

Schema version remains 22.
