"""Hub surface: daily roots, hub edits, hidden doors, hint paths, cooldown and refusal wording, and the typed-play budget.

Merged from:

test_a_daily_root_counts_as_its_leaf.py — A daily root is counted as the leaf it is (v1.12.3).

`/forage` is a root whose body is one call to `/alchemy forage`'s handler
(v1.3.2), so the two doors are one action. The command tree recorded the root
as "/forage" and a hub press recorded the leaf as "/alchemy forage" - one
action counted as two commands, against `usage.py`'s promise of one path per
action. The other four roots share their leaf's path and were always right.

Driven through the tree's own `interaction_check` and through a hub press's
recorder with a counting fake, because the source reads correctly in both the
split and the joined version: the path is decided one call down.

test_a_hub_edit_takes_only_what_an_edit_accepts.py — A hub turns a send into an edit, and an edit takes fewer keywords (v1.14.1).

A leaf answered from a picker is answered by editing the picker's message: the
hub proxy turns the handler's `followup.send(...)` into
`edit_original_response(...)`. A send takes keywords an edit refuses, and
`_safe_edit_kwargs` stripped two of them. The raid card is sent with
`wait=True`, so `/boss start` pressed from the hub raised
`TypeError: edit_original_response() got an unexpected keyword argument 'wait'`
- after the engine had already started the raid. The first parallel playtest
found it, because one part's player happened to be standing at a lair when the
sweep pressed Start; a whole run never had been.

Held against discord.py's own signatures, so the next send-only keyword cannot
reach an edit either.

test_hidden_actions.py — Doors the panel leaves off (v1.0.0-rc.32).

A hub page is a static list, and until now every leaf on it was drawn for
every player: `/family → Enter` from the far side of the world, `/law →
Comprehend` at Qi Condensation, a sect's treasury to somebody in no sect. The
panel now asks one async provider (`register_hidden_actions` in hubs.py) for
the paths to leave off for this player, whenever it refreshes its status.

Two rules are held here. Every name a provider can hide is a real leaf - a
row on the playtest checklist, which `test_playtest_gate` holds to the live
surface - so a renamed command cannot leave a stale hide behind. And no gate
hides a status read or the door into its own system: a road nobody can see is
a road nobody learns exists, so only what the engine would refuse outright is
hidden.

test_hint_path_regex.py — The hint-path pattern must stay linear.

`_HINT_PATH_RE` scans every reply the bot sends for hub paths it can turn
into buttons (`**/world → City → Look**`). Its first form paired `\\s*` with a
character class that also matches whitespace, so a run of N spaces could be
split N ways and the pattern went quadratic on text that never completes a
match — and the text it scans is player-reachable, so a single message could
stall the event loop. CodeQL called it an inefficient regular expression; it
took 29 seconds on three thousand spaces.

test_cooldown_wording.py — Engine cooldown errors reach the player as a wait, not raw seconds (v0.21.5).

Reported: "cultivation cooldown remaining: 10520". That is the engine's exact
truth (cooldowns are wall-clock seconds set from *_COOLDOWN_MINUTES) and no
help at all. runtime._explain_engine_error rewrites the shape every engine
cooldown error shares - "<what> cooldown remaining: <seconds>" - and every
`❌ {exc}` reply in the command modules goes through it.

runtime.py imports discord, so the two pure pieces are exec'd out of its
source here rather than importing the module.

test_every_refusal_is_explained.py — An engine refusal reaches a player through `_explain_engine_error` (v1.27.0).

The explainer turns a raw wait into hours, and since v1.27.0 it says where a
refusal points - "travel there first" names the travel menu, "not inside a
shop" names the city's shops - so a panel can draw the place as a button.
Forty-eight command handlers sent `str(exc)` past it, so a beast refused for
loyalty, a duel, a secret realm and every sense sweep printed the engine's
bare words. This holds every `except GameEngineError` under the player-facing
commands to pass its message through the explainer, or to be named below with
the reason it reads the error rather than printing it.

test_user_budget.py — Per-user token bucket for typed play (v0.21.1). Exercised behaviourally.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
import os
import re
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.ops.user_budget import UserBudget
from tests.support import PROJECT_ROOT, bot_source_files

pytestmark = pytest.mark.unit

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


# --- from test_a_daily_root_counts_as_its_leaf.py ---

def _modules():
    with patch.dict(os.environ, ENV):
        surface = importlib.import_module("app.bot.surface")
        bot = importlib.import_module("app.bot.bot")
        usage = importlib.import_module("app.bot.usage")
        hubs = importlib.import_module("app.bot.hubs")
    return surface, bot, usage, hubs


class _Counting:
    def __init__(self):
        self.paths: list[str] = []

    async def record_command_use(self, path):
        self.paths.append(path)


async def _slash(bot, name, db):
    import discord

    async def none(*_a, **_k):
        return None

    interaction = SimpleNamespace(
        command=SimpleNamespace(qualified_name=name),
        user=SimpleNamespace(id=1),
        type=discord.InteractionType.application_command,
    )
    with patch.object(bot, "DB", db), patch.object(bot.maintenance, "refuse", none), patch.object(bot.seclusion, "refuse", none):
        allowed = await bot.GatedCommandTree.interaction_check(SimpleNamespace(), interaction)
    await asyncio.sleep(0.01)  # the counter is fired, not awaited
    return allowed


class AForageIsOneCommandAtEveryDoor(unittest.TestCase):
    def test_the_slash_command_and_the_hub_press_count_one_path(self):
        surface, bot, usage, hubs = _modules()
        db = _Counting()

        async def scenario():
            self.assertTrue(await _slash(bot, "forage", db))
            with patch.object(bot, "DB", db):
                usage.note(db, "/alchemy forage")  # what a hub press records
                await asyncio.sleep(0.01)

        asyncio.run(scenario())
        self.assertEqual(db.paths, ["/alchemy forage", "/alchemy forage"],
                         "the same action was counted under two names: %s" % db.paths)

    def test_each_daily_root_counts_as_its_hub_leaf(self):
        surface, bot, usage, hubs = _modules()
        for root, _hub, leaf in surface._DAILY_LEAVES:
            with self.subTest(root=root):
                db = _Counting()
                asyncio.run(_slash(bot, root, db))
                self.assertEqual(db.paths, [leaf])

    def test_a_path_that_is_no_alias_is_left_alone(self):
        _surface, _bot, usage, _hubs = _modules()
        self.assertEqual(usage.canonical("/sheet"), "/sheet")
        self.assertEqual(usage.canonical("  /sheet "), "/sheet")


# --- from test_a_hub_edit_takes_only_what_an_edit_accepts.py ---

def keywords(function) -> set[str]:
    return {name for name, p in inspect.signature(function).parameters.items()
            if name != "self" and p.kind in (p.KEYWORD_ONLY, p.POSITIONAL_OR_KEYWORD)}


class AHubEditTakesOnlyWhatAnEditAccepts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, ENV):
            import discord

            from app.bot import hubs
        cls.discord = discord
        cls.hubs = hubs

    def test_every_send_keyword_is_either_kept_or_dropped(self):
        discord = self.discord
        sent = keywords(discord.Webhook.send) | keywords(discord.InteractionResponse.send_message)
        self.assertIn("wait", sent, "discord.py's send no longer names wait; the reader is broken, not the tree")
        accepted = keywords(discord.Interaction.edit_original_response)
        given = {name: None for name in sent}
        given["content"] = "a result"
        clean = self.hubs._safe_edit_kwargs(given, fallback_view=None)
        self.assertEqual(set(clean) - accepted, set(),
                         "an edit is handed keywords edit_original_response refuses")

    def test_the_raid_cards_send_survives_the_edit(self):
        discord = self.discord
        clean = self.hubs._safe_edit_kwargs(
            {"view": None, "ephemeral": False, "wait": True, "allowed_mentions": discord.AllowedMentions.none()},
            fallback_view=None)
        self.assertNotIn("wait", clean)
        self.assertIn("allowed_mentions", clean, "the card's mentions must still not ping")

    def test_a_file_becomes_an_attachment(self):
        marker = object()
        clean = self.hubs._safe_edit_kwargs({"content": "x", "file": marker}, fallback_view=None)
        self.assertEqual(clean.get("attachments"), [marker])
        self.assertNotIn("file", clean)


# --- from test_hidden_actions.py ---

SURFACE = (PROJECT_ROOT / "app" / "bot" / "surface.py").read_text(encoding="utf-8")
# Named after the stamped release, never a literal: `docs/playtest/v<version>.md`
# is renamed by every version bump, and a hardcoded `v1.0.0.md` here made the
# whole file error with FileNotFoundError the first time one happened (v1.0.1).
# That is the same fault `merge_ticks` had one level up - something that only
# holds while the filename does.
VERSION = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
CHECKLIST = (PROJECT_ROOT / "docs" / "playtest" / f"v{VERSION}.md").read_text(encoding="utf-8")
ROWS = {m.group(1) for m in re.finditer(r"^\| `(/[^`]+)`", CHECKLIST, re.M)}


def _literal(name: str):
    tree = ast.parse(SURFACE)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
            return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == name and node.value is not None:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is not a literal assignment in surface.py")


def hidden_names() -> set[str]:
    names = {_literal("HOUSEHOLD_DOOR"), *_literal("HOUSEHOLD_INDOOR_ACTIONS")}
    for leaves in _literal("PROGRESSION_GATES").values():
        names |= set(leaves)
    # Where you stand (v1.1.0): the third provider's table, held to the same
    # two rules - a real leaf, and never a read.
    for leaves in _literal("LOCATION_GATES").values():
        names |= set(leaves)
    return names


class EveryHiddenNameIsARealDoor(unittest.TestCase):
    def test_every_hidden_leaf_is_a_row_on_the_checklist(self):
        for name in sorted(hidden_names()):
            with self.subTest(action=name):
                self.assertIn(f"/{name}", ROWS, f"/{name} is hidden by the panel but is not a command the hubs reach")

    def test_no_gate_hides_a_status_read_or_the_door_into_its_system(self):
        for name in sorted(hidden_names()):
            with self.subTest(action=name):
                leaf = name.split()[-1]
                self.assertNotIn(leaf, ("status", "view", "info", "history", "encounters", "visit", "respond"),
                                 f"/{name} is a read or an entry, and a road nobody can see is a road nobody learns exists")

    def test_both_providers_are_asked_and_registered_once(self):
        self.assertIn("register_hidden_actions(_hidden_actions)", SURFACE)
        self.assertEqual(SURFACE.count("register_hidden_actions("), 1)
        for provider in ("_household_hidden_actions", "_progression_hidden_actions", "_location_hidden_actions"):
            self.assertIn(f"async def {provider}(", SURFACE)
            self.assertIn(provider, SURFACE.split("async def _hidden_actions(")[1])

    def test_the_gates_read_the_same_state_the_engine_reads(self):
        for reader in ("DB.get_sect_membership(uid)", "DB.get_abode(uid)", "DB.get_personal_world(uid)",
                       "DB.get_spirit_beasts(uid)", "DB.get_player_family_membership(uid)", "DB.get_soul_legacy(uid)"):
            self.assertIn(reader, SURFACE)
        self.assertIn('normal_min_realm_index', SURFACE)
        self.assertIn("realm not in ASCENSION_GATES", SURFACE)


# --- from test_hint_path_regex.py ---

def _pattern():
    with patch.dict(os.environ, ENV):
        import importlib

        return importlib.import_module("app.bot.hubs")._HINT_PATH_RE


class TheHintPatternIsUnambiguous(unittest.TestCase):
    def test_it_still_reads_every_shape_of_hint(self):
        pattern = _pattern()
        for text, expected in (
            ("**/world → City → Look**", [("world", " → City → Look")]),
            ("**/cultivation → Qi Body → Refine**", [("cultivation", " → Qi Body → Refine")]),
            ("**/abode → Upgrade**", [("abode", " → Upgrade")]),
            ("**/menu**", [("menu", "")]),
            ("a line with **/world → City → Look** inside", [("world", " → City → Look")]),
            ("two **/menu** and **/sheet**", [("menu", ""), ("sheet", "")]),
        ):
            with self.subTest(text=text):
                self.assertEqual([(m.group(1), m.group(2)) for m in pattern.finditer(text)], expected)

    def test_the_step_list_is_one_flat_class_with_nothing_to_backtrack_over(self):
        """The structural property rather than the timing: no repeated group
        at all, so there is no pair of quantifiers to split text between."""
        source = _pattern().pattern
        self.assertNotIn("(?:", source, "a repeated group is what made this backtrack")
        self.assertIn("[^*]*", source, "the step list is one class that cannot cross the closing **")

    def test_neither_attack_shape_takes_any_time(self):
        pattern = _pattern()
        for name, evil in (
            # Splitting spaces between adjacent repetitions: exponential.
            ("arrow repetitions", "**/a" + "\u2192" + ") \u2192" * 26),
            # Splitting one run of spaces N ways: quadratic.
            ("run of spaces", "**/world \u2192 " + " " * 4000),
        ):
            with self.subTest(attack=name):
                started = time.perf_counter()
                self.assertIsNone(pattern.search(evil))
                self.assertLess(time.perf_counter() - started, 1.0)


# --- from test_cooldown_wording.py ---

RUNTIME = (PROJECT_ROOT / "app" / "bot" / "runtime.py").read_text(encoding="utf-8")


def _pure_namespace():
    tree = ast.parse(RUNTIME)
    keep = [
        node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in ("format_wait", "_explain_engine_error"))
        or (isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "_COOLDOWN_REMAINING_RE" for t in node.targets))
    ]
    ns = {"re": re}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "runtime-extract", "exec"), ns)
    return ns


NS = _pure_namespace()


class FormatWaitTests(unittest.TestCase):
    def test_shapes(self):
        f = NS["format_wait"]
        self.assertEqual(f(10520), "2h 55m")
        self.assertEqual(f(10800), "3h")
        self.assertEqual(f(90), "1m 30s")
        self.assertEqual(f(60), "1m")
        self.assertEqual(f(7), "7s")
        self.assertEqual(f(0), "a moment")
        self.assertEqual(f(-5), "a moment")


class CooldownRewriteTests(unittest.TestCase):
    def test_the_reported_message_becomes_a_wait(self):
        text = NS["_explain_engine_error"](Exception("cultivation cooldown remaining: 10520"))
        self.assertEqual(text, "⏳ Cultivation is still on cooldown — ready in **2h 55m**.")
        self.assertNotIn("10520", text)

    def test_every_engine_cooldown_shape(self):
        for raw, what in (("aptitude cooldown remaining: 61", "Aptitude"), ("manual study cooldown: 5 seconds", "Manual study"),
                          ("cooldown active: 90 seconds", "This action"), ("hunt cooldown remaining: 3600", "Hunt")):
            text = NS["_explain_engine_error"](Exception(raw))
            self.assertTrue(text.startswith(f"⏳ {what} is still on cooldown"), text)
            self.assertNotRegex(text, r"\b\d{3,}\b", "no raw second counts reach the player")

    def test_other_errors_pass_through(self):
        self.assertEqual(NS["_explain_engine_error"](Exception("not enough qi")), "not enough qi")

    def test_every_engine_error_reply_in_the_command_modules_is_explained(self):
        offenders = []
        for path in bot_source_files():
            rel = str(path.relative_to(PROJECT_ROOT / "app" / "bot"))
            if not rel.startswith(("commands/", "ui/", "admin/")):
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if 'f"❌ {exc}"' in line:
                    offenders.append(f"{rel}:{lineno}")
        self.assertEqual(offenders, [], "raw engine errors reach players at:\n" + "\n".join(offenders))


# --- from test_every_refusal_is_explained.py ---

ROOT = Path(__file__).resolve().parents[3]
SCANNED = [*sorted((ROOT / "app" / "bot" / "commands").glob("*.py")), ROOT / "app" / "bot" / "ui" / "event_scene.py"]

# file, the first line of the handler's body: why its message is not explained.
ALLOWED = {
    # The craft reply names each short material and the hall that sells it
    # (v1.0.15); it reads the engine's shortfall and builds its own lines.
    ("exploration.py", "# The engine names exactly which materials are short and by how much"),
    # The purge's scorch refusal is reworded by its own helper.
    ("exploration.py", "await interaction.followup.send(alchemy_purge_refusal(str(exc)), ephemeral=False)"),
}


def _unexplained() -> list[str]:
    out: list[str] = []
    for path in SCANNED:
        text = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(text)):
            if not (isinstance(node, ast.ExceptHandler) and node.name and node.type is not None):
                continue
            if "GameEngineError" not in ast.unparse(node.type):
                continue
            uses = [n for n in ast.walk(node) if isinstance(n, ast.Name) and n.id == node.name]
            explained = [n for n in ast.walk(node) if isinstance(n, ast.Call) and ast.unparse(n.func).endswith("_explain_engine_error")]
            if not uses or explained:
                continue
            first = text.splitlines()[node.body[0].lineno - 1].strip() if node.body else ""
            comment = text.splitlines()[node.lineno].strip()
            if (path.name, first) in ALLOWED or (path.name, comment) in ALLOWED:
                continue
            out.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return out


class EveryRefusalIsExplained(unittest.TestCase):
    def test_the_scan_sees_the_handlers(self) -> None:
        # A reader asserted before it is trusted (rc.57).
        count = sum(
            1 for path in SCANNED for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.ExceptHandler) and node.type is not None and "GameEngineError" in ast.unparse(node.type)
        )
        self.assertGreater(count, 100)

    def test_no_command_prints_the_engines_bare_words(self) -> None:
        self.assertEqual(_unexplained(), [], "these handlers send a GameEngineError without _explain_engine_error")


# --- from test_user_budget.py ---

class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class UserBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        self.budget = UserBudget(burst=3, per_minute=6.0, clock=self.clock)

    def test_burst_then_refusal(self):
        self.assertTrue(all(self.budget.try_acquire(1) for _ in range(3)))
        self.assertFalse(self.budget.try_acquire(1), "the fourth line in the same instant is refused")
        self.assertEqual(self.budget.refused, 1)
        self.assertEqual(self.budget.granted, 3)

    def test_refill_at_the_sustained_rate(self):
        for _ in range(3):
            self.budget.try_acquire(1)
        self.assertFalse(self.budget.try_acquire(1))
        self.clock.now += 10.0  # 6/min = one token per 10s
        self.assertTrue(self.budget.try_acquire(1))
        self.assertFalse(self.budget.try_acquire(1))

    def test_refill_never_exceeds_burst(self):
        self.clock.now += 3600.0
        for _ in range(3):
            self.assertTrue(self.budget.try_acquire(7))
        self.assertFalse(self.budget.try_acquire(7))

    def test_users_are_independent(self):
        for _ in range(3):
            self.budget.try_acquire(1)
        self.assertFalse(self.budget.try_acquire(1))
        self.assertTrue(self.budget.try_acquire(2), "another player's bucket is untouched")

    def test_seconds_until_token(self):
        for _ in range(3):
            self.budget.try_acquire(1)
        self.assertAlmostEqual(self.budget.seconds_until_token(1), 10.0, places=6)
        self.clock.now += 4.0
        self.assertAlmostEqual(self.budget.seconds_until_token(1), 6.0, places=6)
        self.assertEqual(self.budget.seconds_until_token(99), 0.0)

    def test_every_door_draws_on_the_same_bucket(self):
        # v0.31.0: a player refused a typed line is refused the slash command
        # too, and the snapshot says which door each refusal came through.
        # The shorthand (v1.0.0) is the fourth door and draws on the same bucket.
        self.assertTrue(self.budget.try_acquire(1, door="typed"))
        self.assertTrue(self.budget.try_acquire(1, door="shorthand"))
        self.assertTrue(self.budget.try_acquire(1, door="narrate_it"))
        self.assertFalse(self.budget.try_acquire(1, door="slash"))
        self.assertFalse(self.budget.try_acquire(1, door="typed"))
        doors = self.budget.snapshot()["doors"]
        self.assertEqual(doors["typed"], {"granted": 1, "refused": 1})
        self.assertEqual(doors["shorthand"], {"granted": 1, "refused": 0})
        self.assertEqual(doors["narrate_it"], {"granted": 1, "refused": 0})
        self.assertEqual(doors["slash"], {"granted": 0, "refused": 1})
        self.assertEqual(self.budget.refused, 2)

    def test_idle_users_are_evicted(self):
        budget = UserBudget(burst=2, per_minute=6.0, idle_evict_seconds=60.0, clock=self.clock)
        budget.try_acquire(1)
        budget.try_acquire(2)
        self.assertEqual(budget.snapshot()["tracked_users"], 2)
        self.clock.now += 61.0
        budget.try_acquire(3)
        self.assertEqual(budget.snapshot()["tracked_users"], 1, "the two idle buckets are gone")

    def test_invalid_rate_is_rejected(self):
        with self.assertRaises(ValueError):
            UserBudget(burst=1, per_minute=0)


if __name__ == "__main__":
    unittest.main()
