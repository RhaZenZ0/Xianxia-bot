# v0.18 Stage 5 Validation

Validation run after canonical game-time migration and direct-road duration/danger/encounter
integration.

## Go

```text
go test ./...
PASS

go vet ./...
PASS
```

Targeted Stage 5 tests also pass:

```text
go test ./internal/game -run TestStage5 -count=1
PASS
```

## Python

```text
python -m pytest -q
241 passed, 78 subtests passed
```

The Python process emits a non-test artifact-tool spreadsheet warmup timeout in this environment;
pytest itself completes successfully with the counts above.

## Stage 5 authority regressions

- every registered authoritative mutation rejects caller `game_minute`;
- every registered authoritative query rejects caller `game_minute`;
- forged time is rejected before replay receipt lookup;
- canonical world time is injected internally only for legacy Go handlers;
- authoritative domain events use the canonical action minute;
- Python authoritative action payloads contain no direct caller `game_minute`;
- the Python engine client rejects authoritative payloads containing `game_minute`;
- simulation endpoints retain explicit scheduler time;
- legacy `quest.progress` rejects caller time and derives canonical Go time.

## Road regressions

- direct road travel derives positive duration from canonical route context;
- canonical departure and arrival minutes are returned;
- danger is bounded and is the server-owned encounter probability;
- no-encounter travel leaves Vitality unchanged;
- triggered spirit-beast encounter adds delay and applies nonlethal damage;
- road encounters persist to `event_log`;
- direct-road transit creates a per-character canonical arrival lock;
- authoritative mutations fail before arrival and resume at arrival;
- expired transit state is removed automatically.

Schema version remains 22.
