"""Every environment key the engine reads is one compose gives it (v1.2.1).

The engine's compose service takes an explicit `environment:` allowlist and no
`env_file`, so a key it is not given is a key it cannot read - the rc.39
finding that made WORLD_TIME_SCALE dead on arrival, rc.56's for the cooldowns,
and v1.2.1's for ENGINE_SHUTDOWN_GRACE_SECONDS, which was documented, in
.env.example, read by shutdown.go and passed by nothing. This gate reads the
keys off the Go source rather than off a list, so the next one fails the day it
is read.
"""

from __future__ import annotations

import re
import unittest

from tests.support import PROJECT_ROOT

# A key the engine reads only as a fallback for another it is given.
LEGACY_ALIASES = {"CORE_ADDR": "the pre-0.20 spelling of ENGINE_ADDR, read only when that is unset"}


def engine_env_keys() -> set[str]:
    keys: set[str] = set()
    for path in (PROJECT_ROOT / "go_core").rglob("*.go"):
        if path.name.endswith("_test.go"):
            continue
        keys.update(re.findall(r'os\.Getenv\("([A-Z_]+)"\)', path.read_text(encoding="utf-8")))
    return keys


class EveryEngineKeyReachesTheEngine(unittest.TestCase):
    def test_compose_passes_every_key_the_engine_reads(self):
        keys = engine_env_keys()
        self.assertIn("ENGINE_AUTH_TOKEN", keys, "the source walk found no engine keys; the reader is broken, not the tree")
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        engine = compose.split("\n  xianxia-engine:", 1)[1].split("\n  xianxia-", 1)[0]
        passed = set(re.findall(r"^\s+([A-Z_]+):", engine, flags=re.M))
        missing = sorted(k for k in keys - passed if k not in LEGACY_ALIASES)
        self.assertEqual(missing, [], "the engine reads these keys and compose never passes them, so a value set in .env reaches nothing")

    def test_an_alias_is_only_ever_a_fallback(self):
        main = (PROJECT_ROOT / "go_core" / "cmd" / "xianxia-core" / "main.go").read_text(encoding="utf-8")
        for key in LEGACY_ALIASES:
            self.assertIn(f'os.Getenv("{key}")', main, f"{key} is allowlisted as an alias and read nowhere; drop the entry")


if __name__ == "__main__":
    unittest.main()
