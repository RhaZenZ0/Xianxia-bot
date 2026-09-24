"""The wait between two handouts of the household's support is the engine's
(v1.2.1).

`/family -> Support` sent `cooldown_game_minutes` and the engine used it, with
a floor only at zero - so any caller could send 1 and draw the stipend every
minute. That is rc.48's rule (a bound that lives in the client is not a bound)
and rc.56's (the waits are the engine's), found in a fifteenth place. The wire
field is still accepted, and ignored, so an older bot mid-upgrade is not
refused.
"""

from __future__ import annotations

import ast
import unittest

from tests.support import PROJECT_ROOT, bot_function_source

GO = PROJECT_ROOT / "go_core" / "internal" / "game" / "family_dao_actions.go"


class TheFamilyWaitIsTheEngines(unittest.TestCase):
    def test_the_bot_sends_no_wait(self):
        tree = ast.parse(bot_function_source("birth_family_support"))
        sent = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "authoritative_action":
                if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "family.support":
                    payload = node.args[2] if len(node.args) > 2 else None
                    if isinstance(payload, ast.Dict):
                        sent.extend(str(getattr(k, "value", "")) for k in payload.keys)
        self.assertNotIn("cooldown_game_minutes", sent, "the bot sends the wait it is not allowed to decide")

    def test_the_engine_ignores_a_wait_the_caller_sends(self):
        source = GO.read_text(encoding="utf-8")
        self.assertIn("p.CooldownGameMinutes = familySupportCooldownGameMinutes", source,
                      "familySupportActionGo no longer overrides the caller's wait with the engine's")
        self.assertNotIn("if p.CooldownGameMinutes <= 0", source,
                         "the payload decides the wait again whenever it is positive")


if __name__ == "__main__":
    unittest.main()
