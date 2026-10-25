"""A comment that names code names code that exists (v1.39.3).

A pass over every comment in the tree found no dead code, no debris and no
typos, and about forty comments that had quietly stopped being true. They had
one shape: a backticked name - a function, a test, a constant, a file - that
the code no longer declares under that spelling. `PLACE_COLUMNS_LEFT` was
`LEFT_ALONE`; `_event_scene_parent` had gone public; a module docstring sent
the reader to `test_the_ghost_ground_twin.py`, which is a class in another
file; three comments cited `runSystems`, a function `RunDue` replaced; a
registry comment leaned on `TestARegisteredNameNeverShadowsTheContentFile`,
which had been renamed. None of it was carelessness in the writing - the code
moved underneath the prose, and nothing read the prose to notice.

This reads it. Every `#` comment and docstring under `app/` and `scripts/`,
and every `//` line of production Go, is walked for backticked tokens, and
each token that looks like code is asked one of two questions: a file name
must exist in the tree, and a name must appear as an identifier somewhere in
the tree's *code* - never only in its prose, or `docs/FINDINGS.md`, which
narrates every old name by design, would vouch for all of them. A deliberate
tombstone ("`float64Value` stood here until v1.0.7") is named in `ALLOWED`
with its reason, and an entry whose token has gone fails, so the list can
only shrink honestly.

It is a reader of spelling, not of meaning: a comment that names the right
function and describes the wrong behaviour passes. What it makes impossible
is the cheaper half - a name a reader will grep for and not find.
"""
from __future__ import annotations

import ast
import io
import json
import re
import tokenize
import unittest
from pathlib import Path

from tests.support import PROJECT_ROOT

# `file:line` is not stable, so entries key on the file and the token.
ALLOWED: dict[tuple[str, str], str] = {
    ("go_core/internal/simulation/advanced_maintenance.go", "float64Value"):
        "a tombstone: the comment says the helper stood here until v1.0.7 and why it went",
    ("go_core/internal/simulation/advanced_maintenance.go", "eraTemplate"):
        "a tombstone: the comment says the helper stood here until v1.0.7 and why it went",
    ("app/dashboard/contract.py", "active_era"):
        "the payload field `active_eras` replaced; the comment is about the replacement",
    ("scripts/playtest_discord.py", "UnsupportedField"):
        "an exception class of simcord, a dev dependency the tree does not vendor",
    ("go_core/internal/game/privacy_actions.go", "_ad"):
        "the suffix of the SQLite audit triggers (`rag_memories_ad`), which core.py spells whole",
    ("go_core/internal/game/privacy_actions.go", "_au"):
        "the suffix of the SQLite audit triggers (`world_history_au`), which core.py spells whole",
    ("app/rules/advanced_runtime.py", "ERA_CYCLE"):
        "a tombstone: the comment says the roster stood here until v1.0.7 and where it went",
    ("go_core/internal/simulation/bootstrap.go", "recordClanBootstrapHistory"):
        "a tombstone: the doc says what the function was called until v1.0.1 and why it was renamed",
    ("go_core/internal/simulation/advanced_maintenance.go", "eraCycle"):
        "a tombstone: the comment says the literal stood here until v1.0.7 and where the roster went",
    ("go_core/internal/worlddata/catalog.go", "eraCycle"):
        "a tombstone: the field's doc says which Go literal the content roster replaced in v1.0.7",
    ("scripts/author_world_eras.py", "eraCycle"):
        "a tombstone: the script's docstring says which Go literal it replaced in v1.0.7",
    ("app/bot/bot.py", "MaintenanceAwareTree"):
        "a tombstone: the docstring says what the tree was named while maintenance was its only gate",
    ("app/bot/hubs.py", "register_maintenance_gate"):
        "a tombstone: the docstring says what the function was called while there was one gate",
    ("app/bot/surface.py", "bodyperfect"):
        "a tombstone: the hub page that was folded into `perfect` at v1.0.0-rc.13",
    ("app/bot/commands/exploration.py", "required_profession"):
        "history of the rc.13 NameError: the gate that was removed in the same edit as the local",
    ("app/ai/ai_router.py", "ClientError"):
        "google-genai's exception class, a dependency the tree does not vendor",
    ("app/bot/cards.py", "ConnectionState.parse_interaction_create"):
        "discord.py's own internals, named so the hook can be found in the dependency",
    ("app/ops/http_limits.py", "_maybe_resume_transport"):
        "asyncio's own StreamReader internals, named so the limit's behaviour can be read in the stdlib",
    ("scripts/playtest_discord.py", "env.external_wait"):
        "a simcord API, a dev dependency the tree does not vendor",
    ("scripts/playtest_discord.py", "threading._shutdown()"):
        "the standard library's own interpreter-exit hook, which the script has to outlive",
    ("scripts/playtest_engine.py", "threading._shutdown()"):
        "the standard library's own interpreter-exit hook, which the script has to outlive",
}

SOURCE_SUFFIXES = (".py", ".go", ".md", ".js", ".sh", ".json", ".yml", ".yaml", ".html", ".css", ".toml", ".txt")
BACKTICKED = re.compile(r"`([^`\n]{1,80})`")
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
RELEASE_LABEL = re.compile(r"^(v\d|rc\.|\d)")
# Punctuation a name is split on when the comment writes `a.b`, `f()`, `x[0]`, `k:v`.
SPLIT = re.compile(r"[.\(\)\[\]:,]")


def _python_files(*dirs: str) -> list[Path]:
    out: list[Path] = []
    for d in dirs:
        out.extend(sorted((PROJECT_ROOT / d).rglob("*.py")))
    return out


def _go_files(*, tests: bool) -> list[Path]:
    return [p for p in sorted((PROJECT_ROOT / "go_core").rglob("*.go"))
            if p.name.endswith("_test.go") == tests]


def python_prose(path: Path) -> list[tuple[int, str]]:
    """Every comment and docstring in a Python file, with its first line."""
    src = path.read_text(encoding="utf-8")
    out: list[tuple[int, str]] = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            out.append((tok.start[0], tok.string))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                out.append((getattr(node, "lineno", 1), doc))
    return out


def go_prose(path: Path) -> list[tuple[int, str]]:
    """Every whole-line `//` comment in a Go file."""
    out: list[tuple[int, str]] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if s.startswith("//"):
            out.append((n, s))
    return out


def _python_code_words(src: str) -> set[str]:
    """The names a Python source spells in code: every NAME token and every
    string constant that is not a docstring. A docstring is prose, and this
    file's own docstring names the very things it hunts."""
    words: set[str] = set()
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.NAME:
            words.update(WORD.findall(tok.string))
    tree = ast.parse(src)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            words.update(WORD.findall(node.value))
    return words


def code_vocabulary() -> set[str]:
    """Every identifier the tree's code spells: Python names and non-docstring
    string contents, Go words off non-comment lines, the content file, the
    deployment files and the dashboard. Prose - docstrings, comments and the
    documents - is deliberately not in it."""
    known: set[str] = set()
    for p in _python_files("app", "scripts", "tests"):
        known.update(_python_code_words(p.read_text(encoding="utf-8")))
    for p in _go_files(tests=False) + _go_files(tests=True):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("//"):
                continue
            known.update(WORD.findall(line.split(" // ")[0]))
    for pattern in ("content/**/*.json", "dashboard/**/*", ".github/**/*.yml", "*.yml", "*.yaml", "*.sh",
                    "Makefile", ".env.example", "pyproject.toml", "go_core/go.mod", "scripts/**/*.sh"):
        for p in PROJECT_ROOT.glob(pattern):
            if p.is_file():
                known.update(WORD.findall(p.read_text(encoding="utf-8", errors="replace")))
    return known


def file_exists(token: str) -> bool:
    cand = re.sub(r":\d+(-\d+)?$", "", token).lstrip("./")
    if not cand:
        return True
    if (PROJECT_ROOT / cand).exists():
        return True
    name = Path(cand).name
    return any(PROJECT_ROOT.rglob(name))


def looks_like_code(token: str) -> bool:
    """A backticked token worth asking about: no spaces, no markup, no model
    slug or shell flag, not a release label and not a bare slash command."""
    if " " in token or any(c in token for c in "*<>=\"'{}|\\-$@%&!?;"):
        return False
    if RELEASE_LABEL.match(token):
        return False
    if token.startswith("/") and token.count("/") == 1:
        return False  # a slash command; `test_hint_paths.py` holds those
    return bool(WORD.search(token))


class ACommentNamesWhatExists(unittest.TestCase):
    def test_every_backticked_name_in_production_prose_is_spelled_by_the_code(self):
        known = code_vocabulary()
        self.assertGreater(len(known), 20000, "the vocabulary reader is broken, not the tree")
        sites: list[tuple[str, int, str]] = []
        for p in _python_files("app", "scripts"):
            sites.extend((str(p.relative_to(PROJECT_ROOT)), n, text) for n, text in python_prose(p))
        for p in _go_files(tests=False):
            sites.extend((str(p.relative_to(PROJECT_ROOT)), n, text) for n, text in go_prose(p))
        self.assertGreater(len(sites), 5000, "the prose reader is broken, not the tree")

        seen_tokens = 0
        used_allow: set[tuple[str, str]] = set()
        problems: list[str] = []
        for rel, line, text in sites:
            for raw in BACKTICKED.findall(text):
                token = raw.strip().strip("`")
                if not looks_like_code(token):
                    continue
                seen_tokens += 1
                if (rel, token) in ALLOWED:
                    used_allow.add((rel, token))
                    continue
                if token.endswith(SOURCE_SUFFIXES) or re.search(r"\.(py|go|md|js|sh|json|yml|html)(:\d+)?$", token):
                    if not file_exists(token):
                        problems.append(f"{rel}:{line}: `{token}` names a file the tree does not have")
                    continue
                parts = [x for x in SPLIT.split(token.replace("/", ".")) if x]
                missing = []
                for part in parts:
                    if len(part) <= 1 or part.isdigit() or not WORD.fullmatch(part):
                        continue
                    if part in known:
                        continue
                    if part.endswith("_") and any(k.startswith(part) for k in known):
                        continue  # a prefix the comment shortens (`mid_` for `mid_spirit_stone`)
                    if file_exists(part + ".py") or file_exists(part + ".go"):
                        continue  # a module named without its suffix
                    missing.append(part)
                if missing:
                    problems.append(
                        f"{rel}:{line}: `{token}` names {', '.join(missing)}, which no code in the tree "
                        "spells - the thing was renamed, moved or removed and the comment was not; "
                        "say its name today, or name the tombstone in ALLOWED with its reason")
        self.assertGreater(seen_tokens, 1000, "the token reader is broken, not the tree")
        for key, reason in ALLOWED.items():
            if key not in used_allow:
                problems.append(f"ALLOWED names {key[1]} in {key[0]}, which no comment there spells any more: drop the entry")
            if len(reason.strip()) < 20:
                problems.append(f"ALLOWED[{key}] needs a reason of at least twenty characters")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
