"""The bot class and the single bot instance.

Phase 8 of the main.py split (v0.19.43, docs/MAIN_SPLIT_PLAN.md). XianxiaBot
owns startup phases, the health and event-expiry workers, and on_message; the
instance is created here and main.py (the composition root) registers the
command surface on it. Nothing here imports main.py.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import discord
import httpx
from discord.ext import commands

from ..database import SCHEMA_VERSION
from ..ops.health import HealthServer, HealthState
from ..ops.http_limits import HeaderLimits
from ..ops.release_channel import announcement, api_url, newer_than_installed, newest_for_channel, parse_releases
from ..ai.narrator import canonical_location_reply, is_current_location_question
from ..rules.npc_memory import classify_memory, exchange_memory_summary, public_mood_hint
from ..version import RELEASE_VERSION
from .admin.channel_messages import XianxiaInfoView
from .admin.server_setup import dashboard_discord_control
from .channels import post_server_log
from .character_state import _remember_freeform_npc_scene
from .runtime import DB, ENGINE, SETTINGS, WORLD, character_location_display, chunk_text, current_world_time, log
from .services import ALERTS, GUILD, NARRATOR, NARRATOR_CONTEXT, SIM
from .threads import _private_scene_for_thread
from .ui.event_scene import spawn_system_event_thread

class XianxiaBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.messages = True
        intents.message_content = SETTINGS.message_content_intent
        super().__init__(
            command_prefix="!unused-",
            intents=intents,
            # User-supplied character names/RP text must never be able to turn
            # stored/generated text into real Discord notifications.
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                roles=False,
                users=False,
                replied_user=False,
            ),
        )
        self.health_state = HealthState(supported_schema_version=SCHEMA_VERSION)
        control_token = os.getenv("BOT_CONTROL_TOKEN", "").strip() or os.getenv("DASHBOARD_TOKEN", "").strip()
        self.health_server = HealthServer(
            self.health_state,
            host=SETTINGS.health_host,
            port=SETTINGS.health_port,
            control_handler=self._dashboard_discord_control if control_token else None,
            control_token=control_token,
            header_limits=HeaderLimits(
                max_request_line_bytes=SETTINGS.http_max_request_line_bytes,
                max_header_lines=SETTINGS.http_max_header_lines,
                max_header_bytes=SETTINGS.http_max_header_bytes,
                header_deadline_seconds=SETTINGS.http_header_deadline_seconds,
                line_timeout_seconds=SETTINGS.http_header_line_timeout_seconds,
            ),
            max_connections=SETTINGS.http_max_connections,
        )
        self.operational_health_task: asyncio.Task | None = None
        self.update_check_task: asyncio.Task | None = None
        self.announced_release: str | None = None

    async def _dashboard_discord_control(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        # The handler is intentionally hosted by the Discord process. The GM
        # dashboard can request Discord setup work, but only discord.py owns
        # guild/channel/role mutations. Game mechanics remain in Go.
        return await dashboard_discord_control(self, action, payload)

    async def _mark_startup_phase(self, phase: str, detail: dict | None = None) -> None:
        first_ready = self.health_state.mark_phase(phase, detail=detail or {})
        if first_ready:
            try:
                await DB.record_startup_event(
                    self.health_state.boot_id, phase, status="ready", detail=detail or {}
                )
            except Exception:
                # Health-state persistence must never invalidate an otherwise
                # healthy startup after the database itself has been verified.
                log.exception("Could not persist startup phase %s", phase)

    async def setup_hook(self) -> None:
        # Persistent read-only guide controls survive process/container restarts.
        self.add_view(XianxiaInfoView())
        log.info("XIANXIA_STARTUP version=%s schema_version=%s", RELEASE_VERSION, SCHEMA_VERSION)
        phase = "HEALTH_SERVER"
        try:
            await self.health_server.start()

            phase = "DATABASE_READY"
            await DB.init()
            schema = await DB.get_schema_status()
            if not schema.get("compatible"):
                raise RuntimeError(
                    f"Schema mismatch: current={schema.get('current')} supported={schema.get('supported')}"
                )
            self.health_state.set_schema_version(int(schema["current"]))
            db_probe = await DB.operational_health()
            self.health_state.set_check("database", bool(db_probe.get("ok")), **{k: v for k, v in db_probe.items() if k != "ok"})
            await self._mark_startup_phase(
                "DATABASE_READY",
                {
                    "release_version": RELEASE_VERSION,
                    "schema_version": int(schema["current"]),
                    "migrations": int(schema["migrations"]),
                    "journal_mode": db_probe.get("journal_mode", "unknown"),
                },
            )

            phase = "CATALOG_READY"
            await DB.sync_world_catalog(WORLD.data)
            await DB.sync_rag_canon(WORLD.data)
            catalog_counts = await DB.catalog_counts()
            rag_counts = await DB.rag_stats()
            await self._mark_startup_phase("CATALOG_READY", {**catalog_counts, **{f"rag_{k}": v for k, v in rag_counts.items()}})

            phase = "SIMULATION_READY"
            wt_state = await DB.get_world_clock(scale=SETTINGS.world_time_scale)
            await SIM.initialize(int(wt_state["game_minute"]))
            await self._mark_startup_phase(
                "SIMULATION_READY", {"game_minute": int(wt_state["game_minute"])}
            )

            phase = "COMMAND_SYNC"
            synced = await self.tree.sync(guild=GUILD)
            log.info("COMMANDS_READY count=%s guild=%s", len(synced), SETTINGS.guild_id)
            self.event_expiry_task = asyncio.create_task(self.event_expiry_worker())
            self.operational_health_task = asyncio.create_task(self.operational_health_worker())
            if SETTINGS.update_check_enabled:
                self.update_check_task = asyncio.create_task(self.update_check_worker())
        except Exception as exc:
            self.health_state.fail(phase, exc)
            failure_detail = {
                "phase": phase,
                "boot_id": self.health_state.boot_id,
                "type": type(exc).__name__,
                "message": str(exc),
            }
            delivered = await ALERTS.send(
                "startup_failed",
                f"Bot startup failed during {phase}: {exc}",
                severity="critical",
                details=failure_detail,
            )
            if self.health_state.phases["DATABASE_READY"].ready:
                try:
                    await DB.record_startup_event(
                        self.health_state.boot_id, phase, status="failed", detail=failure_detail,
                    )
                    await DB.record_operational_alert(
                        "startup_failed", severity="critical",
                        message=f"Bot startup failed during {phase}: {exc}",
                        detail=failure_detail, delivered=delivered,
                    )
                except Exception:
                    log.exception("Could not persist startup failure")
            raise

    async def operational_health_worker(self) -> None:
        # The exception boundary is per-iteration (like event_expiry_worker),
        # not around the whole loop: a single transient failure from
        # DB.operational_health()/flush_slow_query_log()/observability_snapshot()
        # or alert persistence used to land in an outer `except Exception` that
        # then let the coroutine return - silently disabling operational
        # monitoring for the rest of the process's life. Catching per iteration
        # means a transient SQLite/IO hiccup logs, alerts once, and the worker
        # tries again on the next tick instead of dying for good.
        try:
            while not self.is_closed():
                try:
                    probe = await DB.operational_health()
                    self.health_state.set_check(
                        "database", bool(probe.get("ok")),
                        **{k: v for k, v in probe.items() if k != "ok"},
                    )
                    flushed = await DB.flush_slow_query_log()
                    obs = await DB.observability_snapshot()
                    for key in (
                        "query_count", "slow_query_count", "recent_slow_queries_1h", "max_query_latency_ms",
                        "connections_opened", "connections_reused", "writer_wait_count", "writer_wait_ms",
                        "catalog_cache_entries", "catalog_cache_hits", "catalog_cache_misses",
                    ):
                        self.health_state.set_metric(key, float(obs.get(key, 0)))
                    try:
                        engine_status = await ENGINE.database_status()
                        self.health_state.set_check("game_engine", True, **engine_status)
                        self.health_state.set_metric("go_engine_requests", float(engine_status.get("requests", 0)))
                    except Exception as exc:
                        self.health_state.set_check("game_engine", False, error=str(exc))
                    if not probe.get("ok"):
                        detail = {k: v for k, v in probe.items() if k != "ok"}
                        delivered = await ALERTS.send("database_degraded", "SQLite operational health probe failed", severity="critical", details=detail)
                        if probe.get("schema_intact") is False:
                            # The most common cause is an operator removing the
                            # SQLite file while this process is still alive.  Stop
                            # cleanly so Docker's restart policy runs DB.init()
                            # before Discord commands can touch the replacement.
                            log.critical(
                                "DATABASE_SCHEMA_LOST missing_tables=%s; closing for automatic recovery",
                                ",".join(str(name) for name in probe.get("missing_tables", [])),
                            )
                            self.health_state.clear_phase(
                                "DATABASE_READY", reason="required SQLite tables disappeared"
                            )
                            asyncio.create_task(self.close())
                            return
                        await DB.record_operational_alert("database_degraded", severity="critical", message="SQLite operational health probe failed", detail=detail, delivered=delivered)
                    if int(obs.get("recent_slow_queries_1h", 0)) >= 5:
                        detail = {"recent_slow_queries_1h": obs.get("recent_slow_queries_1h"), "max_query_latency_ms": obs.get("max_query_latency_ms"), "threshold_ms": obs.get("slow_query_threshold_ms"), "flushed": flushed}
                        delivered = await ALERTS.send("slow_query_pressure", "Slow-query pressure exceeded the operational threshold", severity="warning", details=detail)
                        await DB.record_operational_alert("slow_query_pressure", severity="warning", message="Slow-query pressure exceeded the operational threshold", detail=detail, delivered=delivered)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.exception("Operational health worker iteration failed")
                    self.health_state.set_check("database", False, error="health worker failed")
                    delivered = await ALERTS.send("health_worker_failed", str(exc), severity="critical")
                    try:
                        await DB.record_operational_alert("health_worker_failed", severity="critical", message=str(exc), delivered=delivered)
                    except Exception:
                        log.exception("Could not persist health-worker alert")
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            pass

    async def check_for_release(self) -> str | None:
        """One release-channel check. Returns the announcement text when a
        newer release exists on the configured channel (and posts it to the
        log channel the first time per process), else None. Never raises:
        the channel being unreachable is a health check, not a bot failure."""
        channel = SETTINGS.update_channel
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0), headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"xianxia-rp-bot/{RELEASE_VERSION}",
            }) as client:
                response = await client.get(api_url(SETTINGS.update_repository))
                response.raise_for_status()
                releases = parse_releases(response.text)
        except Exception as exc:
            self.health_state.set_check("release_channel", False, channel=channel, error=f"{type(exc).__name__}: {exc}"[:200])
            return None
        newest = newest_for_channel(releases, channel)
        available = newer_than_installed(newest, RELEASE_VERSION)
        self.health_state.set_check(
            "release_channel", True, channel=channel, installed=RELEASE_VERSION,
            newest=newest.version_text if newest else None,
            update_available=bool(available),
        )
        if available is None:
            return None
        text = announcement(available, RELEASE_VERSION, channel)
        if self.announced_release != available.version_text:
            self.announced_release = available.version_text
            await post_server_log(self.get_guild(SETTINGS.guild_id), "Update available", text)
        return text

    async def update_check_worker(self) -> None:
        # Same per-iteration exception boundary as operational_health_worker.
        # First check a minute after the command sync, then every
        # UPDATE_CHECK_HOURS; a newer release is announced once per process.
        try:
            await asyncio.sleep(60)
            while not self.is_closed():
                try:
                    await self.check_for_release()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Update check iteration failed")
                await asyncio.sleep(SETTINGS.update_check_hours * 3600)
        except asyncio.CancelledError:
            pass

    async def close_event_scene(self, record: dict, *, manual: bool = False) -> None:
        guild = self.get_guild(SETTINGS.guild_id)
        if guild is None:
            return

        thread: discord.Thread | None = guild.get_thread(int(record["thread_id"]))
        if thread is None:
            try:
                fetched = await guild.fetch_channel(int(record["thread_id"]))
                if isinstance(fetched, discord.Thread):
                    thread = fetched
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                thread = None

        closing = (
            "🌀 **The secret realm is closing.** Spatial cracks seal one after another, and all remaining "
            "cultivators are expelled before the entrance vanishes."
            if record.get("event_type") == "secret_realm"
            else "🔒 **This event has ended.** The scene is now closed."
        )
        if manual:
            closing += "\n*Closed early by an administrator.*"

        if thread is not None:
            try:
                if thread.archived:
                    await thread.edit(archived=False, reason="Post Xianxia event closing message")
                await thread.send(closing)
            except (discord.Forbidden, discord.HTTPException):
                log.exception("Could not post closing message in thread %s", record["thread_id"])
            try:
                await thread.edit(locked=True, archived=True, reason="Xianxia event expired")
            except discord.Forbidden:
                # Locking requires Manage Threads. Archiving may still be allowed for a bot-owned thread.
                try:
                    await thread.edit(archived=True, reason="Xianxia event expired")
                except (discord.Forbidden, discord.HTTPException):
                    log.exception("Could not archive event thread %s", record["thread_id"])
            except discord.HTTPException:
                log.exception("Could not close event thread %s", record["thread_id"])

        event_key = record.get("event_key")
        if event_key:
            try:
                wt = await current_world_time()
                await DB.finalize_world_event_history(str(event_key), game_minute=wt.total_minutes)
            except Exception:
                log.exception("Could not finalize event participation history for %s", event_key)
        await DB.close_event_thread(int(record["thread_id"]), event_key)

        announcement_channel_id = record.get("announcement_channel_id")
        if announcement_channel_id:
            channel = guild.get_channel(int(announcement_channel_id))
            if isinstance(channel, discord.TextChannel):
                try:
                    label = "Secret realm" if record.get("event_type") == "secret_realm" else "World event"
                    await channel.send(f"🔒 **{label} closed — {record['title']}**")
                except (discord.Forbidden, discord.HTTPException):
                    pass

    async def event_expiry_worker(self) -> None:
        await self.wait_until_ready()
        try:
            while not self.is_closed():
                try:
                    automation = await DB.get_automation_settings()
                    if automation.get("event_expiry", True):
                        for record in await DB.get_expired_event_threads():
                            await self.close_event_scene(record)
                    wt = await current_world_time()
                    # Autonomous world-event selection, activation, RNG and persistent consequences are Go-owned.
                    simulation_runs = await SIM.run_due(wt.total_minutes, automation)
                    for sim_run in simulation_runs:
                        log.info("World simulation %s: %s", sim_run.system, sim_run.summary)
                        for event in sim_run.events:
                            guild=self.get_guild(SETTINGS.guild_id)
                            if not guild: continue
                            impacts=[str(x) for x in list(event.get("impacts") or [])]
                            consequence=str(event.get("consequence_text") or "").strip()
                            await spawn_system_event_thread(
                                guild,title=str(event.get("title") or "World Event"),event_type="random_event",
                                event_key=str(event.get("event_key") or ""),expires_at=float(event.get("expires_at") or time.time()+7200),
                                announcement=(
                                    f"⚡ **AUTONOMOUS WORLD EVENT — {event.get('title','World Event')}**\n📍 **{event.get('location','Unknown')}**\n{event.get('description','')}"
                                    + (f"\n\n**Persistent consequence:** {consequence}" if consequence else "")
                                    + (f"\n**Systems changed:** {'; '.join(impacts)}" if impacts else "")
                                ),
                            )
                    if automation.get("maintenance_cleanup", True) and int(time.time()) % 3600 < 30:
                        await DB.maintenance_cleanup(wt.total_minutes)
                except Exception:
                    log.exception("Event expiry worker failed")
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            pass

    async def close(self) -> None:
        for task_name in ("event_expiry_task", "operational_health_task", "update_check_task"):
            task = getattr(self, task_name, None)
            if task:
                task.cancel()
        self.health_state.clear_phase("DISCORD_READY", reason="shutdown")
        self.health_state.set_check("discord_gateway", False, reason="shutdown")
        await self.health_server.stop()
        await super().close()

    async def on_ready(self) -> None:
        detail = {
            "user": str(self.user),
            "user_id": getattr(self.user, "id", None),
            "guild_id": SETTINGS.guild_id,
            "narrator": NARRATOR.provider_label if NARRATOR.enabled else "disabled",
        }
        await self._mark_startup_phase("DISCORD_READY", detail)
        self.health_state.set_check("discord_gateway", True, guild_id=SETTINGS.guild_id)
        log.info(
            "Logged in as %s (%s). Narrator: %s",
            self.user,
            getattr(self.user, "id", "?"),
            NARRATOR.provider_label if NARRATOR.enabled else "disabled",
        )

    async def on_disconnect(self) -> None:
        self.health_state.clear_phase("DISCORD_READY", reason="gateway disconnected")
        self.health_state.set_check("discord_gateway", False, reason="gateway disconnected")
        log.warning("DISCORD_NOT_READY reason=gateway_disconnected boot_id=%s", self.health_state.boot_id)

    async def on_resumed(self) -> None:
        detail = {
            "user": str(self.user),
            "user_id": getattr(self.user, "id", None),
            "guild_id": SETTINGS.guild_id,
            "resumed": True,
        }
        await self._mark_startup_phase("DISCORD_READY", detail)
        self.health_state.set_check("discord_gateway", True, guild_id=SETTINGS.guild_id, resumed=True)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        if message.guild.id != SETTINGS.guild_id:
            return
        if not SETTINGS.message_content_intent:
            return

        mentioned = self.user is not None and self.user in message.mentions
        parent_id = message.channel.parent_id if isinstance(message.channel, discord.Thread) else None
        hub_record = await DB.get_realm_hub_by_channel(message.guild.id, int(parent_id or message.channel.id))
        character = await DB.get_character(message.author.id)
        private_scene = None
        if character and isinstance(message.channel, discord.Thread):
            private_scene = await _private_scene_for_thread(
                message.guild, message.channel.id, message.author.id, character
            )
        auto_channel = (
            SETTINGS.auto_narrate
            and (
                bool(hub_record)
                or bool(private_scene)
                or (bool(SETTINGS.rp_channel_ids) and (message.channel.id in SETTINGS.rp_channel_ids or parent_id in SETTINGS.rp_channel_ids))
            )
        )
        active_event_thread = (
            SETTINGS.auto_narrate_event_threads
            and isinstance(message.channel, discord.Thread)
            and await DB.is_active_event_thread(message.channel.id)
        )
        if not mentioned and not auto_channel and not active_event_thread:
            return

        if not character:
            await message.reply("Create your cultivator first with **/begin**.")
            return
        if isinstance(message.channel, discord.Thread) and parent_id:
            cfg = await DB.get_server_config(message.guild.id)
            if int(parent_id) == int(cfg.get("exploration_channel_id") or 0) and not private_scene:
                await message.reply("This is not your active private expedition thread.")
                return
        if hub_record and str(character.get("location", "")) != str(hub_record.get("location", "")):
            await message.reply(
                f"🏙️ This channel represents **{hub_record['location']}** in **{hub_record['world_name']}**. "
                f"Your cultivator is currently at **{await character_location_display(character)}**. "
                "Use **/travel → Realm Capitals → Go** before roleplaying here."
            )
            return

        content = message.content
        if self.user:
            content = content.replace(f"<@{self.user.id}>", "").replace(f"<@!{self.user.id}>", "").strip()
        if not content:
            return

        await DB.add_history(
            message.channel.id,
            user_id=message.author.id,
            speaker=character["name"],
            content=content,
        )
        if is_current_location_question(content):
            narration = canonical_location_reply(character)
            await DB.add_history(
                message.channel.id,
                user_id=None,
                speaker="World",
                content=narration,
            )
            await message.reply(narration, mention_author=False)
            return
        history = await DB.get_history(message.channel.id, 24)

        lineage_context = await DB.describe_lineage_context(message.author.id)
        scene_context = await NARRATOR_CONTEXT.build(
            character,
            scene_type=(f"private_{private_scene[0]}" if private_scene else "public_roleplay"),
            lineage_context=lineage_context,
            query_text=content,
        )
        async with message.channel.typing():
            try:
                narration = await NARRATOR.narrate_action(
                    character=character,
                    action=content,
                    history=history,
                    social_context=lineage_context,
                    scene_context=scene_context.text,
                    epic=active_event_thread,
                )
            except Exception:
                log.exception("Narration failed")
                await message.reply("The spiritual currents are unstable; narration failed. Try again shortly.")
                return

        await DB.add_history(
            message.channel.id,
            user_id=None,
            speaker="World",
            content=narration,
        )
        try:
            memory_kind, memory_salience = classify_memory(content, narration)
            # `location=` below stays the raw DB key (memory lookups elsewhere filter by
            # exact match against character.location); only the human-readable summary
            # text - which can feed straight into future narration - gets translated.
            location_label = await character_location_display(character)
            await DB.add_rag_memory(
                message.author.id, memory_kind=memory_kind, salience=memory_salience,
                location=str(character.get("location") or ""), source="freeform",
                game_minute=scene_context.game_minute,
                summary=(
                    f"At {location_label}, {character.get('name','the player')} acted/said: "
                    f"{content[:320]} | Observed response: {narration[:560]}"
                ),
            )
            await _remember_freeform_npc_scene(
                user_id=message.author.id, character=character, player_text=content,
                narration=narration, game_minute=scene_context.game_minute,
            )
        except Exception:
            log.exception("Failed to persist freeform RAG/NPC episodic memory")
        for i, chunk in enumerate(chunk_text(narration)):
            if i == 0:
                await message.reply(chunk, mention_author=False)
            else:
                await message.channel.send(chunk)


bot = XianxiaBot()


# Hubs report failures through the bot instance without importing main.py back
# into the hub module, keeping package dependencies acyclic.
bot.hub_error_reporter = post_server_log


