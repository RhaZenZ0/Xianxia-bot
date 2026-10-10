"""CLAUDE.md is loaded into every session and into every subagent a session starts, so what it
weighs is paid for on every task. It grew by about 4 KB a release until it was 560 KB (about
140,000 tokens), 92% of it release history that was written once and read rarely. The history now
lives in docs/FINDINGS.md and the subsystem essays in docs/ARCHITECTURE.md, neither loaded
automatically, and CLAUDE.md keeps the commands, the architecture core, one line per standing rule
and the delivery steps.

This file holds that shape. It asks six things of CLAUDE.md, each as a function of the text so the
drills below can run it against a text that breaks the rule:

- it stays under CAP characters (the threshold of Claude Code's large-memory warning);
- no heading carries a release label - a release's write-up is a section of docs/FINDINGS.md, and a
  heading with `(v1.34.0)` in it is the first sign the history is growing back;
- it imports nothing (`@docs/...` would load the moved files into every session again);
- it still says the sentences the rest of the tree quotes - code and skills cite CLAUDE.md by
  phrase, and a rewrite that dropped one would leave the citation pointing at nothing;
- every path it names exists, and every subsystem note and release label it cites resolves in the
  file that holds it;
- every pointer to a section of docs/FINDINGS.md - in docs/TODO.md, in docs/ARCHITECTURE.md and in
  CLAUDE.md itself - names a heading that is there.

The headings, paths and labels are read by small parsers, and a parser that silently finds nothing
would make every assertion after it vacuous, so each is asked for something known first.
"""
from tests.support import PROJECT_ROOT
import os
import re
import unittest
from functools import lru_cache

CAP = 40_000

CLAUDE_MD = PROJECT_ROOT / "CLAUDE.md"
FINDINGS_MD = PROJECT_ROOT / "docs" / "FINDINGS.md"
ARCHITECTURE_MD = PROJECT_ROOT / "docs" / "ARCHITECTURE.md"
TODO_MD = PROJECT_ROOT / "docs" / "TODO.md"

# What the rest of the tree quotes from CLAUDE.md, and who quotes it. The citing files are held to
# still saying it, so this table cannot go stale in the other direction: drop an entry when the
# citation goes.
QUOTED_FROM_CODE = {
    # rc.32's limit, quoted by the curriculum's authoring script and its gate.
    "never a status read or the door into the system, because a road nobody can see is a road "
    "nobody learns exists": [
        "scripts/author_feature_unlocks.py",
        "tests/python/unit/test_the_curriculum_opens_as_you_cultivate.py",
    ],
    # The rule that one system's error ends the tick, cited by the town's stall step and its fixture.
    "npc_consignments": [
        "go_core/internal/simulation/npc_stalls.go",
        "go_core/internal/game/stall_actions_test.go",
    ],
    # The github-ci skill's reason the playtests are not in CI.
    "It is a script, not CI": [".claude/skills/github-ci/SKILL.md"],
    # test_release_version holds the number; the sentence's form is what it searches for.
    "Current schema version is": ["tests/python/contracts/test_release_version.py"],
}

# The first path segments that name a place in this repository, and the two Go directories that
# CLAUDE.md writes without their go_core/ prefix.
TOP_LEVEL = {"app", "tests", "docs", "scripts", "go_core", "content", "dashboard", "assets", ".github", ".claude"}
GO_RELATIVE = {"internal", "cmd"}
BARE_FILE = re.compile(r"^[\w.-]+\.(?:md|py|go|sh|json|yml|yaml|sha256|lock|toml|ini|html|js)$")

RELEASE_LABEL = re.compile(r"\b(?:v\d+\.\d+(?:\.\d+)?(?:-rc\.\d+)?|rc\.\d+)\b")
FINDINGS_POINTER = re.compile(r'docs/FINDINGS\.md`?,\s+"([^"]+)"')


def squash(text: str) -> str:
    """Whitespace collapsed and emphasis marks dropped, so a phrase wrapped across comment lines
    or italicised in a docstring is still the phrase."""
    return re.sub(r"\s+", " ", text.replace("*", ""))


def outside_fences(text: str) -> str:
    """The text with fenced code blocks blanked: a command line in a fence is not a prose claim."""
    kept, fenced = [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            kept.append("")
        else:
            kept.append("" if fenced else line)
    return "\n".join(kept)


def headings(text: str) -> list[str]:
    """Every markdown heading's text, hashes dropped, outside code fences."""
    found = []
    for line in outside_fences(text).split("\n"):
        match = re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
        if match:
            found.append(match.group(2))
    return found


@lru_cache(maxsize=None)
def tree_basenames() -> frozenset:
    skip = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache"}
    names = set()
    for _, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in skip]
        names.update(files)
    return frozenset(names)


def size_problem(text: str, cap: int = CAP) -> list[str]:
    if len(text) <= cap:
        return []
    return [
        f"CLAUDE.md is {len(text):,} characters; the cap is {cap:,} (Claude Code warns above it, and "
        "every session and subagent pays for the file). A release's findings go to docs/FINDINGS.md "
        "and a subsystem's description to docs/ARCHITECTURE.md; CLAUDE.md gets a one-line rule only "
        "when the lesson is new."
    ]


def heading_problems(text: str) -> list[str]:
    return [
        f'the heading "{h}" carries a release label; a release is a section of docs/FINDINGS.md, '
        "not a heading here"
        for h in headings(text)
        if RELEASE_LABEL.search(h)
    ]


def import_problems(text: str) -> list[str]:
    problems = []
    for number, line in enumerate(outside_fences(text).split("\n"), start=1):
        if re.match(r"^\s*@\S+", line) or "@docs/" in line:
            problems.append(
                f"line {number} imports a file ({line.strip()[:60]!r}); an import loads docs/FINDINGS.md "
                "or docs/ARCHITECTURE.md into every session again"
            )
    return problems


def phrase_problems(text: str, table: dict = QUOTED_FROM_CODE) -> list[str]:
    """The phrase must be in CLAUDE.md, and in each file that cites it."""
    problems = []
    flat = squash(text)
    for phrase, citing in table.items():
        if phrase not in flat:
            problems.append(f'CLAUDE.md no longer says "{phrase[:120]}"; {", ".join(citing)} cite it')
        for relative in citing:
            source = squash((PROJECT_ROOT / relative).read_text(encoding="utf-8"))
            if phrase not in source:
                problems.append(
                    f'{relative} no longer quotes "{phrase[:120]}"; drop it from QUOTED_FROM_CODE'
                )
    return problems


def named_paths(text: str) -> list[str]:
    """The relative paths CLAUDE.md names in backticks, outside code fences."""
    found = []
    for token in re.findall(r"`([^`\n]+)`", outside_fences(text)):
        token = token.strip()
        if token.startswith("./"):
            token = token[2:]
        if re.search(r"[\s<>*{}$()=:,;|\"'\[\]]", token):
            continue
        first = token.split("/", 1)[0]
        if "/" in token and (first in TOP_LEVEL or first in GO_RELATIVE):
            found.append(token)
        elif "/" not in token and not token.startswith(".") and BARE_FILE.match(token):
            found.append(token)
    return found


def path_problems(text: str, root=PROJECT_ROOT) -> list[str]:
    problems = []
    for token in named_paths(text):
        if "/" not in token:
            if token not in tree_basenames():
                problems.append(f"`{token}` is named in CLAUDE.md and no file of that name exists")
            continue
        first = token.split("/", 1)[0]
        target = root / ("go_core" if first in GO_RELATIVE else "") / token.rstrip("/")
        # A Python module is named by its path without the suffix (`app/ai/rag`).
        if not target.exists() and not target.with_name(target.name + ".py").exists():
            problems.append(f"`{token}` is named in CLAUDE.md and does not exist")
    return problems


def heading_prefixed_by(title: str, document_headings: list[str]) -> bool:
    return any(h == title or h.startswith(title + " (") for h in document_headings)


def subsystem_problems(claude: str, architecture: str) -> list[str]:
    """The one-line subsystem notes name exactly the `###` sections of docs/ARCHITECTURE.md."""
    section = re.search(r"^### Subsystem notes.*?\n(.*?)(?=^## )", claude, re.M | re.S)
    if not section:
        return ["CLAUDE.md has no 'Subsystem notes' section"]
    noted = re.findall(r"^- \*\*(.+?)\*\*", section.group(1), re.M)
    held_sections = [line[4:].strip() for line in architecture.split("\n") if line.startswith("### ")]
    problems = [
        f'the subsystem note "{title}" has no `###` section in docs/ARCHITECTURE.md'
        for title in noted
        if not heading_prefixed_by(title, held_sections)
    ]
    problems += [
        f'docs/ARCHITECTURE.md has a section "{h}" that CLAUDE.md does not note in one line'
        for h in held_sections
        if not any(h == t or h.startswith(t + " (") for t in noted)
    ]
    return problems


def standing_rules(text: str) -> str:
    """The Standing rules section: the one place a rule's release is a pointer into the findings.
    The testing conventions record a few releases of their own (rc.42's dice gate has no write-up
    elsewhere), and the delivery steps use example versions."""
    section = re.search(r"^## Standing rules\n(.*?)(?=^## )", text, re.M | re.S)
    return section.group(1) if section else ""


def findings_label_problems(text: str, findings: str) -> list[str]:
    """Every release a rule cites is a release docs/FINDINGS.md has something to say about."""
    problems = []
    for label in sorted(set(RELEASE_LABEL.findall(text))):
        if label.startswith("v0."):
            continue  # the pre-1.0 history is VERSIONS.md's, not this file's
        bare = label.split("-")[-1] if "-rc." in label else label
        pattern = re.escape(bare) + r"(?![\d]|\.\d)"
        if not re.search(pattern, findings):
            problems.append(f"CLAUDE.md cites {label} and docs/FINDINGS.md never mentions it")
    return problems


def flatten(text: str) -> str:
    """Whitespace collapsed and nothing else: a quoted title is plain, so unlike squash this keeps
    the emphasis marks a pointer's own line may carry."""
    return re.sub(r"\s+", " ", text)


def pointer_problems(text: str, findings: str, where: str) -> list[str]:
    """Each `docs/FINDINGS.md, "<title>"` in `text` names a `###` heading of the findings."""
    held = [line[4:].strip() for line in findings.split("\n") if line.startswith("### ")]
    problems = []
    for title in FINDINGS_POINTER.findall(flatten(text)):
        if not heading_prefixed_by(title, held):
            problems.append(f'{where} points at docs/FINDINGS.md, "{title}", which is not a section there')
    return problems


class ClaudeMdStaysSmallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.claude = CLAUDE_MD.read_text(encoding="utf-8")
        cls.findings = FINDINGS_MD.read_text(encoding="utf-8")
        cls.architecture = ARCHITECTURE_MD.read_text(encoding="utf-8")

    def test_the_readers_find_what_is_known_before_they_are_trusted(self):
        """A parser that finds nothing makes every assertion after it vacuous (rc.57)."""
        self.assertIn("Standing rules", headings(self.claude), "the heading reader cannot find CLAUDE.md's rules")
        self.assertIn("The NPC life cycle (v1.0.0-rc.24)", headings(self.findings))
        self.assertIn("Narration routing", headings(self.architecture))
        self.assertIn("docs/FINDINGS.md", named_paths(self.claude), "the path reader finds nothing in CLAUDE.md")
        self.assertIn("docs/ARCHITECTURE.md", named_paths(self.claude))
        todo = TODO_MD.read_text(encoding="utf-8")
        self.assertGreaterEqual(
            len(FINDINGS_POINTER.findall(flatten(todo))), 15,
            "the pointer reader finds almost none of docs/TODO.md's pointers at the findings",
        )
        self.assertTrue(RELEASE_LABEL.search("Standing rules (rc.48)"))
        self.assertGreaterEqual(len(self.claude.split("\n")), 100)

    def test_the_moved_files_hold_what_was_moved(self):
        """FINDINGS and ARCHITECTURE are the other half of the slim-down: empty ones would make
        the pointers decoration."""
        findings_sections = [h for h in self.findings.split("\n") if h.startswith("### ")]
        self.assertGreaterEqual(len(findings_sections), 140, "docs/FINDINGS.md lost its release write-ups")
        self.assertGreater(len(self.findings), 400_000)
        self.assertIn("\n## What each release found\n", self.findings)
        architecture_sections = [h for h in self.architecture.split("\n") if h.startswith("### ")]
        self.assertGreaterEqual(len(architecture_sections), 13, "docs/ARCHITECTURE.md lost its subsystem notes")
        self.assertGreater(len(self.architecture), 25_000)

    def test_claude_md_stays_under_the_cap(self):
        self.assertEqual(size_problem(self.claude), [])

    def test_no_heading_carries_a_release_label(self):
        self.assertEqual(heading_problems(self.claude), [])

    def test_nothing_is_imported(self):
        self.assertEqual(import_problems(self.claude), [])

    def test_the_phrases_the_tree_quotes_are_still_said(self):
        self.assertEqual(phrase_problems(self.claude), [])

    def test_every_path_it_names_exists(self):
        self.assertEqual(path_problems(self.claude), [])

    def test_every_subsystem_note_names_a_section_and_every_section_has_a_note(self):
        self.assertEqual(subsystem_problems(self.claude, self.architecture), [])

    def test_every_release_it_cites_is_in_the_findings(self):
        rules = standing_rules(self.claude)
        self.assertGreater(len(RELEASE_LABEL.findall(rules)), 30, "the rules reader found no releases cited")
        self.assertEqual(findings_label_problems(rules, self.findings), [])

    def test_every_pointer_at_a_findings_section_resolves(self):
        problems = []
        problems += pointer_problems(TODO_MD.read_text(encoding="utf-8"), self.findings, "docs/TODO.md")
        problems += pointer_problems(self.architecture, self.findings, "docs/ARCHITECTURE.md")
        problems += pointer_problems(self.claude, self.findings, "CLAUDE.md")
        self.assertEqual(problems, [])


class TheGatesFailOnTheTextTheyForbid(unittest.TestCase):
    """The drills, kept inside the suite so each rule is shown to see the fault it was written for
    on every run rather than once (rc.47)."""

    def test_a_text_over_the_cap_is_refused_with_a_sentence_naming_it(self):
        problems = size_problem("x" * 41_000)
        self.assertEqual(len(problems), 1)
        self.assertIn("41,000 characters; the cap is 40,000", problems[0])
        self.assertEqual(size_problem("x" * 40_000), [], "the cap itself is allowed")

    def test_the_real_file_is_far_from_the_cap(self):
        """Headroom is the point: a file that sits at the cap is refused by the next paragraph."""
        self.assertLess(len(CLAUDE_MD.read_text(encoding="utf-8")), CAP - 5_000)

    def test_a_release_heading_is_refused(self):
        for heading in (
            "### The window nobody closed (v1.34.0)",
            "### What the household teaches (v1.0.0-rc.31)",
            "### A thing (`thing.go`, rc.28)",
            "## Findings (schema 79, v1.25.0)",
        ):
            with self.subTest(heading=heading):
                self.assertEqual(len(heading_problems(heading + "\n")), 1)
        self.assertEqual(heading_problems("### Go layout (`go_core/`)\n### Standing rules\n"), [])
        self.assertEqual(heading_problems("```\n### Not a heading (v1.2.3)\n```\n"), [])

    def test_an_import_is_refused(self):
        self.assertEqual(len(import_problems("text\n@docs/FINDINGS.md\n")), 1)
        self.assertEqual(len(import_problems("see @docs/ARCHITECTURE.md for it\n")), 1)
        self.assertEqual(import_problems("Never `@import` either file.\n"), [])

    def test_a_dropped_quoted_phrase_is_named(self):
        problems = phrase_problems("nothing of what the tree quotes")
        self.assertEqual(len(problems), len(QUOTED_FROM_CODE))
        self.assertTrue(any("never a status read" in p for p in problems))
        self.assertTrue(any("It is a script, not CI" in p for p in problems))

    def test_a_phrase_wrapped_across_lines_is_still_the_phrase(self):
        wrapped = "never a status\nread or the door into the system, because a road nobody can see is a road\nnobody learns exists."
        table = {"never a status read or the door into the system, because a road nobody can see is a road nobody learns exists": []}
        self.assertEqual(phrase_problems(wrapped, table), [])

    def test_a_path_that_does_not_exist_is_named(self):
        problems = path_problems("see `docs/NOT_A_FILE.md` and `tests/python/unit/`")
        self.assertEqual(problems, ["`docs/NOT_A_FILE.md` is named in CLAUDE.md and does not exist"])
        self.assertEqual(path_problems("see `no_such_file_anywhere.py`"),
                         ["`no_such_file_anywhere.py` is named in CLAUDE.md and no file of that name exists"])
        self.assertEqual(path_problems("```\nls docs/NOT_A_FILE.md\n```\n"), [])
        self.assertEqual(path_problems("the engine's `internal/game/` package"), [])

    def test_a_subsystem_without_a_section_or_a_note_is_named(self):
        architecture = "# Architecture notes\n\n### Alpha (`a.go`)\n\n### Beta\n"
        claude = "### Subsystem notes (`docs/ARCHITECTURE.md`)\n\n- **Alpha** (`a.go`) - x\n- **Gamma** - y\n\n## Standing rules\n"
        problems = subsystem_problems(claude, architecture)
        self.assertTrue(any('"Gamma" has no `###` section' in p for p in problems), problems)
        self.assertTrue(any('"Beta" that CLAUDE.md does not note' in p for p in problems), problems)

    def test_a_release_the_findings_never_mention_is_named(self):
        findings = "### A section (v1.0.0-rc.48)\n### Another (v1.7.1)\n"
        self.assertEqual(findings_label_problems("rule (rc.48) and (v1.7.1)", findings), [])
        problems = findings_label_problems("rule (rc.99) and (v1.7.10) and (v1.0.0-rc.48)", findings)
        self.assertEqual(len(problems), 2, problems)
        self.assertTrue(any("rc.99" in p for p in problems))
        self.assertTrue(any("v1.7.10" in p for p in problems), "v1.7.1 must not satisfy v1.7.10")

    def test_a_broken_pointer_is_named(self):
        findings = "### A real section (v1.2.3)\n"
        todo = 'fixed. See docs/FINDINGS.md, "A real\n  section". And See docs/FINDINGS.md, "No Such Title".'
        problems = pointer_problems(todo, findings, "docs/TODO.md")
        self.assertEqual(problems, ['docs/TODO.md points at docs/FINDINGS.md, "No Such Title", which is not a section there'])


if __name__ == "__main__":
    unittest.main()
