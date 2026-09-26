#!/usr/bin/env python3
"""Build the release-notes site that `#updates` links to (v1.8.2).

    python scripts/build_release_pages.py --out _site

Writes one page per changelog entry in `VERSIONS.md`, at the path
`app/rules/changelog.py` names (`v<version>/index.html`), and an index listing
them newest first. The `pages` job in `.github/workflows/ci.yml` runs this on every push to
main and publishes the result to GitHub Pages.

The entries are read with the same parse the bot uses, so a version the bot can
announce always has a page: `tests/python/unit/test_release_pages.py` holds
that. The Markdown is the small subset `VERSIONS.md` is written in - paragraphs,
`- ` lists, `code`, **bold**, *italics* and [links](url) - rendered with the
text HTML-escaped first, so nothing in an entry can inject markup.
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.rules.changelog import changelog_entries, page_path, version_key  # noqa: E402 - after the path

TITLE = "Xianxia RP - Release notes"

STYLE = """
:root { color-scheme: dark; --bg: #16150f; --fg: #ebe7df; --muted: #a39d92; --line: #34312a; --accent: #e0a36a; --code: #26241d; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 17px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 46rem; margin: 0 auto; padding: 2rem 1rem 4rem; }
h1 { font-size: 1.6rem; margin: 0 0 .25rem; }
nav, .muted { color: var(--muted); font-size: .9rem; }
a { color: var(--accent); }
code { background: var(--code); padding: .05em .3em; border-radius: 4px; font-size: .9em; overflow-wrap: anywhere; }
ul.releases { list-style: none; padding: 0; }
ul.releases li { border-top: 1px solid var(--line); padding: .9rem 0; }
ul.releases li a.version { font-weight: 600; }
article p, article li { overflow-wrap: break-word; }
.pager { display: flex; justify-content: space-between; gap: 1rem; margin-top: 2.5rem;
  border-top: 1px solid var(--line); padding-top: 1rem; }
"""


def inline(text: str) -> str:
    """One paragraph's inline Markdown, escaped first so an entry cannot inject markup."""
    out = html.escape(text, quote=False)
    spans: list[str] = []

    def keep(match: re.Match[str]) -> str:
        spans.append(f"<code>{match.group(1)}</code>")
        return f"\x00{len(spans) - 1}\x00"

    out = re.sub(r"`([^`]+)`", keep, out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", out)
    out = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
                 lambda m: f'<a href="{html.escape(m.group(2))}">{m.group(1)}</a>', out)
    return re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], out)


def render(entry: str) -> str:
    """An entry's paragraphs and lists as HTML."""
    blocks: list[str] = []
    for block in re.split(r"\n\s*\n", entry.strip()):
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if all(line.lstrip().startswith("- ") or line.startswith("  ") for line in lines) \
                and lines[0].lstrip().startswith("- "):
            items: list[str] = []
            for line in lines:
                if line.lstrip().startswith("- "):
                    items.append(line.lstrip()[2:])
                else:
                    items[-1] += " " + line.strip()
            blocks.append("<ul>" + "".join(f"<li>{inline(item)}</li>" for item in items) + "</ul>")
        else:
            blocks.append(f"<p>{inline(' '.join(line.strip() for line in lines))}</p>")
    return "\n".join(blocks)


def page(title: str, body: str, *, depth: int) -> str:
    nav = f'<nav><a href="{"../" * depth}">All releases</a></nav>' if depth else ""
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{html.escape(title)}</title><style>{STYLE}</style></head>"
        f"<body><main>{nav}{body}</main></body></html>\n"
    )


def build(versions_text: str, out: Path) -> list[str]:
    """Write the site into `out` and return the labels it has a page for."""
    entries = sorted(changelog_entries(versions_text), key=lambda row: version_key(row[0]), reverse=True)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".nojekyll").write_text("", encoding="utf-8")

    listing = []
    for index, (label, entry) in enumerate(entries):
        newer = entries[index - 1][0] if index > 0 else None
        older = entries[index + 1][0] if index + 1 < len(entries) else None
        pager = '<div class="pager"><span>'
        pager += f'<a href="../{page_path(older)}">&larr; v{html.escape(older)}</a>' if older else ""
        pager += "</span><span>"
        pager += f'<a href="../{page_path(newer)}">v{html.escape(newer)} &rarr;</a>' if newer else ""
        pager += "</span></div>"
        body = f"<h1>Xianxia RP v{html.escape(label)}</h1><article>{render(entry)}</article>{pager}"
        target = out / page_path(label) / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page(f"Xianxia RP v{label}", body, depth=1), encoding="utf-8")

        opening = entry.split("\n\n")[0].replace("\n", " ")
        opening = re.sub(r"^\*\*[\d.]+\*\*\s*(?:\(rc\.\d+\)\s*)?", "", opening)
        listing.append(f'<li><a class="version" href="{page_path(label)}">v{html.escape(label)}</a>'
                       f" {inline(opening)}</li>")

    index_body = (f"<h1>{html.escape(TITLE)}</h1>"
                  f'<p class="muted">Every release, newest first, from the changelog.</p>'
                  f'<ul class="releases">{"".join(listing)}</ul>')
    (out / "index.html").write_text(page(TITLE, index_body, depth=0), encoding="utf-8")
    return [label for label, _ in entries]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="_site", help="directory to write the site into")
    parser.add_argument("--versions", default=str(ROOT / "VERSIONS.md"), help="the changelog to read")
    args = parser.parse_args(argv)
    labels = build(Path(args.versions).read_text(encoding="utf-8"), Path(args.out))
    if not labels:
        print(f"no changelog entries found in {args.versions}", file=sys.stderr)
        return 1
    print(f"wrote {len(labels)} release pages into {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
