"""The boundary between the bot and the Go engine.

Merged from:

test_simulation_boundary.py — (no docstring)

test_every_engine_key_reaches_the_engine.py — Every environment key the engine reads is one compose gives it (v1.2.1).

The engine's compose service takes an explicit `environment:` allowlist and no
`env_file`, so a key it is not given is a key it cannot read - the rc.39
finding that made WORLD_TIME_SCALE dead on arrival, rc.56's for the cooldowns,
and v1.2.1's for ENGINE_SHUTDOWN_GRACE_SECONDS, which was documented, in
.env.example, read by shutdown.go and passed by nothing. This gate reads the
keys off the Go source rather than off a list, so the next one fails the day it
is read.

test_the_family_wait_is_the_engines.py — The wait between two handouts of the household's support is the engine's
(v1.2.1).

`/family -> Support` sent `cooldown_game_minutes` and the engine used it, with
a floor only at zero - so any caller could send 1 and draw the stipend every
minute. That is rc.48's rule (a bound that lives in the client is not a bound)
and rc.56's (the waits are the engine's), found in a fifteenth place. The wire
field is still accepted, and ignored, so an older bot mid-upgrade is not
refused.

test_samsara_wipes_real_tables.py — What a rebirth wipes must be tables the bot really builds (v1.12.3).

`reincarnateAction` clears the incarnation-scoped tables from two lists of names
in `lifecycle_actions.go` (`incarnationScopedTables` and, for the tables a later
schema added, `incarnationScopedLaterTables`). The Go tests build their schema
by hand, so a migration that renames one of those tables breaks no build and
fails no Go test: the later list skips a table that is not there, and the first
list errors only at the rebirth itself. A flame (schema 70) and a spirit sense
(schema 71) were each missing from the list for exactly that reason - nothing
compared it with the real schema.

It belongs in Python for `test_character_reset.py`'s reason: only here is the
real schema available to ask. It holds that every name in both lists is a table
the bootstrap makes, with the `user_id` column the wipe deletes by, and that the
two tables a rebirth used to keep are named.

The character row is the other half (v1.33.0). A rebirth keeps that row and
rewrites it in place from a list of columns written when the row had fewer, so a
column added after it passed from one life into the next: the sword intent a
Sword Cultivator had banked (`path_resource`, schema 72) and the anchor the
body's mending counts from (`vitality_recovered_game_minute`, schema 59). The
two are reset by `incarnationScopedLaterColumns`, guarded on the column being
there, and this holds every column of the real `characters` table to being
reset - by the install or by that list - or named in `SAMSARA_KEEPS_COLUMNS`
with the reason it is the soul's and not the life's. A column a later schema
adds is neither until somebody decides, which is the point.

test_autonomous_event_threads.py — Every event the tick reports gets a scene thread of its own kind (v1.0.0-rc.22).

A world event that opens in SQLite and nowhere else is an entrance nobody can
walk through. The engine's tick is the only thing that knows an autonomous
event happened, so every event it reports has to become a Discord scene here -
and become the kind of scene the engine said it was, because a secret realm
that is spawned as a "random_event" closes under the wrong label and draws the
wrong panel.

The rotation is what made this concrete: `RotateSecretRealms` opened a realm
every three game days, wrote its world_events row and its public history row,
and answered the caller with a count. Nothing carried it out to the bot, so
`/admin world events` listed a live realm with "Thread: none" and there was no
way in.
"""
from __future__ import annotations

import ast
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import bot_function_source, install_aiosqlite_shim, PROJECT_ROOT

install_aiosqlite_shim()

from app.simulation import WorldSimulator  # noqa: E402


# --- from test_simulation_boundary.py ---

class RecordingSimulationEngine:
    def __init__(self):
        self.calls = []

    async def run_due_simulation(self, automation):
        self.calls.append(("run_due", dict(automation)))
        return [{
            "system": "npc_life",
            "due_steps": 3,
            "applied_steps": 3,
            "summary": "go-owned simulation",
            "events": [{"kind": "test"}],
        }]

    async def force_simulation(self, system, steps):
        self.calls.append(("force", system, steps))
        return {
            "system": system,
            "due_steps": steps,
            "applied_steps": steps,
            "summary": f"go-owned {system}",
        }


class SimulationBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_due_delegates_to_go_engine(self):
        engine = RecordingSimulationEngine()
        sim = WorldSimulator(None, {}, engine=engine)
        runs = await sim.run_due({"npc_life": True})
        self.assertEqual(engine.calls, [("run_due", {"npc_life": True})])
        self.assertEqual(runs[0].system, "npc_life")
        self.assertEqual(runs[0].summary, "go-owned simulation")
        self.assertEqual(runs[0].events, ({"kind": "test"},))

    async def test_force_run_delegates_to_go_engine(self):
        engine = RecordingSimulationEngine()
        sim = WorldSimulator(None, {}, engine=engine)
        run = await sim.force_run("dynamic_economy", 2)
        self.assertEqual(engine.calls, [("force", "dynamic_economy", 2)])
        self.assertEqual(run.summary, "go-owned dynamic_economy")

    def test_engine_is_required(self):
        with self.assertRaises(TypeError):
            WorldSimulator(None, {})


# --- from test_every_engine_key_reaches_the_engine.py ---

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


# --- from test_the_family_wait_is_the_engines.py ---

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


# --- from test_samsara_wipes_real_tables.py ---

LIFECYCLE_GO = (PROJECT_ROOT / "go_core" / "internal" / "game" / "lifecycle_actions.go").read_text(encoding="utf-8")


def _list(name: str) -> list[str]:
    match = re.search(rf"var {name} = \[\]string\{{(.*?)\}}", LIFECYCLE_GO, re.S)
    if not match:
        raise AssertionError(f"{name} is not a string-slice literal any more")
    names = re.findall(r'"([a-z_0-9]+)"', match.group(1))
    if not names:
        raise AssertionError(f"{name} parsed to nothing; the reader is broken, not the tree")
    return names


# The columns of `characters` a rebirth does not reset, each with the reason.
# A rebirth keeps the row: it is the account's, and a new body is written over it.
# These are the columns that stay the account's whichever body it holds.
SAMSARA_KEEPS_COLUMNS = {
    "user_id": "the row's identity: the account, not the body",
    "discord_name": "the account's display name, written at creation and kept by the bot",
    "created_at": "when the row was made - the account's first life; created_game_minute is the body's",
    "karma_score": "the soul's: the wheel reads it (the family a rebirth is born into is rolled off the dead life's karma)",
    "true_death_count": "counts deaths across lives; true_death adds one, and a rebirth that reset it would forget them",
    "address_style": "how the account asks to be addressed - a preference, not a fact about a body",
    "concept": "the declared Dao; the reincarnate payload has no field for it, so a new life cannot restate it",
    "is_muted": "moderation of the account: a rebirth is not a way out of a mute",
    "is_frozen": "moderation of the account: a rebirth is not a way out of a freeze",
    "moderation_reason": "the reason on the account's moderation record",
    "muted_until": "moderation of the account: the end of a mute outlives the body it was given to",
    "frozen_until": "moderation of the account: the end of a freeze outlives the body it was given to",
    "is_banned": "moderation of the account: a rebirth is not a way out of a ban",
}


def _go_function(source: str, name: str) -> str:
    """One Go function: from its `func` line to the next top-level `func` - or
    to the end of the file, because the function this is asked for is the last
    one in `lifecycle_actions.go` and a slice that needs a following `func` to
    stop would read nothing of it."""
    start = source.find(f"\nfunc {name}(")
    if start < 0:
        raise AssertionError(f"func {name} is not in the source any more")
    end = source.find("\nfunc ", start + 1)
    return source[start:] if end < 0 else source[start:end]


def _code_lines(text: str) -> str:
    """Go source without its whole-line comments, which quote the statements
    they explain."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))


def _install_resets() -> list[str]:
    """The columns the install `UPDATE characters SET ... WHERE user_id=?` in
    `reincarnateAction` assigns."""
    body = _code_lines(_go_function(LIFECYCLE_GO, "reincarnateAction"))
    found = re.findall(r"UPDATE characters SET (.*?) WHERE user_id=\?", body, re.S)
    if len(found) != 1:
        raise AssertionError(f"reincarnateAction holds {len(found)} `UPDATE characters SET` statements; the reader expects the one install")
    return re.findall(r"(?:^|,)\s*([a-z_0-9]+)\s*=", found[0])


def _balanced(source: str, opening: str) -> str:
    """The text between the brace that ends `opening` and its matching close.
    Read by depth, not by line: the literal spans several."""
    start = source.find(opening)
    if start < 0:
        raise AssertionError(f"`{opening}` is not in the source any more")
    depth, i, in_string = 0, start + len(opening) - 1, False
    begin = i + 1
    if source[i] != "{":
        raise AssertionError(f"`{opening}` does not end at an opening brace")
    while i < len(source):
        ch = source[i]
        if ch == '"' and source[i - 1] != "\\":
            in_string = not in_string
        elif not in_string:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return source[begin:i]
        i += 1
    raise AssertionError(f"`{opening}` is never closed")


def _later_column_resets() -> dict[str, str]:
    literal = _balanced(_code_lines(LIFECYCLE_GO), "var incarnationScopedLaterColumns = []incarnationColumn{")
    pairs = re.findall(r'\{\s*"([a-z_0-9]+)"\s*,\s*"([^"]*)"\s*\}', literal)
    if not pairs:
        raise AssertionError("incarnationScopedLaterColumns parsed to nothing; the reader is broken, not the tree")
    return dict(pairs)


class TheRebirthWipesRealTables(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._dir = tempfile.TemporaryDirectory()
        path = Path(cls._dir.name) / "schema.sqlite3"
        env = {
            **os.environ,
            "DISCORD_TOKEN": "test-token",
            "GUILD_ID": "123456789012345678",
            "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890",
            "DATABASE_PATH": str(path),
        }
        result = subprocess.run(
            [sys.executable, "-m", "app.database.bootstrap"],
            cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise AssertionError(f"could not bootstrap the schema: {result.stderr[-2000:]}")
        db = sqlite3.connect(path)
        cls.columns: dict[str, set[str]] = {}
        cls.not_null: dict[str, set[str]] = {}
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
            info = db.execute(f"PRAGMA table_info({table})").fetchall()
            cls.columns[table] = {row[1] for row in info}
            cls.not_null[table] = {row[1] for row in info if row[3]}
        db.close()

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def test_the_reader_found_both_lists(self):
        self.assertGreater(len(_list("incarnationScopedTables")), 30, "the reader found too few tables; it is broken, not the tree")
        self.assertIn("inventory", _list("incarnationScopedTables"))

    def test_every_table_a_rebirth_wipes_exists_and_is_keyed_by_user(self):
        for name in ("incarnationScopedTables", "incarnationScopedLaterTables"):
            for table in _list(name):
                with self.subTest(list=name, table=table):
                    self.assertIn(table, self.columns, f"{table} is wiped by a rebirth and is not a table the bootstrap makes")
                    self.assertIn("user_id", self.columns[table], f"{table} has no user_id for the wipe to delete by")

    def test_the_flame_and_the_spirit_sense_are_wiped(self):
        later = _list("incarnationScopedLaterTables")
        for table in ("character_flames", "character_spirit_sense"):
            self.assertIn(table, later, f"{table} outlives a rebirth: it opens the top grade of a craft in a body that never made it")

    # --- the character row -------------------------------------------------

    def test_the_reader_found_the_install_and_the_later_columns(self):
        installed = _install_resets()
        # assertTrue, not assertIn: a failed assertIn prints the whole set.
        self.assertTrue(len(installed) > 25 and "realm_index" in installed and "spirit_stones" in installed,
                        f"the reader found {len(installed)} install columns and not the realm or the purse; it is broken, not the tree")
        later = _later_column_resets()
        self.assertTrue("path_resource" in later and "vitality_recovered_game_minute" in later,
                        "the reader found the later-columns literal and not the two columns it was written for")

    def test_every_column_a_rebirth_resets_is_a_real_column(self):
        real = self.columns["characters"]
        for column in _install_resets() + list(_later_column_resets()):
            with self.subTest(column=column):
                self.assertTrue(column in real, f"a rebirth resets {column}, which is not a column the bootstrap makes")

    def test_every_column_of_the_character_row_is_reset_or_kept_with_a_reason(self):
        real = self.columns["characters"]
        resets = set(_install_resets()) | set(_later_column_resets())
        undecided = sorted(real - resets - set(SAMSARA_KEEPS_COLUMNS))
        self.assertEqual(
            undecided, [],
            f"characters columns a rebirth neither resets nor keeps on purpose: {undecided}. The install names the columns "
            "that existed when it was written, so a later column passes into the next life; reset it in "
            "incarnationScopedLaterColumns, or name it in SAMSARA_KEEPS_COLUMNS with the reason it is the soul's",
        )

    def test_every_kept_column_is_real_and_none_is_also_reset(self):
        real = self.columns["characters"]
        resets = set(_install_resets()) | set(_later_column_resets())
        stale = sorted(set(SAMSARA_KEEPS_COLUMNS) - real)
        self.assertEqual(stale, [], f"SAMSARA_KEEPS_COLUMNS names columns the bootstrap does not make: {stale}")
        both = sorted(set(SAMSARA_KEEPS_COLUMNS) & resets)
        self.assertEqual(both, [], f"kept and reset at once: {both}; a column is the soul's or the life's, not both")

    def test_a_later_column_reset_to_null_may_be_null(self):
        for column, reset in _later_column_resets().items():
            if reset == "NULL":
                with self.subTest(column=column):
                    self.assertTrue(column not in self.not_null["characters"],
                                    f"{column} is reset to NULL and the column is NOT NULL: every rebirth would be refused")


# --- from test_autonomous_event_threads.py ---

BOT = PROJECT_ROOT / "app" / "bot"
BOT_PY = (BOT / "bot.py").read_text(encoding="utf-8")
EVENT_SCENE = (BOT / "ui" / "event_scene.py").read_text(encoding="utf-8")
ROTATION = (PROJECT_ROOT / "go_core" / "internal" / "game" / "secret_realm_rotation.go").read_text(encoding="utf-8")
MAINTENANCE = (PROJECT_ROOT / "go_core" / "internal" / "simulation" / "advanced_maintenance.go").read_text(encoding="utf-8")
SIM_WORLD = (PROJECT_ROOT / "go_core" / "internal" / "simulation" / "world.go").read_text(encoding="utf-8")


def _body(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef)) and n.name == name
    )
    return ast.get_source_segment(source, node)


class TheTickSpawnsAThreadPerEvent(unittest.TestCase):
    def test_every_reported_event_becomes_a_scene(self):
        worker = _body(BOT_PY, "event_expiry_worker")
        self.assertIn("for sim_run in simulation_runs:", worker)
        self.assertIn("for event in sim_run.events:", worker)
        self.assertIn("spawn_system_event_thread(", worker)
        # The loop walks every run, not only the one system that used to
        # report events - the rotation rides out on advanced_world.
        self.assertNotIn('sim_run.system == "autonomous_world_events"', worker)

    def test_the_engines_event_type_is_not_flattened(self):
        worker = _body(BOT_PY, "event_expiry_worker")
        self.assertIn('event_type=str(event.get("event_type") or "random_event")', worker)
        self.assertIn("event_type=event_type", worker)
        # The old line named every autonomous event a random one.
        self.assertNotIn('event_type="random_event"', worker)

    def test_a_realm_is_announced_as_a_realm(self):
        worker = _body(BOT_PY, "event_expiry_worker")
        self.assertIn('event_type=="secret_realm"', worker)
        self.assertIn("SECRET REALM OPENS", worker)
        scene = _body(EVENT_SCENE, "spawn_system_event_thread")
        self.assertIn('str(event_type) == "secret_realm"', scene)
        self.assertIn("SECRET REALM", scene)


class TheEngineReportsWhatItOpened(unittest.TestCase):
    """The Go half, read as source: Python cannot spawn what it is not told."""

    def test_the_rotation_hands_back_the_realm_not_a_count(self):
        self.assertIn("type OpenedSecretRealm struct", ROTATION)
        self.assertIn(
            "func RotateSecretRealms(conn *storage.Conn, catalog worlddata.Catalog, gm int64) (*OpenedSecretRealm, error)",
            ROTATION,
        )
        for field in ("EventKey", "Name", "Description", "Location", "EndsAt"):
            self.assertIn(field, ROTATION, field)

    def test_maintenance_carries_it_out_on_the_run(self):
        self.assertIn("game.RotateSecretRealms(conn, r.World, gm)", MAINTENANCE)
        self.assertIn('EventType:   "secret_realm"', MAINTENANCE)
        self.assertIn("Events: spawned", MAINTENANCE)

    def test_a_spawned_event_carries_its_type(self):
        self.assertIn('EventType       string   `json:"event_type,omitempty"`', SIM_WORLD)
        self.assertIn('EventType: "random_event"', SIM_WORLD)


if __name__ == "__main__":
    unittest.main()
