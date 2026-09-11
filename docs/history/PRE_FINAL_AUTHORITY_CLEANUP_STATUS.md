# Pre-Final Authority Cleanup Status

Completed after Partials 06–11:

- Discord mutation handlers for auction/black market/public market/bounty hunters, equipment/party/formations/bosses, territory/war/caravans, sect recruitment/economy/discipleship/manor, family support/children/simulation, seclusion, Dao partnership, storage/properties/arrays/spatial keys/personal worlds now delegate to authoritative Go operations.
- Removed the Python auction-door gameplay RNG helpers and black-market/sect recruitment Python roll paths from the Discord layer. These rolls now occur in Go.
- Removed obsolete Python `Database` mutation methods for the migrated domains, including their bounty-hunter and birth-family simulation fallbacks.
- Scheduled advanced maintenance remains Go-owned.
- Corrected sect-manor defensive war power to `defense_array_level * 6` plus the existing Qi-array contribution.
- Scheduled Go seclusion settlement now loads canonical Qi/body phase costs from `content/world.json` rather than using the temporary approximation.

Validation performed:

- `python -m py_compile app/bot/main.py app/database/core.py`
- `pytest -q tests/python/contracts/test_authority_boundary.py` — pass
- `cd go_core && go test ./...` — pass

Note: older Python integration tests that directly invoke the deliberately removed `Database` gameplay mutators are now stale by design; authoritative mechanics are exercised through the Go operation layer going forward.
