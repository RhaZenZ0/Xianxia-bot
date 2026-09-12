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
    """group variable -> Discord group name."""
    out: dict[str, str] = {}
    for text in sources.values():
        for var, name in re.findall(r"^(\w+)\s*=\s*app_commands\.Group\(\s*name=\"([^\"]+)\"", text, re.M):
            out[var] = name
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


def _hubs(sources: dict[Path, str]):
    surface = sources[BOT / "surface.py"]
    hubs = []
    for block in re.finditer(r"HubDefinition\(\s*name=\"([a-z]+)\",\s*title=\"([^\"]+)\"(.*?)\n\s*\)\s*,?\n", surface, re.S):
        name, title, body = block.group(1), block.group(2), block.group(3)
        pages = []
        # A page is `_hub_page(key, label, description, *extra roots)`: the
        # extras (v1.0.0-rc.4) are further roots gathered onto the same page,
        # and their actions belong on the page's rows here too. The call is
        # read by scanning to its own closing parenthesis - a regex that
        # ended on a newline silently dropped the last page of every hub.
        for start in (m.end() for m in re.finditer(r"_hub_page\(", body)):
            quoted = re.findall(r"\"([^\"]*)\"", body[start:_call_end(body, start)])
            if len(quoted) >= 2:
                pages.append((quoted[0], quoted[1], tuple(quoted[3:])))
        pages += [(m.group(1), m.group(2), ()) for m in re.finditer(r"HubPage\(key=\"([a-z_]+)\", label=\"([^\"]+)\"", body)]
        hubs.append((name, title, pages))
    return hubs


def _page_roots(sources: dict[Path, str], groups: dict[str, str]) -> dict[str, tuple[str, str]]:
    """page key -> ("group", group name) or ("root", root name)."""
    surface = sources[BOT / "surface.py"]
    out: dict[str, tuple[str, str]] = {}
    block = surface[surface.index("_GROUP_ACTION_ROOTS = {"):surface.index("_MIGRATED_ROOTS = {")]
    for key, var in re.findall(r"\"([a-z_]+)\":\s*(\w+),", block):
        out[key] = ("group", groups.get(var, var))
    admin = surface[surface.index("_ADMIN_HUB_DEFINITION"):]
    for key, var in re.findall(r"HubPage\(key=\"([a-z_]+)\",[^)]*?command=(\w+)\)", admin):
        out[key] = ("group", groups.get(var, var))
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
        for key, label, extras in pages:
            rows = []
            for page_key in (key, *extras):
                kind, target = page_roots.get(page_key, ("root", page_key))
                found = by_group.get(target, []) if kind == "group" else ([roots[target]] if target in roots else [])
                if not found and page_key in roots:
                    found = [roots[page_key]]
                rows += found
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
