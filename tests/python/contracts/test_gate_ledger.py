"""The gate ledger: every standing rule names the test that holds it, or says why none can.

`docs/ROADMAP_2_0.md`'s first bar is that every fault class has its gate. The thirty-odd standing
rules in `CLAUDE.md` each carry the release that found them, and before v1.40 about half named the
test that holds them while the rest were prose - and a rule that is prose alone is the rule the next
review finds broken (thirteen faults in v1.33.0, thirty-seven in v1.34.0, each an instance of a rule
already written). `docs/GATES.md` is the ledger: one row per standing rule, the class it names, the
gate that reads that class, and what the gate's drill prints - or the word `prose` with the reason
no reader can exist.

This file holds the ledger to the rules and the rules to the ledger:

- every standing rule in `CLAUDE.md` has a row, keyed on the rule's bold lead-in, verbatim;
- every row names a rule that is still there, so a rule renamed or retired takes its row with it;
- every gate a row names exists - a Python test the collector would find, by file and name, or a Go
  test function by file and name - so a renamed test cannot leave the ledger pointing at nothing;
- a `prose` row gives a reason, and a row that names a class is never `prose`;
- the two parsers are asked for something known before any assertion runs (rc.57).

The ledger is *where a gate is recorded*, not what proves it: whether a gate reads its class by AST
or exercises an instance is a column a person fills and the drill column is what they saw. What this
file makes impossible is a rule with no row, and a row with no test behind it.
"""
from __future__ import annotations

import re
import unittest

from tests.support import PROJECT_ROOT

CLAUDE_MD = PROJECT_ROOT / "CLAUDE.md"
LEDGER_MD = PROJECT_ROOT / "docs" / "GATES.md"

# A standing rule is a bullet whose first words are bold: `- **One door per rule.** A rule ...`.
# The lead-in is everything inside the first bold span, punctuation included, so a rule whose bold
# ends in a comma (`**... (rc.46),**`) keys on exactly what CLAUDE.md shows.
RULE = re.compile(r"^- \*\*(.+?)\*\*", re.M)

# A gate is `path::Name` - the Python collector's own spelling for a test function, or a Go test
# file and its `TestName`; `::Class::test_name` is the method spelling. A row may name several,
# separated by `;`.
PYTHON_GATE = re.compile(r"^(tests/[\w./-]+\.py)::([\w:]+)$")
GO_GATE = re.compile(r"^(go_core/[\w./-]+_test\.go)::(Test\w+)$")


def standing_rules(text: str = None) -> list[str]:
    """The bold lead-in of every bullet under `## Standing rules`, in order."""
    text = CLAUDE_MD.read_text(encoding="utf-8") if text is None else text
    start = text.index("## Standing rules")
    end = text.find("\n## ", start + 1)
    section = text[start:end if end > 0 else None]
    return RULE.findall(section)


def ledger_rows(text: str = None) -> list[dict[str, str]]:
    """Every data row of the ledger's table as {rule, class, gate, drill}.

    The ledger is one Markdown table whose header is `| Rule | Class | Gate | Drill |`; a cell may
    not contain a bare `|`, which is why a gate list is `;`-separated.
    """
    text = LEDGER_MD.read_text(encoding="utf-8") if text is None else text
    rows: list[dict[str, str]] = []
    in_table = False
    for line in text.splitlines():
        if line.startswith("| Rule |"):
            in_table = True
            continue
        if not in_table or not line.startswith("|"):
            in_table = in_table and line.startswith("|")
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells[0].startswith("---"):
            continue
        if len(cells) != 4:
            raise AssertionError(f"a ledger row is not four cells: {line!r}")
        rows.append(dict(zip(("rule", "class", "gate", "drill"), cells)))
    return rows


def _unbold(cell: str) -> str:
    return cell.strip().strip("*").strip()


def gate_exists(gate: str) -> str | None:
    """None when `gate` names a test that exists, else the reason it does not."""
    py = PYTHON_GATE.match(gate)
    go = GO_GATE.match(gate)
    if py:
        path, name = py.groups()
        source = PROJECT_ROOT / path
        if not source.exists():
            return f"{path} does not exist"
        text = source.read_text(encoding="utf-8")
        # `Class::test_name` holds the class and the method; a bare name holds the function.
        for part in name.split("::"):
            if not re.search(rf"^\s*(?:async\s+)?(?:def|class)\s+{re.escape(part)}\b", text, re.M):
                return f"{path} has no `{part}`"
        return None
    if go:
        path, name = go.groups()
        source = PROJECT_ROOT / path
        if not source.exists():
            return f"{path} does not exist"
        if not re.search(rf"^func {re.escape(name)}\(", source.read_text(encoding="utf-8"), re.M):
            return f"{path} has no `func {name}(`"
        return None
    return f"{gate!r} is neither `tests/...py::name` nor `go_core/..._test.go::TestName`"


class TheReadersWork(unittest.TestCase):
    """rc.57: a reader that finds nothing makes every assertion after it vacuous."""

    def test_the_rule_parser_finds_a_rule_it_knows(self):
        rules = standing_rules()
        self.assertIn("One door per rule.", rules)
        self.assertGreater(len(rules), 25, "the Standing rules section read nearly empty")

    def test_the_rule_parser_reads_only_the_standing_rules(self):
        # The testing conventions below the section are bold-led bullets too and are not rules.
        self.assertNotIn("A fixture must carry the constraints production carries.", standing_rules())

    def test_the_ledger_parser_finds_a_row_it_knows(self):
        rows = ledger_rows()
        self.assertTrue(rows, "docs/GATES.md has no table rows")
        self.assertIn("One door per rule.", [_unbold(r["rule"]) for r in rows])

    def test_the_gate_checker_can_see_a_missing_test(self):
        self.assertIsNotNone(gate_exists("tests/python/contracts/test_gate_ledger.py::test_no_such_test"))
        self.assertIsNotNone(gate_exists("go_core/internal/game/no_such_file_test.go::TestNothing"))
        self.assertIsNotNone(gate_exists("prose"))
        self.assertIsNone(gate_exists("tests/python/contracts/test_gate_ledger.py::TheReadersWork::test_the_gate_checker_can_see_a_missing_test"))


class EveryRuleHasARowAndEveryRowARule(unittest.TestCase):
    def setUp(self):
        self.rules = standing_rules()
        self.rows = ledger_rows()
        self.by_rule = {_unbold(r["rule"]): r for r in self.rows}

    def test_every_standing_rule_has_a_row(self):
        missing = [rule for rule in self.rules if rule not in self.by_rule]
        self.assertEqual(missing, [], "standing rules with no row in docs/GATES.md (key on the bold lead-in, verbatim)")

    def test_every_row_names_a_standing_rule(self):
        stale = [rule for rule in self.by_rule if rule not in self.rules]
        self.assertEqual(stale, [], "ledger rows whose rule is no longer in CLAUDE.md")

    def test_no_rule_has_two_rows(self):
        seen = [_unbold(r["rule"]) for r in self.rows]
        self.assertEqual(sorted(set(seen)), sorted(seen), "a rule is listed twice")


class EveryGateExists(unittest.TestCase):
    def test_every_gate_a_row_names_is_a_test_in_the_tree(self):
        for row in ledger_rows():
            if row["gate"] == "prose":
                continue
            gates = [g.strip().strip("`") for g in row["gate"].split(";") if g.strip()]
            self.assertTrue(gates, f"{row['rule']}: an empty gate cell")
            for gate in gates:
                with self.subTest(rule=_unbold(row["rule"]), gate=gate):
                    self.assertIsNone(gate_exists(gate), gate_exists(gate))

    def test_a_prose_row_says_why_no_reader_can_hold_it(self):
        for row in ledger_rows():
            if row["gate"] != "prose":
                continue
            with self.subTest(rule=_unbold(row["rule"])):
                self.assertGreater(len(row["drill"]), 40, "a prose row needs the reason no reader can exist")
                self.assertEqual(row["class"], "—", "a row that names a class is never prose: it names the walk to write")
