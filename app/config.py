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
    openrouter_require_free: bool
    openrouter_max_requests_per_minute: int
    openrouter_timeout_seconds: float
    openrouter_epic_timeout_seconds: float
    openrouter_failure_cooldown_seconds: float
    openrouter_app_url: str
    openrouter_app_name: str
    rp_channel_ids: set[int]
    message_content_intent: bool
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
    health_port: int
    slow_query_ms: float
    alert_webhook_url: str | None
    alert_cooldown_seconds: int
    game_engine_url: str
    game_engine_timeout_seconds: float

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
            "OPENROUTER_ROUTINE_FALLBACK_MODEL", "google/gemma-4-26b-a4b-it:free"
        ).strip()
        openrouter_epic_model = os.getenv(
            "OPENROUTER_EPIC_MODEL", "nvidia/nemotron-3-super-120b-a12b:free"
        ).strip()
        openrouter_epic_fallback_model = os.getenv(
            "OPENROUTER_EPIC_FALLBACK_MODEL", "google/gemma-4-31b-it:free"
        ).strip()
        openrouter_dynamic_free_model = os.getenv(
            "OPENROUTER_DYNAMIC_FREE_FALLBACK", "openrouter/free"
        ).strip()
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
        openrouter_max_requests_per_minute = _as_int(
            os.getenv("OPENROUTER_MAX_REQUESTS_PER_MINUTE"), 20, name="OPENROUTER_MAX_REQUESTS_PER_MINUTE"
        )
        if not 1 <= openrouter_max_requests_per_minute <= 120:
            raise RuntimeError("OPENROUTER_MAX_REQUESTS_PER_MINUTE must be between 1 and 120")
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
            openrouter_require_free=openrouter_require_free,
            openrouter_max_requests_per_minute=openrouter_max_requests_per_minute,
            openrouter_timeout_seconds=openrouter_timeout_seconds,
            openrouter_epic_timeout_seconds=openrouter_epic_timeout_seconds,
            openrouter_failure_cooldown_seconds=openrouter_failure_cooldown_seconds,
            openrouter_app_url=openrouter_app_url,
            openrouter_app_name=openrouter_app_name,
            rp_channel_ids=_as_int_set(os.getenv("RP_CHANNEL_IDS"), name="RP_CHANNEL_IDS"),
            message_content_intent=_as_bool(os.getenv("MESSAGE_CONTENT_INTENT"), False),
            auto_narrate=_as_bool(os.getenv("AUTO_NARRATE"), False),
            auto_narrate_event_threads=_as_bool(os.getenv("AUTO_NARRATE_EVENT_THREADS"), False),
            event_thread_auto_archive_minutes=archive_minutes,
            unexpected_event_chance_percent=chance,
            world_time_scale=world_time_scale,
            reincarnation_base_samsara_years=reincarnation_base_samsara_years,
            reincarnation_max_wait_seconds=reincarnation_max_wait_seconds,
            database_path=Path(os.getenv("DATABASE_PATH", "data/xianxia.sqlite3")),
            health_host=health_host,
            health_port=health_port,
            slow_query_ms=slow_query_ms,
            alert_webhook_url=alert_webhook_url,
            alert_cooldown_seconds=alert_cooldown_seconds,
            game_engine_url=game_engine_url,
            game_engine_timeout_seconds=game_engine_timeout_seconds,
            **cooldowns,
        )
