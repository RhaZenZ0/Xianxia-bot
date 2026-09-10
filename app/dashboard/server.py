from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import aiosqlite

from ..ops.http_limits import (
    STREAM_LIMIT,
    ConnectionLimiter,
    EmptyRequest,
    HeaderLimits,
    RequestHeadRejected,
    read_request_head,
    LoginThrottle,
)
from ..rules.worldtime import from_game_minutes
from ..database.remote import GoDatabaseTransport, RemoteDatabaseError
from ..ops.game_engine import GameEngineClient, GameEngineError

# Release-blocking browser/API/schema contract shared with the standard checker.
from .contract import (
    DASHBOARD_API_VERSION,
    DASHBOARD_GET_API_PATHS,
    DASHBOARD_REVIEWED_SCHEMA_VERSION,
    DASHBOARD_SYSTEM_TABLES,
    DASHBOARD_VIEW_ENDPOINTS,
)

from ..rules.quests import (
    MAX_OBJECTIVE_COUNT,
    MAX_OBJECTIVES,
    OBJECTIVE_TYPES,
    REWARD_KEYS,
    rewardable_items,
    SCENE_ACTION_KEYS,
    quest_key_for,
    validate_quest_definition,
)

log = logging.getLogger("xianxia.dashboard")

ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = ROOT / "dashboard"

# JavaScript numbers are IEEE-754 doubles, so integers above this lose precision.
JS_MAX_SAFE_INTEGER = 2**53 - 1


def _json_list(raw: Any) -> list[dict[str, Any]]:
    """A stored JSON array as a list of objects, or nothing at all.

    Rows come off the wire as text and a malformed one must not take the page
    down - a single unreadable definition is a thing to notice on the row, not
    a reason the GM cannot see any of the others.
    """
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    try:
        parsed = json.loads(str(raw or "") or "[]")
    except Exception:
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def _quest_source(row: dict[str, Any]) -> str:
    """One word for where a definition came from (v0.24.0).

    Two columns decide it and neither alone is the answer a GM wants:
    `source_type` says how the quest was written, `giver_npc` says how it
    reaches a player. A commission is a commission whether a model drafted it
    or somebody typed it; a forged quest with no giver is found in the world.
    The Quests page filters and groups on this, so it lives here rather than in
    the browser, where two screens once disagreed about it.
    """
    if row.get("owner_user_id") not in (None, ""):
        # Invented for one player by their own commission ladder: never pooled,
        # never listed for anyone else.
        return "invented"
    if str(row.get("giver_npc") or "").strip():
        return "commission"
    source_type = str(row.get("source_type") or "").strip().lower()
    if source_type in {"system", "static"}:
        return "static"
    if source_type == "authored":
        return "authored"
    if str(row.get("source_key") or "").startswith("history:") or str(row.get("origin") or "") == "world_event":
        return "world_event"
    return "forge"


class _QuestWorld:
    """The three tables `validate_quest_definition` reads, and nothing else.

    The validator is the single vocabulary for what a quest may name - the same
    one the Forge is held to - so the dashboard uses it rather than a second
    copy that could drift. It wants a World, but only for `.locations`, `.npcs`
    and `.items`, and building a real one drags the whole rules tier into this
    process for three dictionaries.
    """

    def __init__(self, data: dict[str, Any]):
        self.locations = dict(data.get("locations") or {})
        self.npcs = dict(data.get("npcs") or {})
        self.items = dict(data.get("items") or {})


def json_safe_numbers(value: Any) -> Any:
    """Emit integers JavaScript cannot represent exactly as decimal strings.

    Every Discord ID - user_id, guild_id, channel_id, thread_id, message_id,
    role_id - is a snowflake: a 17-19 digit integer, far past
    Number.MAX_SAFE_INTEGER (2**53-1 = 9007199254740991). JSON has no integer
    type of its own, so a bare 847706123456789012 in the response body is parsed
    by JSON.parse into the nearest double and silently becomes
    847706123456789000. The dashboard then writes that rounded value into
    <option value="...">, posts it back on the next admin action, and the lookup
    targets a user id that does not exist - so every teleport, currency grant,
    karma and fate adjustment against that player fails with "character not
    found", with nothing in the logs pointing at rounding.

    This happens before any dashboard JavaScript runs, which is why the fix has
    to live here rather than in app.js: by the time the browser can inspect the
    value, the low digits are already gone.

    Serialising them as strings is what Discord's own API does, for this exact
    reason, and costs the front end nothing - these values are only compared,
    displayed and echoed back, never used in arithmetic.
    """
    if isinstance(value, bool):
        # bool subclasses int; keep true/false as JSON booleans.
        return value
    if isinstance(value, int):
        return str(value) if abs(value) > JS_MAX_SAFE_INTEGER else value
    if isinstance(value, dict):
        return {key: json_safe_numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe_numbers(item) for item in value]
    return value


def origin_allowed(headers: dict[str, str], allowed_origins: tuple[str, ...] = ()) -> bool:
    """Is this mutation from the dashboard's own origin?

    Basic Auth rides on every request the browser makes, so a page on another
    origin that can make the browser POST here posts as the logged-in GM. The
    ``x-xianxia-admin`` header already stops a plain HTML form; this stops a
    cross-origin ``fetch`` too. A browser sends ``Origin`` on every POST, so a
    present ``Origin`` must name the ``Host`` the request arrived at (or an
    operator-listed public origin, for a proxy that rewrites ``Host``). A
    request with no ``Origin`` is not from a browser page - curl, a script -
    and is allowed unless ``Sec-Fetch-Site`` says otherwise. ``Origin: null``
    (a sandboxed frame, a redirect from elsewhere) is refused. Header names
    are already lower-cased by the request-head reader.
    """
    origin = headers.get("origin", "").strip().lower()
    site = headers.get("sec-fetch-site", "").strip().lower()
    if not origin:
        return site not in {"cross-site", "same-site"}
    if origin == "null":
        return False
    if origin in {o.strip().lower() for o in allowed_origins if o.strip()}:
        return True
    host = headers.get("host", "").strip().lower()
    return bool(host) and urlsplit(origin).netloc.lower() == host


@dataclass(frozen=True)
class DashboardSettings:
    database_path: Path
    host: str
    port: int
    username: str
    token: str
    dashboard_actor_id: int = 1
    admin_writes: bool = False
    engine_url: str = ""
    bot_control_url: str = ""
    bot_control_token: str = ""
    # Pre-auth request-head bounds. Defaults are generous for a browser (Chrome
    # sends ~15 headers, 1-2 KiB) and mean for a slow-header attacker.
    max_request_line_bytes: int = 8192
    max_header_lines: int = 100
    max_header_bytes: int = 16384
    header_deadline_seconds: float = 10.0
    header_line_timeout_seconds: float = 5.0
    max_connections: int = 64
    # Failed-login lockout per source address, and the origins a browser
    # mutation may come from besides the Host it was sent to (v0.29.0).
    login_max_failures: int = 5
    login_window_seconds: float = 300.0
    login_lockout_seconds: float = 900.0
    allowed_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "DashboardSettings":
        database_path = Path(os.getenv("DATABASE_PATH", "data/xianxia.sqlite3"))
        # Loopback unless told otherwise (v0.29.0). docker-compose.yml sets
        # 0.0.0.0 inside the container, where the published port needs it; a
        # bare-metal run should not offer Basic Auth in plaintext to the LAN by
        # default - README, "Configure dashboard access", for the proxy recipe.
        host = os.getenv("DASHBOARD_HOST", "127.0.0.1").strip() or "127.0.0.1"
        try:
            port = int(os.getenv("DASHBOARD_INTERNAL_PORT", "8090"))
        except ValueError as exc:
            raise RuntimeError("DASHBOARD_INTERNAL_PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise RuntimeError("DASHBOARD_INTERNAL_PORT must be between 1 and 65535")
        username = os.getenv("DASHBOARD_USERNAME", "admin").strip() or "admin"
        token = os.getenv("DASHBOARD_TOKEN", "").strip()
        if len(token) < 20 or token in {"change-me", "CHANGE_ME", "CHANGE_ME_LONG_RANDOM_TOKEN"}:
            raise RuntimeError(
                "DASHBOARD_TOKEN must be set to a private random value of at least 20 characters before starting the dashboard"
            )
        # Every dashboard-originated admin write is attributed to this actor id in
        # admin_audit_log, instead of the hardcoded 0 ("unattributed") every write
        # used before this setting existed. Small integers (this defaults to 1)
        # can never collide with a real Discord snowflake (17-19 digits), which is
        # what the bot's slash-command path already uses as ActorID - so 0 stays
        # meaning "unattributed/legacy" and this is unambiguous going forward. Set
        # it to your real Discord user id instead if you want dashboard-originated
        # and bot-originated audit rows to attribute to the same identity.
        try:
            dashboard_actor_id = int(os.getenv("DASHBOARD_ACTOR_ID", "1"))
        except ValueError as exc:
            raise RuntimeError("DASHBOARD_ACTOR_ID must be an integer") from exc
        if dashboard_actor_id < 0:
            raise RuntimeError("DASHBOARD_ACTOR_ID must not be negative")
        admin_writes = os.getenv("DASHBOARD_ADMIN_WRITES", "true").strip().lower() in {"1", "true", "yes", "on"}
        engine_url = os.getenv("GAME_ENGINE_URL", "").strip().rstrip("/")
        if admin_writes and not engine_url:
            raise RuntimeError("GAME_ENGINE_URL is required when DASHBOARD_ADMIN_WRITES is enabled")
        # The same rule the bot and the engine apply. The engine transports read
        # the variable themselves; checking it here turns "every engine call
        # answers 401" into one sentence at startup.
        if engine_url and len(os.getenv("ENGINE_AUTH_TOKEN", "").strip()) < 20:
            raise RuntimeError(
                "ENGINE_AUTH_TOKEN must be set to at least 20 characters, the same value the Go engine runs with"
            )
        allowed_origins = tuple(
            o.strip().lower() for o in os.getenv("DASHBOARD_ALLOWED_ORIGINS", "").split(",") if o.strip()
        )
        bot_control_url = os.getenv("BOT_CONTROL_URL", "http://127.0.0.1:8082").strip().rstrip("/")
        bot_control_token = os.getenv("BOT_CONTROL_TOKEN", "").strip() or token


        def _limit(name: str, default: int, low: int, high: int) -> int:
            try:
                value = int(os.getenv(name, str(default)) or default)
            except ValueError as exc:
                raise RuntimeError(f"{name} must be an integer") from exc
            if not low <= value <= high:
                raise RuntimeError(f"{name} must be between {low} and {high}")
            return value

        def _seconds(name: str, default: float, low: float, high: float) -> float:
            try:
                value = float(os.getenv(name, str(default)) or default)
            except ValueError as exc:
                raise RuntimeError(f"{name} must be a number") from exc
            if not low <= value <= high:
                raise RuntimeError(f"{name} must be between {low} and {high}")
            return value

        return cls(
            database_path=database_path, host=host, port=port, username=username, token=token,
            dashboard_actor_id=dashboard_actor_id,
            admin_writes=admin_writes, engine_url=engine_url, bot_control_url=bot_control_url,
            bot_control_token=bot_control_token,
            max_request_line_bytes=_limit("HTTP_MAX_REQUEST_LINE_BYTES", 8192, 256, 65536),
            max_header_lines=_limit("HTTP_MAX_HEADER_LINES", 100, 8, 1000),
            max_header_bytes=_limit("HTTP_MAX_HEADER_BYTES", 16384, 1024, 262144),
            header_deadline_seconds=_seconds("HTTP_HEADER_DEADLINE_SECONDS", 10.0, 1.0, 120.0),
            header_line_timeout_seconds=_seconds("HTTP_HEADER_LINE_TIMEOUT_SECONDS", 5.0, 0.5, 60.0),
            max_connections=_limit("HTTP_MAX_CONNECTIONS", 64, 4, 4096),
            login_max_failures=_limit("DASHBOARD_LOGIN_MAX_FAILURES", 5, 1, 100),
            login_window_seconds=_seconds("DASHBOARD_LOGIN_WINDOW_SECONDS", 300.0, 1.0, 86400.0),
            login_lockout_seconds=_seconds("DASHBOARD_LOGIN_LOCKOUT_SECONDS", 900.0, 1.0, 86400.0),
            allowed_origins=allowed_origins,
        )


class ReadOnlyDashboardStore:
    """Read-only projection of canonical Xianxia RP state.

    Production reads use query-only sessions hosted by the authoritative Go engine,
    so the dashboard never opens the canonical SQLite file directly. Local tests may
    use SQLite ``mode=ro`` with ``PRAGMA query_only=ON`` when no engine is configured.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        engine_url = os.getenv("GAME_ENGINE_URL", "").strip()
        self._go_transport = GoDatabaseTransport(engine_url) if engine_url else None
        self._quest_world: _QuestWorld | None = None

    def quest_world(self) -> _QuestWorld:
        """The content pack, read once, for quest validation and coverage."""
        if self._quest_world is None:
            try:
                data = json.loads((ROOT / "content" / "world.json").read_text(encoding="utf-8"))
            except Exception:
                log.warning("Could not load content/world.json for quest validation", exc_info=True)
                data = {}
            self._quest_world = _QuestWorld(data)
        return self._quest_world

    @staticmethod
    def quest_budget() -> dict[str, int]:
        """The GM reward budget, from the same environment the bot reads.

        Not imported from app.ops.config: that builds the whole Settings object,
        which wants a Discord token and an engine URL this process has no
        business asserting. The keys and defaults are the ones in config.py and
        are asserted to match by the dashboard contract test.
        """
        def _cap(name: str, default: int) -> int:
            try:
                return max(0, int(os.getenv(name, str(default))))
            except ValueError:
                return default

        return {
            "max_xp": _cap("QUEST_REWARD_MAX_XP", 50),
            "max_stones": _cap("QUEST_REWARD_MAX_STONES", 200),
            "max_items": _cap("QUEST_REWARD_MAX_ITEMS", 3),
        }

    @asynccontextmanager
    async def _connect(self):
        # Production reads through a query-only session owned by the Go engine, so
        # even the dashboard never opens the canonical SQLite file itself.
        if self._go_transport is not None:
            db = await self._go_transport.open()
            db.row_factory = object()
            try:
                await db.execute("PRAGMA query_only=ON")
                await db.execute("PRAGMA busy_timeout=3000")
                yield db
            finally:
                await db.close()
            return

        # Local test/dev fallback when no engine is configured.
        uri = f"file:{self.path.resolve()}?mode=ro"
        try:
            connector = aiosqlite.connect(uri, uri=True)
        except TypeError:  # lightweight test shim has no uri= argument
            connector = aiosqlite.connect(str(self.path))
        async with connector as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA query_only=ON")
            await db.execute("PRAGMA busy_timeout=3000")
            yield db

    @staticmethod
    async def _fetchall(db: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cur = await db.execute(sql, params)
        return [dict(row) for row in await cur.fetchall()]

    @staticmethod
    async def _fetchone(db: Any, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        cur = await db.execute(sql, params)
        row = await cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    async def _scalar(db: Any, sql: str, params: tuple[Any, ...] = (), default: int = 0) -> int:
        cur = await db.execute(sql, params)
        row = await cur.fetchone()
        return int(row[0]) if row and row[0] is not None else default

    @staticmethod
    async def _table_exists(db: Any, name: str) -> bool:
        cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
        return bool(await cur.fetchone())

    @classmethod
    async def _fetchall_if_table(cls, db: Any, table: str, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if not await cls._table_exists(db, table):
            return []
        return await cls._fetchall(db, sql, params)

    async def schema_version(self) -> int:
        async with self._connect() as db:
            if not await self._table_exists(db, "schema_version"):
                return 0
            return await self._scalar(db, "SELECT current_version FROM schema_version WHERE singleton=1")

    async def _world_clock(self, db: Any) -> dict[str, Any]:
        row = await self._fetchone(db, "SELECT value_json FROM world_state WHERE key='world_clock'")
        now = time.time()
        if not row:
            game_minute = 8 * 60
            scale = 0
        else:
            try:
                state = json.loads(str(row.get("value_json") or "{}"))
            except Exception:
                state = {}
            scale = max(0, int(state.get("scale", 0)))
            anchor_game = int(state.get("anchor_game_minute", 8 * 60))
            anchor_real = float(state.get("anchor_real_ts", now))
            elapsed = max(0.0, (now - anchor_real) / 60.0)
            game_minute = int(anchor_game + elapsed * scale)
        wt = from_game_minutes(game_minute)
        return {
            "game_minute": game_minute,
            "display": wt.display,
            "year": wt.year,
            "month": wt.month,
            "day": wt.day,
            "clock": wt.clock,
            "season": wt.season,
            "period": wt.period,
            "scale": scale,
        }

    async def overview(self) -> dict[str, Any]:
        async with self._connect() as db:
            clock = await self._world_clock(db)
            schema = 0
            if await self._table_exists(db, "schema_version"):
                schema = await self._scalar(db, "SELECT current_version FROM schema_version WHERE singleton=1")
            counts: dict[str, int] = {}
            count_queries = {
                "players": "SELECT COUNT(*) FROM characters",
                "players_alive": "SELECT COUNT(*) FROM characters WHERE life_status='alive'",
                "npcs_alive": "SELECT COUNT(*) FROM npc_civilization_state WHERE status='alive'",
                "npcs_injured": "SELECT COUNT(*) FROM npc_life_state l JOIN npc_civilization_state c ON c.npc_name=l.npc_name WHERE c.status='alive' AND l.injury_severity>0",
                "npc_marriages": "SELECT COUNT(*) FROM npc_life_state WHERE relationship_status='married'",
                "npc_descendants": "SELECT COUNT(*) FROM npc_descendants WHERE status='alive'",
                "world_history": "SELECT COUNT(*) FROM world_history_events",
                "rag_memories": "SELECT COUNT(*) FROM rag_memories",
                "active_world_events": "SELECT COUNT(*) FROM world_events WHERE active=1",
                "active_battles": "SELECT COUNT(*) FROM battles WHERE status='active'",
                "active_wars": "SELECT COUNT(*) FROM territory_wars WHERE status='active'",
                "sects": "SELECT COUNT(*) FROM sect_politics_state",
                "birth_families": "SELECT COUNT(*) FROM birth_families WHERE line_status='active'",
            }
            for key, sql in count_queries.items():
                try:
                    counts[key] = await self._scalar(db, sql)
                except Exception:
                    counts[key] = 0
            sims = await self._fetchall(
                db,
                "SELECT system,last_game_minute,interval_game_minutes,last_run_real,runs FROM world_simulation_state ORDER BY system",
            ) if await self._table_exists(db, "world_simulation_state") else []
            for row in sims:
                row["lag_game_minutes"] = max(0, int(clock["game_minute"]) - int(row.get("last_game_minute") or 0))
            era = None
            if await self._table_exists(db, "world_eras"):
                era = await self._fetchone(db, "SELECT * FROM world_eras WHERE active=1 ORDER BY era_id DESC LIMIT 1")
            recent = await self._fetchall(
                db,
                """SELECT history_id,event_type,title,summary,significance,visibility,location,faction,actor_name,target_name,game_minute
                   FROM world_history_events ORDER BY game_minute DESC,significance DESC,history_id DESC LIMIT 8""",
            ) if await self._table_exists(db, "world_history_events") else []
            attention = await self._attention(db, clock, sims)
            return {"schema_version": schema, "clock": clock, "counts": counts, "simulations": sims,
                    "active_era": era, "recent_history": recent, "attention": attention}

    async def _attention(self, db, clock: dict[str, Any], sims: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The handful of rows a GM would otherwise have to go looking for (v0.25.0).

        Nothing here is new data. Every item already exists on another page; what
        was missing was any reason to visit that page today. A session used to
        start on Overview, which showed twelve counts and some history, and the
        three drafts waiting for review were two clicks away behind a tab that
        looked exactly like the other twenty-two.

        Deliberately short and deliberately boring. It reports only conditions
        with an obvious next action, it never invents a severity the underlying
        row does not have, and anything it cannot read it simply omits - an
        attention feed that fails loudly on a missing table would be worse than
        the absence it is fixing.
        """
        now_minute = int(clock.get("game_minute") or 0)
        items: list[dict[str, Any]] = []

        async def _count(sql: str, params: tuple = ()) -> int:
            try:
                return int(await self._scalar(db, sql, params) or 0)
            except Exception:
                return 0

        if await self._table_exists(db, "quest_definitions"):
            drafts = await _count("SELECT COUNT(*) FROM quest_definitions WHERE status='draft'")
            if drafts:
                items.append({
                    "kind": "quest_drafts", "severity": "warn", "count": drafts,
                    "text": f"{drafts} quest draft{'s' if drafts != 1 else ''} waiting for review",
                    "view": "quests", "action": "Review",
                })

        # A system that is further behind than its own interval has missed a
        # tick, which is the only lag reading that means anything: a 720-minute
        # system 200 minutes behind is early, not late.
        for row in sims:
            interval = int(row.get("interval_game_minutes") or 0)
            lag = int(row.get("lag_game_minutes") or 0)
            if interval and lag > interval:
                items.append({
                    "kind": "simulation_lag", "severity": "bad", "count": lag,
                    "text": f"{row.get('system')} is {lag} game-minutes behind a {interval}-minute interval",
                    "view": "overview", "action": "Force a tick", "system": str(row.get("system") or ""),
                })

        if await self._table_exists(db, "character_quests"):
            overdue = await _count(
                """SELECT COUNT(*) FROM character_quests
                   WHERE commission=1 AND status='active'
                     AND deadline_game_minute IS NOT NULL AND deadline_game_minute<=?""",
                (now_minute,),
            )
            if overdue:
                items.append({
                    "kind": "commissions_overdue", "severity": "warn", "count": overdue,
                    "text": f"{overdue} commission{'s are' if overdue != 1 else ' is'} past the deadline its giver set",
                    "view": "commissions", "action": "Open",
                })

        if await self._table_exists(db, "characters"):
            held = await _count(
                "SELECT COUNT(*) FROM characters WHERE (is_frozen=1 OR is_muted=1 OR is_banned=1) AND COALESCE(moderation_reason,'')=''")
            if held:
                items.append({
                    "kind": "moderation_unexplained", "severity": "warn", "count": held,
                    "text": f"{held} player{'s are' if held != 1 else ' is'} frozen, muted or banned with no reason recorded",
                    "view": "players", "action": "Open",
                })

        order = {"bad": 0, "warn": 1, "info": 2}
        items.sort(key=lambda item: (order.get(str(item.get("severity")), 3), -int(item.get("count") or 0)))
        return items

    async def timeline(self, *, limit: int = 100, q: str = "", event_type: str = "", visibility: str = "") -> dict[str, Any]:
        limit = max(1, min(300, int(limit)))
        clauses = ["1=1"]
        params: list[Any] = []
        if q.strip():
            needle = f"%{q.strip()}%"
            clauses.append("(title LIKE ? COLLATE NOCASE OR summary LIKE ? COLLATE NOCASE OR actor_name LIKE ? COLLATE NOCASE OR target_name LIKE ? COLLATE NOCASE OR location LIKE ? COLLATE NOCASE OR faction LIKE ? COLLATE NOCASE)")
            params.extend([needle] * 6)
        if event_type.strip():
            clauses.append("event_type=?")
            params.append(event_type.strip())
        if visibility.strip():
            clauses.append("visibility=?")
            params.append(visibility.strip())
        params.append(limit)
        async with self._connect() as db:
            rows = await self._fetchall(
                db,
                "SELECT * FROM world_history_events WHERE " + " AND ".join(clauses) + " ORDER BY game_minute DESC,significance DESC,history_id DESC LIMIT ?",
                tuple(params),
            )
            types = [r["event_type"] for r in await self._fetchall(db, "SELECT DISTINCT event_type FROM world_history_events ORDER BY event_type")]
            return {"rows": rows, "event_types": types}

    async def npcs(self, *, limit: int = 200, q: str = "", location: str = "", faction: str = "", status: str = "alive") -> dict[str, Any]:
        limit = max(1, min(500, int(limit)))
        clauses = ["1=1"]
        params: list[Any] = []
        if q.strip():
            needle = f"%{q.strip()}%"
            clauses.append("(c.npc_name LIKE ? COLLATE NOCASE OR c.profession LIKE ? COLLATE NOCASE OR c.activity LIKE ? COLLATE NOCASE OR m.current_goal LIKE ? COLLATE NOCASE)")
            params.extend([needle] * 4)
        if location.strip():
            clauses.append("c.current_location=?")
            params.append(location.strip())
        if faction.strip():
            clauses.append("c.faction=?")
            params.append(faction.strip())
        if status.strip() and status != "all":
            clauses.append("c.status=?")
            params.append(status.strip())
        params.append(limit)
        sql = """SELECT c.npc_name,c.home_location,c.current_location,c.world_name,c.profession,c.faction,c.wealth,c.influence,c.ambition,
                        c.realm_index,c.phase,c.status,c.activity,c.last_game_minute,
                        l.birth_game_minute,l.age_at_creation_years,l.natural_lifespan_years,l.health,l.injury,l.injury_severity,l.sect_rank,
                        l.relationship_status,l.spouse_name,l.children_count,l.death_game_minute,l.cause_of_death,
                        m.current_goal,m.mood,m.focus_target,m.recent_event,m.goal_progress
                 FROM npc_civilization_state c
                 LEFT JOIN npc_life_state l ON l.npc_name=c.npc_name
                 LEFT JOIN npc_mind_state m ON m.npc_name=c.npc_name
                 WHERE """ + " AND ".join(clauses) + " ORDER BY c.status DESC,c.realm_index DESC,c.phase DESC,c.influence DESC,c.npc_name LIMIT ?"
        async with self._connect() as db:
            rows = await self._fetchall(db, sql, tuple(params))
            clock = await self._world_clock(db)
            for row in rows:
                if row.get("birth_game_minute") is not None:
                    elapsed = max(0, int(clock["game_minute"]) - int(row.get("birth_game_minute") or 0))
                    row["age_years"] = round(float(row.get("age_at_creation_years") or 18) + elapsed / (60 * 24 * 30 * 12), 1)
            locations = [r["current_location"] for r in await self._fetchall(db, "SELECT DISTINCT current_location FROM npc_civilization_state WHERE current_location<>'' ORDER BY current_location")]
            factions = [r["faction"] for r in await self._fetchall(db, "SELECT DISTINCT faction FROM npc_civilization_state WHERE faction<>'' ORDER BY faction")]
            return {"rows": rows, "locations": locations, "factions": factions}

    async def npc_detail(self, name: str) -> dict[str, Any]:
        async with self._connect() as db:
            row = await self._fetchone(
                db,
                """SELECT c.*,l.birth_game_minute,l.age_at_creation_years,l.natural_lifespan_years,l.health,l.injury,l.injury_severity,
                          l.sect_rank,l.career_progress,l.relationship_status,l.spouse_name,l.children_count,l.death_game_minute,l.cause_of_death,
                          m.current_goal,m.mood,m.focus_target,m.recent_event,m.goal_progress,m.last_game_minute AS mind_game_minute
                   FROM npc_civilization_state c
                   LEFT JOIN npc_life_state l ON l.npc_name=c.npc_name
                   LEFT JOIN npc_mind_state m ON m.npc_name=c.npc_name
                   WHERE c.npc_name=?""",
                (name,),
            )
            if not row:
                return {}
            social = await self._fetchall(db, "SELECT * FROM npc_social_relations WHERE npc_a=? OR npc_b=? ORDER BY grudge DESC,affinity DESC,trust DESC", (name, name))
            disciples = await self._fetchall(db, "SELECT * FROM npc_disciple_bonds WHERE master_name=? OR disciple_name=? ORDER BY started_game_minute DESC", (name, name))
            descendants = await self._fetchall(db, "SELECT * FROM npc_descendants WHERE parent_a=? OR parent_b=? ORDER BY birth_game_minute DESC", (name, name))
            history = await self._fetchall(db, "SELECT * FROM world_history_events WHERE actor_name=? OR target_name=? OR related_npc_name=? ORDER BY game_minute DESC,significance DESC LIMIT 40", (name, name, name))
            catalog = await self._fetchone(db, "SELECT data_json FROM catalog_npcs WHERE name=?", (name,))
            gm_definition: dict[str, Any] = {}
            if catalog:
                try:
                    gm_definition = json.loads(str(catalog.get("data_json") or "{}"))
                except Exception:
                    gm_definition = {"raw": str(catalog.get("data_json") or "")}
            return {"npc": row, "social": social, "disciples": disciples, "descendants": descendants, "history": history, "gm_definition": gm_definition}

    async def families(self) -> dict[str, Any]:
        async with self._connect() as db:
            clock = await self._world_clock(db)
            birth = await self._fetchall(db, "SELECT * FROM birth_families ORDER BY line_status DESC,influence DESC,wealth DESC,family_name")
            birth_npcs = await self._fetchall(db, "SELECT * FROM birth_family_npcs ORDER BY family_id,status DESC,relation,name")
            player_links = await self._fetchall(
                db,
                """SELECT cbf.family_id,cbf.user_id,cbf.birth_order,cbf.generation,c.name,c.discord_name,c.life_status,c.realm_index,c.phase
                   FROM character_birth_family cbf JOIN characters c ON c.user_id=cbf.user_id ORDER BY cbf.family_id,cbf.birth_order""",
            )
            branches = await self._fetchall(db, "SELECT * FROM martial_clan_branches ORDER BY family_id,status DESC,martial_strength DESC") if await self._table_exists(db, "martial_clan_branches") else []
            retainers = await self._fetchall(db, "SELECT * FROM martial_clan_retainers ORDER BY family_id,status DESC,loyalty DESC") if await self._table_exists(db, "martial_clan_retainers") else []
            custom = await self._fetchall(
                db,
                """SELECT pf.*,c.name AS founder_name FROM player_families pf JOIN characters c ON c.user_id=pf.founder_user_id ORDER BY pf.family_id""",
            )
            custom_members = await self._fetchall(
                db,
                """SELECT pfm.*,c.name,c.discord_name,c.realm_index,c.phase,c.life_status FROM player_family_members pfm JOIN characters c ON c.user_id=pfm.user_id ORDER BY pfm.family_id,pfm.seniority_order""",
            )
            custom_children = await self._fetchall(db, "SELECT * FROM family_children ORDER BY family_id,birth_game_minute")
            npc_desc = await self._fetchall(db, "SELECT * FROM npc_descendants ORDER BY birth_game_minute DESC LIMIT 500")
            npc_marriages = await self._fetchall(db, "SELECT npc_name,spouse_name,children_count,sect_rank FROM npc_life_state WHERE relationship_status='married' ORDER BY npc_name")
            household_threads = (
                await self._fetchall(
                    db,
                    "SELECT guild_id,family_id,thread_id,parent_channel_id,created_at,updated_at FROM birth_family_household_threads ORDER BY family_id,guild_id",
                )
                if await self._table_exists(db, "birth_family_household_threads")
                else []
            )
            current_occupants = await self._fetchall(
                db,
                """SELECT CAST(SUBSTR(c.location,14) AS INTEGER) AS family_id,c.user_id,c.name,c.discord_name,c.life_status
                   FROM characters c WHERE c.location LIKE 'birth_family:%' ORDER BY family_id,c.name""",
            )
            return {
                "clock": clock,
                "birth_families": birth,
                "birth_family_npcs": birth_npcs,
                "birth_family_players": player_links,
                "clan_branches": branches,
                "clan_retainers": retainers,
                "player_families": custom,
                "player_family_members": custom_members,
                "player_family_children": custom_children,
                "npc_descendants": npc_desc,
                "npc_marriages": npc_marriages,
                "household_threads": household_threads,
                "current_occupants": current_occupants,
            }

    async def party(self) -> dict[str, Any]:
        async with self._connect() as db:
            parties = await self._fetchall_if_table(
                db, "parties",
                """SELECT p.*,l.name AS leader_name FROM parties p
                   JOIN characters l ON l.user_id=p.leader_user_id
                   ORDER BY p.status DESC,p.updated_at DESC""",
            )
            members = await self._fetchall_if_table(
                db, "party_members",
                """SELECT pm.*,c.name,c.discord_name,c.life_status,c.realm_index,c.phase
                   FROM party_members pm JOIN characters c ON c.user_id=pm.user_id
                   ORDER BY pm.party_id,pm.role DESC""",
            )
            formations = await self._fetchall_if_table(
                db, "party_formations",
                "SELECT * FROM party_formations ORDER BY party_id,active DESC,formation_id",
            )
            positions = await self._fetchall_if_table(
                db, "formation_positions",
                """SELECT fp.*,c.name FROM formation_positions fp
                   JOIN characters c ON c.user_id=fp.user_id
                   ORDER BY fp.formation_id,fp.position""",
            )
            return {
                "parties": parties,
                "party_members": members,
                "party_formations": formations,
                "formation_positions": positions,
            }

    async def pvp(self) -> dict[str, Any]:
        async with self._connect() as db:
            challenges = await self._fetchall_if_table(
                db, "pvp_challenges",
                """SELECT pc.*,a.name AS challenger_name,b.name AS target_name
                   FROM pvp_challenges pc
                   JOIN characters a ON a.user_id=pc.challenger_user_id
                   JOIN characters b ON b.user_id=pc.target_user_id
                   ORDER BY pc.status DESC,pc.created_at DESC LIMIT 200""",
            )
            matches = await self._fetchall_if_table(
                db, "pvp_matches",
                """SELECT m.*,a.name AS player1_name,b.name AS player2_name,w.name AS winner_name
                   FROM pvp_matches m
                   JOIN characters a ON a.user_id=m.player1_user_id
                   JOIN characters b ON b.user_id=m.player2_user_id
                   LEFT JOIN characters w ON w.user_id=m.winner_user_id
                   ORDER BY m.status DESC,m.updated_at DESC LIMIT 200""",
            )
            return {"pvp_challenges": challenges, "pvp_matches": matches}

    async def conditions(self) -> dict[str, Any]:
        async with self._connect() as db:
            rows = await self._fetchall_if_table(
                db, "character_conditions",
                """SELECT cc.*,c.name AS player_name,c.discord_name,c.life_status
                   FROM character_conditions cc
                   JOIN characters c ON c.user_id=cc.user_id
                   WHERE cc.state='active'
                   ORDER BY cc.severity DESC,cc.updated_game_minute DESC LIMIT 300""",
            )
            return {"character_conditions": rows}

    async def sects(self) -> dict[str, Any]:
        async with self._connect() as db:
            sects = await self._fetchall(
                db,
                """SELECT p.*,COALESCE(s.prestige,0) AS prestige,COALESCE(s.treasury_stones,0) AS treasury_stones,
                          (SELECT COUNT(*) FROM sect_membership sm WHERE sm.sect_name=p.sect_name) AS player_members,
                          (SELECT COUNT(*) FROM npc_civilization_state nc WHERE nc.faction=p.sect_name AND nc.status='alive') AS npc_members
                   FROM sect_politics_state p LEFT JOIN sects s ON s.sect_name=p.sect_name
                   ORDER BY p.influence DESC,p.resources DESC,p.sect_name""",
            )
            factions = await self._fetchall(db, "SELECT * FROM sect_factions ORDER BY sect_name,power DESC,faction_name")
            relations = await self._fetchall(db, "SELECT * FROM sect_relations ORDER BY ABS(relation_score) DESC,sect_a,sect_b")
            events = await self._fetchall(db, "SELECT * FROM sect_politics_events ORDER BY game_minute DESC,event_id DESC LIMIT 100")
            memberships = await self._fetchall(
                db,
                """SELECT sm.*,c.name,c.discord_name,c.realm_index,c.phase,c.life_status FROM sect_membership sm JOIN characters c ON c.user_id=sm.user_id ORDER BY sm.sect_name,sm.rank_level DESC,c.realm_index DESC""",
            )
            return {"sects": sects, "factions": factions, "relations": relations, "events": events, "memberships": memberships}

    async def conflicts(self) -> dict[str, Any]:
        async with self._connect() as db:
            wars = await self._fetchall(
                db,
                """SELECT w.*,t.name AS territory_name,t.region,t.controller_key,o.siege_progress,o.attacker_morale,o.defender_morale,
                          o.attacker_force,o.defender_force,o.winner_key,o.resolution,o.occupation_until_game_minute
                   FROM territory_wars w JOIN territory_state t ON t.territory_key=w.territory_key
                   LEFT JOIN territory_war_operations o ON o.war_id=w.war_id
                   ORDER BY (w.status='active') DESC,w.updated_game_minute DESC,w.war_id DESC LIMIT 100""",
            )
            battles = await self._fetchall(
                db,
                """SELECT b.*,c.name AS player_name,c.discord_name FROM battles b JOIN characters c ON c.user_id=b.user_id
                   ORDER BY (b.status='active') DESC,b.updated_at DESC LIMIT 100""",
            )
            npc_feuds = await self._fetchall(db, "SELECT * FROM npc_social_relations WHERE grudge>0 OR relation_type IN ('grudge','blood_feud','rival') ORDER BY grudge DESC,last_interaction_game_minute DESC LIMIT 150")
            player_grudges = await self._fetchall(
                db,
                """SELECT g.*,c.name AS player_name FROM grudges g JOIN characters c ON c.user_id=g.user_id WHERE g.status='active' ORDER BY g.intensity DESC,g.updated_at DESC LIMIT 150""",
            )
            bounties = await self._fetchall(
                db,
                """SELECT b.*,c.name AS player_name,p.pursuit_id,p.hunter_name,p.hunter_power,p.status AS pursuit_status,p.pressure,p.escape_progress,p.capture_progress
                   FROM bounties b JOIN characters c ON c.user_id=b.user_id LEFT JOIN bounty_hunter_pursuits p ON p.bounty_id=b.bounty_id
                   WHERE b.status='active' OR p.status IN ('tracking','engaged') ORDER BY b.amount DESC,b.bounty_id DESC LIMIT 100""",
            ) if await self._table_exists(db, "bounty_hunter_pursuits") else []
            bosses = await self._fetchall(db, "SELECT * FROM boss_encounters ORDER BY (status='active') DESC,updated_at DESC LIMIT 50") if await self._table_exists(db, "boss_encounters") else []
            return {"wars": wars, "battles": battles, "npc_feuds": npc_feuds, "player_grudges": player_grudges, "bounties": bounties, "bosses": bosses}

    async def events(self) -> dict[str, Any]:
        async with self._connect() as db:
            world_events = await self._fetchall(db, "SELECT * FROM world_events ORDER BY active DESC,ends_at DESC LIMIT 150")
            civilization = await self._fetchall(db, "SELECT * FROM civilization_regions ORDER BY unrest DESC,security ASC,population DESC")
            civilization_events = await self._fetchall(db, "SELECT * FROM civilization_events ORDER BY game_minute DESC,event_id DESC LIMIT 100")
            era = await self._fetchall(db, "SELECT * FROM world_eras ORDER BY active DESC,started_game_minute DESC") if await self._table_exists(db, "world_eras") else []
            era_events = await self._fetchall(db, "SELECT * FROM world_era_events ORDER BY game_minute DESC,event_id DESC LIMIT 100") if await self._table_exists(db, "world_era_events") else []
            return {"world_events": world_events, "regions": civilization, "civilization_events": civilization_events, "eras": era, "era_events": era_events}

    async def players(self, *, limit: int = 200) -> dict[str, Any]:
        limit = max(1, min(500, int(limit)))
        async with self._connect() as db:
            rows = await self._fetchall(
                db,
                """SELECT c.user_id,c.discord_name,c.name,c.origin,c.path,c.spiritual_root,c.gender,c.life_status,c.karma_score,
                          c.realm_index,c.phase,c.cultivation,c.body_realm_index,c.body_phase,c.qi,c.qi_max,c.vitality,c.vitality_max,
                          c.spirit_stones,c.location,c.updated_at,sm.sect_name,sm.rank_name,
                          (SELECT COUNT(*) FROM battles b WHERE b.user_id=c.user_id AND b.status='active') AS active_battles,
                          (SELECT COUNT(*) FROM rag_memories r WHERE r.user_id=c.user_id) AS memory_count
                   FROM characters c LEFT JOIN sect_membership sm ON sm.user_id=c.user_id
                   ORDER BY c.life_status DESC,c.realm_index DESC,c.phase DESC,c.updated_at DESC LIMIT ?""",
                (limit,),
            )
            actions = await self._fetchall(
                db,
                """SELECT a.*,c.name AS player_name,c.discord_name FROM world_action_events a LEFT JOIN characters c ON c.user_id=a.user_id
                   ORDER BY a.game_minute DESC,a.action_id DESC LIMIT 120""",
            )
            scenes = await self._fetchall(
                db,
                """SELECT s.user_id,c.name AS player_name,MAX(s.id) AS last_scene_id,MAX(s.created_at) AS last_scene_at,
                          SUM(CASE WHEN s.speaker='player' THEN 1 ELSE 0 END) AS player_messages
                   FROM scene_history s LEFT JOIN characters c ON c.user_id=s.user_id WHERE s.user_id IS NOT NULL
                   GROUP BY s.user_id,c.name ORDER BY last_scene_at DESC LIMIT 100""",
            )
            return {"players": rows, "recent_actions": actions, "scene_activity": scenes}

    async def player_detail(self, user_id: int) -> dict[str, Any]:
        """Full character sheet + inventory for one player, mirroring npc_detail's
        drawer pattern above. Needed so the Admin Console can show what a GM is
        about to edit (realm/phase, resource caps, inventory) before editing it."""
        async with self._connect() as db:
            row = await self._fetchone(
                db,
                """SELECT c.*,sm.sect_name,sm.rank_name
                   FROM characters c LEFT JOIN sect_membership sm ON sm.user_id=c.user_id
                   WHERE c.user_id=?""",
                (user_id,),
            )
            if not row:
                return {}
            inventory = await self._fetchall(db, "SELECT item_id,quantity FROM inventory WHERE user_id=? ORDER BY item_id", (user_id,))
            cooldowns = await self._fetchall(db, "SELECT action,available_at FROM cooldowns WHERE user_id=? ORDER BY action", (user_id,))
            scene = await self._fetchone(db, "SELECT * FROM player_scene_state WHERE user_id=?", (user_id,))
            conditions = await self._fetchall(
                db,
                "SELECT condition_id,condition_key,category,name,severity FROM character_conditions WHERE user_id=? AND state='active' ORDER BY severity DESC",
                (user_id,),
            )
            return {"player": row, "inventory": inventory, "cooldowns": cooldowns, "scene": scene or {}, "conditions": conditions}

    async def capabilities(self) -> dict[str, Any]:
        """Describe the dashboard/API contract and whether newer-system tables are present."""
        groups = DASHBOARD_SYSTEM_TABLES
        async with self._connect() as db:
            schema = 0
            if await self._table_exists(db, "schema_version"):
                schema = await self._scalar(db, "SELECT current_version FROM schema_version WHERE singleton=1")
            systems: dict[str, Any] = {}
            for name, tables in groups.items():
                present = [table for table in tables if await self._table_exists(db, table)]
                missing = [table for table in tables if table not in present]
                systems[name] = {
                    "available": not missing,
                    "present_tables": present,
                    "missing_tables": missing,
                    "endpoint": DASHBOARD_VIEW_ENDPOINTS[name],
                }
        return {
            "api_version": DASHBOARD_API_VERSION,
            "schema_version": schema,
            "implementation": {
                "reviewed_schema_version": DASHBOARD_REVIEWED_SCHEMA_VERSION,
                "schema_review_current": schema == DASHBOARD_REVIEWED_SCHEMA_VERSION,
                "standard_check": "python scripts/check_dashboard_implementation.py",
            },
            "views": DASHBOARD_VIEW_ENDPOINTS,
            "get_endpoints": sorted(DASHBOARD_GET_API_PATHS),
            "systems": systems,
        }

    async def cultivation(self) -> dict[str, Any]:
        async with self._connect() as db:
            if await self._table_exists(db, "character_spiritual_roots"):
                roots = await self._fetchall(
                    db,
                    """SELECT c.user_id,c.name,c.discord_name,c.path,c.life_status,c.realm_index,c.phase,
                              c.body_realm_index,c.body_phase,c.location,c.spiritual_root AS legacy_root,
                              r.grade AS root_grade,r.purity,r.elements_json,r.mutation,r.stability,
                              r.refinement_progress,r.compatibility,r.updated_at
                       FROM characters c LEFT JOIN character_spiritual_roots r ON r.user_id=c.user_id
                       ORDER BY c.life_status DESC,c.realm_index DESC,c.phase DESC,c.name""",
                )
            else:
                roots = await self._fetchall(
                    db,
                    """SELECT user_id,name,discord_name,path,life_status,realm_index,phase,body_realm_index,body_phase,
                              location,spiritual_root AS legacy_root
                       FROM characters ORDER BY life_status DESC,realm_index DESC,phase DESC,name""",
                )
            bloodlines = await self._fetchall_if_table(
                db, "character_bloodlines",
                """SELECT b.*,c.name AS player_name,c.discord_name FROM character_bloodlines b
                   JOIN characters c ON c.user_id=b.user_id
                   ORDER BY b.primary_lineage DESC,b.purity DESC,b.evolution_stage DESC,c.name""",
            )
            physiques = await self._fetchall_if_table(
                db, "character_physiques",
                """SELECT p.*,c.name AS player_name,c.discord_name FROM character_physiques p
                   JOIN characters c ON c.user_id=p.user_id ORDER BY p.evolution_stage DESC,p.progress DESC,c.name""",
            )
            dao = await self._fetchall_if_table(
                db, "dao_progress",
                """SELECT d.*,c.name AS player_name FROM dao_progress d JOIN characters c ON c.user_id=d.user_id
                   ORDER BY d.progress DESC,d.dao_id,c.name LIMIT 300""",
            )
            laws = await self._fetchall_if_table(
                db, "law_progress",
                """SELECT l.*,c.name AS player_name FROM law_progress l JOIN characters c ON c.user_id=l.user_id
                   ORDER BY l.comprehension DESC,l.insights DESC,l.law_id,c.name LIMIT 300""",
            )
            tribulations = await self._fetchall_if_table(
                db, "tribulation_state",
                """SELECT t.*,c.name AS player_name,c.realm_index,c.phase FROM tribulation_state t
                   JOIN characters c ON c.user_id=t.user_id
                   ORDER BY t.cleared ASC,t.gate_realm_index DESC,t.updated_game_minute DESC LIMIT 250""",
            )
            attempts = await self._fetchall_if_table(
                db, "tribulation_attempts",
                """SELECT a.*,c.name AS player_name FROM tribulation_attempts a JOIN characters c ON c.user_id=a.user_id
                   ORDER BY a.created_game_minute DESC,a.attempt_id DESC LIMIT 120""",
            )
            perfection = await self._fetchall_if_table(
                db, "realm_perfection",
                """SELECT p.*,c.name AS player_name FROM realm_perfection p JOIN characters c ON c.user_id=p.user_id
                   WHERE p.active=1 OR p.completed=1 ORDER BY p.active DESC,p.realm_index DESC,p.progress DESC LIMIT 200""",
            )
            body_perfection = await self._fetchall_if_table(
                db, "body_realm_perfection",
                """SELECT p.*,c.name AS player_name FROM body_realm_perfection p JOIN characters c ON c.user_id=p.user_id
                   WHERE p.active=1 OR p.completed=1 ORDER BY p.active DESC,p.realm_index DESC,p.progress DESC LIMIT 200""",
            )
            seclusion = await self._fetchall_if_table(
                db, "seclusion_sessions",
                """SELECT s.*,c.name AS player_name,c.location FROM seclusion_sessions s JOIN characters c ON c.user_id=s.user_id
                   ORDER BY (s.status='active') DESC,s.updated_at DESC LIMIT 150""",
            )
            summary = {
                "roots": len(roots),
                "mutated_roots": sum(1 for r in roots if str(r.get("mutation") or "").strip()),
                "active_seclusion": sum(1 for r in seclusion if str(r.get("status")) == "active"),
                "uncleared_tribulations": sum(1 for r in tribulations if not int(r.get("cleared") or 0)),
                "bloodlines": len(bloodlines),
            }
            return {
                "summary": summary, "roots": roots, "bloodlines": bloodlines, "physiques": physiques,
                "dao": dao, "laws": laws, "tribulations": tribulations, "tribulation_attempts": attempts,
                "realm_perfection": perfection, "body_realm_perfection": body_perfection, "seclusion": seclusion,
            }

    async def crafting(self) -> dict[str, Any]:
        async with self._connect() as db:
            professions = await self._fetchall_if_table(
                db, "profession_progress",
                """SELECT p.*,c.name AS player_name,c.discord_name FROM profession_progress p
                   JOIN characters c ON c.user_id=p.user_id
                   ORDER BY p.level DESC,p.xp DESC,p.profession,c.name LIMIT 300""",
            )
            alchemy = await self._fetchall_if_table(
                db, "alchemy_state",
                """SELECT a.*,c.name AS player_name,c.discord_name FROM alchemy_state a
                   JOIN characters c ON c.user_id=a.user_id
                   ORDER BY a.pill_toxicity DESC,a.total_refinements DESC,c.name""",
            )
            batches = await self._fetchall_if_table(
                db, "alchemy_batches",
                """SELECT b.*,c.name AS player_name FROM alchemy_batches b JOIN characters c ON c.user_id=b.user_id
                   ORDER BY b.game_minute DESC,b.batch_id DESC LIMIT 150""",
            )
            beasts = await self._fetchall_if_table(
                db, "spirit_beasts",
                """SELECT b.*,c.name AS player_name,c.location AS player_location FROM spirit_beasts b
                   JOIN characters c ON c.user_id=b.user_id
                   ORDER BY b.active DESC,b.evolution_stage DESC,b.loyalty DESC,b.rank DESC LIMIT 200""",
            )
            artifacts = await self._fetchall_if_table(
                db, "artifact_bonds",
                """SELECT a.*,c.name AS player_name FROM artifact_bonds a JOIN characters c ON c.user_id=a.user_id
                   ORDER BY a.awakened DESC,a.bond_level DESC,a.resonance DESC,c.name LIMIT 200""",
            )
            abodes = await self._fetchall_if_table(
                db, "cave_abodes",
                """SELECT a.*,c.name AS owner_name FROM cave_abodes a JOIN characters c ON c.user_id=a.user_id
                   ORDER BY a.grade DESC,a.cultivation_level DESC,a.updated_at DESC LIMIT 200""",
            )
            sect_abodes = await self._fetchall_if_table(
                db, "sect_abodes",
                """SELECT a.*,c.name AS owner_name FROM sect_abodes a JOIN characters c ON c.user_id=a.user_id
                   ORDER BY a.sect_name,a.name LIMIT 200""",
            )
            personal_worlds = await self._fetchall_if_table(
                db, "personal_worlds",
                """SELECT w.*,c.name AS owner_name FROM personal_worlds w JOIN characters c ON c.user_id=w.user_id
                   ORDER BY w.stability DESC,w.updated_at DESC LIMIT 200""",
            )
            arrays = await self._fetchall_if_table(
                db, "deployed_location_arrays",
                """SELECT a.*,c.name AS owner_name FROM deployed_location_arrays a
                   LEFT JOIN characters c ON c.user_id=a.owner_user_id
                   ORDER BY a.ends_game_minute DESC,a.location,a.name LIMIT 200""",
            )
            equipment = await self._fetchall_if_table(
                db, "equipment_instances",
                """SELECT e.*,c.name AS player_name FROM equipment_instances e JOIN characters c ON c.user_id=e.user_id
                   ORDER BY e.equipped DESC,e.quality DESC,e.updated_at DESC LIMIT 250""",
            )
            return {
                "summary": {
                    "profession_tracks": len(professions), "alchemy_users": len(alchemy),
                    "active_beasts": sum(1 for r in beasts if int(r.get("active") or 0)),
                    "awakened_artifacts": sum(1 for r in artifacts if int(r.get("awakened") or 0)),
                    "properties": len(abodes) + len(sect_abodes) + len(personal_worlds),
                    "deployed_arrays": len(arrays),
                },
                "professions": professions, "alchemy": alchemy, "alchemy_batches": batches,
                "spirit_beasts": beasts, "artifact_bonds": artifacts, "cave_abodes": abodes,
                "sect_abodes": sect_abodes, "personal_worlds": personal_worlds,
                "deployed_arrays": arrays, "equipment": equipment,
            }

    async def exploration(self) -> dict[str, Any]:
        async with self._connect() as db:
            events = await self._fetchall_if_table(
                db, "exploration_events",
                """SELECT e.*,
                          (SELECT COUNT(*) FROM exploration_event_participants p WHERE p.event_id=e.event_id) AS participants
                   FROM exploration_events e
                   ORDER BY (e.state='active') DESC,e.created_game_minute DESC,e.updated_at DESC LIMIT 200""",
            )
            participants = await self._fetchall_if_table(
                db, "exploration_event_participants",
                """SELECT p.*,c.name AS player_name,e.title,e.location FROM exploration_event_participants p
                   JOIN characters c ON c.user_id=p.user_id JOIN exploration_events e ON e.event_id=p.event_id
                   ORDER BY p.joined_game_minute DESC LIMIT 250""",
            )
            secret_realms = await self._fetchall_if_table(
                db, "secret_realm_runs",
                """SELECT r.*,c.name AS player_name,c.location FROM secret_realm_runs r JOIN characters c ON c.user_id=r.user_id
                   ORDER BY r.active DESC,r.expires_at DESC LIMIT 150""",
            )
            discoveries = await self._fetchall_if_table(
                db, "character_location_discoveries",
                """SELECT d.*,c.name AS player_name FROM character_location_discoveries d
                   JOIN characters c ON c.user_id=d.user_id
                   ORDER BY d.discovered_game_minute DESC,d.created_at DESC LIMIT 250""",
            )
            beast_encounters = await self._fetchall_if_table(
                db, "wild_beast_encounters",
                """SELECT w.*,c.name AS player_name FROM wild_beast_encounters w JOIN characters c ON c.user_id=w.user_id
                   ORDER BY (w.status='active') DESC,w.created_game_minute DESC LIMIT 150""",
            )
            caravans = await self._fetchall_if_table(
                db, "caravans",
                """SELECT c.*,o.escort_strength,o.concealment,o.smuggling,o.tax_rate,o.toll_paid,o.intercepted,
                          o.seized,o.payout_final,o.outcome,o.resolved_game_minute
                   FROM caravans c LEFT JOIN caravan_operations o ON o.caravan_id=c.caravan_id
                   ORDER BY (c.status='traveling') DESC,c.depart_game_minute DESC,c.caravan_id DESC LIMIT 180""",
            )
            expeditions = await self._fetchall_if_table(
                db, "expedition_threads",
                """SELECT e.*,c.name AS player_name FROM expedition_threads e JOIN characters c ON c.user_id=e.user_id
                   ORDER BY e.updated_at DESC LIMIT 150""",
            )
            # Quest Forge (v0.20.6): drafted / approved / retired definitions.
            forged_quests = await self._fetchall_if_table(
                db, "quest_definitions",
                """SELECT quest_key,title,description,status,origin,source_key,objectives_json,rewards_json,model,
                          created_by,created_at,reviewed_by,reviewed_at
                   FROM quest_definitions ORDER BY (status='draft') DESC,created_at DESC LIMIT 100""",
            )
            return {
                "summary": {
                    "quest_drafts": sum(1 for r in forged_quests if str(r.get("status")) == "draft"),
                    "active_events": sum(1 for r in events if str(r.get("state")) == "active"),
                    "active_secret_realms": sum(1 for r in secret_realms if int(r.get("active") or 0)),
                    "discoveries_shown": len(discoveries),
                    "active_beast_encounters": sum(1 for r in beast_encounters if str(r.get("status")) == "active"),
                    "traveling_caravans": sum(1 for r in caravans if str(r.get("status")) == "traveling"),
                },
                "events": events, "participants": participants, "secret_realms": secret_realms,
                "discoveries": discoveries, "wild_beast_encounters": beast_encounters,
                "caravans": caravans, "expedition_threads": expeditions, "forged_quests": forged_quests,
            }

    async def quests(self) -> dict[str, Any]:
        """Every quest definition, whatever produced it (v0.24.0).

        There are four producers and they used to be reviewable in two
        unconnected places. Static quests (`app/rules/quests.py`) and Forge
        drafts had no giver, so the Commissions page filtered them out; the
        Exploration page listed forged ones read-only, so the only way to
        approve a draft was the Discord embed the Forge replied with. A GM
        looking for "what is waiting for me" had to know which of three screens
        a quest happened to land on.

        One query, one `source` label, and the same review controls on all of
        them - `status` is one column and always was.
        """
        async with self._connect() as db:
            clock = await self._world_clock(db)
            now_minute = int(clock.get("game_minute") or 0)
            definitions = await self._fetchall_if_table(
                db, "quest_definitions",
                """SELECT q.quest_key,q.title,q.description,q.status,q.origin,q.source_type,q.source_key,
                          q.giver_npc,q.realm_band,q.tier,q.owner_user_id,q.deadline_game_minutes,
                          q.objectives_json,q.rewards_json,q.variants_json,q.seed_json,q.model,
                          q.story_prompt,q.created_at,q.created_by,q.reviewed_by,q.reviewed_at,
                          COALESCE(q.requires_sect,'') AS requires_sect,
                          COALESCE(q.reward_visibility,'shown') AS reward_visibility,
                          COALESCE(q.boast,'') AS boast,
                          c.name AS owner_name,
                          (SELECT COUNT(*) FROM character_quests h WHERE h.quest_key=q.quest_key) AS taken,
                          (SELECT COUNT(*) FROM character_quests h WHERE h.quest_key=q.quest_key
                             AND h.status='completed') AS completed
                   FROM quest_definitions q LEFT JOIN characters c ON c.user_id=q.owner_user_id
                   ORDER BY (q.status='draft') DESC,q.created_at DESC LIMIT 400""",
            )
            for row in definitions:
                row["source"] = _quest_source(row)
            held = await self._fetchall_if_table(
                db, "character_quests",
                """SELECT h.user_id,h.quest_key,h.status,h.progress_json,h.accepted_game_minute,
                          h.deadline_game_minute,h.variant_index,h.resolved_game_minute,h.commission,
                          COALESCE(h.terms_json,'') AS terms_json,
                          c.name AS player_name,q.title,q.giver_npc,q.objectives_json
                   FROM character_quests h JOIN characters c ON c.user_id=h.user_id
                   LEFT JOIN quest_definitions q ON q.quest_key=h.quest_key
                   ORDER BY (h.status='active') DESC,h.updated_at DESC LIMIT 200""",
            )
            for row in held:
                due = row.get("deadline_game_minute")
                row["due_in_game_minutes"] = (int(due) - now_minute) if due is not None else None
                row["overdue"] = bool(due is not None and int(due) <= now_minute and str(row.get("status")) == "active")
            drafts = [r for r in definitions if str(r.get("status")) == "draft"]
            approved = [r for r in definitions if str(r.get("status")) == "approved"]
            held_counts: dict[str, int] = {}
            for row in held:
                if str(row.get("status")) == "active":
                    held_counts[str(row.get("quest_key"))] = held_counts.get(str(row.get("quest_key")), 0) + 1
            for row in definitions:
                row["held_now"] = held_counts.get(str(row.get("quest_key")), 0)
            return {
                "summary": {
                    "drafts": len(drafts),
                    "approved": len(approved),
                    "retired": sum(1 for r in definitions if str(r.get("status")) in {"retired", "discarded"}),
                    "forged": sum(1 for r in definitions if r["source"] == "forge"),
                    "commissions": sum(1 for r in definitions if r["source"] in {"commission", "invented"}),
                    "held_active": sum(1 for r in held if str(r.get("status")) == "active"),
                    "never_taken": sum(1 for r in approved if not int(r.get("taken") or 0)),
                },
                "definitions": definitions,
                "held": held,
                "coverage": self._quest_coverage(approved),
                "vocabulary": {
                    "objective_types": {
                        key: {"target": spec["target"], "label": spec["label"]}
                        for key, spec in OBJECTIVE_TYPES.items()
                    },
                    "scene_actions": list(SCENE_ACTION_KEYS),
                    "reward_keys": list(REWARD_KEYS),
                    # What a target may name. The editor binds its pickers to
                    # these, so a GM cannot type a place or a person the world
                    # does not have - which is the failure the review inbox
                    # spends most of its time reporting on forged drafts.
                    "locations": sorted(
                        name for name, loc in self.quest_world().locations.items() if not (loc or {}).get("private")
                    ),
                    "npcs": sorted(
                        name for name, npc in self.quest_world().npcs.items()
                        if not isinstance((npc or {}).get("hidden_master"), dict)
                    ),
                    "items": sorted(rewardable_items(self.quest_world())),
                    "max_objectives": MAX_OBJECTIVES,
                    "max_objective_count": MAX_OBJECTIVE_COUNT,
                    "budget": self.quest_budget(),
                    "hold_policies": ["keep", "migrate", "revoke"],
                },
                "game_minute": now_minute,
            }

    def _quest_coverage(self, approved: list[dict[str, Any]]) -> dict[str, Any]:
        """Where the live pool is thin (v0.24.0).

        Not a fault report - a map of quiet ground. A GM asking "what should I
        forge next" was previously reduced to reading the whole table and
        remembering, and the answer is nearly always a realm band nothing
        serves or a city nothing sends anyone to.
        """
        world = self.quest_world()
        bands: dict[str, int] = {}
        objective_types: dict[str, int] = {}
        referenced_locations: set[str] = set()
        unknown_targets: list[dict[str, str]] = []
        npc_names = {str(name).lower() for name in world.npcs}
        location_names = {
            str(name) for name, loc in world.locations.items() if not (loc or {}).get("private")
        }
        location_lookup = {name.lower(): name for name in location_names}
        for row in approved:
            bands[str(row.get("realm_band") or "any")] = bands.get(str(row.get("realm_band") or "any"), 0) + 1
            for objective in _json_list(row.get("objectives_json")):
                kind = str(objective.get("type") or "")
                objective_types[kind] = objective_types.get(kind, 0) + 1
                target = str(objective.get("target") or "").strip()
                if not target:
                    continue
                expects = (OBJECTIVE_TYPES.get(kind) or {}).get("target")
                if expects == "location":
                    if target.lower() in location_lookup:
                        referenced_locations.add(location_lookup[target.lower()])
                    else:
                        unknown_targets.append({"quest_key": str(row.get("quest_key")), "type": kind, "target": target,
                                                "expects": "location"})
                elif expects == "npc" and target.lower() not in npc_names:
                    unknown_targets.append({"quest_key": str(row.get("quest_key")), "type": kind, "target": target,
                                            "expects": "npc"})
        return {
            "realm_bands": sorted(({"band": band, "count": count} for band, count in bands.items()),
                                  key=lambda item: item["band"]),
            "objective_types": [
                {"type": kind, "count": objective_types.get(kind, 0)} for kind in OBJECTIVE_TYPES
            ],
            # Places that exist, are public, and that no approved quest sends
            # anyone to. Capped: the interesting answer is the shape of the
            # list, not every entry in a large content pack.
            "unreferenced_locations": sorted(location_names - referenced_locations)[:40],
            "unreferenced_location_count": len(location_names - referenced_locations),
            # An approved quest pointing at something the world does not have is
            # a dead end a player can accept. Rare - the Forge is validated -
            # but content edits can strand one, and it should be visible.
            "unknown_targets": unknown_targets[:20],
        }

    async def commissions(self) -> dict[str, Any]:
        """The GM's view of the commission pipeline (v0.22.0).

        Three questions, in the order a GM asks them: what is waiting for my
        approval, what are players carrying right now, and how have commissions
        been ending. Personal (invented) definitions are shown with their owner
        and their seed, because reviewing them after the fact is the only
        control over that producer - retiring one before it completes costs the
        player nothing and pays nothing.
        """
        async with self._connect() as db:
            clock = await self._world_clock(db)
            now_minute = int(clock.get("game_minute") or 0)
            definitions = await self._fetchall_if_table(
                db, "quest_definitions",
                """SELECT q.quest_key,q.title,q.description,q.status,q.origin,q.source_key,q.giver_npc,q.realm_band,
                          q.tier,q.owner_user_id,q.deadline_game_minutes,q.objectives_json,q.rewards_json,
                          q.variants_json,q.seed_json,q.model,q.created_at,q.reviewed_by,q.reviewed_at,
                          COALESCE(q.requires_sect,'') AS requires_sect,
                          COALESCE(q.reward_visibility,'shown') AS reward_visibility,
                          COALESCE(q.boast,'') AS boast,
                          c.name AS owner_name,
                          (SELECT COUNT(*) FROM character_quests h WHERE h.quest_key=q.quest_key) AS taken
                   FROM quest_definitions q LEFT JOIN characters c ON c.user_id=q.owner_user_id
                   WHERE q.giver_npc<>''
                   ORDER BY (q.status='draft') DESC,q.tier,q.created_at DESC LIMIT 200""",
            )
            held = await self._fetchall_if_table(
                db, "character_quests",
                """SELECT h.user_id,h.quest_key,h.status,h.progress_json,h.accepted_game_minute,
                          h.deadline_game_minute,h.variant_index,h.resolved_game_minute,
                          c.name AS player_name,q.title,q.giver_npc,q.tier,q.owner_user_id,q.objectives_json,q.variants_json
                   FROM character_quests h JOIN characters c ON c.user_id=h.user_id
                   LEFT JOIN quest_definitions q ON q.quest_key=h.quest_key
                   WHERE h.commission=1
                   ORDER BY (h.status='active') DESC,h.deadline_game_minute,h.updated_at DESC LIMIT 200""",
            )
            standing = await self._fetchall_if_table(
                db, "npc_relationships",
                """SELECT r.user_id,r.npc_name,r.trust,r.respect,r.grudge,
                          (r.trust+r.respect-r.grudge) AS standing,
                          r.commission_cooldown_until_game_minute,r.commissions_completed,
                          r.commissions_failed,r.commissions_abandoned,r.last_commission_outcome,
                          c.name AS player_name
                   FROM npc_relationships r JOIN characters c ON c.user_id=r.user_id
                   WHERE r.commissions_completed+r.commissions_failed+r.commissions_abandoned > 0
                   ORDER BY (r.commission_cooldown_until_game_minute > ?) DESC,r.updated_at DESC LIMIT 200""",
                (now_minute,),
            )
            for row in held:
                due = row.get("deadline_game_minute")
                row["due_in_game_minutes"] = (int(due) - now_minute) if due is not None else None
                row["overdue"] = bool(due is not None and int(due) <= now_minute and str(row.get("status")) == "active")
            active = [r for r in held if str(r.get("status")) == "active"]
            return {
                "summary": {
                    "approved_pool": sum(1 for r in definitions if str(r.get("status")) == "approved" and r.get("owner_user_id") is None),
                    "drafts": sum(1 for r in definitions if str(r.get("status")) == "draft"),
                    "personal": sum(1 for r in definitions if r.get("owner_user_id") is not None),
                    "undisclosed": sum(1 for r in definitions if str(r.get("reward_visibility")) == "hidden"),
                    "sect_gated": sum(1 for r in definitions if str(r.get("requires_sect") or "")),
                    "held": len(active),
                    "overdue": sum(1 for r in active if r.get("overdue")),
                    "completed": sum(1 for r in held if str(r.get("status")) == "completed"),
                    "failed": sum(1 for r in held if str(r.get("status")) == "failed"),
                    "abandoned": sum(1 for r in held if str(r.get("status")) == "abandoned"),
                },
                "game_minute": now_minute,
                "definitions": definitions,
                "held": held,
                "standing": standing,
                # The engine's numbers, shown so a GM can see what the outcomes
                # actually cost without reading Go. They are compiled constants
                # in commission_actions.go, not settings, so they are read-only
                # here rather than a form that would silently do nothing.
                "rules": {
                    "cooldown_world_days": 2,
                    "completed": {"trust": 6, "respect": 4, "grudge": 0},
                    "failed": {"trust": -5, "respect": -3, "grudge": 4},
                    "abandoned": {"trust": -5, "respect": -3, "grudge": 4},
                    "note": "Failed and abandoned cost the same. Nothing pays at accept; rewards move on completion only.",
                },
            }

    async def threads(self) -> dict[str, Any]:
        """Unified view of every Discord thread the bot tracks across all systems.

        Each source table only ever knows about its own thread column, so a GM
        previously had to hunt across the Families/Exploration pages (and had no
        visibility at all into battle or abode threads) to find a stuck or
        abandoned thread. This aggregates all of them into one sortable list with
        a direct Discord jump link, using the single configured guild's ID as a
        fallback for tables that don't store guild_id themselves (this bot only
        ever binds to one guild).
        """
        async with self._connect() as db:
            guild_row = await self._fetchone(db, "SELECT guild_id FROM server_config LIMIT 1")
            fallback_guild_id = int(guild_row["guild_id"]) if guild_row and guild_row.get("guild_id") else 0

            expeditions = await self._fetchall_if_table(
                db, "expedition_threads",
                """SELECT 'Expedition Journal' AS kind, e.guild_id AS guild_id, e.thread_id AS thread_id,
                          e.parent_channel_id AS parent_channel_id, c.name AS owner_name,
                          e.last_location AS detail, NULL AS status, e.updated_at AS updated_at
                   FROM expedition_threads e JOIN characters c ON c.user_id=e.user_id""",
            )
            households = await self._fetchall_if_table(
                db, "birth_family_household_threads",
                """SELECT 'Birth Family Household' AS kind, h.guild_id AS guild_id, h.thread_id AS thread_id,
                          h.parent_channel_id AS parent_channel_id, f.family_name AS owner_name,
                          NULL AS detail, NULL AS status, h.updated_at AS updated_at
                   FROM birth_family_household_threads h
                   LEFT JOIN birth_families f ON f.family_id=h.family_id""",
            )
            sect_abodes = await self._fetchall_if_table(
                db, "sect_abodes",
                """SELECT 'Sect Abode' AS kind, NULL AS guild_id, a.thread_id AS thread_id,
                          a.thread_channel_id AS parent_channel_id, c.name AS owner_name,
                          a.name AS detail, NULL AS status, a.updated_at AS updated_at
                   FROM sect_abodes a JOIN characters c ON c.user_id=a.user_id
                   WHERE a.thread_id IS NOT NULL""",
            )
            cave_abodes = await self._fetchall_if_table(
                db, "cave_abodes",
                """SELECT 'Cave Abode' AS kind, NULL AS guild_id, ca.thread_id AS thread_id,
                          ca.thread_channel_id AS parent_channel_id, c.name AS owner_name,
                          ca.name AS detail, NULL AS status, ca.updated_at AS updated_at
                   FROM cave_abodes ca JOIN characters c ON c.user_id=ca.user_id
                   WHERE ca.thread_id IS NOT NULL""",
            )
            events = await self._fetchall_if_table(
                db, "event_threads",
                """SELECT 'World Event Scene' AS kind, NULL AS guild_id, thread_id AS thread_id,
                          channel_id AS parent_channel_id, event_type AS owner_name,
                          title AS detail, (CASE WHEN active=1 THEN 'active' ELSE 'closed' END) AS status,
                          expires_at AS updated_at
                   FROM event_threads""",
            )
            battles = await self._fetchall_if_table(
                db, "battles",
                """SELECT 'Battle' AS kind, NULL AS guild_id, b.thread_id AS thread_id,
                          NULL AS parent_channel_id, c.name AS owner_name,
                          b.npc_name AS detail, b.status AS status, b.updated_at AS updated_at
                   FROM battles b JOIN characters c ON c.user_id=b.user_id
                   WHERE b.thread_id IS NOT NULL""",
            )
            rows = [*expeditions, *households, *sect_abodes, *cave_abodes, *events, *battles]
            for row in rows:
                guild_id = int(row["guild_id"]) if row.get("guild_id") else fallback_guild_id
                row["guild_id"] = guild_id or None
                row["jump_url"] = (
                    f"https://discord.com/channels/{guild_id}/{int(row['thread_id'])}" if guild_id else None
                )
            rows.sort(key=lambda r: float(r.get("updated_at") or 0), reverse=True)
            counts: dict[str, int] = {}
            for row in rows:
                counts[row["kind"]] = counts.get(row["kind"], 0) + 1
            return {
                "summary": {"total_threads": len(rows), "by_kind": counts},
                "threads": rows,
            }

    async def economy(self) -> dict[str, Any]:
        async with self._connect() as db:
            markets = await self._fetchall_if_table(
                db, "economy_markets",
                """SELECT * FROM economy_markets
                   ORDER BY ABS(price_index-1.0) DESC,demand DESC,supply ASC,location,item_id LIMIT 300""",
            )
            economy_events = await self._fetchall_if_table(
                db, "economy_events",
                "SELECT * FROM economy_events ORDER BY game_minute DESC,event_id DESC LIMIT 160",
            )
            auctions = await self._fetchall_if_table(
                db, "auctions",
                """SELECT a.*,seller.name AS seller_name,bidder.name AS bidder_name,
                          (SELECT COUNT(*) FROM auction_bids b WHERE b.auction_id=a.auction_id) AS bid_count
                   FROM auctions a JOIN characters seller ON seller.user_id=a.seller_user_id
                   LEFT JOIN characters bidder ON bidder.user_id=a.current_bidder_user_id
                   ORDER BY a.active DESC,a.ends_at DESC,a.auction_id DESC LIMIT 180""",
            )
            black_posts = await self._fetchall_if_table(
                db, "black_market_posts",
                "SELECT * FROM black_market_posts ORDER BY active DESC,heat DESC,world_name",
            )
            black_stock = await self._fetchall_if_table(
                db, "black_market_stock",
                "SELECT * FROM black_market_stock ORDER BY world_name,legal_status DESC,unit_price DESC LIMIT 250",
            )
            crimes = await self._fetchall_if_table(
                db, "crime_records",
                """SELECT cr.*,c.name AS player_name FROM crime_records cr JOIN characters c ON c.user_id=cr.user_id
                   ORDER BY (cr.status='open') DESC,cr.severity DESC,cr.created_game_minute DESC LIMIT 180""",
            )
            return {
                "summary": {
                    "markets": len(markets),
                    "active_auctions": sum(1 for r in auctions if int(r.get("active") or 0)),
                    "active_black_markets": sum(1 for r in black_posts if int(r.get("active") or 0)),
                    "open_crimes": sum(1 for r in crimes if str(r.get("status")) == "open"),
                },
                "markets": markets, "events": economy_events, "auctions": auctions,
                "black_market_posts": black_posts, "black_market_stock": black_stock, "crimes": crimes,
            }

    async def dynasties(self) -> dict[str, Any]:
        async with self._connect() as db:
            reincarnation = await self._fetchall_if_table(
                db, "reincarnation_state",
                """SELECT r.*,c.name AS current_name,c.discord_name FROM reincarnation_state r
                   LEFT JOIN characters c ON c.user_id=r.user_id
                   ORDER BY r.active DESC,r.death_game_minute DESC LIMIT 150""",
            )
            soul_legacy = await self._fetchall_if_table(
                db, "soul_legacy",
                """SELECT s.*,c.name AS player_name FROM soul_legacy s JOIN characters c ON c.user_id=s.user_id
                   ORDER BY s.incarnation_count DESC,s.legacy_points DESC,c.name LIMIT 150""",
            )
            history = await self._fetchall_if_table(
                db, "samsara_dynasty_history",
                """SELECT h.*,c.name AS player_name,c.discord_name FROM samsara_dynasty_history h
                   LEFT JOIN characters c ON c.user_id=h.user_id
                   ORDER BY h.created_game_minute DESC,h.history_id DESC LIMIT 250""",
            )
            leads = await self._fetchall_if_table(
                db, "samsara_ancestral_leads",
                """SELECT l.*,c.name AS player_name,h.source_family_name,h.destination_family_name
                   FROM samsara_ancestral_leads l LEFT JOIN characters c ON c.user_id=l.user_id
                   LEFT JOIN samsara_dynasty_history h ON h.history_id=l.history_id
                   ORDER BY CASE l.status WHEN 'discovered' THEN 0 WHEN 'active' THEN 0 WHEN 'hidden' THEN 2 ELSE 1 END,
                            l.danger DESC,l.created_game_minute DESC LIMIT 300""",
            )
            quests = await self._fetchall_if_table(
                db, "samsara_investigation_quests",
                """SELECT q.*,c.name AS player_name,l.name AS lead_name,l.location
                   FROM samsara_investigation_quests q LEFT JOIN characters c ON c.user_id=q.user_id
                   LEFT JOIN samsara_ancestral_leads l ON l.lead_id=q.lead_id
                   ORDER BY CASE q.status WHEN 'active' THEN 0 WHEN 'available' THEN 1 WHEN 'locked' THEN 3 ELSE 2 END,
                            q.created_game_minute DESC LIMIT 300""",
            )
            claims = await self._fetchall_if_table(
                db, "samsara_dynasty_claims",
                """SELECT cl.*,c.name AS player_name FROM samsara_dynasty_claims cl
                   LEFT JOIN characters c ON c.user_id=cl.user_id
                   ORDER BY CASE cl.status WHEN 'contested' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
                            cl.legitimacy DESC,cl.created_game_minute DESC LIMIT 220""",
            )
            conflicts = await self._fetchall_if_table(
                db, "samsara_dynasty_conflicts",
                """SELECT cf.*,c.name AS player_name,cl.dynasty_name,cl.claim_type
                   FROM samsara_dynasty_conflicts cf LEFT JOIN characters c ON c.user_id=cf.user_id
                   LEFT JOIN samsara_dynasty_claims cl ON cl.claim_id=cf.claim_id
                   ORDER BY (cf.status='active') DESC,cf.created_game_minute DESC LIMIT 200""",
            )
            return {
                "summary": {
                    "active_reincarnations": sum(1 for r in reincarnation if int(r.get("active") or 0)),
                    "dynasty_records": len(history),
                    "open_leads": sum(1 for r in leads if str(r.get("status")) not in {"resolved", "closed"}),
                    "active_quests": sum(1 for r in quests if str(r.get("status")) in {"active", "available"}),
                    "contested_claims": sum(1 for r in claims if str(r.get("status")) == "contested"),
                    "active_conflicts": sum(1 for r in conflicts if str(r.get("status")) == "active"),
                },
                "reincarnation": reincarnation, "soul_legacy": soul_legacy, "history": history,
                "leads": leads, "quests": quests, "claims": claims, "conflicts": conflicts,
            }

    async def rag(self, *, limit: int = 150, q: str = "", user_id: int | None = None, npc: str = "") -> dict[str, Any]:
        limit = max(1, min(400, int(limit)))
        clauses = ["1=1"]
        params: list[Any] = []
        if q.strip():
            needle = f"%{q.strip()}%"
            clauses.append("(r.summary LIKE ? COLLATE NOCASE OR r.memory_kind LIKE ? COLLATE NOCASE OR r.location LIKE ? COLLATE NOCASE OR r.npc_name LIKE ? COLLATE NOCASE OR r.faction LIKE ? COLLATE NOCASE)")
            params.extend([needle] * 5)
        if user_id is not None:
            clauses.append("r.user_id=?")
            params.append(int(user_id))
        if npc.strip():
            clauses.append("r.npc_name=?")
            params.append(npc.strip())
        params.append(limit)
        async with self._connect() as db:
            memories = await self._fetchall(
                db,
                """SELECT r.*,c.name AS player_name,c.discord_name FROM rag_memories r LEFT JOIN characters c ON c.user_id=r.user_id
                   WHERE """ + " AND ".join(clauses) + " ORDER BY r.salience DESC,r.game_minute DESC,r.memory_id DESC LIMIT ?",
                tuple(params),
            )
            canon_count = await self._scalar(db, "SELECT COUNT(*) FROM rag_canon_documents")
            history_count = await self._scalar(db, "SELECT COUNT(*) FROM world_history_events")
            players = await self._fetchall(db, "SELECT user_id,name,discord_name FROM characters ORDER BY name")
            return {"memories": memories, "canon_count": canon_count, "history_count": history_count, "players": players}

    async def decisions(self, *, limit: int = 150) -> dict[str, Any]:
        limit = max(1, min(400, int(limit)))
        async with self._connect() as db:
            minds = await self._fetchall(
                db,
                """SELECT m.*,c.current_location,c.faction,c.activity,c.realm_index,c.phase,c.status,l.health,l.injury,l.injury_severity,l.sect_rank
                   FROM npc_mind_state m JOIN npc_civilization_state c ON c.npc_name=m.npc_name
                   LEFT JOIN npc_life_state l ON l.npc_name=m.npc_name
                   ORDER BY m.last_game_minute DESC,m.goal_progress DESC LIMIT ?""",
                (limit,),
            )
            npc_history = await self._fetchall(
                db,
                """SELECT * FROM world_history_events
                   WHERE actor_type='npc' OR related_npc_name<>'' OR event_type IN ('marriage','descendant_birth','discipleship','major_battle','death','faction_change','sect_rank_change','breakthrough')
                   ORDER BY game_minute DESC,significance DESC,history_id DESC LIMIT ?""",
                (limit,),
            )
            sim = await self._fetchall(db, "SELECT * FROM world_simulation_state ORDER BY system")
            return {"minds": minds, "history": npc_history, "simulation": sim}


class AdminDashboardController:
    """Authenticated GM mutation surface backed by the authoritative Go engine."""

    ACTION_MAP = {
        "world.advance_time": "admin.world.advance_time",
        "player.grant_currency": "admin.player.grant_currency",
        "player.karma": "admin.player.karma",
        "player.fate": "admin.player.fate",
        "player.teleport": "admin.player.teleport",
        "player.revive": "admin.player.revive",
        "player.clear_battle": "admin.player.clear_battle",
        "automation.set": "admin.automation.set",
        "simulation.interval": "admin.simulation.interval",
        "player.set_realm": "admin.player.set_realm",
        "player.set_resource_caps": "admin.player.set_resource_caps",
        "player.adjust_item": "admin.player.adjust_item",
        "player.reset_cooldowns": "admin.player.reset_cooldowns",
        "player.force_end_scene": "admin.player.force_end_scene",
        "npc.relocate": "admin.npc.relocate",
        "world_event.end": "admin.world_event.end",
        "bulk.grant_currency": "admin.bulk.grant_currency",
        "bulk.reset_cooldowns": "admin.bulk.reset_cooldowns",
        "player.set_sect": "admin.player.set_sect",
        "player.set_realm_perfection": "admin.player.set_realm_perfection",
        "player.set_spiritual_root": "admin.player.set_spiritual_root",
        "player.set_bloodline": "admin.player.set_bloodline",
        "player.set_physique": "admin.player.set_physique",
        "player.set_tribulation": "admin.player.set_tribulation",
        "player.clear_condition": "admin.player.clear_condition",
        # Commissions (v0.22.0). review moves a definition between draft /
        # approved / retired; retire ends one player's held commission at no
        # cost to them. Both audit inside the engine transaction.
        "commission.review": "admin.commission.review",
        "commission.retire": "admin.commission.retire",
        # Quests (v0.24.0). review is the same status change as the commission
        # one - it always was - under a name that does not claim only
        # commissions have a lifecycle. save is the verb that did not exist:
        # it edits a definition and settles what happens to whoever is holding
        # it, in the engine, in one transaction.
        "quest.review": "admin.quest.review",
        "quest.save": "admin.quest.save",
        "player.force_reincarnation_ready": "admin.player.force_reincarnation_ready",
        "player.set_pill_toxicity": "admin.player.set_pill_toxicity",
        "player.set_beast_stats": "admin.player.set_beast_stats",
        "player.remove_equipment": "admin.player.remove_equipment",
        "player.set_abode_access": "admin.player.set_abode_access",
        "player.set_moderation": "admin.player.set_moderation",
        "audit.undo_last": "admin.audit.undo_last",
    }

    def __init__(self, store: ReadOnlyDashboardStore, engine_url: str, enabled: bool, actor_id: int = 1):
        self.store = store
        self.enabled = bool(enabled)
        self.engine_url = str(engine_url).strip().rstrip("/")
        self.engine = GameEngineClient(self.engine_url) if self.engine_url else None
        self.transport = GoDatabaseTransport(self.engine_url) if self.engine_url else None
        # Real, distinguishable ActorID for every dashboard-originated write and
        # audit entry - see DashboardSettings.dashboard_actor_id for why this
        # replaced the hardcoded 0 every call in this class used before.
        self.actor_id = int(actor_id)
        self._item_catalog: dict[str, str] | None = None

    def item_catalog(self) -> dict[str, str]:
        """item_id -> display name from content/world.json, loaded once.

        The dashboard is a separate process from the bot and reads the content
        pack straight from disk (as snapshot() does for locations) rather than
        importing app.rules.game.World, which drags the whole rules tier into
        the dashboard for one lookup table.
        """
        if self._item_catalog is None:
            try:
                world = json.loads((ROOT / "content" / "world.json").read_text(encoding="utf-8"))
                items = dict(world.get("items") or {})
            except Exception:
                log.warning("Could not load content/world.json for the item catalog", exc_info=True)
                items = {}
            self._item_catalog = {
                str(item_id): str((item or {}).get("name") or item_id) for item_id, item in items.items()
            }
        return self._item_catalog

    def resolve_item_id(self, raw: Any) -> str:
        """Turn whatever was typed into the Adjust Inventory card into a catalog item id.

        The card is a free-text field and the Go engine's adjust_item is a plain
        signed delta that stores whatever string it is handed, so before this
        guard "Bugslayer Sword" (the display name) became an inventory row that
        no catalog lookup - bind, the market, the inventory description - could
        ever match. Accept the exact id, then the id or display name
        case-insensitively; refuse anything else with the closest ids so the GM
        can correct the field instead of the database.
        """
        typed = str(raw or "").strip()
        if not typed:
            raise ValueError("item_id is required")
        catalog = self.item_catalog()
        if typed in catalog:
            return typed
        needle = typed.casefold()
        for item_id, name in catalog.items():
            if needle in (item_id.casefold(), name.casefold()):
                return item_id
        tokens = [t for t in needle.replace("_", " ").split() if t]
        suggestions = sorted(
            item_id for item_id, name in catalog.items()
            if any(t in item_id.casefold() or t in name.casefold() for t in tokens)
        )[:5]
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        raise ValueError(f"Unknown item {typed!r}: not an item id or item name in content/world.json.{hint}")

    async def _adjust_item_target(self, payload: dict[str, Any]) -> str:
        """The item id an Adjust Inventory request should reach the engine with.

        Grants must name a catalog item (resolve_item_id). Removals are how a
        bad grant is undone, and the rows a GM most needs to remove are exactly
        the ones the resolver would now redirect - `Bugslayer Sword` resolves
        to `bugslayer_sword`, which is not the row that is wrong. So for a
        negative delta, a row that exists under the typed string as-is wins;
        otherwise the resolver applies as for a grant.
        """
        typed = str(payload.get("item_id") or "").strip()
        try:
            delta = int(payload.get("quantity") or 0)
        except (TypeError, ValueError):
            delta = 0
        if delta < 0 and typed:
            try:
                user_id = int(payload.get("user_id") or 0)
                async with self.store._connect() as db:
                    row = await self.store._fetchone(
                        db, "SELECT 1 AS present FROM inventory WHERE user_id=? AND item_id=?", (user_id, typed)
                    )
            except Exception:
                log.warning("Could not check inventory for a raw item_id removal", exc_info=True)
                row = None
            if row:
                return typed
        return self.resolve_item_id(typed)

    async def snapshot(self) -> dict[str, Any]:
        if not self.enabled or self.engine is None or self.transport is None:
            return {"enabled": False, "message": "Dashboard admin writes are disabled."}
        async with self.store._connect() as db:
            players = await self.store._fetchall(
                db,
                "SELECT user_id,name,discord_name,life_status,location,realm_index,phase,karma_score,vitality,vitality_max,qi,qi_max,is_muted,is_frozen,is_banned,muted_until,frozen_until,moderation_reason FROM characters ORDER BY name",
            )
            locations = [str(r["name"]) for r in await self.store._fetchall(db, "SELECT name FROM catalog_locations ORDER BY name")]
            if not locations:
                try:
                    world = json.loads((ROOT / "content" / "world.json").read_text(encoding="utf-8"))
                    locations = sorted(str(name) for name in (world.get("locations") or {}).keys())
                except Exception:
                    locations = []
            simulations = await self.store._fetchall(db, "SELECT system,last_game_minute,interval_game_minutes,runs FROM world_simulation_state ORDER BY system")
            audit = await self.store._fetchall(db, "SELECT * FROM admin_audit_log ORDER BY audit_id DESC LIMIT 40")
            automation = {
                "npc_civilization": True,
                "npc_life": True,
                "sect_politics": True,
                "dynamic_economy": True,
                "clan_dynamics": True,
                "background_seclusion": True,
                "black_markets": True,
                "autonomous_world_events": True,
                "merchants": True,
            }
            row = await self.store._fetchone(db, "SELECT value_json FROM world_state WHERE key='automation_settings'")
            if row:
                try:
                    stored = json.loads(str(row.get("value_json") or "{}"))
                    automation = {k: bool(stored.get(k, v)) for k, v in automation.items()}
                except Exception:
                    pass
            clock = await self.store._world_clock(db)
            currencies = [str(r["currency_id"]) for r in await self.store._fetchall(db, "SELECT DISTINCT currency_id FROM currency_wallets ORDER BY currency_id")]
            # NPC names + active world events, so the NPC/world-state admin controls
            # below can offer real dropdowns instead of free-text fields the GM has
            # to get exactly right.
            npc_names = [str(r["npc_name"]) for r in await self.store._fetchall(db, "SELECT npc_name FROM npc_civilization_state WHERE status='alive' ORDER BY npc_name")]
            active_events = await self.store._fetchall(db, "SELECT event_key,title FROM world_events WHERE active=1 ORDER BY title")
            sects = [str(r["sect_name"]) for r in await self.store._fetchall(db, "SELECT sect_name FROM sect_politics_state ORDER BY sect_name")]
            if not sects:
                try:
                    world = json.loads((ROOT / "content" / "world.json").read_text(encoding="utf-8"))
                    sects = sorted(str(name) for name in (world.get("sects") or {}).keys())
                except Exception:
                    sects = []
        for currency in ("low_spirit_stone", "low_spirit_crystal", "low_immortal_stone", "low_celestial_crystal"):
            if currency not in currencies:
                currencies.append(currency)
        return {
            "enabled": True,
            "players": players,
            "locations": locations,
            "currencies": sorted(currencies),
            "simulations": simulations,
            "automation": automation,
            "audit": audit,
            "clock": clock,
            "npcs": npc_names,
            "active_events": active_events,
            "sects": sects,
            "backups": await self.transport.list_backups(),
            "database": await self.transport.status(),
        }

    async def _audit(self, *, action: str, target: str = "", after: dict[str, Any] | None = None, reason: str = "") -> None:
        if self.engine is None:
            return
        await self.engine.action("admin.audit", self.actor_id, {"action": action, "target": target, "after": after or {}, "reason": reason})

    async def run(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled or self.engine is None or self.transport is None:
            raise PermissionError("Dashboard admin writes are disabled")
        action = str(action).strip()
        payload = dict(payload or {})
        reason = str(payload.get("reason") or "GM dashboard")[:500]
        if action == "player.adjust_item":
            payload["item_id"] = await self._adjust_item_target(payload)
        if action == "quest.save":
            saved, notes = await self._validated_quest(payload)
            result = await self.engine.action(self.ACTION_MAP[action], self.actor_id, saved)
            if notes:
                # Non-fatal: a reward trimmed to the budget, a count clamped.
                # The save happened; the GM is told what was changed on the way.
                result = {**dict(result or {}), "validation_notes": notes}
            return {"ok": True, "action": action, "result": result}
        if action in self.ACTION_MAP:
            result = await self.engine.action(self.ACTION_MAP[action], self.actor_id, payload)
            return {"ok": True, "action": action, "result": result}
        if action == "simulation.force":
            system = str(payload.get("system") or "").strip()
            steps = max(1, min(120, int(payload.get("steps") or 1)))
            async with self.store._connect() as db:
                clock = await self.store._world_clock(db)
            result = await self.engine.force_simulation(system, steps, int(clock["game_minute"]))
            await self._audit(action="dashboard.simulation.force", target=system, after={"steps": steps, "result": result}, reason=reason)
            return {"ok": True, "action": action, "result": result}
        if action == "backup.create":
            result = await self.transport.create_backup()
            await self._audit(action="dashboard.backup.create", target=str(result.get("name") or "backup"), after=result, reason=reason)
            return {"ok": True, "action": action, "result": result}
        if action == "backup.restore":
            name = str(payload.get("name") or "").strip()
            if not name:
                raise ValueError("backup.restore requires a name")
            result = await self.transport.restore_backup(name)
            await self._audit(action="dashboard.backup.restore", target=name, after=result, reason=reason)
            return {"ok": True, "action": action, "result": result}
        if action in {"database.optimize", "database.vacuum"}:
            maintenance = "optimize" if action.endswith("optimize") else "vacuum"
            result = await self.transport.maintenance(maintenance)
            await self._audit(action=f"dashboard.{action}", target="sqlite", after=result, reason=reason)
            return {"ok": True, "action": action, "result": result}
        raise ValueError(f"Unsupported dashboard admin action: {action}")

    async def _validated_quest(self, payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Hold a hand-edited quest to the same rules a forged one meets.

        `validate_quest_definition` is the Forge's own gate: it is what refuses
        a drafted quest that names an NPC the world does not have or pays more
        than the budget. An edit made in the dashboard goes through the same
        function, so the two ways of authoring cannot disagree about what a
        valid quest is - which was half of why forged drafts and commissions
        ended up on separate screens with separate rules in the first place.

        The engine still owns what happens to players; this owns only the
        question of whether the content is sayable at all.
        """
        draft = dict(payload)
        title = str(draft.get("title") or "").strip()
        quest_key = str(draft.get("quest_key") or "").strip()
        if not quest_key:
            if not title:
                raise ValueError("A quest needs a title before it can be saved.")
            # A new quest derives its key from its title, and must not land on
            # a key that already exists: `admin.quest.save` treats a known key
            # as an edit, so a collision would silently overwrite a different
            # quest that happened to be called something similar.
            quest_key = quest_key_for(title, await self._existing_quest_keys(), prefix="quest")
        definition, errors = validate_quest_definition(draft, self.store.quest_world(), self.store.quest_budget())
        if definition is None:
            raise ValueError("This quest cannot be saved yet:\n- " + "\n- ".join(errors[:8]))
        saved = {
            "quest_key": quest_key,
            "title": definition["title"],
            "description": definition["description"],
            "objectives": definition["objectives"],
            "rewards": definition["rewards"],
            "giver_npc": str(draft.get("giver_npc") or "").strip(),
            "realm_band": str(draft.get("realm_band") or "").strip(),
            "tier": max(1, int(draft.get("tier") or 1)),
            "deadline_game_minutes": max(0, int(draft.get("deadline_game_minutes") or 0)),
            "variants": list(draft.get("variants") or []),
            "requires_sect": str(draft.get("requires_sect") or "").strip(),
            "reward_visibility": str(draft.get("reward_visibility") or "shown").strip().lower(),
            "boast": str(draft.get("boast") or "").strip(),
            "hold_policy": str(draft.get("hold_policy") or "keep").strip().lower(),
            "reason": str(draft.get("reason") or "GM dashboard")[:500],
        }
        if draft.get("owner_user_id") not in (None, ""):
            saved["owner_user_id"] = int(draft["owner_user_id"])
        return saved, errors[:8]

    async def _existing_quest_keys(self) -> set[str]:
        async with self.store._connect() as db:
            rows = await self.store._fetchall_if_table(db, "quest_definitions", "SELECT quest_key FROM quest_definitions")
        return {str(row.get("quest_key")) for row in rows}


class DiscordDashboardController:
    """Proxy Discord provisioning requests to the connected Python bot process."""

    def __init__(self, base_url: str, token: str, enabled: bool, actor_id: int = 1) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.token = str(token or "")
        self.enabled = bool(enabled)
        # The same distinguishable id the engine writes into admin_audit_log for
        # dashboard-originated actions. Forging a quest is audited on the bot
        # side (app/bot/admin/quest_control.py) and the browser cannot be
        # trusted to say who it is, so the id is stamped here.
        self.actor_id = int(actor_id)

    async def _request(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.base_url:
            raise RuntimeError("BOT_CONTROL_URL is not configured")
        if not self.token:
            raise RuntimeError("BOT_CONTROL_TOKEN/DASHBOARD_TOKEN is not configured")
        body = json.dumps({"action": action, "payload": payload or {}}, separators=(",", ":")).encode("utf-8")
        req = Request(
            self.base_url + "/control/discord",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Xianxia-Control": self.token,
            },
        )

        def do_request() -> dict[str, Any]:
            try:
                with urlopen(req, timeout=15) as response:
                    raw = response.read()
            except HTTPError as exc:
                raw = exc.read()
                try:
                    detail = json.loads(raw.decode("utf-8"))
                    message = detail.get("message") or detail.get("error") or str(exc)
                except Exception:
                    message = raw.decode("utf-8", "replace")[:300] or str(exc)
                raise RuntimeError(f"Discord bot control rejected request: {message}") from exc
            except URLError as exc:
                raise RuntimeError(f"Discord bot control is unavailable: {exc.reason}") from exc
            return dict(json.loads(raw.decode("utf-8")))

        return await asyncio.to_thread(do_request)

    async def ai_routing(self) -> dict[str, Any]:
        """Narration routing as the bot process sees it.

        Read-only, and it has to come through the bot: the router's chains,
        health counters and audit verdicts live in that process's memory, not
        in SQLite. Nothing here changes state, so there is no admin_audit_log
        row to write.
        """
        try:
            response = await self._request("ai_routing")
            result = dict(response.get("result") or {})
            result["control_available"] = True
            return result
        except Exception as exc:
            return {"control_available": False, "message": str(exc)}

    async def snapshot(self) -> dict[str, Any]:
        try:
            response = await self._request("status")
            result = dict(response.get("result") or {})
            result["control_available"] = True
            result["admin_writes"] = self.enabled
            return result
        except Exception as exc:
            return {
                "connected": False,
                "control_available": False,
                "admin_writes": self.enabled,
                "message": str(exc),
            }

    async def run_readonly(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Ask the bot something without claiming admin-write permission.

        The narration panel reads the live router and OpenRouter's catalogue on
        every open. Neither mutates anything, so gating them behind
        DASHBOARD_ADMIN_WRITES would blank the panel for a read-only GM and
        teach nobody anything.
        """
        return await self._request(action, dict(payload or {}))

    async def run(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise PermissionError("Dashboard admin writes are disabled")
        payload = dict(payload or {})
        if str(action).startswith("quest."):
            # Quest authoring is the one thing on this channel that writes a
            # row and audits it (app/bot/admin/quest_control.py), so it needs to
            # know who asked. Discord layout actions do not, and adding a field
            # to their payloads would change a contract they already have.
            payload["admin_user_id"] = self.actor_id
        return await self._request(action, payload)


class NarrationDashboardController:
    """The GM's view of, and control over, the narration chain.

    Reads go to the bot process, because that is where the router lives and
    where the OpenRouter key is; the WRITE goes to the engine, because that is
    what makes a GM choice durable and audited. The dashboard never edits the
    chain in its own memory - it has no router to edit - so the order is
    always: engine accepts and audits, then the bot is told to re-read.

    A failed poke is reported, not raised. The choice is already stored and
    will be applied at the next restart either way, and telling a GM "that
    did not work" when it did is worse than telling them it is not live yet.
    """

    def __init__(self, control: "DiscordDashboardController", engine_url: str, enabled: bool, actor_id: int = 1) -> None:
        self.control = control
        self.enabled = bool(enabled)
        self.engine_url = str(engine_url or "").strip().rstrip("/")
        self.engine = GameEngineClient(self.engine_url) if self.engine_url else None
        self.actor_id = int(actor_id)

    async def snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "admin_writes": self.enabled,
            "slots": {},
            "status": {},
            "catalogue": {"models": []},
            "connected": False,
        }
        try:
            status = await self.control.run_readonly("narration.status", {})
            out["slots"] = dict((status.get("result") or {}).get("slots") or {})
            out["status"] = dict((status.get("result") or {}).get("status") or {})
            out["connected"] = True
        except Exception as exc:
            out["message"] = str(exc)[:300]
            return out
        try:
            catalogue = await self.control.run_readonly("narration.catalogue", {})
            out["catalogue"] = dict(catalogue.get("result") or {})
        except Exception as exc:
            # The picker degrades to "keep what you have": the chain and its
            # probe verdicts are still worth rendering without a catalogue.
            out["catalogue"] = {"models": [], "error": str(exc)[:300]}
        return out

    async def run(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise PermissionError("Dashboard admin writes are disabled")
        action = str(action or "").strip().lower()
        if action == "narration.refresh_catalogue":
            return await self.control.run_readonly("narration.catalogue", {"refresh": True})
        if action != "narration.set_chain":
            raise ValueError(f"Unsupported narration action: {action}")
        if self.engine is None:
            raise RuntimeError("GAME_ENGINE_URL is not configured")
        slots = {str(k): str(v or "") for k, v in dict(payload.get("slots") or {}).items()}
        if not slots:
            raise ValueError("Choose at least one route to change")
        stored = await self.engine.action(
            "admin.narration.set_chain",
            self.actor_id,
            {"slots": slots, "reason": str(payload.get("reason") or "")},
        )
        applied: dict[str, Any]
        try:
            applied = await self.control.run_readonly("narration.apply", {})
        except Exception as exc:
            applied = {"ok": False, "result": {"applied": False, "reason": str(exc)[:300]}}
        return {
            "ok": True,
            "stored": stored,
            "applied": bool(applied.get("ok")),
            "apply_detail": applied.get("result") or {},
        }


class DashboardServer:
    def __init__(self, settings: DashboardSettings):
        self.settings = settings
        self.store = ReadOnlyDashboardStore(settings.database_path)
        self.admin = AdminDashboardController(self.store, settings.engine_url or os.getenv("GAME_ENGINE_URL", ""), settings.admin_writes, settings.dashboard_actor_id)
        self.discord = DiscordDashboardController(settings.bot_control_url, settings.bot_control_token, settings.admin_writes, settings.dashboard_actor_id)
        self.narration = NarrationDashboardController(
            self.discord,
            settings.engine_url or os.getenv("GAME_ENGINE_URL", ""),
            settings.admin_writes,
            settings.dashboard_actor_id,
        )
        # Bounds for the pre-auth request head. See app/ops/http_limits.py: a per-line
        # timeout that resets on every line is not a limit, it is an invitation.
        self.header_limits = HeaderLimits(
            max_request_line_bytes=settings.max_request_line_bytes,
            max_header_lines=settings.max_header_lines,
            max_header_bytes=settings.max_header_bytes,
            header_deadline_seconds=settings.header_deadline_seconds,
            line_timeout_seconds=settings.header_line_timeout_seconds,
        ).validated()
        self.connections = ConnectionLimiter(settings.max_connections)
        self.logins = LoginThrottle(
            max_failures=settings.login_max_failures,
            window_seconds=settings.login_window_seconds,
            lockout_seconds=settings.login_lockout_seconds,
        )
        self.rejected_heads = 0
        self._server: asyncio.AbstractServer | None = None

    def _authorized(self, headers: dict[str, str]) -> bool:
        value = headers.get("authorization", "")
        if not value.lower().startswith("basic "):
            return False
        try:
            raw = base64.b64decode(value.split(" ", 1)[1].strip(), validate=True).decode("utf-8")
            username, password = raw.split(":", 1)
        except Exception:
            return False
        return hmac.compare_digest(username, self.settings.username) and hmac.compare_digest(password, self.settings.token)

    @staticmethod
    def _peer(writer: asyncio.StreamWriter) -> str:
        peer = writer.get_extra_info("peername")
        if isinstance(peer, (tuple, list)) and peer:
            return str(peer[0])
        return str(peer or "unknown")

    async def serve(self) -> None:
        if self.store._go_transport is None and not self.settings.database_path.exists():
            raise RuntimeError(f"Dashboard database does not exist: {self.settings.database_path}")
        self._server = await asyncio.start_server(
            self._handle, self.settings.host, self.settings.port, limit=STREAM_LIMIT
        )
        addrs = ", ".join(str(sock.getsockname()) for sock in (self._server.sockets or []))
        log.info("Xianxia RP GM dashboard listening on %s", addrs)
        async with self._server:
            await self._server.serve_forever()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # This is the only network-facing listener in the stack, and everything
        # up to _authorized() below runs for an anonymous peer - so the request
        # head has to be bounded before anything else happens.
        if not self.connections.try_acquire():
            try:
                await self._send_json(writer, 503, {"error": "too_many_connections"})
            except Exception:
                pass
            try:
                writer.close()
            except Exception:
                pass
            return
        try:
            try:
                head = await read_request_head(reader, limits=self.header_limits)
            except EmptyRequest:
                return
            except RequestHeadRejected as rejected:
                self.rejected_heads += 1
                log.warning(
                    "DASHBOARD_HEAD_REJECTED status=%s error=%s detail=%s",
                    rejected.status, rejected.error, rejected.detail,
                )
                await self._send_json(writer, rejected.status, {"error": rejected.error})
                return
            headers = head.headers
            try:
                method, target, _version = head.request_line.split(" ", 2)
            except ValueError:
                await self._send_json(writer, 400, {"error": "bad_request"})
                return

            split = urlsplit(target)
            path = unquote(split.path)
            query = parse_qs(split.query, keep_blank_values=False)

            if path == "/livez":
                await self._send_json(writer, 200, {"ok": True, "service": "xianxia-rp-dashboard"})
                return

            if method not in {"GET", "POST"}:
                await self._send_json(writer, 405, {"error": "method_not_allowed"}, extra_headers={"Allow": "GET, POST"})
                return

            # The lock is consulted before the credentials are read, so a locked
            # address learns nothing from its next guess; and it is keyed by the
            # socket peer, never a forwarded-for header an unknown peer could set.
            peer = self._peer(writer)
            retry_after = self.logins.locked_for(peer)
            if retry_after > 0:
                wait = int(retry_after) + 1
                await self._send_json(
                    writer, 429, {"error": "login_locked", "retry_after_seconds": wait},
                    extra_headers={"Retry-After": str(wait)},
                )
                return

            if not self._authorized(headers):
                if self.logins.failure(peer):
                    log.warning("DASHBOARD_LOGIN_LOCKED peer=%s after %d failures", peer, self.logins.max_failures)
                await self._send_text(
                    writer,
                    401,
                    "Authentication required.\n",
                    "text/plain; charset=utf-8",
                    extra_headers={"WWW-Authenticate": 'Basic realm="Xianxia RP GM Dashboard", charset="UTF-8"'},
                )
                return
            self.logins.success(peer)

            if method == "POST":
                if path not in {"/api/admin/action", "/api/discord/action", "/api/narration/action"}:
                    await self._send_json(writer, 404, {"error": "not_found"}); return
                if not self.settings.admin_writes:
                    await self._send_json(writer, 403, {"error": "admin_writes_disabled"}); return
                if not origin_allowed(headers, self.settings.allowed_origins):
                    await self._send_json(writer, 403, {"error": "origin_mismatch"}); return
                if headers.get("x-xianxia-admin") != "1" or "application/json" not in headers.get("content-type", "").lower():
                    await self._send_json(writer, 403, {"error": "admin_header_required"}); return
                try:
                    length = int(headers.get("content-length", "0") or 0)
                except ValueError:
                    await self._send_json(writer, 400, {"error": "invalid_content_length"}); return
                if length < 2 or length > 65536:
                    await self._send_json(writer, 400, {"error": "invalid_body_size"}); return
                raw = await asyncio.wait_for(reader.readexactly(length), timeout=5)
                try:
                    body = json.loads(raw.decode("utf-8"))
                    action = str(body.get("action") or "")
                    payload = dict(body.get("payload") or {})
                    if path == "/api/discord/action":
                        result = await self.discord.run(action, payload)
                    elif path == "/api/narration/action":
                        result = await self.narration.run(action, payload)
                    else:
                        result = await self.admin.run(action, payload)
                except PermissionError as exc:
                    await self._send_json(writer, 403, {"error": "forbidden", "message": str(exc)}); return
                except (ValueError, TypeError, RuntimeError, GameEngineError, RemoteDatabaseError) as exc:
                    await self._send_json(writer, 400, {"error": "admin_action_failed", "message": str(exc)}); return
                await self._send_json(writer, 200, result); return

            if path == "/" or path == "/index.html":
                await self._send_file(writer, STATIC_DIR / "index.html", "text/html; charset=utf-8")
                return
            if path == "/app.js":
                await self._send_file(writer, STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
                return
            if path == "/styles.css":
                await self._send_file(writer, STATIC_DIR / "styles.css", "text/css; charset=utf-8")
                return
            if path == "/api/overview":
                await self._send_json(writer, 200, await self.store.overview()); return
            if path == "/api/capabilities":
                await self._send_json(writer, 200, await self.store.capabilities()); return
            if path == "/api/timeline":
                await self._send_json(writer, 200, await self.store.timeline(
                    limit=_qint(query, "limit", 100), q=_q(query, "q"), event_type=_q(query, "event_type"), visibility=_q(query, "visibility")
                )); return
            if path == "/api/npcs":
                await self._send_json(writer, 200, await self.store.npcs(
                    limit=_qint(query, "limit", 200), q=_q(query, "q"), location=_q(query, "location"), faction=_q(query, "faction"), status=_q(query, "status", "alive")
                )); return
            if path == "/api/npc":
                name = _q(query, "name")
                await self._send_json(writer, 200 if name else 400, await self.store.npc_detail(name) if name else {"error": "name_required"}); return
            if path == "/api/families":
                await self._send_json(writer, 200, await self.store.families()); return
            if path == "/api/sects":
                await self._send_json(writer, 200, await self.store.sects()); return
            if path == "/api/conflicts":
                await self._send_json(writer, 200, await self.store.conflicts()); return
            if path == "/api/events":
                await self._send_json(writer, 200, await self.store.events()); return
            if path == "/api/players":
                await self._send_json(writer, 200, await self.store.players(limit=_qint(query, "limit", 200))); return
            if path == "/api/player":
                user_id = _qint(query, "user_id", 0)
                await self._send_json(writer, 200 if user_id else 400, await self.store.player_detail(user_id) if user_id else {"error": "user_id_required"}); return
            if path == "/api/cultivation":
                await self._send_json(writer, 200, await self.store.cultivation()); return
            if path == "/api/crafting":
                await self._send_json(writer, 200, await self.store.crafting()); return
            if path == "/api/exploration":
                await self._send_json(writer, 200, await self.store.exploration()); return
            if path == "/api/commissions":
                await self._send_json(writer, 200, await self.store.commissions()); return
            if path == "/api/quests":
                await self._send_json(writer, 200, await self.store.quests()); return
            if path == "/api/threads":
                await self._send_json(writer, 200, await self.store.threads()); return
            if path == "/api/economy":
                await self._send_json(writer, 200, await self.store.economy()); return
            if path == "/api/dynasties":
                await self._send_json(writer, 200, await self.store.dynasties()); return
            if path == "/api/party":
                await self._send_json(writer, 200, await self.store.party()); return
            if path == "/api/pvp":
                await self._send_json(writer, 200, await self.store.pvp()); return
            if path == "/api/conditions":
                await self._send_json(writer, 200, await self.store.conditions()); return
            if path == "/api/rag":
                uid_raw = _q(query, "user_id")
                uid = int(uid_raw) if uid_raw.isdigit() else None
                await self._send_json(writer, 200, await self.store.rag(limit=_qint(query, "limit", 150), q=_q(query, "q"), user_id=uid, npc=_q(query, "npc"))); return
            if path == "/api/decisions":
                await self._send_json(writer, 200, await self.store.decisions(limit=_qint(query, "limit", 150))); return
            if path == "/api/admin":
                await self._send_json(writer, 200, await self.admin.snapshot()); return
            if path == "/api/discord":
                await self._send_json(writer, 200, await self.discord.snapshot()); return
            if path == "/api/ai_routing":
                await self._send_json(writer, 200, await self.discord.ai_routing()); return
            if path == "/api/narration":
                await self._send_json(writer, 200, await self.narration.snapshot()); return
            if path == "/api/health":
                await self._send_json(writer, 200, {"ok": True, "schema_version": await self.store.schema_version(), "database": str(self.settings.database_path), "admin_writes": self.settings.admin_writes}); return
            await self._send_json(writer, 404, {"error": "not_found"})
        except (asyncio.TimeoutError, ConnectionError):
            pass
        except Exception as exc:
            log.exception("Dashboard request failed")
            try:
                await self._send_json(writer, 500, {"error": "internal_error", "message": str(exc)[:300]})
            except Exception:
                pass
        finally:
            self.connections.release()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _send_file(self, writer: asyncio.StreamWriter, path: Path, content_type: str) -> None:
        if not path.exists() or not path.is_file():
            await self._send_json(writer, 404, {"error": "not_found"}); return
        body = path.read_bytes()
        await self._send_bytes(writer, 200, body, content_type)

    async def _send_json(self, writer: asyncio.StreamWriter, status: int, payload: Any, *, extra_headers: dict[str, str] | None = None) -> None:
        # Every dashboard JSON response funnels through here, so this is the one
        # place snowflake precision has to be protected (see json_safe_numbers).
        body = (json.dumps(json_safe_numbers(payload), ensure_ascii=False, separators=(",", ":"), default=str) + "\n").encode("utf-8")
        await self._send_bytes(writer, status, body, "application/json; charset=utf-8", extra_headers=extra_headers)

    async def _send_text(self, writer: asyncio.StreamWriter, status: int, text: str, content_type: str, *, extra_headers: dict[str, str] | None = None) -> None:
        await self._send_bytes(writer, status, text.encode("utf-8"), content_type, extra_headers=extra_headers)

    @staticmethod
    async def _send_bytes(writer: asyncio.StreamWriter, status: int, body: bytes, content_type: str, *, extra_headers: dict[str, str] | None = None) -> None:
        # 408/414/431/503 come from the request-head limiter and the connection
        # cap; without them a rejected request went out as "HTTP/1.1 431 OK".
        reason = {200:"OK",400:"Bad Request",401:"Unauthorized",403:"Forbidden",404:"Not Found",405:"Method Not Allowed",408:"Request Timeout",414:"URI Too Long",431:"Request Header Fields Too Large",500:"Internal Server Error",503:"Service Unavailable"}.get(status,"OK")
        headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            "Cache-Control": "no-store",
            "Connection": "close",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        }
        if extra_headers:
            headers.update(extra_headers)
        raw = [f"HTTP/1.1 {status} {reason}\r\n"] + [f"{k}: {v}\r\n" for k,v in headers.items()] + ["\r\n"]
        writer.write("".join(raw).encode("latin-1") + body)
        await writer.drain()


def _q(query: dict[str, list[str]], key: str, default: str = "") -> str:
    values = query.get(key)
    return str(values[0]) if values else default


def _qint(query: dict[str, list[str]], key: str, default: int) -> int:
    raw = _q(query, key)
    try:
        return int(raw) if raw else int(default)
    except ValueError:
        return int(default)


async def _main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = DashboardSettings.from_env()
    server = DashboardServer(settings)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(_main())
