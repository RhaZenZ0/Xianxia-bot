#!/usr/bin/env python3
"""Write the playtest checklist (v0.34.0): every hub, page and action, with
the columns a machine can fill and the ones only a live server can.

    python3 scripts/playtest_checklist.py            # writes docs/playtest/v<version>.md
    python3 scripts/playtest_checklist.py --check    # exits 1 if the checked-in file is missing an action

The walk is source-level, like the contract tests: hub definitions from
surface.py, the page-to-command map, every registered root and group leaf,
each parameter's picker.

**Every action carried three live checkboxes until v1.0.0-rc.47** - reachable
from the hub, error text actionable, narration or fallback fired - written
for a person at the keyboard, because when this generator was written in
v0.34.0 there was no other way to press a button. There is now:
`scripts/playtest_discord.py` (v1.0.0-rc.33) boots the real bot under a
simulated Discord and presses every leaf the hubs register, and
`tests/python/contracts/test_playtest_coverage.py` holds it to the live
definitions so a new leaf is covered the day it is registered. The sweep is
what ticks the first column, and most of the second.

Two hundred and forty-eight actions times three boxes is seven hundred and
forty-four, and **not one of them was ever ticked** - across twelve releases
of regenerating this file. A list that large is not a plan, and `merge_ticks`
spent those releases carefully preserving nothing. So the per-action columns
are one machine-filled `Swept` column now, and what genuinely needs a person
is the short table at the end, which is the length somebody actually walks.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "app" / "bot"
DISCORD_SCRIPT = ROOT / "scripts" / "playtest_discord.py"
sys.path.insert(0, str(ROOT))

FREE_TEXT = {"name", "spirit_name", "reason", "message", "action", "stakes", "rule", "definition", "duration", "category_name", "story"}


def deferred_leaves() -> dict[str, str]:
    """`DEFERRED_LEAVES` from the Discord harness, read without importing it.

    The harness may only import the bot after it has set the environment, so
    this is the same AST read `test_playtest_coverage.py` makes. Reading it
    rather than restating it is what keeps the `Swept` column from claiming
    more than the sweep actually drives: a leaf deferred there is deferred
    here the moment the checklist is regenerated, and the gate holds the two
    equal so it cannot be forgotten.
    """
    tree = ast.parse(DISCORD_SCRIPT.read_text(encoding="utf-8"))
    for node in tree.body:
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign):
            target = next((t.id for t in node.targets if isinstance(t, ast.Name)), None)
        if target == "DEFERRED_LEAVES":
            return dict(ast.literal_eval(node.value))
    raise AssertionError("playtest_discord.py has no module-level DEFERRED_LEAVES")


def version() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def _sources() -> dict[Path, str]:
    return {p: p.read_text(encoding="utf-8") for p in sorted(BOT.rglob("*.py"))}


def _groups(sources: dict[Path, str]) -> dict[str, str]:
    """group variable -> the group's full Discord path ("sect recruitment").

    Read with ast, and following `parent=`: a subgroup is declared over several
    lines, so the single-line regex this used to be matched none of them, and
    every nested command - all of `/sect recruitment`, `/sect discipleship`
    and `/sect manor` - was missing from the checklist entirely.
    """
    parents: dict[str, str] = {}
    names: dict[str, str] = {}
    for text in sources.values():
        for node in ast.walk(ast.parse(text)):
            if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
                continue
            call = node.value
            if not (isinstance(call.func, ast.Attribute) and call.func.attr == "Group"):
                continue
            target = next((t.id for t in node.targets if isinstance(t, ast.Name)), None)
            if target is None:
                continue
            for keyword in call.keywords:
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                    names[target] = str(keyword.value.value)
                elif keyword.arg == "parent" and isinstance(keyword.value, ast.Name):
                    parents[target] = keyword.value.id
    out: dict[str, str] = {}
    for var, name in names.items():
        path, seen = [name], {var}
        cursor = var
        while cursor in parents and parents[cursor] not in seen:
            cursor = parents[cursor]
            seen.add(cursor)
            path.insert(0, names.get(cursor, cursor))
        out[var] = " ".join(path)
    return out


def _commands(sources: dict[Path, str], groups: dict[str, str]):
    """(kind, owner, name, qualified path, function node, decorators) for every registered command."""
    all_text = "\n".join(sources.values())
    rows = []
    for path, text in sources.items():
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call) or getattr(dec.func, "id", "") not in ("registered_group_command", "registered_root_command"):
                    continue
                name = next((k.value.value for k in dec.keywords if k.arg == "name"), None)
                if not name:
                    continue
                if dec.func.id == "registered_root_command":
                    rows.append(("root", "", name, f"/{name}", node, path, all_text))
                elif dec.args and isinstance(dec.args[0], ast.Name):
                    rows.append(("leaf", dec.args[0].id, name, f"/{groups.get(dec.args[0].id, dec.args[0].id)} {name}", node, path, all_text))
    return rows


def _parameters(node: ast.AsyncFunctionDef, all_text: str) -> list[str]:
    choices, autocompletes = set(), set()
    for d in node.decorator_list:
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute):
            keywords = {k.arg for k in d.keywords if k.arg}
            if d.func.attr == "choices":
                choices |= keywords
            elif d.func.attr == "autocomplete":
                autocompletes |= keywords
    out = []
    for arg in node.args.args[1:]:
        annotation = ast.unparse(arg.annotation) if arg.annotation else ""
        # An optional parameter is its type: `hours: int | None` is a number
        # input, not a free string. Before this it fell through every branch to
        # TYPED, which is the label for "a typed id with no picker" and is what
        # the release gate refuses.
        annotation = re.sub(r"\s*\|\s*None\b", "", annotation)
        annotation = re.sub(r"^Optional\[(.*)\]$", r"\1", annotation).strip()
        name = arg.arg
        if name in choices:
            how = "choices"
        elif name in autocompletes or re.search(rf"@{node.name}\.autocomplete\(\"{name}\"\)", all_text) or re.search(rf"register_hub_option_provider\({node.name},\s*\"{name}\"", all_text):
            how = "picker"
        elif "Member" in annotation or "Channel" in annotation:
            how = "select"
        elif annotation in ("bool",) or "Range[" in annotation or annotation in ("int", "float"):
            how = "guided"
        elif "Choice[" in annotation:
            how = "choices"
        elif name in FREE_TEXT:
            how = "prose"
        else:
            how = "TYPED"
        hint = " +hint" if re.search(rf"register_hub_option_hint\(\s*{node.name},\s*\"{name}\"", all_text) else ""
        out.append(f"{name}:{how}{hint}")
    return out


def _acked(node: ast.AsyncFunctionDef) -> str:
    """Whether a handler that reaches the engine acknowledges first. The
    contract test is the judge; this column only says which handlers it
    covers ("yes") and which never mutate ("n/a")."""
    text = ast.unparse(node)
    if "authoritative_action(" not in text:
        return "n/a"
    return "yes" if any(marker in text for marker in ("response.defer(", "is_done()", "response.send_message(", "respond(", "edit_message(")) else "via helper"


def _call_end(text: str, start: int) -> int:
    """Index of the parenthesis that closes a call opened just before `start`,
    ignoring parentheses inside string literals."""
    depth, index, quote = 1, start, ""
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return len(text)


def _literal(node) -> object | None:
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _page_from_call(call: ast.Call) -> tuple[str, str, tuple[str, ...], tuple[str, ...]] | None:
    """(page key, label, extra roots, `only` selectors) for one page call.

    Read with ast rather than by counting quoted strings: since v1.0.0-rc.13 a
    page call can carry `only=` and `key=` keywords, and a positional read
    counted every selector string in `only` as another root - which rendered
    each of `/sect`'s four pages as all twenty-five actions.
    """
    name = getattr(call.func, "id", "")
    keywords = {k.arg: k.value for k in call.keywords if k.arg}
    only = tuple(_literal(keywords["only"]) or ()) if "only" in keywords else ()
    if name == "_hub_page":
        args = [_literal(a) for a in call.args]
        if len(args) < 2 or not isinstance(args[0], str) or not isinstance(args[1], str):
            return None
        root, label = args[0], args[1]
        extras = tuple(a for a in args[3:] if isinstance(a, str))
        key = _literal(keywords["key"]) if "key" in keywords else None
        return (str(key or root), label, (root, *extras) if key else (root, *extras), only)
    if name == "HubPage":
        key = _literal(keywords.get("key"))
        label = _literal(keywords.get("label"))
        if not isinstance(key, str) or not isinstance(label, str):
            return None
        roots = []
        for field in ("command", "extras"):
            value = keywords.get(field)
            if value is None:
                continue
            if isinstance(value, ast.Name):
                roots.append(value.id)
            elif isinstance(value, ast.Tuple):
                roots += [e.id for e in value.elts if isinstance(e, ast.Name)]
        return (key, label, tuple(roots), only)
    return None


def _hubs(sources: dict[Path, str]):
    tree = ast.parse(sources[BOT / "surface.py"])
    hubs = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "HubDefinition"):
            continue
        keywords = {k.arg: k.value for k in node.keywords if k.arg}
        name, title = _literal(keywords.get("name")), _literal(keywords.get("title"))
        if not isinstance(name, str) or not isinstance(title, str):
            continue
        pages = []
        for element in getattr(keywords.get("pages"), "elts", ()):
            if isinstance(element, ast.Call):
                page = _page_from_call(element)
                if page:
                    pages.append(page)
        hubs.append((name, title, pages))
    return hubs


def _page_roots(sources: dict[Path, str], groups: dict[str, str]) -> dict[str, tuple[str, str]]:
    """page key -> ("group", group name) or ("root", root name)."""
    surface = sources[BOT / "surface.py"]
    out: dict[str, tuple[str, str]] = {}
    block = surface[surface.index("_GROUP_ACTION_ROOTS = {"):surface.index("_MIGRATED_ROOTS = {")]
    for key, var in re.findall(r"\"([a-z_]+)\":\s*(\w+),", block):
        out[key] = ("group", groups.get(var, var))
    # The admin panel names its groups by variable; a page may also gather a
    # second group in `extras`, and both map to the same Discord group name.
    for node in ast.walk(ast.parse(surface)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "HubPage":
            for keyword in node.keywords:
                if keyword.arg in ("command", "extras"):
                    names = ([keyword.value.id] if isinstance(keyword.value, ast.Name)
                             else [e.id for e in getattr(keyword.value, "elts", ()) if isinstance(e, ast.Name)])
                    for var in names:
                        out.setdefault(var, ("group", groups.get(var, var)))
    return out


def build() -> str:
    sources = _sources()
    groups = _groups(sources)
    commands = _commands(sources, groups)
    by_group: dict[str, list] = {}
    roots: dict[str, list] = {}
    for kind, owner, name, qualified, node, path, all_text in commands:
        row = (qualified, name, node, path, all_text)
        if kind == "leaf":
            by_group.setdefault(groups.get(owner, owner), []).append(row)
        else:
            roots[name] = row
    page_roots = _page_roots(sources, groups)
    deferred = deferred_leaves()
    lines = [f"# Playtest checklist — v{version()}", "",
             "Generated by `scripts/playtest_checklist.py` from the tree; regenerate after any command change. "
             "Every column here is filled by a machine. What needs a person is the one short table at the end, "
             "**What only a live server can show** — that is the pass, and it is the length somebody actually walks.", "",
             "**Columns.** *Params* names each parameter and how the hub asks for it — `picker`, `choices`, `select`, `guided`, `prose`; "
             "`TYPED` means a free string with no picker and would fail `test_hub_pickers.py`. `+hint` marks a picker that explains itself when empty. "
             "*Acked* says whether a handler that reaches the engine defers first (`test_ack_before_mutation.py` holds this). "
             "*Swept* says `scripts/playtest_discord.py` reaches this leaf: it boots the real bot under a simulated Discord and either "
             "presses the action — holding that the reply is a result or a designed refusal, never the hub's own failure text, never the "
             "action meter, never an exception — or finds the panel hiding it and holds its `🔒` lock line. "
             "`deferred` means the sweep skips it on purpose; the reasons are listed under **Deferred leaves** below.", "",
             "Until v1.0.0-rc.47 each action instead carried three checkboxes — reachable, error text actionable, narration fired — "
             "written in v0.34.0 when nothing could press a button. Seven hundred and forty-four boxes, none of them ever ticked. "
             "The sweep ticks the first outright and the second as far as wiring goes; what it cannot judge is whether a refusal reads "
             "helpfully to a human, and what it cannot reach is a live AI route, because it runs `NARRATOR_PROVIDER=procedural`. "
             "Both of those are rows in the live table now, against an artifact that exists — the sweep prints every reply it got.", ""]
    total = 0
    for hub, title, pages in _hubs(sources):
        lines += [f"## /{hub} — {title}", ""]
        # The admin panel is not playtested from the board: it is GM-only, its
        # every action writes to `admin_audit_log`, and fifty-three rows no
        # tester can open is noise on a list whose whole job is to be walked.
        # The GM half lives in the channel checklist instead. The heading stays
        # so the hub is still visibly accounted for.
        if hub == "admin":
            lines += ["GM-only; not posted to the playtest board. The admin pass is the GM checklist.", ""]
            continue
        for key, label, page_roots_named, only in pages:
            rows = []
            for page_key in page_roots_named:
                kind, target = page_roots.get(page_key, ("root", page_key))
                if kind == "group":
                    # A group's page carries its own leaves and every nested
                    # subgroup's: `/sect` owns `/sect recruitment trial`.
                    found = [row for name, group_rows in by_group.items()
                             if name == target or name.startswith(f"{target} ")
                             for row in group_rows]
                else:
                    found = [roots[target]] if target in roots else []
                if not found and page_key in roots:
                    found = [roots[page_key]]
                rows += found
            if only:
                # `only` names the leaves this page takes - the qualified name
                # ("sect status") or a prefix claiming a subgroup ("sect
                # recruitment"). Without this a split page lists its whole
                # group, and a tester ticks the same action once per page.
                rows = [r for r in rows
                        if any(r[0].lstrip("/") == pick or r[0].lstrip("/").startswith(f"{pick} ")
                               for pick in only)]
            lines += [f"### {label} (`{key}`)", "", "| Action | Params | Acked | Swept |", "|---|---|---|---|"]
            for qualified, name, node, path, all_text in sorted(rows, key=lambda r: r[0]):
                params = ", ".join(_parameters(node, all_text)) or "—"
                swept = "deferred" if qualified in deferred else "sim"
                lines.append(f"| `{qualified}` | {params} | {_acked(node)} | {swept} |")
                total += 1
            lines.append("")
    lines += ["## Loops beyond the hubs", "",
              "Run `scripts/playtest_engine.py --launch` for the engine half: the roadmap's loops (character creation, `$ I explore`, "
              "joining a sect and studying the gift, commissions from Qiao completed, abandoned and failed, a live quest edited under "
              "each hold policy, a narration route changed and read back, a timed mute and its undo, a live auction struck, a backup "
              "restored) and then every operation the engine answers, driven on a state the GM levers build - the ones it does not "
              "drive are named in its `DEFERRED_OPERATIONS`, each with its reason. Run `scripts/playtest_discord.py --launch` for the "
              "half a player touches, driven through a simulated Discord: the scripted loops (the bot boots and syncs its commands, "
              "`/admin` refuses a player and binds the base channels, `/begin` through the form to the household thread, the hidden "
              "household door and its locked line, an errand, `$ I explore` in the journal, the shorthand, `/cooldowns`, the capital "
              "and the Hearth-Return Talisman home, Support and Contribute, the lesson, Enter from the town, the road, a panel "
              "expiring to Reopen) and then every leaf on this board and on the admin hub, pressed once with each input step answered "
              "generically, holding that the reply is a result or a designed refusal and never the hub's own failure text. "
              "`tests/python/contracts/test_playtest_coverage.py` holds both harnesses to the code.", ""]
    if deferred:
        lines += ["### Deferred leaves", "",
                  "The sweep presses every leaf but these, and says why. A deferral that names a leaf no hub reaches, "
                  "`test_playtest_coverage.py` fails as stale.", ""]
        for path, reason in sorted(deferred.items()):
            lines.append(f"- `{path}` — {' '.join(str(reason).split())}")
        lines.append("")
    lines += ["### What only a live server can show", "",
              "Real Discord, real network, real routes, real money. The two harnesses cover everything else, so this "
              "is the whole of the manual pass — walk it once per release on the NAS and tick as you go; the "
              "generator keeps these ticks when it rewrites the file, and carries them when the version is "
              "bumped.", "",
              "**A tick carries the release it was walked on.** Write a bare `[x]` and the next regeneration "
              "stamps it — the person ticks, the generator dates it. So `[x] v1.0.0` on a 1.0.1 checklist is a "
              "row nobody has walked since 1.0.0, and re-walking it is writing `[x]` over the stamp. Carrying a "
              "tick forward unstamped would claim a pass that never happened, which is the one thing a "
              "checklist must not do.", "",
              "| Loop | Live |", "|---|---|",
              "| the Discord sweep's log reads end to end: every refusal names what is missing, in words a player can act on | [ ] |",
              "| a live AI route narrates a scene (the sweep runs `NARRATOR_PROVIDER=procedural` and can never reach one) | [ ] |",
              "| an epic beat and an NPC reply both come back as prose, and a retired route falls through to the next | [ ] |",
              "| `$ I explore` from a scene channel routes to /explore | [ ] |",
              "| `$ I travel to <known place>` and `$ I drink <carried item>` dispatch with the argument | [ ] |",
              "| a narration route changed on the dashboard applies without a restart (AI Routing page shows it) | [ ] |",
              "| a lot listed at a grand house appears in its channel; a bid updates the card; the tick strikes it | [ ] |",
              "| a lot at a local floor appears in the world's shared channel | [ ] |",
              "| an event in the Spiritual World announces in #spiritual-world-events, not the global feed | [ ] |",
              "| the weekend gift still announces in #world-events, which no world owns | [ ] |",
              "| a cultivator who has not reached a world cannot see that world's events channel | [ ] |",
              "| `/menu` opens every hub; Admin only for an administrator | [ ] |",
              "| `/vote` prints the listing link and pays the gift once; a second claim names the wait | [ ] |",
              "| the `/vote` gift names a material the character's craft or path uses, and doubles Fri-Sun | [ ] |",
              "| the weekend bonus is announced once in the announcement channel, and not again after a restart | [ ] |",
              "| `/cooldowns` lists a live wait and a ready action, and names where each ready one is done | [ ] |",
              "| `/admin player mute` for `30m` expires on its own; `/admin player inspect` shows it | [ ] |",
              "",
              "### The surface regrouping (v1.0.0-rc.13)", "",
              "The pages moved. These are the ones a player reaches differently than they did "
              "last release, so they are worth one pass each even where the mechanic is untouched.", "",
              "| Loop | Live |", "|---|---|",
              "| `/ascend` opens; `/quests` is the other thing and still opens | [ ] |",
              "| `/ascend -> Perfection -> Start` asks Realm or Body, and the Body path still starts | [ ] |",
              "| `/character -> Overview` shows sheet, lifespan, karma, Dao heart, standing, inheritances and soul in one read | [ ] |",
              "| `/character -> Afflictions` shows a buff, a curse and a persistent injury together, and treats one | [ ] |",
              "| `/character -> Consequences` shows an open crime, a bounty and a grudge | [ ] |",
              "| `/world -> Almanac` answers era, calendar, rulers, laws and running events in one read | [ ] |",
              "| `/sect` is four pages and every sect action is on exactly one of them | [ ] |",
              "| a reply printing a hub path (`**/world -> Act -> Explore**`) still offers it as a button | [ ] |",
              "| `/craft` makes an alchemy recipe (`/alchemy refine` is gone and nothing is unreachable) | [ ] |",
              "",
              "### The server layout (v1.0.0-rc.59)", "",
              "Four of these reach a server somebody is already running rather than a fresh guild, "
              "which is the half a fresh-guild test can never see: the category split, the re-parent "
              "and the read-only lock each had to move or lock a channel that already existed, and "
              "until rc.59 none of them did. Walk them on a guild with history, not on a new one.", "",
              "| Loop | Live |", "|---|---|",
              "| Full Setup (or Repair) leaves eight categories, in order: Start Here, Announcements, Realm Capitals, World Events, Auction Houses, Cultivation World, Feedback, Admin | [ ] |",
              "| channels that already existed have moved into their new categories, not only the ones the run created | [ ] |",
              "| an ordinary member cannot post in #xianxia-info, #expeditions, #player-homes or #updates, including ones that predate the release | [ ] |",
              "| no new #event-scenes is created; an event's scene anchors in its world's own feed, and an existing one keeps the blurb saying it is retired | [ ] |",
              "| #updates is silent on a guild whose marker was NULL, and the release after this one posts there once | [ ] |",
              "",
              "### Upgrading the deployment", "",
              "Which channel depends on the tag: a version with no `-` in it is published as GitHub's "
              "latest rather than a prerelease, so 1.0.0 and every patch after it is on **stable** and "
              "the release candidates were on `beta`. Walk the channel the release you are installing "
              "actually went out on.", "",
              "| Loop | Live |", "|---|---|",
              "| `sudo ./update.sh --check --channel stable` names the newest release, not \"already the newest\" | [ ] |",
              "| `sudo ./update.sh --fetch --channel stable` installs it, verifying the archive against its `.sha256` sidecar | [ ] |",
              "| a release stamped with RELEASE_TAG passes both VERSION checks | [ ] |",
              "| the post-install manifest check passes, skipping the deferred updater | [ ] |",
              "| `./migrate_env.sh --dry-run` runs on the NAS with no missing command | [ ] |",
              "| the bot comes up on the schema the release ships (a duplicate column here is v1.0.0-rc.57's fault returning) | [ ] |",
              "", f"_{total} actions across {len(_hubs(sources))} hubs._", ""]
    return "\n".join(lines)


TICK = re.compile(r"^\[[ xX]\](?:\s+v\d+(?:\.\d+)*)?$")
CHECKLIST_NAME = re.compile(r"^v(\d+(?:\.\d+)*)\.md$")


def _tickable(line: str) -> tuple[str, str] | None:
    """A two-cell table row whose second cell is a checkbox: (loop, tick).

    The live tables are `| Loop | Live |`, and a loop is prose that may or may
    not begin with a backtick. Keying on the shape rather than on the first
    character is what v1.0.0-rc.47 fixed: `merge_ticks` only ever looked at
    lines starting with `` | ` ``, and only at rows of six cells or more, so
    the loop rows - the only ticks in the file that are a person's - were
    silently dropped on every regeneration. Nobody noticed because nobody had
    ticked one, which is the same reason the per-action boxes went.

    A tick may carry the release it was walked on (`[x] v1.0.0`), which is
    what lets one survive a version bump without claiming a pass that never
    happened - see `_stamp`.
    """
    if not line.startswith("| ") or not line.endswith(" |"):
        return None
    cells = [c.strip() for c in line[1:-1].split("|")]
    if len(cells) == 2 and TICK.match(cells[1]):
        return cells[0], cells[1]
    return None


def _stamp(tick: str, release: str) -> str:
    """Give a bare `[x]` the release it was walked on; leave a stamped one alone.

    The person ticks, the generator stamps. A bare tick is one somebody wrote
    since the last regeneration, so it belongs to the release being generated;
    a stamped one keeps the release it already names, so carrying it forward
    is not a claim that the new release was walked.

    An unticked box never carries a stamp - there is nothing to date.

    The stamp is read as the text *after* the box, not as "does this cell
    contain a space". The first version asked the latter, and it was right
    only by accident: `[ ]` contains a space too, so an empty box was treated
    as already stamped and left alone, which happens to be the correct answer
    for the wrong reason. Its drill passed, which is how it was found.
    """
    box, _, walked = tick.partition("]")
    if not box[1:].strip():
        return "[ ]"
    return tick if walked.strip() else f"[x] v{release}"


def merge_ticks(old: str, new: str, release: str) -> str:
    """Keep the live-table ticks from a previous checklist, for loops that remain.

    `old` is the file this one supersedes: the same release's, when the
    generator is re-run after a command change, or the previous release's,
    when the version has been bumped and `docs/playtest/v<new>.md` does not
    exist yet. Both are the same operation - a tick is a person's work and
    outlives the file it was written in.
    """
    ticks = dict(filter(None, (_tickable(line) for line in old.splitlines())))
    out = []
    for line in new.splitlines():
        found = _tickable(line)
        if found and found[0] in ticks:
            line = f"| {found[0]} | {_stamp(ticks[found[0]], release)} |"
        out.append(line)
    return "\n".join(out) + "\n"


def _superseded(target: Path) -> Path | None:
    """The newest other checklist in `docs/playtest/`, whose ticks `target` inherits.

    Sorted on the version as a tuple of integers rather than as a string,
    because `v1.0.10` is newer than `v1.0.9` and sorts before it as text.
    """
    newest: tuple[tuple[int, ...], Path] | None = None
    for path in target.parent.glob("v*.md"):
        if path == target:
            continue
        match = CHECKLIST_NAME.match(path.name)
        if not match:
            continue
        key = tuple(int(part) for part in match.group(1).split("."))
        if newest is None or key > newest[0]:
            newest = (key, path)
    return None if newest is None else newest[1]


def main(argv: list[str] | None = None) -> int:
    # `argv` is taken rather than read off `sys.argv` so a test can drive this
    # function itself. Asserting that `merge_ticks` carries a tick says nothing
    # about whether `main` ever hands it the previous release's file, and that
    # wire is the one that was missing.
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    target = ROOT / "docs" / "playtest" / f"v{version()}.md"
    fresh = build()
    if args.check:
        if not target.exists():
            print(f"missing {target}")
            return 1
        have = {l.split("|")[1].strip() for l in target.read_text(encoding="utf-8").splitlines() if l.startswith("| `")}
        want = {l.split("|")[1].strip() for l in fresh.splitlines() if l.startswith("| `")}
        missing = sorted(want - have)
        for row in missing:
            print("not on the checklist:", row)
        return 1 if missing else 0
    superseded = None if target.exists() else _superseded(target)
    source = target if target.exists() else superseded
    old = source.read_text(encoding="utf-8") if source is not None else ""
    target.write_text(merge_ticks(old, fresh, version()) if old else fresh + "\n", encoding="utf-8")
    print(f"wrote {target}")
    if superseded is not None:
        # One checklist, not one per release. Its only human content is the
        # ticks, and those have just been carried into `target` carrying the
        # release each was walked on, so what is left behind is a generated
        # copy of the tree at an older version - which git already keeps.
        superseded.unlink()
        print(f"carried the live pass from {superseded.name} and removed it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
