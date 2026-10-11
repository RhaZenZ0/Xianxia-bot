"""A challenge's opponent is the world's to name, not the caller's.

`combat.start kind=challenge` took the opponent's realm and stage off the
payload, with a comment saying the caller had already resolved a real NPC, and
stored the payload's `source` and `target_key` as sent. The bot did resolve one
- `battle.py` asked `SIM.combat_target` and forwarded what it found - so
nothing a player does could forge it, which is exactly why it lasted: a bound
that lives in the client is not a bound (rc.48), and this one was worth a
stage-lead on every roll of the fight, the severity of the kill, and the region
and sects the kill marks. The engine also never asked whether the named person
was here, alive or a real hidden master.

The engine finds the person now (`resolveChallengeTargetTx`) and the payload's
realm and stage are accepted and ignored, so a bot mid-upgrade is not refused.
The behaviour is held in Go (`combat_challenge_target_test.go`), where it can
be driven; this is the Python half, and what it can honestly check is that
nothing on this side states any of it again.
"""
from __future__ import annotations

import ast
import asyncio
import unittest

from tests.support import PROJECT_ROOT

APP = PROJECT_ROOT / "app"
GO = PROJECT_ROOT / "go_core" / "internal" / "game"
SERVICES = APP / "ops" / "core_services.py"
BATTLE = APP / "bot" / "commands" / "battle.py"
WORLD = APP / "simulation" / "world.py"
PLAYTEST = PROJECT_ROOT / "scripts" / "playtest_engine.py"

# What the world says about a challenge's opponent, and what follows from whom
# it is. A caller names the person and nothing else.
WORLDS_TO_SAY = ("npc_realm_index", "npc_stage")
DERIVED_FROM_THE_OPPONENT = ("source", "target_key")


def _parse(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def _function(path, name, *, cls=None):
    tree = _parse(path)
    scope = tree
    if cls is not None:
        scope = next((n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == cls), None)
        assert scope is not None, f"{cls} is gone from {path.name}; this gate is reading the wrong file"
    found = next((n for n in ast.walk(scope) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name), None)
    assert found is not None, f"{name} is gone from {path.name}; this gate is reading the wrong file"
    return found


def _start_calls(path):
    """Every `<...>COMBAT.start(...)` call in a file, as (line, keyword names, kind)."""
    out = []
    for node in ast.walk(_parse(path)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "start"):
            continue
        owner = node.func.value
        if not ((isinstance(owner, ast.Name) and owner.id == "COMBAT") or (isinstance(owner, ast.Attribute) and owner.attr == "COMBAT")):
            continue
        keywords = {k.arg: k.value for k in node.keywords if k.arg}
        kind = keywords.get("kind")
        out.append((node.lineno, set(keywords), kind.value if isinstance(kind, ast.Constant) else None))
    return out


class TheServiceStatesNothingAboutTheOpponent(unittest.TestCase):
    def test_start_takes_no_realm_stage_or_challenge_source(self):
        start = _function(SERVICES, "start", cls="CombatService")
        params = [a.arg for a in start.args.args + start.args.kwonlyargs]
        # A reader is asserted before it is trusted (rc.57): an empty parameter
        # list would make every assertion below vacuous.
        self.assertIn("npc_name", params, "the reader did not find CombatService.start's parameters; the gate is broken, not the tree")
        for name in WORLDS_TO_SAY:
            self.assertNotIn(name, params, f"CombatService.start takes {name} again; the engine reads the opponent's realm and stage off the world")
        defaults = dict(zip((a.arg for a in start.args.kwonlyargs), start.args.kw_defaults))
        source = defaults.get("source")
        self.assertTrue(
            isinstance(source, ast.Constant) and source.value == "",
            "`source` must default to empty: a challenge states none, and only an event names its own",
        )

    def test_start_sends_no_realm_or_stage(self):
        start = _function(SERVICES, "start", cls="CombatService")
        keys = {k.value for d in ast.walk(start) if isinstance(d, ast.Dict) for k in d.keys if isinstance(k, ast.Constant)}
        self.assertIn("npc_name", keys, "the reader did not find the payload; the gate is broken, not the tree")
        for name in WORLDS_TO_SAY:
            self.assertNotIn(name, keys, f"CombatService.start sends {name}; the engine ignores it and a rolling deploy is the only reason to")

    def test_a_challenge_driven_through_the_service_sends_none(self):
        """Drive it, because a source read cannot see a key built from a loop."""
        from app.ops.core_services import CombatService

        class Engine:
            def __init__(self):
                self.sent = []

            async def authoritative_action(self, operation, user_id, payload, *, action_id):
                self.sent.append((operation, dict(payload)))
                return {"result": {}}

        engine = Engine()
        service = CombatService(None, engine=engine)
        asyncio.run(service.start(7, kind="challenge", npc_name="Elder Feng", action_id="a"))
        (operation, payload), = engine.sent
        self.assertEqual(operation, "combat.start")
        self.assertEqual(payload["npc_name"], "Elder Feng")
        for name in WORLDS_TO_SAY:
            self.assertNotIn(name, payload, f"a challenge sent {name}")
        for name in DERIVED_FROM_THE_OPPONENT:
            self.assertEqual(payload.get(name, ""), "", f"a challenge sent a {name}; the engine derives it from whom it names")
        with self.assertRaises(TypeError, msg="the realm parameter is back on the service"):
            asyncio.run(service.start(7, kind="challenge", npc_name="x", action_id="b", npc_realm_index=9))


class NothingInTheTreeStatesIt(unittest.TestCase):
    def test_no_combat_start_call_passes_a_realm_or_stage(self):
        offenders, calls = [], 0
        for path in sorted(APP.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for line, keywords, _ in _start_calls(path):
                calls += 1
                for name in WORLDS_TO_SAY:
                    if name in keywords:
                        offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{line} passes {name}")
        # The challenge and the event scene: if the walk found fewer, it is
        # looking at the wrong thing (rc.57).
        self.assertGreaterEqual(calls, 2, "the walk found fewer COMBAT.start calls than the tree has; the gate is broken, not the tree")
        self.assertEqual(offenders, [], "a caller states the opponent's realm or stage again: " + "; ".join(offenders))

    def test_a_challenge_call_passes_no_source_or_lock(self):
        offenders, challenges = [], 0
        for path in sorted(APP.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for line, keywords, kind in _start_calls(path):
                if kind != "challenge":
                    continue
                challenges += 1
                for name in DERIVED_FROM_THE_OPPONENT:
                    if name in keywords:
                        offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{line} passes {name}")
        self.assertGreaterEqual(challenges, 1, "the walk found no challenge call; the gate is broken, not the tree")
        self.assertEqual(offenders, [], "a challenge states what the engine derives from whom it names: " + "; ".join(offenders))

    def test_the_command_does_not_resolve_the_opponent_itself(self):
        """`SIM.combat_target` was the bot's own copy of "who is here".

        The picker still asks `combat.targets` (one rule, listed capped); the
        press asks the engine, which applies the same rule uncapped. A second
        lookup before the press is a round trip, a fifty-row cap on who can be
        challenged, and a place for the two to part company.
        """
        command = _function(BATTLE, "battle_challenge")
        called = {n.func.attr for n in ast.walk(command) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        self.assertIn("start", called, "the reader did not find the challenge's start call; the gate is broken, not the tree")
        self.assertNotIn("combat_target", called, "battle_challenge resolves the opponent itself again")
        self.assertNotIn("combat_targets", called, "battle_challenge reads the picker's list to decide who may be challenged")
        world = _parse(WORLD)
        methods = {n.name for n in ast.walk(world) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertIn("combat_targets", methods, "the picker's list is gone; the gate is reading the wrong file")
        self.assertNotIn("combat_target", methods, "WorldSimulator.combat_target is back; nothing in production calls it")

    def test_the_refusal_is_the_engines_sentence(self):
        """One sentence for absent, dead, missing and hidden, printed as it is.

        A bot that wrote its own would be the second sentence, and the engine
        holds that there is only one so the refusal cannot become a hidden-power
        detector. The handler sends it through `_explain_engine_error` like
        every other engine refusal.
        """
        command = _function(BATTLE, "battle_challenge")
        handlers = [h for t in ast.walk(command) if isinstance(t, ast.Try) for h in t.handlers]
        self.assertTrue(handlers, "the reader did not find the challenge's except clause; the gate is broken, not the tree")
        calls = {n.func.id for h in handlers for n in ast.walk(h) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertIn("_explain_engine_error", calls)
        strings = [n.value for n in ast.walk(command) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        self.assertFalse([s for s in strings if "not mechanically present" in s], "the bot words its own absence refusal again")

    def test_the_engine_playtest_states_none_of_it(self):
        offenders, calls = [], 0
        for node in ast.walk(_parse(PLAYTEST)):
            if not (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "combat.start"):
                continue
            payload = next((a for a in node.args if isinstance(a, ast.Dict)), None)
            if payload is None:
                continue
            keys = {k.value for k in payload.keys if isinstance(k, ast.Constant)}
            kind = next((v.value for k, v in zip(payload.keys, payload.values) if isinstance(k, ast.Constant) and k.value == "kind" and isinstance(v, ast.Constant)), None)
            if kind != "challenge":
                continue
            calls += 1
            for name in WORLDS_TO_SAY + DERIVED_FROM_THE_OPPONENT:
                if name in keys:
                    offenders.append(f"scripts/playtest_engine.py:{node.lineno} sends {name}")
        self.assertGreaterEqual(calls, 4, "the walk found fewer challenge calls than the harness makes; the gate is broken, not the tree")
        self.assertEqual(offenders, [], "the harness states what the engine derives, so it proves nothing about the engine deriving it: " + "; ".join(offenders))


class TheEngineHalfStillExists(unittest.TestCase):
    def test_the_challenge_is_resolved_in_go_and_the_fields_stay_on_the_wire(self):
        combat = (GO / "combat_actions.go").read_text(encoding="utf-8")
        self.assertIn("resolveChallengeTargetTx(conn, catalog, c.Location", combat,
                      "combat.start no longer finds a challenge's opponent among those standing where the caller stands")
        self.assertIn("challengeTargetAbsent", combat)
        self.assertIn('json:"npc_realm_index"', combat,
                      "the wire field is gone; a bot mid-upgrade that still sends it is now refused")
        self.assertIn("deliberately ignored", combat, "the field is there without the comment that says why")
        queries = (GO / "world_status_queries.go").read_text(encoding="utf-8")
        self.assertIn("func combatTargetRows(", queries, "the one rule for who may be challenged is gone")
        self.assertTrue((GO / "combat_challenge_target_test.go").exists(), "the Go half of this rule is gone")

    def test_the_fields_are_ignored_not_refused(self):
        """A refusal would turn a rolling deploy into an outage (rc.48)."""
        authoritative = (GO / "authoritative.go").read_text(encoding="utf-8")
        start = authoritative.index("var callerOwnedNothing")
        refused = authoritative[start:authoritative.index("\n}", start)]
        self.assertIn('"game_minute"', refused, "the reader did not find callerOwnedNothing; the gate is broken, not the tree")
        for name in WORLDS_TO_SAY:
            self.assertNotIn(f'"{name}"', refused, f"{name} is refused now; an older bot's challenge fails during an upgrade")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
