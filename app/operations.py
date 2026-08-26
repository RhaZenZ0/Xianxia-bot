from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
import urllib.parse
from typing import Any

log = logging.getLogger("xianxia.operations")


class AlertDispatcher:
    """Best-effort external webhook alerts with per-key cooldown.

    The gameplay loop never blocks on alert delivery: network work runs in a
    worker thread, failures are logged, and cooldowns prevent alert storms.
    """

    def __init__(self, webhook_url: str | None, *, cooldown_seconds: int = 300) -> None:
        self.webhook_url = (webhook_url or "").strip()
        self.cooldown_seconds = max(0, int(cooldown_seconds))
        self._last_sent: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def due(self, key: str) -> bool:
        if not self.enabled:
            return False
        return time.time() - self._last_sent.get(str(key), 0.0) >= self.cooldown_seconds

    async def send(self, key: str, message: str, *, severity: str = "warning", details: dict[str, Any] | None = None) -> bool:
        if not self.due(key):
            return False
        payload = {
            "service": "xianxia-discord-bot",
            "severity": str(severity),
            "key": str(key),
            "message": str(message),
            "details": details or {},
            "created_at": time.time(),
        }
        host = (urllib.parse.urlparse(self.webhook_url).hostname or "").casefold()
        if host in {"discord.com", "discordapp.com", "canary.discord.com", "ptb.discord.com"} and "/api/webhooks/" in self.webhook_url:
            detail_text = json.dumps(details or {}, ensure_ascii=False, sort_keys=True)
            discord_payload = {
                "username": "Xianxia Bot Health",
                "content": f"**[{str(severity).upper()}] {key}**\n{message}\n```json\n{detail_text[:1500]}\n```",
                "allowed_mentions": {"parse": []},
            }
            body = json.dumps(discord_payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        else:
            body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

        def _post() -> None:
            req = urllib.request.Request(
                self.webhook_url,
                data=body,
                headers={"Content-Type": "application/json", "User-Agent": "xianxia-bot-health/1"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                if not 200 <= int(response.status) < 300:
                    raise RuntimeError(f"alert webhook HTTP {response.status}")

        try:
            await asyncio.to_thread(_post)
        except Exception as exc:
            log.warning("ALERT_FAILED key=%s error=%s", key, exc)
            return False
        self._last_sent[str(key)] = time.time()
        log.info("ALERT_SENT key=%s severity=%s", key, severity)
        return True
