"""Small dependency shims for artifact-only tests.

Production and Docker install requirements.txt. The source archive's tests can
also run in a minimal Python environment where aiosqlite is unavailable.
"""

import importlib.util
import json
import secrets
import time
import importlib.machinery
import sqlite3
import sys
import types
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def install_aiosqlite_shim() -> None:
    if "aiosqlite" in sys.modules:
        return
    if importlib.util.find_spec("aiosqlite") is not None:
        return

    class _Cursor:
        def __init__(self, cursor):
            self._cursor = cursor

        @property
        def lastrowid(self):
            return self._cursor.lastrowid

        @property
        def rowcount(self):
            return self._cursor.rowcount

        async def fetchone(self):
            return self._cursor.fetchone()

        async def fetchall(self):
            return self._cursor.fetchall()

    class _Connection:
        def __init__(self, path, timeout=5):
            self._db = sqlite3.connect(path, timeout=timeout)

        @property
        def row_factory(self):
            return self._db.row_factory

        @row_factory.setter
        def row_factory(self, value):
            self._db.row_factory = value

        async def execute(self, sql, params=()):
            return _Cursor(self._db.execute(sql, params))

        async def executescript(self, sql):
            self._db.executescript(sql)

        async def commit(self):
            self._db.commit()

        async def rollback(self):
            self._db.rollback()

        async def close(self):
            if self._db is not None:
                self._db.close()
                self._db = None

        def __del__(self):
            db = getattr(self, "_db", None)
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass
                self._db = None

    class _ConnectContext:
        def __init__(self, path, timeout=5):
            self.path = path
            self.timeout = timeout
            self.connection = None

        def __await__(self):
            async def _get():
                if self.connection is None:
                    self.connection = _Connection(self.path, self.timeout)
                return self.connection

            return _get().__await__()

        async def __aenter__(self):
            if self.connection is None:
                self.connection = _Connection(self.path, self.timeout)
            return self.connection

        async def __aexit__(self, *_):
            if self.connection is not None:
                await self.connection.close()
                self.connection = None

    shim = types.ModuleType("aiosqlite")
    shim.__spec__ = importlib.machinery.ModuleSpec("aiosqlite", loader=None)
    shim.connect = lambda path, timeout=5: _ConnectContext(path, timeout)
    shim.Row = sqlite3.Row
    shim.Connection = _Connection
    shim.IntegrityError = sqlite3.IntegrityError
    sys.modules["aiosqlite"] = shim


def install_dotenv_shim() -> None:
    if importlib.util.find_spec("dotenv") is not None or "dotenv" in sys.modules:
        return
    shim = types.ModuleType("dotenv")
    shim.__spec__ = importlib.machinery.ModuleSpec("dotenv", loader=None)
    shim.load_dotenv = lambda *args, **kwargs: False
    sys.modules["dotenv"] = shim


def install_openai_shim() -> None:
    """Provide the tiny AsyncOpenAI surface needed by narrator unit tests."""
    try:
        from openai import AsyncOpenAI  # noqa: F401
        return
    except (ImportError, ModuleNotFoundError):
        pass

    class _Responses:
        async def create(self, **kwargs):
            raise RuntimeError("OpenAI shim cannot perform network requests")

    class _AsyncOpenAI:
        def __init__(self, *args, **kwargs):
            self.responses = _Responses()

    shim = types.ModuleType("openai")
    shim.__spec__ = importlib.machinery.ModuleSpec("openai", loader=None)
    shim.AsyncOpenAI = _AsyncOpenAI
    sys.modules["openai"] = shim


def httpx_is_shimmed() -> bool:
    """True when httpx here is the stub above rather than the real library."""
    import httpx  # noqa: PLC0415 - resolved after install_httpx_shim has run

    return bool(getattr(httpx, "__xianxia_shim__", False))


def install_httpx_shim() -> None:
    """Provide the tiny httpx surface the transport modules touch at import.

    `app.database.remote` and `app.ops.game_engine` construct an AsyncClient at
    module scope, so a machine without httpx cannot even import the Database -
    which took roughly twenty test files out of the run on any minimal
    environment, including every integration test of the layers above it.

    Nothing here pretends to be a client. Any attempt to send a request raises,
    so a test that reaches the network fails loudly rather than passing against
    a stub.
    """
    if "httpx" in sys.modules or importlib.util.find_spec("httpx") is not None:
        return

    class _Timeout:
        def __init__(self, *args, **kwargs):
            self.args, self.kwargs = args, kwargs

    class _Limits:
        def __init__(self, *args, **kwargs):
            self.args, self.kwargs = args, kwargs

    class _Response:
        def __init__(self, status_code: int = 200, json_body: Any = None, text: str = ""):
            self.status_code = status_code
            self._json = json_body
            self.text = text

        def json(self):
            return self._json

        def raise_for_status(self):
            if self.status_code >= 400:
                raise _HTTPStatusError(f"HTTP {self.status_code}")

    class _HTTPError(Exception):
        pass

    class _RequestError(_HTTPError):
        pass

    class _HTTPStatusError(_HTTPError):
        pass

    class _AsyncClient:
        def __init__(self, *args, **kwargs):
            self.args, self.kwargs = args, kwargs
            self.is_closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            await self.aclose()
            return False

        async def request(self, *args, **kwargs):
            raise RuntimeError("httpx shim cannot perform network requests")

        async def get(self, *args, **kwargs):
            return await self.request("GET", *args, **kwargs)

        async def post(self, *args, **kwargs):
            return await self.request("POST", *args, **kwargs)

        async def aclose(self):
            self.is_closed = True

    shim = types.ModuleType("httpx")
    shim.__spec__ = importlib.machinery.ModuleSpec("httpx", loader=None)
    # Tests that drive httpx itself (MockTransport, request routing) cannot run
    # against this; they check the flag and skip rather than assert against a
    # stub that would agree with anything.
    shim.__xianxia_shim__ = True
    shim.AsyncClient = _AsyncClient
    shim.Timeout = _Timeout
    shim.Limits = _Limits
    shim.Response = _Response
    shim.HTTPError = _HTTPError
    shim.RequestError = _RequestError
    shim.HTTPStatusError = _HTTPStatusError
    sys.modules["httpx"] = shim


async def seed_simulation_fixture(db, world_data: dict, game_minute: int = 0) -> None:
    """Seed read-model simulation rows for Python tests without gameplay authority."""
    systems = {
        "npc_civilization": 1440,
        "npc_life": 10080,
        "dynamic_economy": 1440,
        "black_markets": 4320,
        "sect_politics": 10080,
        "clan_dynamics": 43200,
        "autonomous_world_events": 1440,
    }
    async with db._connect() as conn:
        for name, interval in systems.items():
            await conn.execute(
                """INSERT INTO world_simulation_state(
                       system,last_game_minute,interval_game_minutes,last_run_real,runs
                   ) VALUES(?,?,?,?,0) ON CONFLICT(system) DO NOTHING""",
                (name, int(game_minute), int(interval), 0.0),
            )
        for location, data in dict(world_data.get("locations") or {}).items():
            world_name = str(data.get("world") or "Mortal World")
            await conn.execute(
                """INSERT INTO civilization_regions(
                       location,world_name,population,prosperity,security,spirit_resources,
                       food_supply,migration_pressure,unrest,last_game_minute,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(location) DO NOTHING""",
                (str(location), world_name, 10000, 50, 50, 50, 50, 0, 0, int(game_minute), 0.0),
            )
        for npc_name, data in dict(world_data.get("npcs") or {}).items():
            location = str(data.get("location") or "Greenriver Town")
            world_name = str((world_data.get("locations") or {}).get(location, {}).get("world") or "Mortal World")
            await conn.execute(
                """INSERT INTO npc_civilization_state(
                       npc_name,home_location,current_location,world_name,profession,faction,
                       wealth,influence,ambition,realm_index,phase,status,activity,last_game_minute,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(npc_name) DO NOTHING""",
                (
                    str(npc_name), location, location, world_name, str(data.get("role") or "NPC"),
                    "Independent", 10, 10, 10, 0, 1, "alive", "Fixture state", int(game_minute), 0.0,
                ),
            )
        await conn.commit()


# Test-only fixture seeding. This intentionally bypasses production gameplay authority.
async def seed_character(
    db_obj,
    *,
    user_id: int,
    discord_name: str,
    name: str,
    origin: str,
    path: str,
    spiritual_root: str,
    concept: str,
    location: str,
    attributes: dict[str, int],
    qi_max: int,
    vitality_max: int,
    created_game_minute: int = 0,
    age_at_creation_years: int = 18,
    natural_lifespan_years: int = 75,
    gender: str = "neutral",
    birth_family_profile: dict[str, Any] | None = None,
    aptitude_profile: dict[str, Any] | None = None,
) -> bool:
    gender = str(gender or "neutral").strip().lower()
    if gender not in {"male", "female", "neutral"}:
        gender = "neutral"
    now = time.time()
    try:
        async with db_obj._connect() as db:
            await db.execute("PRAGMA foreign_keys=ON;")
            await db.execute(
                """
                INSERT INTO characters (
                    user_id, discord_name, name, origin, path, spiritual_root, concept, gender,
                    age_at_creation_years, created_game_minute, natural_lifespan_years, life_extension_years, life_status,
                    realm_index, phase, cultivation, qi, qi_max, vitality, vitality_max,
                    spirit_stones, insight_xp, location, attributes_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'alive', 0, 1, 0, ?, ?, ?, ?, 25, 0, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    discord_name,
                    name,
                    origin,
                    path,
                    spiritual_root,
                    concept,
                    gender,
                    max(0, int(age_at_creation_years)),
                    max(0, int(created_game_minute)),
                    max(1, int(natural_lifespan_years)),
                    qi_max,
                    qi_max,
                    vitality_max,
                    vitality_max,
                    location,
                    json.dumps(attributes),
                    now,
                    now,
                ),
            )
            # Small starter crafting kit so the systems are usable immediately.
            for item_id, qty in {"spirit_herb": 2, "spirit_iron": 1}.items():
                await db.execute(
                    "INSERT INTO inventory(user_id, item_id, quantity) VALUES (?, ?, ?)",
                    (user_id, item_id, qty),
                )
            await db.execute(
                "INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(?,?,?)",
                (user_id, "low_spirit_stone", 25),
            )
            await db.execute(
                """INSERT INTO storage_containers(
                       user_id,container_id,name,grade,slot_capacity,living_space,updated_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (user_id, "common_spatial_pouch", "Common Spatial Pouch", "Mortal", 24, 0, now),
            )
            await db.execute(
                """INSERT OR IGNORE INTO character_location_discoveries(
                       user_id,location,discovery_kind,discovered_game_minute,created_at
                   ) VALUES(?,?,?,?,?)""",
                (user_id, str(location), "birthplace", max(0, int(created_game_minute)), now),
            )
            await db.execute(
                "INSERT INTO event_log(user_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (user_id, "character_created", json.dumps({
                    "name": name, "path": path, "spiritual_root": spiritual_root, "gender": gender,
                    "origin": origin, "location": location,
                    "family_archetype": str((birth_family_profile or {}).get("id") or (birth_family_profile or {}).get("archetype") or ""),
                }), now),
            )
            family_id: int | None = None
            if birth_family_profile:
                fp = birth_family_profile
                cur = await db.execute(
                    """INSERT INTO birth_families(
                           family_name,surname,archetype,tier,wealth,influence,stability,alignment_bias,location,
                           head_name,head_gender,head_title,head_realm_index,head_phase,treasury_balance,generation,
                           created_game_minute,last_simulated_game_minute,history_json,
                           clan_structure,bloodline_name,bloodline_affinity,bloodline_trait,bloodline_purity,branch_count,retainer_count,confederacy_name,
                           created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (str(fp['family_name']),str(fp['surname']),str(fp['id']),int(fp.get('tier',1)),int(fp.get('wealth',20)),
                     int(fp.get('influence',10)),int(fp.get('stability',60)),int(fp.get('alignment_bias',0)),str(fp.get('location',location)),
                     str(fp.get('head_name','Family Head')),str(fp.get('head_gender','neutral')),str(fp.get('head_title','Family Head')),
                     int(fp.get('head_realm_index',0)),int(fp.get('head_phase',1)),max(0,int(fp.get('wealth',20))*4),1,
                     max(0,int(created_game_minute)),max(0,int(created_game_minute)),json.dumps([f"{fp['family_name']} welcomed {name} into the household."]),
                     str(fp.get('clan_structure','extended_household')),str(fp.get('bloodline_name','None')),str(fp.get('bloodline_affinity','None')),
                     str(fp.get('bloodline_trait','No awakened ancestral bloodline')),max(0,min(100,int(fp.get('bloodline_purity',0)))),
                     max(1,int(fp.get('branch_count',1))),max(0,int(fp.get('retainer_count',0))),str(fp.get('confederacy_name','None')),now,now)
                )
                family_id=int(cur.lastrowid)
                await db.execute(
                    "INSERT INTO character_birth_family(user_id,family_id,birth_order,generation,last_support_game_minute) VALUES(?,?,?,?,?)",
                    (user_id,family_id,max(1,int(fp.get('birth_order',1))),1,-999999999)
                )
                for rel in list(fp.get('relatives',[])):
                    age=max(1,int(rel.get('age',30)))
                    birth_min=max(0,int(created_game_minute)-age*518400)
                    rel_realm=int(rel.get('realm_index',0)); rel_phase=int(rel.get('phase',1))
                    rel_natural=max(1,int(rel.get('natural_lifespan_years',75)))
                    await db.execute(
                        """INSERT INTO birth_family_npcs(family_id,name,relation,gender,age_at_creation,birth_game_minute,natural_lifespan_years,status,spiritual_root,realm_index,phase,personality,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (family_id,str(rel.get('name','Relative')),str(rel.get('relation','Relative')),str(rel.get('gender','neutral')),age,birth_min,rel_natural,'alive','Mortal Root',rel_realm,rel_phase,'Family member',now)
                    )
            aptitude = dict(aptitude_profile or {})
            root = dict(aptitude.get("root") or {
                "grade": "Common", "purity": 50, "elements": [spiritual_root],
                "mutation": "", "stability": 100, "refinement_progress": 0, "compatibility": 50,
            })
            await db.execute(
                """INSERT INTO character_spiritual_roots(
                       user_id,grade,purity,elements_json,mutation,stability,refinement_progress,compatibility,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    user_id, str(root.get("grade", "Common")), max(1, min(100, int(root.get("purity", 50)))),
                    json.dumps(list(root.get("elements") or [spiritual_root])), str(root.get("mutation", "")),
                    max(0, min(100, int(root.get("stability", 100)))),
                    max(0, min(100, int(root.get("refinement_progress", 0)))),
                    max(0, min(100, int(root.get("compatibility", 50)))), now,
                ),
            )
            bloodline = aptitude.get("bloodline")
            if bloodline:
                await db.execute(
                    """INSERT INTO character_bloodlines(
                           user_id,bloodline_id,name,affinity,purity,state,evolution_stage,progress,rejection,mutation,
                           primary_lineage,source_family_id,unlocked_techniques_json,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        user_id, str(bloodline.get("bloodline_id", "legacy_family_bloodline")),
                        str(bloodline.get("name", "Ancestral Bloodline")), str(bloodline.get("affinity", "None")),
                        max(0, min(100, int(bloodline.get("purity", 0)))), str(bloodline.get("state", "dormant")),
                        max(0, int(bloodline.get("evolution_stage", 0))), max(0, min(100, int(bloodline.get("progress", 0)))),
                        max(0, min(100, int(bloodline.get("rejection", 0)))), str(bloodline.get("mutation", "")),
                        1 if bloodline.get("primary_lineage", 1) else 0, family_id,
                        json.dumps(list(bloodline.get("unlocked_techniques") or [])), now,
                    ),
                )
            physique = dict(aptitude.get("physique") or {
                "physique_id": "ordinary_mortal_body", "name": "Ordinary Mortal Body", "state": "ordinary",
                "evolution_stage": 0, "progress": 0, "stability": 100, "instability": 0,
            })
            await db.execute(
                """INSERT INTO character_physiques(
                       user_id,physique_id,name,state,evolution_stage,progress,stability,instability,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    user_id, str(physique.get("physique_id", "ordinary_mortal_body")),
                    str(physique.get("name", "Ordinary Mortal Body")), str(physique.get("state", "ordinary")),
                    max(0, int(physique.get("evolution_stage", 0))), max(0, min(100, int(physique.get("progress", 0)))),
                    max(0, min(100, int(physique.get("stability", 100)))),
                    max(0, min(100, int(physique.get("instability", 0)))), now,
                ),
            )
            await db.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def install_discord_ui_shim() -> None:
    """Provide the Components V2 surface app/bot/scene_layout.py builds against.

    No-ops when real discord.py is installed, so the same tests exercise the real
    library on a machine that has it and the shim everywhere else. The ActionRow
    assertion below mirrors Discord's own five-per-row rule, which is the limit a
    layout is most likely to trip.
    """
    if "discord" in sys.modules:
        return
    if importlib.util.find_spec("discord") is not None:
        return

    import enum

    discord = types.ModuleType("discord")
    discord.__spec__ = importlib.machinery.ModuleSpec("discord", loader=None)

    class ButtonStyle(enum.Enum):
        primary = 1
        secondary = 2
        success = 3
        danger = 4

    class Interaction:
        def __init__(self, user_id: int = 0):
            self.user = types.SimpleNamespace(id=user_id)
            self.edited_with = None
            self.sent = []
            self.response = self

        def is_done(self) -> bool:
            return False

        async def edit_message(self, **kwargs):
            self.edited_with = kwargs

        async def send_message(self, *args, **kwargs):
            self.sent.append((args, kwargs))

        async def send_modal(self, modal):
            self.sent.append(("modal", modal))

    class _Item:
        def __init__(self, *args, **kwargs):
            self.children: list[Any] = []

    class Button(_Item):
        def __init__(self, *, label="", emoji=None, style=None, **kwargs):
            super().__init__()
            self.label = label
            self.emoji = emoji
            self.style = style
            assert len(str(label)) <= 80, "button label over 80 characters"

    class Select(_Item):
        pass

    class TextInput(_Item):
        def __init__(self, **kwargs):
            super().__init__()

    class TextDisplay(_Item):
        def __init__(self, content="", **kwargs):
            super().__init__()
            self.content = content
            assert len(str(content)) <= 4000, "text display over 4000 characters"

    class Separator(_Item):
        pass

    class _Holder(_Item):
        def __init__(self, *children, **kwargs):
            super().__init__()
            self.children = list(children)

        def add_item(self, item):
            self.children.append(item)
            return self

    class ActionRow(_Holder):
        def add_item(self, item):
            assert len(self.children) < 5, "more than five components in one action row"
            return super().add_item(item)

    class Section(_Holder):
        def __init__(self, *children, accessory=None, **kwargs):
            super().__init__(*children)
            self.accessory = accessory

    class Container(_Holder):
        def __init__(self, *children, accent_colour=None, **kwargs):
            super().__init__(*children)
            self.accent_colour = accent_colour

    class View:
        def __init__(self, *, timeout=180):
            self.timeout = timeout
            self._items: list[Any] = []

        def add_item(self, item):
            self._items.append(item)

        def clear_items(self):
            self._items = []

        @property
        def children(self):
            return list(self._items)

    class LayoutView(View):
        pass

    class Modal:
        def __init__(self, *, title=None, timeout=None):
            self.title = title

        def add_item(self, item):
            return None

    ui = types.ModuleType("discord.ui")
    for name, obj in (
        ("Item", _Item), ("Button", Button), ("Select", Select), ("TextInput", TextInput),
        ("TextDisplay", TextDisplay), ("Separator", Separator), ("ActionRow", ActionRow),
        ("Section", Section), ("Container", Container), ("View", View),
        ("LayoutView", LayoutView), ("Modal", Modal),
    ):
        setattr(ui, name, obj)

    discord.ui = ui
    discord.ButtonStyle = ButtonStyle
    discord.Interaction = Interaction
    sys.modules["discord"] = discord
    sys.modules["discord.ui"] = ui


def load_module_by_path(name: str, relative_path: str):
    """Import one project module without importing its package __init__.

    app/bot/__init__.py imports main.py, which needs the whole runtime. Loading
    a single file directly is what lets a leaf module be unit tested on its own.
    """
    path = PROJECT_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Source-scanning helpers for the app/bot package
# ---------------------------------------------------------------------------
# main.py is being decomposed one block at a time (see docs/MAIN_SPLIT_PLAN.md).
# A test that reads app/bot/main.py by path and slices it does not fail when the
# code it guards moves to another module - it silently stops guarding it, which
# is what happened to test_engine_result_keys' EquipmentOptionTests in split
# stage 4. Tests that need "the source of handler X" or "every bot source file"
# go through these instead, so a later move needs no test edit at all.

BOT_PACKAGE = PROJECT_ROOT / "app" / "bot"


def bot_source_files() -> list[Path]:
    """Every .py file under app/bot, in a stable order."""
    return sorted(BOT_PACKAGE.rglob("*.py"))


def declared_hub_definitions() -> list:
    """Every `HubDefinition(...)` call node anywhere under app/bot (hubs.py
    defines the class and declares none). Shared by the three surface scans
    that used to each carry this walk (v0.20.3)."""
    import ast

    nodes = []
    for path in bot_source_files():
        if path.name == "hubs.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "HubDefinition":
                nodes.append(node)
    return nodes


def declared_hub_names() -> list[str]:
    """The `name=` of every declared hub, in source order (16 player hubs + admin)."""
    import ast

    names = []
    for node in declared_hub_definitions():
        for kw in node.keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                names.append(str(kw.value.value))
    return names


def bot_package_source() -> str:
    """All of app/bot concatenated, for scans that assert an invariant holds
    everywhere (or that a string appears nowhere)."""
    return "\n".join(path.read_text(encoding="utf-8") for path in bot_source_files())


def _bot_definition_source(name: str, kinds: tuple, what: str) -> str:
    import ast

    hits: list[tuple[Path, str]] = []
    for path in bot_source_files():
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines(keepends=True)
        body = ast.parse(source).body
        for index, node in enumerate(body):
            if not (isinstance(node, kinds) and node.name == name):
                continue
            start = min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1
            if index + 1 < len(body):
                nxt = body[index + 1]
                decos = getattr(nxt, "decorator_list", [])
                end = (min(d.lineno for d in decos) if decos else nxt.lineno) - 1
            else:
                end = len(lines)
            hits.append((path, "".join(lines[start:end])))
    if not hits:
        raise AssertionError(f"no top-level {what} named {name!r} under app/bot")
    if len(hits) > 1:
        where = ", ".join(str(p.relative_to(PROJECT_ROOT)) for p, _ in hits)
        raise AssertionError(f"{name!r} is defined in more than one bot module: {where}")
    return hits[0][1]


def bot_function_source(name: str) -> str:
    """The source text of top-level function ``name``, wherever it lives under
    app/bot: from its ``def``/``async def`` line (decorators included) up to the
    next top-level definition. Raises if the name is defined in zero or more
    than one module - both are bugs a split can introduce and both should fail
    loudly rather than return the wrong text."""
    import ast

    return _bot_definition_source(name, (ast.FunctionDef, ast.AsyncFunctionDef), "function")


def bot_class_source(name: str) -> str:
    """bot_function_source for a top-level class."""
    import ast

    return _bot_definition_source(name, (ast.ClassDef,), "class")


def bot_module_defining(name: str) -> Path:
    """Path of the app/bot module that defines top-level ``name`` (function,
    class or assignment). Same uniqueness rule as bot_function_source."""
    import ast

    hits: list[Path] = []
    for path in bot_source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
                hits.append(path)
            elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets
            ):
                hits.append(path)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
                hits.append(path)
    if not hits:
        raise AssertionError(f"nothing named {name!r} is defined at module level under app/bot")
    if len(hits) > 1:
        raise AssertionError(f"{name!r} is defined in more than one bot module: {hits}")
    return hits[0]
