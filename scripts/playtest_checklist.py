#!/usr/bin/env python3
"""Write the playtest checklist (v0.34.0): every hub, page and action, with
the columns a machine can fill and the ones only a person at the keyboard
can.

    python3 scripts/playtest_checklist.py            # writes docs/playtest/v<version>.md
    python3 scripts/playtest_checklist.py --check    # exits 1 if the checked-in file is missing an action

The walk is source-level, like the contract tests: hub definitions from
surface.py, the page-to-command map, every registered root and group leaf,
each parameter's picker. Static columns come from the tree; the live columns
(reachable, error text actionable, narration fallback fired) are checkboxes
for the pass on the live server, and the file keeps whatever was ticked when
it is regenerated.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "app" / "bot"
sys.path.insert(0, str(ROOT))

FREE_TEXT = {"name", "spirit_name", "reason", "message", "action", "stakes", "rule", "definition", "duration", "category_name", "story"}


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
    lines = [f"# Playtest checklist — v{version()}", "",
             "Generated by `scripts/playtest_checklist.py` from the tree; regenerate after any command change. "
             "Static columns are filled from the source. The three live columns are for the pass on the live server: "
             "tick them as you go, and the generator keeps what was ticked.", "",
             "**Columns.** *Params* names each parameter and how the hub asks for it — `picker`, `choices`, `select`, `guided`, `prose`; "
             "`TYPED` means a free string with no picker and would fail `test_hub_pickers.py`. `+hint` marks a picker that explains itself when empty. "
             "*Acked* says whether a handler that reaches the engine defers first (`test_ack_before_mutation.py` holds this). "
             "*Live* is reachable from the hub · error text actionable · narration or fallback fired.", ""]
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
            lines += [f"### {label} (`{key}`)", "", "| Action | Params | Acked | Live: reachable | error text | narration |", "|---|---|---|---|---|---|"]
            for qualified, name, node, path, all_text in sorted(rows, key=lambda r: r[0]):
                params = ", ".join(_parameters(node, all_text)) or "—"
                lines.append(f"| `{qualified}` | {params} | {_acked(node)} | [ ] | [ ] | [ ] |")
                total += 1
            lines.append("")
    lines += ["## Loops beyond the hubs", "",
              "Run `scripts/playtest_engine.py --launch` for the engine half (character creation, `$ I explore`, joining a sect and studying "
              "the gift, commissions from Qiao completed, abandoned and failed, a live quest edited under each hold policy, a narration "
              "route changed and read back, a timed mute and its undo, a live auction struck, a backup restored). The Discord half:", "",
              "| Loop | Live |", "|---|---|",
              "| `$ I explore` from a scene channel routes to /explore | [ ] |",
              "| `$ I travel to <known place>` and `$ I drink <carried item>` dispatch with the argument | [ ] |",
              "| a narration route changed on the dashboard applies without a restart (AI Routing page shows it) | [ ] |",
              "| a lot listed at a grand house appears in its channel; a bid updates the card; the tick strikes it | [ ] |",
              "| a lot at a local floor appears in the world's shared channel | [ ] |",
              "| `/menu` opens every hub; Admin only for an administrator | [ ] |",
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
              "### Upgrading the deployment", "",
              "| Loop | Live |", "|---|---|",
              "| `sudo ./update.sh --fetch --channel beta` offers the newest rc, not \"already the newest\" | [ ] |",
              "| it installs it: a release stamped with RELEASE_TAG passes both VERSION checks | [ ] |",
              "| the post-install manifest check passes, skipping the deferred updater | [ ] |",
              "| `./migrate_env.sh --dry-run` runs on the NAS with no missing command | [ ] |",
              "", f"_{total} actions across {len(_hubs(sources))} hubs._", ""]
    return "\n".join(lines)


def merge_ticks(old: str, new: str) -> str:
    """Keep the live-column ticks from the checked-in file for rows that still exist."""
    ticks: dict[str, str] = {}
    for line in old.splitlines():
        if line.startswith("| `") and "|" in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 6:
                ticks[cells[0]] = " | ".join(cells[3:6])
    out = []
    for line in new.splitlines():
        if line.startswith("| `"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 6 and cells[0] in ticks:
                line = "| " + " | ".join(cells[:3] + [ticks[cells[0]]]) + " |"
        out.append(line)
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
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
    old = target.read_text(encoding="utf-8") if target.exists() else ""
    target.write_text(merge_ticks(old, fresh) if old else fresh + "\n", encoding="utf-8")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
