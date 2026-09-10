"""Typed play: the router (v0.21.1). Pure logic, no Discord, no I/O.

A line a player types in a scene channel is one of three things:

* **an action** - it starts with the typed-play prefix (``> I explore the
  ravine``). This module turns it into a *route*: which existing handler to
  run, with what target. It never resolves an outcome; the engine does that
  when the handler runs, exactly as if the player had pressed the hub button.
* **dialogue** - un-prefixed, but it addresses an NPC who is present
  (``Qiao, what is the caravan carrying?``). That is ``/talk``.
* **speech** - everything else. History only, no call, no cost.

Three deterministic stages, no model call:

1. **Verb table** (``content/typed_play.json``): whole-word alias phrases per
   action. A longer alias beats a shorter one; an alias at the start of the
   line beats one in the middle.
2. **Entity resolution** against what is *present*: the NPC names and
   "Player <id>: <name>" strings the scene panel already offers as targets.
   Naming an absent NPC is a canonical refusal, not a guess.
3. **Ambiguity -> picker.** Two candidates of equal standing, or none, become
   a list the player chooses from. The router never guesses.

Kept free of Discord so ``tests/python/unit/test_typed_play_router.py`` can
load it on its own and pin every rule above with a table of lines.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

DEFAULT_TABLE_PATH = Path(__file__).resolve().parents[2] / "content" / "typed_play.json"

MAX_LINE_CHARS = 600
MAX_PICKER_CANDIDATES = 4
# A name token this short ("Li", "Yu") would match inside ordinary words.
MIN_NAME_TOKEN = 3
# Titles that many NPCs share; alone they do not identify one of them.
_TITLE_TOKENS = frozenset({
    "elder", "master", "steward", "sect", "lord", "lady", "emperor", "empress",
    "sovereign", "immortal", "celestial", "grand", "senior", "junior", "brother",
    "sister", "disciple", "young", "old", "the", "sir", "madam", "captain",
    "guard", "merchant", "patriarch", "matriarch", "prince", "princess", "king", "queen",
})

_WORD_RE = re.compile(r"[a-z0-9']+")


ARGUMENT_SOURCES = ("location", "item")


@dataclass(frozen=True)
class VerbAction:
    key: str
    kind: str                # "root" | "scene" | "talk"
    label: str
    aliases: tuple[str, ...]
    command: str = ""        # kind == root; a root name or a group leaf ("travel go")
    scene_action: str = ""   # kind == scene
    # A root that takes one argument (v0.33.0): the handler parameter it fills
    # and what the line is searched for to fill it - a known location or a
    # carried item. Unresolved, the action is not offered; the router never
    # guesses a destination.
    parameter: str = ""
    source: str = ""


@dataclass(frozen=True)
class Candidate:
    """One thing the line could mean. ``payload`` is what the dispatcher needs."""
    kind: str                # "root" | "scene" | "talk"
    label: str
    payload: dict[str, Any] = field(default_factory=dict)
    score: int = 0

    @property
    def id(self) -> str:
        if self.kind == "root":
            arguments = dict(self.payload.get("arguments") or {})
            suffix = "".join(f":{value}" for value in arguments.values())
            return f"root:{self.payload.get('command','')}{suffix}"
        if self.kind == "scene":
            return f"scene:{self.payload.get('scene_action','')}:{self.payload.get('target','')}"
        if self.kind == "talk":
            return f"talk:{self.payload.get('npc','')}"
        return self.kind


@dataclass(frozen=True)
class Route:
    """``kind`` is one of:

    * ``dispatch`` - exactly one candidate; run it.
    * ``picker``   - several candidates (or none); let the player choose.
    * ``refusal``  - the line named an NPC who is not here; ``message`` says so.
    """
    kind: str
    candidates: tuple[Candidate, ...] = ()
    message: str = ""
    text: str = ""

    @property
    def single(self) -> Candidate | None:
        return self.candidates[0] if self.kind == "dispatch" and self.candidates else None


class VerbTable:
    def __init__(self, actions: Sequence[VerbAction], leading_phrases: Sequence[str]) -> None:
        self.actions = tuple(actions)
        # Longest first so "i try to" is stripped before "i".
        self.leading_phrases = tuple(sorted((p.strip().casefold() for p in leading_phrases if p.strip()), key=len, reverse=True))
        self._alias_index: list[tuple[str, VerbAction]] = [
            (alias.casefold().strip(), action) for action in self.actions for alias in action.aliases if alias.strip()
        ]
        self._alias_index.sort(key=lambda item: len(item[0]), reverse=True)

    @classmethod
    def load(cls, path: Path | str = DEFAULT_TABLE_PATH) -> "VerbTable":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_data(data)

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> "VerbTable":
        actions: list[VerbAction] = []
        seen: set[str] = set()
        for raw in data.get("actions") or []:
            key = str(raw.get("key") or "").strip()
            kind = str(raw.get("kind") or "").strip()
            if not key or kind not in {"root", "scene", "talk"}:
                raise ValueError(f"typed_play.json: action {raw!r} needs a key and a kind of root/scene/talk")
            if key in seen:
                raise ValueError(f"typed_play.json: duplicate action key {key!r}")
            seen.add(key)
            if kind == "root" and not str(raw.get("command") or "").strip():
                raise ValueError(f"typed_play.json: root action {key!r} needs a command")
            if kind == "scene" and not str(raw.get("scene_action") or "").strip():
                raise ValueError(f"typed_play.json: scene action {key!r} needs a scene_action")
            argument = dict(raw.get("argument") or {})
            parameter = str(argument.get("parameter") or "").strip()
            source = str(argument.get("source") or "").strip()
            if argument and (kind != "root" or not parameter or source not in ARGUMENT_SOURCES):
                raise ValueError(f"typed_play.json: action {key!r} argument needs a parameter and a source of {'/'.join(ARGUMENT_SOURCES)}, on a root")
            actions.append(VerbAction(
                key=key, kind=kind, label=str(raw.get("label") or key),
                aliases=tuple(str(a) for a in (raw.get("aliases") or []) if str(a).strip()),
                command=str(raw.get("command") or ""), scene_action=str(raw.get("scene_action") or ""),
                parameter=parameter, source=source,
            ))
        return cls(actions, [str(p) for p in (data.get("leading_phrases") or [])])

    # -- stage 1: verbs ----------------------------------------------------

    def strip_leading(self, text: str) -> str:
        """Drop the 'I / I'll / let me / I try to' a player naturally starts with."""
        current = text.strip()
        for _ in range(2):  # "let me ... i ..." - two layers is plenty
            lowered = current.casefold()
            for phrase in self.leading_phrases:
                if lowered == phrase:
                    return ""
                if lowered.startswith(phrase + " "):
                    current = current[len(phrase):].lstrip(" ,")
                    break
            else:
                break
        return current

    def matches(self, text: str) -> list[tuple[VerbAction, str, int]]:
        """Every alias found as a whole-word phrase: (action, alias, position)."""
        lowered = " " + _normalise(text) + " "
        found: list[tuple[VerbAction, str, int]] = []
        for alias, action in self._alias_index:
            pos = lowered.find(" " + alias + " ")
            if pos != -1:
                found.append((action, alias, pos))
        return found

    def best(self, text: str) -> list[tuple[VerbAction, int]]:
        """Actions ranked; the top one is 'clear' when it stands alone or leads.

        Score: alias length, plus a large bonus for an alias at the start of
        the (leading-phrase-stripped) line. One action may match through
        several aliases; it keeps its best score.
        """
        stripped = self.strip_leading(text)
        scored: dict[str, tuple[VerbAction, int]] = {}
        for action, alias, pos in self.matches(stripped):
            score = len(alias) + (100 if pos == 0 else 0)
            if action.key not in scored or scored[action.key][1] < score:
                scored[action.key] = (action, score)
        return sorted(scored.values(), key=lambda item: (-item[1], item[0].key))

    def looks_like_action(self, text: str) -> bool:
        """An un-prefixed line that *would* have routed - used only for the hint."""
        ranked = self.best(text)
        return bool(ranked) and ranked[0][1] >= 100


# -- stage 2: entities -----------------------------------------------------

def _normalise(text: str) -> str:
    return " ".join(_WORD_RE.findall(text.casefold().replace("’", "'")))


def _name_tokens(name: str) -> list[str]:
    """Distinctive tokens: long enough to be safe as a word, and not a shared title."""
    return [t for t in _WORD_RE.findall(name.casefold()) if len(t) >= MIN_NAME_TOKEN and t not in _TITLE_TOKENS]


def _bare_name(name: str) -> str:
    """The name without titles, short tokens kept: "Elder Mu Feng" -> "mu feng".

    Used only as a whole phrase, so "mu" is safe here even though it is too
    short to stand alone as a token.
    """
    tokens = [t for t in _WORD_RE.findall(name.casefold()) if t not in _TITLE_TOKENS]
    return " ".join(tokens) if len(tokens) >= 2 else ""


def _player_target_name(target: str) -> str:
    # "Player 42: Li Feng" -> "Li Feng"
    if target.startswith("Player ") and ":" in target:
        return target.split(":", 1)[1].strip()
    return ""


def resolve_entities(text: str, present: Iterable[str]) -> list[str]:
    """Which of the present targets the line names, most specific first.

    ``present`` is the scene panel's target list: NPC names and
    ``"Player <id>: <name>"`` strings. A target matches, in order of strength,
    on its full display name ("Elder Mu Feng"), on its name without shared
    titles ("Mu Feng"), or on one distinctive token ("Qiao": >= 3 letters, not
    a title). A token shared by two present targets ("Feng" in "Elder Mu Feng"
    and "Li Feng") identifies neither by itself.
    """
    words = set(_normalise(text).split())
    normalised = " " + _normalise(text) + " "
    present_list = list(present)
    scored: dict[str, int] = {}
    token_owners: dict[str, list[str]] = {}
    for target in present_list:
        display = _player_target_name(target) or target
        full = _normalise(display)
        bare = _bare_name(display)
        if full and " " + full + " " in normalised:
            scored[target] = max(scored.get(target, 0), 1000 + len(full))
        elif bare and bare != full and " " + bare + " " in normalised:
            scored[target] = max(scored.get(target, 0), 900 + len(bare))
        for token in _name_tokens(display):
            token_owners.setdefault(token, []).append(target)
    for token, owners in token_owners.items():
        if token in words and len(owners) == 1 and owners[0] not in scored:
            scored[owners[0]] = len(token)
    return [target for target, _ in sorted(scored.items(), key=lambda item: (-item[1], item[0]))]


def resolve_argument(text: str, names: Iterable[tuple[str, str]]) -> tuple[str, str] | None:
    """The one (value, display) pair the line names, or None (v0.33.0).

    ``names`` maps a value the handler takes (an item id, a location name) to
    the display name a player would type. Matching is resolve_entities' rule -
    full name, then a distinctive token - so "go to greenriver" finds
    Greenriver Town and "drink a pill" finds nothing when three carried pills
    share the token. Two names of equal standing resolve to neither: an
    argument is dispatched with, never guessed.
    """
    by_display: dict[str, str] = {}
    for value, display in names:
        display = str(display).strip()
        if display and display not in by_display:
            by_display[display] = str(value)
    if not by_display:
        return None
    ranked = resolve_entities(text, by_display.keys())
    if not ranked:
        return None
    # resolve_entities puts full-name matches first, longest first, so the
    # most specific name a line spells out wins ("Cloud Pill of the East"
    # over "Cloud Pill"). Without a full name, one token hit stands and two
    # different token hits ("greenriver and frostwatch") name nobody.
    normalised = " " + _normalise(text) + " "
    if " " + _normalise(ranked[0]) + " " in normalised:
        return by_display[ranked[0]], ranked[0]
    if len(ranked) > 1:
        return None
    return by_display[ranked[0]], ranked[0]


def named_absent_npc(text: str, present: Iterable[str], all_npcs: Iterable[str]) -> str | None:
    """An NPC the line names by full name who is NOT present - a refusal, not a guess.

    Full-name only: a bare token like "Qiao" in speech about someone should
    not turn into a refusal about them.
    """
    present_set = set(present)
    normalised = " " + _normalise(text) + " "
    for npc in all_npcs:
        if npc in present_set:
            continue
        if " " + _normalise(npc) + " " in normalised:
            return npc
    return None


def addressed_npc(text: str, present: Iterable[str]) -> str | None:
    """Does an un-prefixed line *address* a present NPC?

    Deliberately narrow: the name (or a distinctive token of it) is within the
    first two words, or the line is a question that names them. Mentioning an
    NPC to another player in passing ("I think Qiao is lying") does not count.
    Entities are resolved against everyone present, players included, so a
    token two of them share ("Feng") still identifies nobody.
    """
    present_list = list(present)
    if not any(not _player_target_name(t) for t in present_list):
        return None
    words = _normalise(text).split()
    if not words:
        return None
    hits = [t for t in resolve_entities(text, present_list) if not _player_target_name(t)]
    if not hits:
        return None
    npc = hits[0]
    leading = set(words[:2])
    tokens = set(_name_tokens(npc))
    if leading & tokens or " ".join(words[:4]).startswith(_normalise(npc)):
        return npc
    if text.strip().endswith("?"):
        return npc
    return None


# -- stage 3: route --------------------------------------------------------

def parse_prefixed(content: str, prefix: str) -> str | None:
    """The action text of a prefixed line, or None if the line is not prefixed.

    The prefix counts at the very start of the line with or without a space
    after it, and a line that is only the prefix is not an action. Matched with
    ``startswith`` rather than a pattern, so a prefix that means something in a
    regular expression - ``$``, the default since v0.25.1, or ``*`` or ``+`` -
    needs no escaping and cannot silently match nothing.
    """
    raw = content.lstrip()
    if not raw.startswith(prefix):
        return None
    text = raw[len(prefix):].strip()
    if not text:
        return None
    return text[:MAX_LINE_CHARS]


def route_line(
    text: str,
    *,
    table: VerbTable,
    present: Sequence[str],
    all_npcs: Iterable[str] = (),
    locations: Iterable[str] = (),
    items: Iterable[tuple[str, str]] = (),
) -> Route:
    """Turn the text of a prefixed line into a Route. See the module docstring.

    ``locations`` are the places this player knows and ``items`` the
    (id, name) pairs they carry: what a root with an argument is resolved
    against (v0.33.0).
    """
    text = text.strip()[:MAX_LINE_CHARS]
    sources = {
        "location": [(name, name) for name in locations],
        "item": list(items),
    }
    unresolved: list[VerbAction] = []
    entities = resolve_entities(text, present)
    npc = next((e for e in entities if not _player_target_name(e)), None)
    player_target = next((e for e in entities if _player_target_name(e)), None)
    target = npc or player_target or "Environment"

    if not entities:
        absent = named_absent_npc(text, present, all_npcs)
        if absent:
            return Route("refusal", message=f"**{absent}** is not here.", text=text)

    ranked = table.best(text)
    candidates: list[Candidate] = []
    for action, score in ranked:
        if action.kind == "root":
            if action.parameter:
                found = resolve_argument(text, sources.get(action.source, ()))
                if found is None:
                    unresolved.append(action)
                    continue
                value, display = found
                candidates.append(Candidate("root", f"{action.label} → {display}",
                                            {"command": action.command, "arguments": {action.parameter: value}}, score))
                continue
            candidates.append(Candidate("root", action.label, {"command": action.command}, score))
        elif action.kind == "scene":
            label = action.label if target == "Environment" else f"{action.label} → {_player_target_name(target) or target}"
            candidates.append(Candidate("scene", label, {"scene_action": action.scene_action, "target": target, "detail": text}, score))
        elif action.kind == "talk":
            if npc:
                candidates.append(Candidate("talk", f"Talk to {npc}", {"npc": npc, "message": text}, score))
            else:
                # Talk-shaped with nobody named: offer each present NPC.
                for present_npc in [t for t in present if not _player_target_name(t)][:MAX_PICKER_CANDIDATES - 1]:
                    candidates.append(Candidate("talk", f"Talk to {present_npc}", {"npc": present_npc, "message": text}, score - 1))

    if not ranked and npc:
        # "> Qiao, do you have work?" - a name and no verb is dialogue.
        candidates.append(Candidate("talk", f"Talk to {npc}", {"npc": npc, "message": text}, 100))

    if not candidates:
        if unresolved:
            wants = {"location": "a place you know", "item": "something you carry"}
            asks = sorted({f"{a.label.lower()} needs {wants.get(a.source, 'a name')}" for a in unresolved})
            return Route("picker", (), message="; ".join(asks).capitalize() + ".", text=text)
        return Route("picker", (), text=text)

    top = candidates[0]
    if len(candidates) == 1:
        return Route("dispatch", (top,), text=text)
    # Clear winner: leads the line (>= 100) and the runner-up does not, or
    # the runner-up is the same action aimed at a different NPC (talk fan-out).
    runner = candidates[1]
    if top.score >= 100 and runner.score < 100:
        return Route("dispatch", (top,), text=text)
    return Route("picker", tuple(candidates[:MAX_PICKER_CANDIDATES]), text=text)
