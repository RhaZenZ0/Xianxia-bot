"""Stepping back into the world must open the room your scenes are written in.

Reported by a player: after leaving the birth household there was no personal
expedition thread until they happened to run ``/explore``. The exit worked, the
location was right, and the place their own scenes go simply did not exist yet.

``PRIVATE_LOCATION_EXITS`` lists four private locations and the command that
steps out of each - birth household, sect residence, own property, personal
world. All of them land the player back in the shared world, so all of them need
the journal. Only one was reported; none of the three handlers had it.

This test derives the handlers from ``PRIVATE_LOCATION_EXITS`` itself rather than
naming them, so adding a fifth private location without wiring its exit fails
here instead of in a player's face.
"""

import re
import unittest

from tests.support import PROJECT_ROOT

# Scan the whole bot package, not main.py. The decomposition moves handlers out
# - /family's leave landed in app/bot/commands/family.py in split stage 2 - and a
# check anchored to one file silently stops covering them the moment they move.
# This test was written against main.py and went red the day family moved, which
# is the right failure but for the wrong reason.
BOT_DIR = PROJECT_ROOT / "app" / "bot"
BOT = "\n".join(
    path.read_text(encoding="utf-8") for path in sorted(BOT_DIR.rglob("*.py"))
)
RUNTIME = (BOT_DIR / "runtime.py").read_text(encoding="utf-8")

HELPER = "open_expedition_thread_after_exit"


def _exit_hubs() -> set[str]:
    """The hub behind each entry of PRIVATE_LOCATION_EXITS, e.g. {'family', ...}."""
    block = re.search(r"PRIVATE_LOCATION_EXITS[^=]*=\s*\((.*?)\n\)", RUNTIME, re.S)
    assert block, "PRIVATE_LOCATION_EXITS not found"
    return set(re.findall(r"/([a-z]+)\s*(?:→|->)\s*Leave", block.group(1)))


def _leave_handler(hub: str) -> tuple[str, str]:
    """(function name, body) for `<hub>_group` name="leave"."""
    pattern = (
        rf'@registered_group_command\({hub}_group, ?name="leave"[^\n]*\n'
        rf'(?:@\w+[^\n]*\n)*'
        rf'async def (\w+)\('
    )
    match = re.search(pattern, BOT)
    assert match, f"no leave handler found for /{hub}"
    start = match.end()
    nxt = re.search(r"^(?:@registered_|async def |def |class )", BOT[start:], re.M)
    return match.group(1), BOT[start : start + (nxt.start() if nxt else 2000)]


class ExitOpensTheJournalTests(unittest.TestCase):
    def setUp(self):
        self.hubs = _exit_hubs()

    def test_the_exit_table_was_actually_parsed(self):
        # A parser that matched nothing would make every check below vacuous.
        self.assertGreaterEqual(len(self.hubs), 3)
        self.assertIn("family", self.hubs)

    def test_every_private_location_exit_opens_the_expedition_journal(self):
        missing = []
        for hub in sorted(self.hubs):
            name, body = _leave_handler(hub)
            if HELPER not in body:
                missing.append(f"/{hub} -> {name}() never opens the expedition journal")
        self.assertEqual(missing, [], "\n" + "\n".join(missing))

    def test_the_journal_opens_after_the_move_is_confirmed(self):
        """The helper must come after EVERY reply in the handler.

        Measuring the first send is not enough: the first one in these handlers
        is the GameEngineError branch, which always precedes the helper whatever
        order the success path is in. The rule that actually matters is that the
        journal is opened last, so a thread that cannot be created delays or
        breaks nothing.
        """
        for hub in sorted(self.hubs):
            _name, body = _leave_handler(hub)
            last_reply = max(
                body.rfind("interaction.response.send_message("),
                body.rfind("interaction.followup.send("),
            )
            self.assertGreater(last_reply, -1, f"/{hub} sends no confirmation at all")
            self.assertGreater(body.index(HELPER), last_reply, hub)


class HelperContractTests(unittest.TestCase):
    def setUp(self):
        start = BOT.index(f"async def {HELPER}(")
        nxt = re.search(r"^async def ", BOT[start + 10 :], re.M)
        self.body = BOT[start : start + 10 + (nxt.start() if nxt else 2000)]

    def test_the_character_is_re_read_rather_than_reused(self):
        # Every caller fetched its copy BEFORE the engine moved them; reusing it
        # would stamp the journal with the location they just walked out of.
        self.assertIn("await DB.get_character(", self.body)

    def test_it_refuses_when_the_exit_led_into_another_private_location(self):
        self.assertIn("private_location_exit(", self.body)

    def test_it_never_raises(self):
        self.assertIn("except Exception:", self.body)
        self.assertIn("log.warning(", self.body)

    def test_it_uses_a_followup_so_it_cannot_stall_the_interaction(self):
        self.assertIn("interaction.followup.send(", self.body)
        self.assertNotIn("interaction.response.send_message(", self.body)

    def test_it_names_the_thread_so_the_player_can_find_it(self):
        self.assertIn("thread.mention", self.body)


if __name__ == "__main__":
    unittest.main()
