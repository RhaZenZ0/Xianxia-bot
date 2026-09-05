from __future__ import annotations

import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Iterable


# Deliberately small stop-list. Xianxia terms such as qi, dao, sect, soul and
# core are useful retrieval keys and therefore are NOT treated as stop words.
_STOP_WORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "onto",
    "your", "you", "their", "they", "them", "then", "than", "there", "here",
    "what", "when", "where", "which", "while", "have", "has", "had", "will",
    "would", "could", "should", "about", "after", "before", "again", "just",
    "want", "try", "trying", "look", "looks", "make", "does", "doing", "say",
    "says", "said", "tell", "tells", "told", "some", "more", "very", "really",
    "through", "around", "toward", "towards", "inside", "outside", "over", "under",
}

_WORD_RE = re.compile(r"[\w'-]+", re.UNICODE)


def build_fts_query(text: str, *, extra_terms: Iterable[str] = (), max_terms: int = 14) -> str:
    """Turn untrusted RP text into a safe FTS5 OR query.

    We never pass player text directly to MATCH, which avoids FTS operators,
    malformed quotes and accidental broad queries becoming executable syntax.
    """
    terms: list[str] = []
    seen: set[str] = set()
    source = f"{text} {' '.join(str(x) for x in extra_terms if x)}"
    for raw in _WORD_RE.findall(source.casefold()):
        token = raw.strip("'-_")
        if not token or token in _STOP_WORDS:
            continue
        if len(token) < 2 and token not in {"qi"}:
            continue
        if token in seen:
            continue
        seen.add(token)
        terms.append(token)
        if len(terms) >= max(1, int(max_terms)):
            break
    # Tokens come from \w / apostrophe / dash only, but quote them anyway so
    # words such as OR or NEAR can never become FTS operators.
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _mentioned(text: str, value: str) -> bool:
    needle = " ".join(str(value or "").casefold().split())
    haystack = " ".join(str(text or "").casefold().split())
    return bool(needle and len(needle) >= 3 and needle in haystack)


class TTLCache:
    """Tiny bounded in-process cache for deterministic retrieval results.

    It intentionally stores only already permission-scoped keys/results. The
    game database remains authoritative and no cache entry can mutate state.
    """

    def __init__(self, *, ttl_seconds: float, max_entries: int):
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self.max_entries = max(1, int(max_entries))
        self._items: OrderedDict[Any, tuple[float, Any]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: Any) -> Any | None:
        if self.ttl_seconds <= 0:
            self.misses += 1
            return None
        item = self._items.get(key)
        if item is None:
            self.misses += 1
            return None
        expires_at, value = item
        if expires_at < time.monotonic():
            self._items.pop(key, None)
            self.misses += 1
            return None
        self._items.move_to_end(key)
        self.hits += 1
        return value

    def put(self, key: Any, value: Any) -> None:
        if self.ttl_seconds <= 0:
            return
        self._items[key] = (time.monotonic() + self.ttl_seconds, value)
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses, "entries": len(self._items)}


@dataclass(frozen=True, slots=True)
class RetrievalProfile:
    """Scene-specific RAG/context budget.

    Context characters are deliberately smaller for routine scenes so a local
    narrator spends less time prefilling prompts. Epic scenes keep more memory
    and canon because continuity matters more than minimum latency there.
    """

    name: str
    context_chars: int
    rag_chars: int
    memory_limit: int
    canon_limit: int
    memory_candidates: int
    canon_candidates: int
    history_limit: int
    history_candidates: int
    fts_terms: int
    nearby_npc_limit: int
    director_npc_limit: int


_PROFILE_DEFAULTS: dict[str, RetrievalProfile] = {
    # name, context, rag, memory/canon limits+candidates, history limit+candidates, fts terms, nearby/director NPCs
    "dialogue": RetrievalProfile("dialogue", 2700, 750, 4, 1, 12, 20, 2, 12, 9, 3, 1),
    "roleplay": RetrievalProfile("roleplay", 2900, 900, 4, 2, 16, 28, 3, 18, 10, 5, 2),
    "exploration": RetrievalProfile("exploration", 3900, 1500, 4, 4, 18, 40, 4, 24, 12, 6, 2),
    "battle": RetrievalProfile("battle", 3200, 900, 3, 1, 12, 18, 2, 14, 8, 3, 1),
    "cultivation": RetrievalProfile("cultivation", 3500, 1200, 4, 3, 16, 30, 3, 18, 10, 4, 1),
    "epic": RetrievalProfile("epic", 6200, 2500, 6, 5, 20, 50, 6, 36, 14, 8, 4),
    "default": RetrievalProfile("default", 3500, 1700, 6, 5, 20, 50, 4, 24, 14, 5, 2),
}


def resolve_retrieval_profile(
    scene_type: str,
    *,
    focus_npc: str = "",
    routine_cap: int = 4000,
    epic_cap: int = 6500,
) -> RetrievalProfile:
    """Map arbitrary scene labels to a compact deterministic retrieval profile."""
    scene = " ".join(str(scene_type or "").casefold().replace("_", " ").replace(":", " ").split())
    if any(x in scene for x in ("breakthrough", "tribulation", "ascension", "sect war", "world event", "major event", "major sect")):
        base = _PROFILE_DEFAULTS["epic"]
        cap = max(int(routine_cap), int(epic_cap))
    elif any(x in scene for x in ("battle", "combat", "hunt", "duel")):
        base = _PROFILE_DEFAULTS["battle"]
        cap = int(routine_cap)
    elif any(x in scene for x in ("exploration", "expedition", "secret realm")):
        base = _PROFILE_DEFAULTS["exploration"]
        cap = int(routine_cap)
    elif any(x in scene for x in ("cultivation", "meditation", "seclusion", "alchemy", "craft")):
        base = _PROFILE_DEFAULTS["cultivation"]
        cap = int(routine_cap)
    elif focus_npc or any(x in scene for x in ("dialogue", "talk", "recommendation", "npc")):
        base = _PROFILE_DEFAULTS["dialogue"]
        cap = int(routine_cap)
    elif any(x in scene for x in ("roleplay", "scene action", "structured scene")):
        base = _PROFILE_DEFAULTS["roleplay"]
        cap = int(routine_cap)
    else:
        base = _PROFILE_DEFAULTS["default"]
        cap = int(routine_cap)

    # NARRATOR_CONTEXT_MAX_CHARS remains the hard ceiling and also scales the
    # profile. 4000 is the routine baseline; 6500 is the epic baseline. This
    # preserves operator tuning while still making dialogue/battle cheaper than
    # exploration or major events at the same configured cap.
    baseline = 6500 if base.name == "epic" else 4000
    scaled_context = round(int(base.context_chars) * max(1800, cap) / baseline)
    effective_context = max(1800, min(max(1800, cap), scaled_context))
    rag_scale = effective_context / max(1, int(base.context_chars))
    effective_rag = min(max(500, effective_context // 2), max(500, round(int(base.rag_chars) * rag_scale)))
    return RetrievalProfile(
        name=base.name,
        context_chars=effective_context,
        rag_chars=effective_rag,
        memory_limit=base.memory_limit,
        canon_limit=base.canon_limit,
        memory_candidates=base.memory_candidates,
        canon_candidates=base.canon_candidates,
        history_limit=base.history_limit,
        history_candidates=base.history_candidates,
        fts_terms=base.fts_terms,
        nearby_npc_limit=base.nearby_npc_limit,
        director_npc_limit=base.director_npc_limit,
    )


@dataclass(slots=True)
class RAGContext:
    text: str = ""
    memory_count: int = 0
    canon_count: int = 0
    history_count: int = 0
    fts_query: str = ""
    profile: str = "default"
    memory_cache_hit: bool = False
    canon_cache_hit: bool = False
    history_cache_hit: bool = False
    context_cache_hit: bool = False


class MemoryRAGRetriever:
    """Permission-safe deterministic retrieval for narrator context.

    v1 intentionally has no embeddings and no LLM summarizer. It combines:
    * structured facts already owned by SQLite,
    * FTS5 player memories scoped by user_id,
    * FTS5 narrator-safe canon scoped by location / learned manuals,
    * structured + FTS5 world history with viewpoint-aware permissions.

    Retrieval text is context only. The game engine remains authoritative.
    """

    def __init__(
        self,
        *,
        db: Any,
        world: Any,
        query_cache_seconds: float = 4.0,
        canon_cache_seconds: float = 120.0,
    ):
        self.db = db
        self.world = world
        # Memory rows are user-scoped in the key and cached only very briefly.
        # Canon is immutable between startup resyncs, so a longer TTL is safe.
        self._memory_cache = TTLCache(ttl_seconds=query_cache_seconds, max_entries=512)
        self._context_cache = TTLCache(ttl_seconds=query_cache_seconds, max_entries=512)
        self._canon_cache = TTLCache(ttl_seconds=canon_cache_seconds, max_entries=256)
        self._history_cache = TTLCache(ttl_seconds=max(query_cache_seconds, 6.0), max_entries=384)

    @staticmethod
    def _memory_score(row: dict[str, Any], *, query_text: str, location: str, game_minute: int) -> float:
        # FTS bm25 is lower-is-better. Convert it to a modest relevance bonus
        # rather than letting lexical score overpower salience and locality.
        raw_rank = float(row.get("fts_rank") or 0.0)
        lexical = 1.0 / (1.0 + abs(raw_rank))
        salience = max(0.0, min(1.0, float(row.get("salience") or 0) / 100.0))
        age = max(0, int(game_minute) - int(row.get("game_minute") or 0))
        # Half-life-ish recency curve. Old high-salience memories still survive.
        recency = 1.0 / (1.0 + age / 12000.0)
        locality = 0.35 if location and str(row.get("location") or "") == location else 0.0
        npc_bonus = 0.55 if _mentioned(query_text, str(row.get("npc_name") or "")) else 0.0
        kind = str(row.get("memory_kind") or "")
        kind_bonus = 0.28 if kind in {"vow", "betrayal", "threat", "debt", "rescue", "secret", "alliance"} else 0.0
        return lexical * 0.75 + salience * 1.25 + recency * 0.65 + locality + npc_bonus + kind_bonus

    @staticmethod
    def _history_allowed(
        row: dict[str, Any], *, user_id: int, focus_npc: str, known_factions: set[str],
    ) -> bool:
        visibility = str(row.get("visibility") or "public").strip().lower()
        if visibility == "hidden":
            return False
        if visibility == "public":
            return True
        if visibility == "faction":
            faction = str(row.get("faction") or "").casefold().strip()
            return bool(faction and faction in known_factions)

        # Participant history is deliberately viewpoint-aware. During focused
        # NPC dialogue the NPC may recall only events involving itself, not the
        # player's private history. Outside focused dialogue, the player-owned
        # narrator can recall participant events tied to the player.
        if visibility == "participant":
            if focus_npc:
                focus = str(focus_npc).casefold().strip()
                return any(
                    str(row.get(field) or "").casefold().strip() == focus
                    for field in ("related_npc_name", "actor_name", "target_name")
                )
            uid = str(int(user_id))
            return (
                int(row.get("related_user_id") or 0) == int(user_id)
                or str(row.get("actor_key") or "").strip() == uid
                or str(row.get("target_key") or "").strip() == uid
            )
        return False

    @staticmethod
    def _history_score(
        row: dict[str, Any], *, query_text: str, location: str, game_minute: int, known_factions: set[str],
    ) -> float:
        raw_rank = row.get("fts_rank")
        lexical = 0.15 if raw_rank is None else 1.0 / (1.0 + abs(float(raw_rank or 0.0)))
        significance = max(0.0, min(1.0, float(row.get("significance") or 0) / 100.0))
        age = max(0, int(game_minute) - int(row.get("game_minute") or 0))
        # Historical significance decays much more slowly than personal memory.
        recency = 1.0 / (1.0 + age / 60000.0)
        locality = 0.45 if location and str(row.get("location") or "") == str(location) else 0.0
        faction = str(row.get("faction") or "").casefold().strip()
        faction_bonus = 0.3 if faction and faction in known_factions else 0.0
        mention_bonus = 0.0
        for field in ("actor_name", "target_name", "title"):
            if _mentioned(query_text, str(row.get(field) or "")):
                mention_bonus = max(mention_bonus, 0.5)
        event_type = str(row.get("event_type") or "")
        type_bonus = 0.25 if event_type in {
            "death", "war_started", "war_resolved", "marriage", "alliance", "betrayal",
            "leadership_change", "location_destroyed", "major_battle", "inheritance",
            "ascension", "blood_feud", "discovery", "friendship", "discipleship",
            "faction_change", "rank_promotion", "descendant_birth", "npc_breakthrough",
        } else 0.0
        return lexical * 0.75 + significance * 1.45 + recency * 0.55 + locality + faction_bonus + mention_bonus + type_bonus

    @staticmethod
    def _manual_allowed(doc: dict[str, Any], known_manual_ids: set[str]) -> bool:
        key = str(doc.get("source_key") or "")
        if key.startswith("manual:"):
            return key.split(":", 1)[1] in known_manual_ids
        if key.startswith("technique:"):
            tags = str(doc.get("tags") or "")
            return any(f"manual:{manual_id}" in tags for manual_id in known_manual_ids)
        return False

    def cache_stats(self) -> dict[str, dict[str, int]]:
        return {
            "context": self._context_cache.stats(),
            "memory": self._memory_cache.stats(),
            "canon": self._canon_cache.stats(),
            "history": self._history_cache.stats(),
        }

    def clear_caches(self) -> None:
        self._context_cache.clear()
        self._memory_cache.clear()
        self._canon_cache.clear()
        self._history_cache.clear()

    async def retrieve(
        self,
        character: dict[str, Any],
        *,
        query_text: str,
        game_minute: int,
        location: str,
        known_manuals: list[dict[str, Any]] | None = None,
        known_factions: list[str] | tuple[str, ...] | None = None,
        focus_npc: str = "",
        max_chars: int | None = None,
        profile: RetrievalProfile | None = None,
    ) -> RAGContext:
        user_id = int(character.get("user_id") or 0)
        query_text = str(query_text or "").strip()
        profile = profile or _PROFILE_DEFAULTS["default"]
        if not user_id or not query_text:
            return RAGContext(profile=profile.name)

        extra = [location, focus_npc]
        fts_query = build_fts_query(query_text, extra_terms=extra, max_terms=profile.fts_terms)
        if not fts_query:
            return RAGContext(profile=profile.name)

        known_manual_ids = {
            str(row.get("manual_id") or "").strip()
            for row in (known_manuals or []) if row.get("manual_id")
        }
        known_faction_set = {
            str(x).casefold().strip() for x in (known_factions or []) if str(x).strip()
        }
        effective_max_chars = max(500, int(max_chars if max_chars is not None else profile.rag_chars))
        normalized_query = " ".join(query_text.casefold().split())
        context_cache_key = (
            user_id, normalized_query, fts_query, str(location), str(focus_npc).casefold().strip(),
            tuple(sorted(known_manual_ids)), tuple(sorted(known_faction_set)), profile.name,
            profile.memory_limit, profile.canon_limit, profile.history_limit, effective_max_chars,
        )
        cached_context = self._context_cache.get(context_cache_key)
        if cached_context is not None:
            cached = cached_context
            return RAGContext(
                text=cached.text, memory_count=cached.memory_count, canon_count=cached.canon_count,
                history_count=cached.history_count, fts_query=cached.fts_query, profile=cached.profile,
                memory_cache_hit=True, canon_cache_hit=True, history_cache_hit=True, context_cache_hit=True,
            )

        memory_cache_key = (user_id, fts_query, int(profile.memory_candidates))
        cached_memories = self._memory_cache.get(memory_cache_key)
        memory_cache_hit = cached_memories is not None
        if cached_memories is None:
            try:
                memory_rows = await self.db.search_rag_memories(
                    user_id, fts_query, limit=profile.memory_candidates, mark_recalled=False,
                )
            except Exception:
                memory_rows = []
            self._memory_cache.put(memory_cache_key, list(memory_rows))
        else:
            memory_rows = list(cached_memories)

        if focus_npc:
            focused: list[dict[str, Any]] = []
            focus_key = str(focus_npc).casefold().strip()
            for row in memory_rows:
                row_npc = str(row.get("npc_name") or "").casefold().strip()
                # An NPC can recall its own exchanges and public/general world
                # episodes, but not private talk/freeform memories belonging to
                # another NPC. This prevents retrieval-induced omniscience.
                if row_npc and row_npc != focus_key:
                    continue
                if not row_npc and str(row.get("source") or "") in {"talk", "freeform"}:
                    continue
                focused.append(row)
            memory_rows = focused

        ranked_memories = sorted(
            memory_rows,
            key=lambda r: self._memory_score(r, query_text=query_text, location=location, game_minute=game_minute),
            reverse=True,
        )[: profile.memory_limit]

        canon_cache_key = (fts_query, int(profile.canon_candidates))
        cached_canon = self._canon_cache.get(canon_cache_key)
        canon_cache_hit = cached_canon is not None
        if cached_canon is None:
            try:
                canon_candidates = await self.db.search_rag_canon(
                    fts_query, limit=profile.canon_candidates,
                )
            except Exception:
                canon_candidates = []
            self._canon_cache.put(canon_cache_key, list(canon_candidates))
        else:
            canon_candidates = list(cached_canon)

        canon_rows: list[dict[str, Any]] = []
        for row in canon_candidates:
            scope = str(row.get("knowledge_scope") or "global")
            allowed = False
            if scope == "global":
                allowed = True
            elif scope == "location":
                # Only current-location prose is retrievable. This prevents FTS
                # from revealing undiscovered places or their encounter content.
                allowed = str(row.get("location") or "") == str(location)
            elif scope == "known_manual":
                allowed = self._manual_allowed(row, known_manual_ids)
            if not allowed:
                continue
            canon_rows.append(row)
            if len(canon_rows) >= profile.canon_limit:
                break

        # World history uses both structured locality/participant candidates and
        # lexical FTS candidates. This lets a historically important local event
        # survive even when the player's exact wording does not match its prose.
        history_cache_key = (
            user_id, fts_query, str(location), str(focus_npc).casefold().strip(),
            tuple(sorted(known_faction_set)), int(profile.history_candidates),
        )
        cached_history = self._history_cache.get(history_cache_key)
        history_cache_hit = cached_history is not None
        if cached_history is None:
            history_candidates: list[dict[str, Any]] = []
            try:
                structured = await self.db.get_structured_world_history(
                    location=location, user_id=(None if focus_npc else user_id), npc_name=focus_npc,
                    factions=tuple(known_factions or ()), min_significance=35,
                    limit=max(profile.history_candidates, 12),
                )
            except Exception:
                structured = []
            try:
                lexical_history = await self.db.search_world_history(
                    fts_query, limit=max(profile.history_candidates, 12),
                )
            except Exception:
                lexical_history = []
            merged: dict[int, dict[str, Any]] = {}
            for row in [*structured, *lexical_history]:
                hid = int(row.get("history_id") or 0)
                if not hid:
                    continue
                current = merged.get(hid)
                if current is None or (current.get("fts_rank") is None and row.get("fts_rank") is not None):
                    merged[hid] = dict(row)
            history_candidates = list(merged.values())
            self._history_cache.put(history_cache_key, history_candidates)
        else:
            history_candidates = list(cached_history)

        allowed_history = [
            row for row in history_candidates
            if self._history_allowed(
                row, user_id=user_id, focus_npc=focus_npc, known_factions=known_faction_set,
            )
        ]
        ranked_history = sorted(
            allowed_history,
            key=lambda r: self._history_score(
                r, query_text=query_text, location=location, game_minute=game_minute,
                known_factions=known_faction_set,
            ),
            reverse=True,
        )[: profile.history_limit]

        lines: list[str] = []
        if ranked_memories:
            lines.append("RETRIEVED PLAYER MEMORY — prior recollections only; never override current mechanics:")
            for row in ranked_memories:
                kind = str(row.get("memory_kind") or "scene").replace("_", " ")
                where = str(row.get("location") or "")
                npc = str(row.get("npc_name") or "")
                tags = "; ".join(x for x in (f"at {where}" if where else "", f"involving {npc}" if npc else "") if x)
                suffix = f" ({tags})" if tags else ""
                lines.append(f"- [{kind}; salience {int(row.get('salience') or 0)}/100]{suffix} {_clip(row.get('summary'), 360)}")

        if ranked_history:
            lines.append("RETRIEVED WORLD HISTORY — canonical past events; current structured state wins if circumstances later changed:")
            for row in ranked_history:
                etype = str(row.get("event_type") or "event").replace("_", " ")
                where = str(row.get("location") or "")
                faction = str(row.get("faction") or "")
                details = "; ".join(x for x in (where, faction) if x)
                suffix = f"; {details}" if details else ""
                lines.append(
                    f"- [{etype}; significance {int(row.get('significance') or 0)}/100; game-minute {int(row.get('game_minute') or 0)}{suffix}] "
                    f"{_clip(row.get('title'), 120)} — {_clip(row.get('summary'), 390)}"
                )

        if canon_rows:
            lines.append("RETRIEVED KNOWN CANON — descriptive lore only; structured game state wins on any conflict:")
            for row in canon_rows:
                lines.append(f"- **{_clip(row.get('title'), 100)}**: {_clip(row.get('body'), 350)}")

        if not lines:
            result = RAGContext(
                fts_query=fts_query, profile=profile.name,
                memory_cache_hit=memory_cache_hit, canon_cache_hit=canon_cache_hit, history_cache_hit=history_cache_hit,
            )
            self._context_cache.put(context_cache_key, result)
            return result

        text = "\n".join(lines)
        budget = effective_max_chars
        if len(text) > budget:
            text = text[: budget - 70].rstrip() + "\n[RAG context truncated to its scene budget.]"
        result = RAGContext(
            text=text,
            memory_count=len(ranked_memories),
            canon_count=len(canon_rows),
            history_count=len(ranked_history),
            fts_query=fts_query,
            profile=profile.name,
            memory_cache_hit=memory_cache_hit,
            canon_cache_hit=canon_cache_hit,
            history_cache_hit=history_cache_hit,
        )
        self._context_cache.put(context_cache_key, result)
        return result
