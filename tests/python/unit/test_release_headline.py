"""What a server is told about a release is derived, so the entry has to parse.

`#updates` gets one line per release (rc.59), and it is **derived, never
authored twice**: `release_headline` takes the opening sentence of the entry
`release_notes_for` found. That makes two properties of `VERSIONS.md` load-
bearing in a way nothing was checking, and v1.0.1 broke both at once by
writing its entry as three paragraphs each beginning `**1.0.1**`:

1. **An entry is one header.** `_ENTRY` matches any line opening with a version
   stamp, so a second `**1.0.1**` mid-entry starts a *new* entry - the notes
   silently truncate to the first paragraph and the rest of the release is
   never reported anywhere.
2. **The first paragraph leads.** Continuation paragraphs are written "It
   also…" / "And…", so the first sentence is the release's headline. Reorder
   them and a server is told about whatever now happens to be first - in the
   case that prompted this, a *test assertion*, with the two things players
   actually got left unmentioned and the line reading "**v1.0.1** also fixes…".

Neither is visible from a source read of the bot, and neither shows up in any
suite: the changelog is prose, and the only thing that had ever been held about
it is that the stamped version has an entry at all
(`test_release_version.py`).
"""
from __future__ import annotations

import re
import unittest

from tests.support import PROJECT_ROOT

VERSIONS = (PROJECT_ROOT / "VERSIONS.md").read_text(encoding="utf-8")
CHANGELOG = VERSIONS.split("## Changelog", 1)[-1]

# The same expression `release_notes.py` uses to find an entry; read from the
# source rather than copied, so a change there fails here instead of drifting.
RELEASE_NOTES = (PROJECT_ROOT / "app" / "bot" / "admin" / "release_notes.py").read_text(encoding="utf-8")


def entry_pattern() -> re.Pattern[str]:
    match = re.search(r'_ENTRY = re\.compile\(\s*r"(?P<pattern>[^"]+)"', RELEASE_NOTES)
    if not match:
        raise AssertionError("_ENTRY is no longer a compiled literal; the reader is broken, not the tree")
    return re.compile(match.group("pattern"), re.M)


def entries() -> list[tuple[str, str]]:
    """(version, text) per entry, split exactly as the bot splits them."""
    pattern = entry_pattern()
    marks = [(m.start(), m.group("version")) for m in pattern.finditer(CHANGELOG)]
    out = []
    for i, (start, version) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(CHANGELOG)
        out.append((version, CHANGELOG[start:end].strip()))
    return out


class EveryReleaseIsOneEntryThatLeadsWithItself(unittest.TestCase):
    def test_the_reader_found_the_changelog(self):
        # Asserted before it is trusted: an empty parse would make every check
        # below pass by finding nothing to disagree with.
        found = entries()
        self.assertGreater(len(found), 3, "the changelog parsed to almost nothing")
        self.assertIn("1.0.0", [version for version, _ in found])

    def test_no_version_is_split_across_two_entries(self):
        """A repeated stamp truncates the notes at the repeat."""
        seen = [version for version, _ in entries()]
        duplicates = sorted({v for v in seen if seen.count(v) > 1})
        self.assertEqual(
            duplicates, [],
            "these versions open more than one entry, so `release_notes_for` stops at the second "
            "and everything after it is never announced; continuation paragraphs are written "
            '"It also…" / "And…", not with another version stamp',
        )

    def test_no_entry_opens_by_continuing_something(self):
        """The first sentence is the headline, so it cannot begin mid-thought."""
        offenders = []
        for version, text in entries():
            body = re.sub(r"^\*\*[\d.]+\*\*\s*(?:\(rc\.\d+\)\s*)?", "", text.split("\n\n")[0])
            if re.match(r"^(also|and|it also|too)\b", body.strip(), re.I):
                offenders.append(f"{version}: {body.strip()[:60]}")
        self.assertEqual(
            offenders, [],
            "an entry whose first sentence continues a previous one becomes the #updates line "
            'verbatim, e.g. "v1.0.1 also fixes…" - reorder so the release leads with itself',
        )

    def test_every_headline_is_a_sentence_that_fits_a_message(self):
        # Read off the source rather than imported: importing the module builds
        # SETTINGS, which wants a DISCORD_TOKEN this unit test has no business
        # supplying.
        limit = re.search(r"^MESSAGE_LIMIT = (\d+)", RELEASE_NOTES, re.M)
        self.assertIsNotNone(limit, "MESSAGE_LIMIT is no longer a literal")
        MESSAGE_LIMIT = int(limit.group(1))
        for version, text in entries():
            with self.subTest(version=version):
                para = text.split("\n\n")[0].replace("\n", " ").strip()
                body = re.sub(r"^\*\*[\d.]+\*\*\s*(?:\(rc\.\d+\)\s*)?", "", para)
                end = re.search(r"(?<=[a-z0-9)`])\.(?=\s|$)", body)
                headline = body[: end.start() + 1] if end else body
                self.assertTrue(headline, f"{version} has no headline sentence")
                self.assertLess(len(headline), MESSAGE_LIMIT,
                                f"{version}'s opening sentence alone overruns a Discord message")


if __name__ == "__main__":
    unittest.main()
