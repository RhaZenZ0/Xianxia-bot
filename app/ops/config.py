from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, default: int, *, name: str = "value") -> int:
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def _as_float(value: str | None, default: float, *, name: str = "value") -> float:
    if value is None or value.strip() == "":
        return float(default)
    try:
        return float(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be numeric") from exc



def _typed_play_prefix(value: str | None) -> str:
    """The one character that marks a typed line as an action to resolve.

    Default ``>``: one keystroke, conventional for roleplay bots, and Discord
    renders ``> text`` as a quote block so actions look different from speech.
    Exactly one character, and not a letter, digit or whitespace - anything
    else would swallow ordinary speech ("i explore" must never be an action
    because someone set the prefix to "i").
    """
    prefix = (value if value is not None else "$").strip("\r\n")
    if prefix == "":
        prefix = "$"
    if len(prefix) != 1 or prefix.isalnum() or prefix.isspace():
        raise ValueError("TYPED_PLAY_PREFIX must be exactly one non-alphanumeric, non-space character")
    return prefix


def _as_int_set(value: str | None, *, name: str = "value") -> set[int]:
    if not value:
        return set()
    try:
        return {int(part.strip()) for part in value.split(",") if part.strip()}
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a comma-separated list of Discord channel IDs") from exc


@dataclass(frozen=True)
class Settings:
    discord_token: str
    guild_id: int
    narrator_provider: str
    narrator_context_max_chars: int
    rag_context_cache_seconds: float
    rag_canon_cache_seconds: float
    openai_api_key: str | None
    openai_model: str
    openrouter_api_key: str | None
    openrouter_base_url: str
    openrouter_routine_model: str
    openrouter_routine_fallback_model: str
    openrouter_epic_model: str
    openrouter_epic_fallback_model: str
    openrouter_dynamic_free_model: str
    google_ai_studio_api_key: str | None
    google_ai_studio_model: str
    openrouter_require_free: bool
    openrouter_max_requests_per_minute: int
    openrouter_max_requests_per_day: int
    openrouter_route_requests_per_minute: int
    openrouter_route_requests_per_day: int
    openrouter_timeout_seconds: float
    openrouter_epic_timeout_seconds: float
    openrouter_failure_cooldown_seconds: float
    openrouter_disable_reasoning: bool
    openrouter_app_url: str
    openrouter_app_name: str
    rp_channel_ids: set[int]
    message_content_intent: bool
    monitor_max_messages: int
    monitor_lookback_hours: int
    monitor_chunk_chars: int
    monitor_max_chunks: int
    auto_narrate: bool
    auto_narrate_event_threads: bool
    event_thread_auto_archive_minutes: int
    cultivate_cooldown_minutes: int
    explore_cooldown_minutes: int
    hunt_cooldown_minutes: int
    perfect_quest_cooldown_minutes: int
    perfect_trial_cooldown_minutes: int
    secret_realm_cooldown_minutes: int
    unexpected_event_chance_percent: int
    world_time_scale: int
    reincarnation_base_samsara_years: int
    reincarnation_max_wait_seconds: int
    database_path: Path
    health_host: str
    http_max_request_line_bytes: int
    http_max_header_lines: int
    http_max_header_bytes: int
    http_header_deadline_seconds: float
    http_header_line_timeout_seconds: float
    http_max_connections: int
    health_port: int
    slow_query_ms: float
    alert_webhook_url: str | None
    alert_cooldown_seconds: int
    game_engine_url: str
    game_engine_timeout_seconds: float
    game_engine_auth_token: str
    update_check_enabled: bool
    update_channel: str
    update_repository: str
    update_check_hours: int
    quest_forge_auto: bool
    quest_forge_min_significance: int
    quest_forge_interval_hours: int
    quest_reward_max_xp: int
    quest_reward_max_stones: int
    quest_reward_max_items: int
    typed_play_prefix: str
    typed_play_burst: int
    typed_play_per_minute: float
    typed_play_hint: bool

    @classmethod
    def from_env(cls) -> "Settings":
        discord_token = os.getenv("DISCORD_TOKEN", "").strip()
        guild_id_raw = os.getenv("GUILD_ID", "").strip()
        if not discord_token:
            raise RuntimeError("DISCORD_TOKEN is missing from .env")
        if not guild_id_raw:
            raise RuntimeError("GUILD_ID is missing from .env")

        openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip() or None
        default_provider = "openrouter" if openrouter_key else "procedural"
        narrator_provider = os.getenv("NARRATOR_PROVIDER", default_provider).strip().lower() or default_provider
        if narrator_provider not in {"openrouter", "openai", "procedural", "disabled"}:
            raise RuntimeError("NARRATOR_PROVIDER must be one of: openrouter, openai, procedural, disabled")
        if narrator_provider == "openrouter" and not openrouter_key:
            raise RuntimeError("OPENROUTER_API_KEY is required when NARRATOR_PROVIDER=openrouter")
        narrator_context_max_chars = _as_int(
            os.getenv("NARRATOR_CONTEXT_MAX_CHARS"), 4000, name="NARRATOR_CONTEXT_MAX_CHARS"
        )
        if not 2500 <= narrator_context_max_chars <= 12000:
            raise RuntimeError("NARRATOR_CONTEXT_MAX_CHARS must be between 2500 and 12000")
        rag_context_cache_seconds = _as_float(
            os.getenv("RAG_CONTEXT_CACHE_SECONDS"), 4.0, name="RAG_CONTEXT_CACHE_SECONDS"
        )
        if not 0 <= rag_context_cache_seconds <= 60:
            raise RuntimeError("RAG_CONTEXT_CACHE_SECONDS must be between 0 and 60")
        rag_canon_cache_seconds = _as_float(
            os.getenv("RAG_CANON_CACHE_SECONDS"), 120.0, name="RAG_CANON_CACHE_SECONDS"
        )
        if not 0 <= rag_canon_cache_seconds <= 3600:
            raise RuntimeError("RAG_CANON_CACHE_SECONDS must be between 0 and 3600")

        openai_key = os.getenv("OPENAI_API_KEY", "").strip() or None
        model = os.getenv("OPENAI_MODEL", "gpt-5-mini").strip() or "gpt-5-mini"

        openrouter_base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip().rstrip("/")
        if not openrouter_base_url.startswith(("http://", "https://")):
            raise RuntimeError("OPENROUTER_BASE_URL must start with http:// or https://")
        openrouter_routine_model = os.getenv(
            "OPENROUTER_ROUTINE_MODEL", "google/gemma-4-31b-it:free"
        ).strip()
        openrouter_routine_fallback_model = os.getenv(
            "OPENROUTER_ROUTINE_FALLBACK_MODEL", "z-ai/glm-5.2:free"
        ).strip()
        openrouter_epic_model = os.getenv(
            "OPENROUTER_EPIC_MODEL", "google/gemma-4-31b-it:free"
        ).strip()
        openrouter_epic_fallback_model = os.getenv(
            "OPENROUTER_EPIC_FALLBACK_MODEL", "z-ai/glm-5.2:free"
        ).strip()
        openrouter_dynamic_free_model = os.getenv(
            "OPENROUTER_DYNAMIC_FREE_FALLBACK", "openrouter/free"
        ).strip()
        # v0.26.0: optional direct Google AI Studio route. GEMINI_API_KEY is
        # accepted as an alias because that is the name Google's own quickstart
        # tells you to export, and an operator who followed it should not have
        # to discover a second spelling.
        google_ai_studio_api_key = (
            os.getenv("GOOGLE_AI_STUDIO_API_KEY", "").strip()
            or os.getenv("GEMINI_API_KEY", "").strip()
            or None
        )
        google_ai_studio_model = os.getenv(
            "GOOGLE_AI_STUDIO_MODEL", "aistudio/gemini-3.8-flash"
        ).strip()
        if google_ai_studio_api_key and not google_ai_studio_model.startswith("aistudio/"):
            raise RuntimeError(
                "GOOGLE_AI_STUDIO_MODEL must start with 'aistudio/' so the router can tell "
                f"it apart from an OpenRouter route, got: {google_ai_studio_model}"
            )
        openrouter_require_free = _as_bool(os.getenv("OPENROUTER_REQUIRE_FREE"), True)
        if openrouter_require_free:
            for env_name, route_model in (
                ("OPENROUTER_ROUTINE_MODEL", openrouter_routine_model),
                ("OPENROUTER_ROUTINE_FALLBACK_MODEL", openrouter_routine_fallback_model),
                ("OPENROUTER_EPIC_MODEL", openrouter_epic_model),
                ("OPENROUTER_EPIC_FALLBACK_MODEL", openrouter_epic_fallback_model),
                ("OPENROUTER_DYNAMIC_FREE_FALLBACK", openrouter_dynamic_free_model),
            ):
                if not (route_model.endswith(":free") or route_model == "openrouter/free"):
                    raise RuntimeError(
                        f"{env_name} must use a :free endpoint or openrouter/free while OPENROUTER_REQUIRE_FREE=true"
                    )
        # Chat monitor budgets.  The ceilings exist because the monitor runs on the
        # same free OpenRouter chain as narration and shares its 20 req/min limiter:
        # an unbounded transcript would starve the narrator for a whole minute.
        monitor_max_messages = _as_int(
            os.getenv("MONITOR_MAX_MESSAGES"), 400, name="MONITOR_MAX_MESSAGES"
        )
        if not 20 <= monitor_max_messages <= 5000:
            raise RuntimeError("MONITOR_MAX_MESSAGES must be between 20 and 5000")
        monitor_lookback_hours = _as_int(
            os.getenv("MONITOR_LOOKBACK_HOURS"), 24, name="MONITOR_LOOKBACK_HOURS"
        )
        if not 1 <= monitor_lookback_hours <= 720:
            raise RuntimeError("MONITOR_LOOKBACK_HOURS must be between 1 and 720")
        monitor_chunk_chars = _as_int(
            os.getenv("MONITOR_CHUNK_CHARS"), 6000, name="MONITOR_CHUNK_CHARS"
        )
        if not 1000 <= monitor_chunk_chars <= 20000:
            raise RuntimeError("MONITOR_CHUNK_CHARS must be between 1000 and 20000")
        monitor_max_chunks = _as_int(
            os.getenv("MONITOR_MAX_CHUNKS"), 6, name="MONITOR_MAX_CHUNKS"
        )
        if not 1 <= monitor_max_chunks <= 20:
            raise RuntimeError("MONITOR_MAX_CHUNKS must be between 1 and 20")
        openrouter_max_requests_per_minute = _as_int(
            os.getenv("OPENROUTER_MAX_REQUESTS_PER_MINUTE"), 20, name="OPENROUTER_MAX_REQUESTS_PER_MINUTE"
        )
        if not 1 <= openrouter_max_requests_per_minute <= 120:
            raise RuntimeError("OPENROUTER_MAX_REQUESTS_PER_MINUTE must be between 1 and 120")
        # OpenRouter's free tier is 20 req/min AND 50 req/day under $10 of lifetime
        # credits - 1000/day at $10 or more. The default here is the smaller,
        # honest number; raise it to 1000 once credits are on the account.
        openrouter_max_requests_per_day = _as_int(
            os.getenv("OPENROUTER_MAX_REQUESTS_PER_DAY"), 50, name="OPENROUTER_MAX_REQUESTS_PER_DAY"
        )
        if not 10 <= openrouter_max_requests_per_day <= 200000:
            raise RuntimeError("OPENROUTER_MAX_REQUESTS_PER_DAY must be between 10 and 200000")
        # PER-ROUTE ceilings. The two above are account-wide (OpenRouter's own);
        # these are what a PROVIDER enforces per model - Google allows Gemma 4
        # about 15 requests/minute and 1500/day per model. Defaults are those
        # figures, which are right in both regimes: on the shared free pool the
        # account-wide daily cap binds first and these never fire; on a BYOK
        # provider key these are the real ceiling.
        openrouter_route_requests_per_minute = _as_int(
            os.getenv("OPENROUTER_ROUTE_REQUESTS_PER_MINUTE"), 15,
            name="OPENROUTER_ROUTE_REQUESTS_PER_MINUTE",
        )
        if not 1 <= openrouter_route_requests_per_minute <= 600:
            raise RuntimeError("OPENROUTER_ROUTE_REQUESTS_PER_MINUTE must be between 1 and 600")
        openrouter_route_requests_per_day = _as_int(
            os.getenv("OPENROUTER_ROUTE_REQUESTS_PER_DAY"), 1500,
            name="OPENROUTER_ROUTE_REQUESTS_PER_DAY",
        )
        if not 10 <= openrouter_route_requests_per_day <= 1000000:
            raise RuntimeError("OPENROUTER_ROUTE_REQUESTS_PER_DAY must be between 10 and 1000000")
        openrouter_timeout_seconds = _as_float(
            os.getenv("OPENROUTER_TIMEOUT_SECONDS"), 30.0, name="OPENROUTER_TIMEOUT_SECONDS"
        )
        if not 5 <= openrouter_timeout_seconds <= 300:
            raise RuntimeError("OPENROUTER_TIMEOUT_SECONDS must be between 5 and 300")
        openrouter_epic_timeout_seconds = _as_float(
            os.getenv("OPENROUTER_EPIC_TIMEOUT_SECONDS"), 60.0, name="OPENROUTER_EPIC_TIMEOUT_SECONDS"
        )
        if not 5 <= openrouter_epic_timeout_seconds <= 300:
            raise RuntimeError("OPENROUTER_EPIC_TIMEOUT_SECONDS must be between 5 and 300")
        openrouter_failure_cooldown_seconds = _as_float(
            os.getenv("OPENROUTER_FAILURE_COOLDOWN_SECONDS"), 20.0, name="OPENROUTER_FAILURE_COOLDOWN_SECONDS"
        )
        if not 1 <= openrouter_failure_cooldown_seconds <= 300:
            raise RuntimeError("OPENROUTER_FAILURE_COOLDOWN_SECONDS must be between 1 and 300")
        openrouter_app_url = os.getenv("OPENROUTER_APP_URL", "").strip()
        openrouter_app_name = os.getenv("OPENROUTER_APP_NAME", "Xianxia RP").strip() or "Xianxia RP"

        try:
            guild_id = int(guild_id_raw)
        except ValueError as exc:
            raise RuntimeError("GUILD_ID must be a numeric Discord server ID") from exc
        if guild_id <= 0:
            raise RuntimeError("GUILD_ID must be a positive Discord server ID")

        archive_minutes = _as_int(
            os.getenv("EVENT_THREAD_AUTO_ARCHIVE_MINUTES"), 1440,
            name="EVENT_THREAD_AUTO_ARCHIVE_MINUTES",
        )
        if archive_minutes not in {60, 1440, 4320, 10080}:
            raise RuntimeError(
                "EVENT_THREAD_AUTO_ARCHIVE_MINUTES must be one of 60, 1440, 4320, or 10080"
            )

        chance = _as_int(
            os.getenv("UNEXPECTED_EVENT_CHANCE_PERCENT"), 28,
            name="UNEXPECTED_EVENT_CHANCE_PERCENT",
        )
        if not 0 <= chance <= 100:
            raise RuntimeError("UNEXPECTED_EVENT_CHANCE_PERCENT must be between 0 and 100")

        world_time_scale = _as_int(os.getenv("WORLD_TIME_SCALE"), 4, name="WORLD_TIME_SCALE")
        if not 0 <= world_time_scale <= 60:
            raise RuntimeError("WORLD_TIME_SCALE must be between 0 and 60 game-minutes per real minute")

        reincarnation_base_samsara_years = _as_int(
            os.getenv("REINCARNATION_BASE_SAMSARA_YEARS"),
            320,
            name="REINCARNATION_BASE_SAMSARA_YEARS",
        )
        if not 1 <= reincarnation_base_samsara_years <= 5000:
            raise RuntimeError("REINCARNATION_BASE_SAMSARA_YEARS must be between 1 and 5000")
        reincarnation_max_wait_seconds = _as_int(
            os.getenv("REINCARNATION_MAX_WAIT_SECONDS"), 300,
            name="REINCARNATION_MAX_WAIT_SECONDS",
        )
        if not 30 <= reincarnation_max_wait_seconds <= 3600:
            raise RuntimeError("REINCARNATION_MAX_WAIT_SECONDS must be between 30 and 3600")

        # Bounds for the pre-auth HTTP request head on the health listener.
        # Shared names with the dashboard: one knob per limit, both servers.
        http_max_request_line_bytes = _as_int(
            os.getenv("HTTP_MAX_REQUEST_LINE_BYTES"), 8192, name="HTTP_MAX_REQUEST_LINE_BYTES"
        )
        if not 256 <= http_max_request_line_bytes <= 65536:
            raise RuntimeError("HTTP_MAX_REQUEST_LINE_BYTES must be between 256 and 65536")
        http_max_header_lines = _as_int(
            os.getenv("HTTP_MAX_HEADER_LINES"), 100, name="HTTP_MAX_HEADER_LINES"
        )
        if not 8 <= http_max_header_lines <= 1000:
            raise RuntimeError("HTTP_MAX_HEADER_LINES must be between 8 and 1000")
        http_max_header_bytes = _as_int(
            os.getenv("HTTP_MAX_HEADER_BYTES"), 16384, name="HTTP_MAX_HEADER_BYTES"
        )
        if not 1024 <= http_max_header_bytes <= 262144:
            raise RuntimeError("HTTP_MAX_HEADER_BYTES must be between 1024 and 262144")
        http_header_deadline_seconds = _as_float(
            os.getenv("HTTP_HEADER_DEADLINE_SECONDS"), 10.0, name="HTTP_HEADER_DEADLINE_SECONDS"
        )
        if not 1.0 <= http_header_deadline_seconds <= 120.0:
            raise RuntimeError("HTTP_HEADER_DEADLINE_SECONDS must be between 1 and 120")
        http_header_line_timeout_seconds = _as_float(
            os.getenv("HTTP_HEADER_LINE_TIMEOUT_SECONDS"), 5.0, name="HTTP_HEADER_LINE_TIMEOUT_SECONDS"
        )
        if not 0.5 <= http_header_line_timeout_seconds <= 60.0:
            raise RuntimeError("HTTP_HEADER_LINE_TIMEOUT_SECONDS must be between 0.5 and 60")
        http_max_connections = _as_int(
            os.getenv("HTTP_MAX_CONNECTIONS"), 64, name="HTTP_MAX_CONNECTIONS"
        )
        if not 4 <= http_max_connections <= 4096:
            raise RuntimeError("HTTP_MAX_CONNECTIONS must be between 4 and 4096")
        health_host = os.getenv("HEALTH_HOST", "0.0.0.0").strip() or "0.0.0.0"
        health_port = _as_int(os.getenv("HEALTH_PORT"), 8080, name="HEALTH_PORT")
        if not 1 <= health_port <= 65535:
            raise RuntimeError("HEALTH_PORT must be between 1 and 65535")
        slow_query_ms = _as_float(os.getenv("SLOW_QUERY_MS"), 100.0, name="SLOW_QUERY_MS")
        if slow_query_ms < 0:
            raise RuntimeError("SLOW_QUERY_MS cannot be negative")
        alert_webhook_url = os.getenv("ALERT_WEBHOOK_URL", "").strip() or None
        alert_cooldown_seconds = _as_int(os.getenv("ALERT_COOLDOWN_SECONDS"), 300, name="ALERT_COOLDOWN_SECONDS")
        if alert_cooldown_seconds < 0:
            raise RuntimeError("ALERT_COOLDOWN_SECONDS cannot be negative")
        game_engine_url = os.getenv("GAME_ENGINE_URL", "http://127.0.0.1:8081").strip().rstrip("/")
        if not game_engine_url.startswith(("http://", "https://")):
            raise RuntimeError("GAME_ENGINE_URL must start with http:// or https://")
        game_engine_timeout_seconds = _as_float(
            os.getenv("GAME_ENGINE_TIMEOUT_SECONDS"), 30.0, name="GAME_ENGINE_TIMEOUT_SECONDS"
        )
        if not 1 <= game_engine_timeout_seconds <= 300:
            raise RuntimeError("GAME_ENGINE_TIMEOUT_SECONDS must be between 1 and 300")
        game_engine_auth_token = os.getenv("ENGINE_AUTH_TOKEN", "").strip()

        cooldowns = {
            "cultivate_cooldown_minutes": _as_int(os.getenv("CULTIVATE_COOLDOWN_MINUTES"), 180, name="CULTIVATE_COOLDOWN_MINUTES"),
            "explore_cooldown_minutes": _as_int(os.getenv("EXPLORE_COOLDOWN_MINUTES"), 20, name="EXPLORE_COOLDOWN_MINUTES"),
            "hunt_cooldown_minutes": _as_int(os.getenv("HUNT_COOLDOWN_MINUTES"), 30, name="HUNT_COOLDOWN_MINUTES"),
            "perfect_quest_cooldown_minutes": _as_int(os.getenv("PERFECT_QUEST_COOLDOWN_MINUTES"), 60, name="PERFECT_QUEST_COOLDOWN_MINUTES"),
            "perfect_trial_cooldown_minutes": _as_int(os.getenv("PERFECT_TRIAL_COOLDOWN_MINUTES"), 360, name="PERFECT_TRIAL_COOLDOWN_MINUTES"),
            "secret_realm_cooldown_minutes": _as_int(os.getenv("SECRET_REALM_COOLDOWN_MINUTES"), 15, name="SECRET_REALM_COOLDOWN_MINUTES"),
        }
        for field_name, value in cooldowns.items():
            if value < 0:
                raise RuntimeError(f"{field_name.upper()} cannot be negative")

        update_channel = (os.getenv("UPDATE_CHANNEL") or "stable").strip().lower()
        if update_channel not in ("stable", "beta"):
            raise ValueError("UPDATE_CHANNEL must be 'stable' or 'beta'")
        update_check_hours = int(os.getenv("UPDATE_CHECK_HOURS", "24"))
        if update_check_hours < 1:
            raise ValueError("UPDATE_CHECK_HOURS must be at least 1")
        quest_forge_min_significance = int(os.getenv("QUEST_FORGE_MIN_SIGNIFICANCE", "80"))
        if not 0 <= quest_forge_min_significance <= 100:
            raise ValueError("QUEST_FORGE_MIN_SIGNIFICANCE must be 0-100")
        quest_forge_interval_hours = int(os.getenv("QUEST_FORGE_INTERVAL_HOURS", "6"))
        if quest_forge_interval_hours < 1:
            raise ValueError("QUEST_FORGE_INTERVAL_HOURS must be at least 1")
        quest_budget = {}
        for key, default in (("QUEST_REWARD_MAX_XP", 50), ("QUEST_REWARD_MAX_STONES", 200), ("QUEST_REWARD_MAX_ITEMS", 3)):
            quest_budget[key] = int(os.getenv(key, str(default)))
            if quest_budget[key] < 0:
                raise ValueError(f"{key} cannot be negative")
        typed_play_prefix = _typed_play_prefix(os.getenv("TYPED_PLAY_PREFIX"))
        typed_play_burst = _as_int(os.getenv("TYPED_PLAY_BURST"), 4, name="TYPED_PLAY_BURST")
        typed_play_per_minute = _as_float(os.getenv("TYPED_PLAY_PER_MINUTE"), 6.0, name="TYPED_PLAY_PER_MINUTE")
        if typed_play_burst < 1:
            raise ValueError("TYPED_PLAY_BURST must be at least 1")
        if typed_play_per_minute <= 0:
            raise ValueError("TYPED_PLAY_PER_MINUTE must be positive")
        return cls(
            discord_token=discord_token,
            guild_id=guild_id,
            narrator_provider=narrator_provider,
            narrator_context_max_chars=narrator_context_max_chars,
            rag_context_cache_seconds=rag_context_cache_seconds,
            rag_canon_cache_seconds=rag_canon_cache_seconds,
            openai_api_key=openai_key,
            openai_model=model,
            openrouter_api_key=openrouter_key,
            openrouter_base_url=openrouter_base_url,
            openrouter_routine_model=openrouter_routine_model,
            openrouter_routine_fallback_model=openrouter_routine_fallback_model,
            openrouter_epic_model=openrouter_epic_model,
            openrouter_epic_fallback_model=openrouter_epic_fallback_model,
            openrouter_dynamic_free_model=openrouter_dynamic_free_model,
            google_ai_studio_api_key=google_ai_studio_api_key,
            google_ai_studio_model=google_ai_studio_model,
            openrouter_require_free=openrouter_require_free,
            openrouter_max_requests_per_minute=openrouter_max_requests_per_minute,
            openrouter_max_requests_per_day=openrouter_max_requests_per_day,
            openrouter_route_requests_per_minute=openrouter_route_requests_per_minute,
            openrouter_route_requests_per_day=openrouter_route_requests_per_day,
            openrouter_timeout_seconds=openrouter_timeout_seconds,
            openrouter_epic_timeout_seconds=openrouter_epic_timeout_seconds,
            openrouter_failure_cooldown_seconds=openrouter_failure_cooldown_seconds,
            openrouter_disable_reasoning=_as_bool(os.getenv("OPENROUTER_DISABLE_REASONING"), True),
            openrouter_app_url=openrouter_app_url,
            openrouter_app_name=openrouter_app_name,
            rp_channel_ids=_as_int_set(os.getenv("RP_CHANNEL_IDS"), name="RP_CHANNEL_IDS"),
            message_content_intent=_as_bool(os.getenv("MESSAGE_CONTENT_INTENT"), False),
            monitor_max_messages=monitor_max_messages,
            monitor_lookback_hours=monitor_lookback_hours,
            monitor_chunk_chars=monitor_chunk_chars,
            monitor_max_chunks=monitor_max_chunks,
            auto_narrate=_as_bool(os.getenv("AUTO_NARRATE"), False),
            auto_narrate_event_threads=_as_bool(os.getenv("AUTO_NARRATE_EVENT_THREADS"), False),
            event_thread_auto_archive_minutes=archive_minutes,
            unexpected_event_chance_percent=chance,
            world_time_scale=world_time_scale,
            reincarnation_base_samsara_years=reincarnation_base_samsara_years,
            reincarnation_max_wait_seconds=reincarnation_max_wait_seconds,
            database_path=Path(os.getenv("DATABASE_PATH", "data/xianxia.sqlite3")),
            health_host=health_host,
            http_max_request_line_bytes=http_max_request_line_bytes,
            http_max_header_lines=http_max_header_lines,
            http_max_header_bytes=http_max_header_bytes,
            http_header_deadline_seconds=http_header_deadline_seconds,
            http_header_line_timeout_seconds=http_header_line_timeout_seconds,
            http_max_connections=http_max_connections,
            health_port=health_port,
            slow_query_ms=slow_query_ms,
            alert_webhook_url=alert_webhook_url,
            alert_cooldown_seconds=alert_cooldown_seconds,
            game_engine_url=game_engine_url,
            game_engine_timeout_seconds=game_engine_timeout_seconds,
            game_engine_auth_token=game_engine_auth_token,
            update_check_enabled=_as_bool(os.getenv("UPDATE_CHECK_ENABLED"), True),
            update_channel=update_channel,
            update_repository=(os.getenv("UPDATE_REPOSITORY") or "RhaZenZ0/Xianxia-bot").strip(),
            update_check_hours=update_check_hours,
            quest_forge_auto=_as_bool(os.getenv("QUEST_FORGE_AUTO"), False),
            quest_forge_min_significance=quest_forge_min_significance,
            quest_forge_interval_hours=quest_forge_interval_hours,
            quest_reward_max_xp=quest_budget["QUEST_REWARD_MAX_XP"],
            quest_reward_max_stones=quest_budget["QUEST_REWARD_MAX_STONES"],
            quest_reward_max_items=quest_budget["QUEST_REWARD_MAX_ITEMS"],
            typed_play_prefix=typed_play_prefix,
            typed_play_burst=typed_play_burst,
            typed_play_per_minute=typed_play_per_minute,
            typed_play_hint=_as_bool(os.getenv("TYPED_PLAY_HINT"), True),
            **cooldowns,
        )
